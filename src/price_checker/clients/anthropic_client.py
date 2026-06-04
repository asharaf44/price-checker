import anthropic
import boto3

from price_checker.config import settings

MODEL = "claude-sonnet-4-6"
SYSTEM = (
    "You explain stock moves to a curious 10-year-old. Given a ticker, search the web for "
    "the most recent news and tell the reader, in plain friendly language, whether the stock "
    "went up or down recently and WHY. Keep it short and skimmable: start with a one-line "
    "summary, then 2-4 short bullet points of the main reasons, then a single 'bottom line' "
    "sentence. Avoid jargon; when you must use a term, explain it in a few words. Use markdown. "
    "Do not invent numbers or news — only use what you find in the search results."
)


class AnthropicClient:
    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            key = boto3.client("secretsmanager").get_secret_value(SecretId=settings.anthropic_secret_arn)["SecretString"]
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def generate_report(self, ticker: str) -> tuple[str, list[str]]:
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]
        messages = [{"role": "user", "content": f"Why did {ticker} stock move recently? Explain like I'm 5."}]

        for _ in range(4):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=SYSTEM,
                tools=tools,
                messages=messages,
            )
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})

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
