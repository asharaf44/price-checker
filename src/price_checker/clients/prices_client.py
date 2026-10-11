import json
import urllib.request
from datetime import datetime, timezone

from aws_lambda_powertools import Logger

from price_checker.models import INDEXES, Price

logger = Logger(child=True)

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1mo&interval=1d"


def fetch_prices(ticker: str) -> list[Price]:
    # Best-effort: a missing chart shouldn't fail the report.
    try:
        # Yahoo spells share classes with a dash (BRK-B) and indexes with a caret (^IXIC).
        symbol = f"%5E{ticker}" if ticker in INDEXES else ticker.replace(".", "-")
        req = urllib.request.Request(URL.format(symbol), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as res:
            result = json.load(res)["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        return [
            Price(date=datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), close=round(close, 2))
            for ts, close in zip(result["timestamp"], closes)
            if close is not None
        ]
    except Exception:
        logger.exception("price fetch failed", extra={"ticker": ticker})
        return []


def recent_moves(prices: list[Price]) -> tuple[str, float, list[tuple[str, float]]]:
    """A table of the last 5 trading days, the total % move over them, and each day's (date, % move)."""
    p = prices[-6:]
    days = [(b.date, (b.close / a.close - 1) * 100) for a, b in zip(p, p[1:])]
    total = (p[-1].close / p[0].close - 1) * 100
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = "\n".join(
        f"{datetime.strptime(d, '%Y-%m-%d').strftime('%a')} {d}: close {b.close} ({c:+.2f}%)"
        + (" (today, may still be trading)" if d == today else "")
        for (d, c), b in zip(days, p[1:])
    )
    table = (
        f"Daily closes (Yahoo Finance), from {p[0].date} at {p[0].close}:\n{rows}\n"
        f"Total over these {len(days)} trading days: {total:+.2f}%."
    )
    return table, total, days
