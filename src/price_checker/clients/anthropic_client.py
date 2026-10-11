import json
import re
from collections.abc import Callable
from datetime import datetime, timezone

import anthropic
import boto3
from aws_lambda_powertools import Logger

from price_checker.config import settings
from price_checker.clients.prices_client import recent_moves
from price_checker.models import INDEXES, Explanation, Level, Price, Turn

logger = Logger(child=True)

MODEL = "claude-sonnet-5-5"
AUDIENCE = {
    Level.KID: "a curious 10-year-old",
    Level.BEGINNER: "a beginner investor who knows the basics",
    Level.PRO: "an experienced investor",
}

STYLE = {
    Level.KID: "Use short sentences and everyday words; keep the whole report under about 150 words. "
    "Define any finance word simply in terms.",
    Level.BEGINNER: "Use plain language, under about 200 words. Define finance terms in terms.",
    Level.PRO: "Be concise and specific: include figures from the news (other companies' % moves, guidance, "
    "data). Standard market terms need no definitions, so leave terms empty.",
}
# What each index contains, so the model can tell which companies may be named as its drivers.
MEMBERS = {
    "IXIC": "It contains only stocks listed on the Nasdaq exchange; NYSE-listed companies such as Oracle or "
    "Crown Castle are not in it.",
    "NDX": "It holds the ~100 largest non-financial companies listed on Nasdaq; NYSE-listed companies are not in it.",
    "GSPC": "It holds ~500 large US companies listed on the NYSE or Nasdaq.",
    "DJI": "It holds just 30 large US companies.",
    "RUT": "It holds ~2,000 small US companies, not large ones like Nvidia or Apple.",
}

# A strict client tool rather than output_config.format, which rejects web search's citations.
# The final answer arrives as this tool's input (stop_reason "tool_use").
SUBMIT_TOOL = {
    "name": "submit_report",
    "description": "Submit your final explanation. Call it exactly once, after searching.",
    "strict": True,
    "input_schema": anthropic.transform_schema(Explanation),
}
# Required for the model only; stored reports from before tickers existed still load with the default.
SUBMIT_TOOL["input_schema"]["$defs"]["Reason"]["required"].append("tickers")


def _system(level: Level) -> str:
    return (
        f"You explain why a stock or market index moved, for {AUDIENCE[level]}. {STYLE[level]}\n\n"
        "The user message gives recent daily closing prices from Yahoo Finance. They are the facts about how "
        "much it moved and when: explain the move over the trading days shown, focusing on the days it names. "
        "Don't state a price or % move for it that the table doesn't support; if an article's numbers disagree "
        "with the table, go with the table. If no prices are given, find the recent move in the news.\n\n"
        "Your job is the why. Search for news about each day named (put the name and the date in your "
        "queries), until you've found its cause or are confident there isn't a specific one (for example, the "
        "whole market moved). Base every reason on an article from your search results, preferring news "
        "articles from reputable outlets over quote pages. If you can't find a cause, say so plainly instead "
        "of guessing, and never invent numbers, news, or reasons.\n\n"
        "When done, call submit_report with a one-sentence summary, 2-4 short reasons, and a one-sentence "
        "bottom line. Never tell anyone to buy or sell, and don't add a not-financial-advice disclaimer."
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
        # No-search lookup: map a ticker / company name / index / typo to one US ticker symbol.
        # Thinking counts toward max_tokens, so leave headroom even at low effort.
        msg = self.client.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={
                "effort": "low",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"symbol": {"type": ["string", "null"]}},
                        "required": ["symbol"],
                        "additionalProperties": False,
                    },
                },
            },
            cache_control={"type": "ephemeral"},
            system=(
                "Map the user's input to one US ticker symbol. The input may be a ticker, a company name, a "
                "misspelling, or a market index / exchange. For an index or exchange, use one of these index "
                f"symbols: {', '.join(f'{s} ({n})' for s, n in INDEXES.items())}; a bare 'Nasdaq' means the "
                "Composite. Use an ETF such as QQQ, SPY, or DIA only when the input names the ETF. For a "
                "company with several share classes, use the most traded one (e.g. BRK.B). Give the uppercase "
                "symbol, or null if nothing listed in the US matches (e.g. a cryptocurrency)."
            ),
            messages=[{"role": "user", "content": query}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        symbol = (json.loads(text)["symbol"] or "").strip().upper() if text else ""
        resolved = symbol if re.fullmatch(r"[A-Z0-9.]{1,10}", symbol) else None
        logger.info(
            "ticker resolved",
            extra={
                "query": query,
                "reply": text,
                "resolved": resolved,
                "model": MODEL,
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            },
        )
        return resolved

    def answer_follow_up(self, ticker: str, level: Level, report: str, turns: list[Turn], question: str) -> str:
        # Grounded in the report only (no web search) so it answers synchronously within CloudFront's 30s timeout.
        messages: list[dict] = []
        for i, q in enumerate([t.question for t in turns] + [question]):
            context = f"<explanation ticker=\"{ticker}\">\n{report}\n</explanation>\n\n" if i == 0 else ""
            messages.append({"role": "user", "content": f"{context}Question: {q}"})
            if i < len(turns):
                messages.append({"role": "assistant", "content": turns[i].answer})
        msg = self.client.messages.create(
            model=MODEL,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=(
                f"You answer a follow-up question about a stock-move explanation, for {AUDIENCE[level]}. "
                "Base your answer on the explanation and price table provided; you can't search the web, so if "
                "they don't cover the question, say so plainly instead of guessing, and don't invent numbers or news. "
                "Reply in one short paragraph of at most 3 sentences, friendly markdown, and briefly define any "
                "jargon. Never tell anyone to buy or sell, and don't add a not-financial-advice disclaimer."
            ),
            messages=messages,
            cache_control={"type": "ephemeral"},
        )
        logger.info(
            "follow-up answered",
            extra={
                "ticker": ticker,
                "model": MODEL,
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            },
        )
        if msg.stop_reason == "refusal":
            return "Sorry, I can't help with that question."
        return "".join(b.text for b in msg.content if b.type == "text")

    def generate_report(
        self,
        ticker: str,
        level: Level,
        prior: tuple[str, int] | None,
        on_search: Callable[[str], None],
        prices: list[Price],
    ) -> Explanation:
        name = f"the {INDEXES[ticker]} index" if ticker in INDEXES else f"{ticker} stock"
        focus = (
            f" It's a market index. {MEMBERS[ticker]} Its move is the sum of its members' moves: search for its "
            "biggest gainers and losers on those days, and name the member companies whose moves pushed it most "
            "(with their rough % moves), plus any sector or market-wide news."
            if ticker in INDEXES
            else ""
        )
        # Real prices pin down how much it moved and when, so the model only has to explain why.
        moves = recent_moves(prices) if len(prices) > 1 else None
        facts = ""
        if moves:
            table, _, days = moves
            big = sorted(days, key=lambda d: -abs(d[1]))[:2]
            facts = f"\n\n{table}\nExplain this move, especially {' and '.join(f'{d} ({c:+.2f}%)' for d, c in big)}."
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # With a prior report, do a leaner "what's changed" search; from scratch, search wider.
        if prior:
            since = datetime.fromtimestamp(prior[1], timezone.utc).strftime("%Y-%m-%d")
            user = (
                f"Today is {today}. Previous explanation for {ticker}:\n\n{prior[0]}\n\nThis may be outdated. "
                f"Search the latest news, confirm what's still true, find anything new, and give an updated "
                f"explanation of why {name} moved. In changes, say in 1-2 sentences what's new since {since}, or "
                f"'Nothing major is new.'{focus}{facts}"
            )
            max_uses = 5
        else:
            user = f"Today is {today}. Why did {name} move?{focus}{facts}"
            max_uses = 8

        system = _system(level)
        # The basic search tool: in live tests the dynamic-filtering web_search_20260209 often sent malformed
        # searches that failed and used up max_uses, leaving reports without causes.
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}, SUBMIT_TOOL]
        messages = [{"role": "user", "content": user}]

        in_tok = out_tok = searches = 0
        for _ in range(4):
            # Streamed so each web search can be reported as it happens, not only once the turn ends.
            with self.client.messages.stream(
                model=MODEL,
                max_tokens=16000,
                output_config={"effort": "medium"},
                system=system,
                tools=tools,
                messages=messages,
                cache_control={"type": "ephemeral"},
            ) as stream:
                for event in stream:
                    if (
                        event.type == "content_block_stop"
                        and event.content_block.type == "server_tool_use"
                        and event.content_block.name == "web_search"
                    ):
                        on_search(event.content_block.input["query"])
                response = stream.get_final_message()
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
                "level": level,
                "model": MODEL,
                "incremental": prior is not None,
                "max_uses": max_uses,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "web_search_requests": searches,
            },
        )

        submitted = next((b for b in response.content if b.type == "tool_use"), None)
        if submitted is None:
            raise RuntimeError(f"Claude finished without submitting a report (stop_reason: {response.stop_reason})")
        explanation = Explanation.model_validate(submitted.input)
        if moves:
            total = moves[1]
            explanation.pctMove = round(total, 2)
            explanation.direction = "up" if total > 0.1 else "down" if total < -0.1 else "flat"
        return explanation
