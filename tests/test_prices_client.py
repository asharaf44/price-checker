import io
import json
from unittest.mock import patch

from price_checker.clients.prices_client import fetch_prices

CHART = {
    "chart": {
        "result": [
            {
                "timestamp": [1789047000, 1789133400, 1789392600],
                "indicators": {"quote": [{"close": [326.5700073, None, 333.0799865]}]},
            }
        ]
    }
}


def test_fetch_prices_parses_closes():
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(CHART).encode())):
        prices = fetch_prices("AAPL")
    assert [(p.date, p.close) for p in prices] == [("2026-09-10", 326.57), ("2026-09-14", 333.08)]


def test_fetch_prices_adds_caret_for_indexes():
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(CHART).encode())) as urlopen:
        fetch_prices("IXIC")
    assert "/chart/%5EIXIC?" in urlopen.call_args.args[0].full_url


def test_fetch_prices_uses_dash_for_share_classes():
    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(CHART).encode())) as urlopen:
        fetch_prices("BRK.B")
    assert "/chart/BRK-B?" in urlopen.call_args.args[0].full_url


def test_fetch_prices_failure_returns_empty():
    with patch("urllib.request.urlopen", side_effect=OSError("down")):
        assert fetch_prices("AAPL") == []
