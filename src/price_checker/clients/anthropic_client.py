import re
from datetime import datetime, timezone

import anthropic
import boto3
from aws_lambda_powertools import Logger

from price_checker.config import settings

logger = Logger(child=True)

MODEL = "claude-sonnet-4-6"
RESOLVE_MODEL = "claude-haiku-4-5"


def _system(today: str) -> str:
    return (
        f"You explain why a stock moved, to a curious 10-year-old. Today is {today}. "
        "Use the web_search tool to find the most RECENT news for the ticker before answering, and base "
        "every statement on what you find — prefer reputable, recent sources. "
        "Say whether the stock recently went up or down (and the rough size of the move if you find it) and "
        "WHY, in plain, friendly language. "
        "Structure: a one-line summary, then 2-4 short bullets of the main reasons, then a one-sentence "
        "'Bottom line'. Keep it under ~150 words and skimmable. Avoid jargon; briefly define any term you "
        "must use. Use markdown. Mention roughly when the news is from. "
        "Honesty: do not invent numbers, news, or reasons. If you can't find recent news, or the move had no "
        "clear single cause (e.g. the whole market moved), say so plainly instead of guessing. If the ticker "
        "doesn't match a real company you can find, say you couldn't find it. "
        "This is an explanation, not financial advice — never tell anyone to buy or sell. "
        "If a previous explanation is provided, treat it as possibly outdated: verify it against fresh search "
        "results, keep what still holds, correct what changed, and note if nothing major is new."
    )


class AnthropicClient:
    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            key = boto3.client("secretsmanager").get_secret_value(SecretId=settings.anthropic_secret_arn)[
                "SecretString"
            ]
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def resolve_ticker(self, query: str) -> str | None:
        # Cheap, no-search lookup: map a ticker / company name / typo to one US ticker symbol.
        msg = self.client.messages.create(
            model=RESOLVE_MODEL,
            max_tokens=16,
            cache_control={"type": "ephemeral"},
            system=(
                "Map the user's input to one US stock ticker symbol. The input may be a ticker, a company "
                "name, or a misspelling. Reply with ONLY the uppercase ticker (e.g. BA), or NONE if it isn't "
                "a real publicly-traded company."
            ),
            messages=[{"role": "user", "content": query}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip().upper()
        token = text.split()[0].strip(".,") if text else ""
        resolved = token if token and token != "NONE" and re.fullmatch(r"[A-Z0-9.]{1,10}", token) else None
        logger.info(
            "ticker resolved",
            extra={
                "query": query,
                "resolved": resolved,
                "model": RESOLVE_MODEL,
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            },
        )
        return resolved

    def generate_report(self, ticker: str, prior: tuple[str, int] | None = None) -> tuple[str, list[str]]:
        # With a prior report, do a leaner "what's changed" search; from scratch, search wider.
        if prior:
            body = prior[0].split("\n\n## Sources")[0]
            user = (
                f"Previous explanation for {ticker}:\n\n{body}\n\nThis may be outdated. Search the latest news, "
                f"confirm what's still true, find anything new, and give an updated, concise ELI5 of why {ticker} "
                "moved recently."
            )
            max_uses = 3
        else:
            user = f"Why did {ticker} stock move recently? Explain like I'm 5."
            max_uses = 5

        system = _system(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
        messages = [{"role": "user", "content": user}]

        in_tok = out_tok = searches = 0
        for _ in range(4):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=system,
                tools=tools,
                messages=messages,
                cache_control={"type": "ephemeral"},
            )
            in_tok += response.usage.input_tokens
            out_tok += response.usage.output_tokens
            stu = getattr(response.usage, "server_tool_use", None)
            searches += getattr(stu, "web_search_requests", 0) or 0
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})

        # Logged on every run so incremental vs full token cost can be compared/analyzed in CloudWatch.
        logger.info(
            "report generated",
            extra={
                "ticker": ticker,
                "model": MODEL,
                "incremental": prior is not None,
                "max_uses": max_uses,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "web_search_requests": searches,
            },
        )

        report = "".join(b.text for b in response.content if b.type == "text")
        sources: list[str] = []
        links: list[str] = []

        def add(url: str | None, title: str | None) -> None:
            if url and url not in sources and len(sources) < 10:
                sources.append(url)
                links.append(f"- [{title or url}]({url})")

        # web_search_20260209 filters results via code and doesn't attach inline citations, so pull
        # the sources from the search-result blocks. (Inline citations, when present, are used first.)
        for block in response.content:
            for citation in getattr(block, "citations", None) or []:
                add(getattr(citation, "url", None), getattr(citation, "title", None))
        if not links:
            for block in response.content:
                if block.type == "web_search_tool_result":
                    for result in getattr(block, "content", None) or []:
                        add(getattr(result, "url", None), getattr(result, "title", None))

        if links:
            report += "\n\n## Sources\n\n" + "\n".join(links)
        return report, sources
