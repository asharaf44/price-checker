from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from price_checker.clients.anthropic_client import AnthropicClient
from price_checker.models import Level, Price, Turn

EXPLANATION = {
    "direction": "down",
    "pctMove": -4.2,
    "summary": "Shares fell after weak earnings.",
    "reasons": [{"title": "Earnings miss", "detail": "Profit was lower.", "date": "2026-10-01", "sources": [0]}],
    "bottomLine": "Investors wanted more.",
    "terms": [{"term": "Earnings", "definition": "Money a company makes."}],
    "sources": ["https://example.com/news"],
}


def _response(stop_reason, content):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, server_tool_use=None)
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage)


def _stream(response):
    stream = MagicMock()
    stream.__enter__.return_value.__iter__.return_value = iter([])
    stream.__enter__.return_value.get_final_message.return_value = response
    return stream


def _client(*responses):
    client = AnthropicClient()
    client._client = MagicMock()
    client._client.messages.stream.side_effect = [_stream(r) for r in responses]
    return client


def test_generate_report_resumes_pause_turn_and_parses_submitted_report():
    paused = [SimpleNamespace(type="server_tool_use")]
    client = _client(
        _response("pause_turn", paused),
        _response("tool_use", [SimpleNamespace(type="tool_use", input=EXPLANATION)]),
    )

    explanation = client.generate_report("BA", Level.KID, ('{"summary": "old"}', 0), lambda q: None, [])

    assert explanation.direction == "down"
    assert explanation.reasons[0].sources == [0]
    calls = client._client.messages.stream.call_args_list
    assert len(calls) == 2
    messages = calls[1].kwargs["messages"]
    assert '{"summary": "old"}' in messages[0]["content"]
    assert messages[1] == {"role": "assistant", "content": paused}


def test_generate_report_fails_without_submission():
    client = _client(_response("end_turn", [SimpleNamespace(type="text", text="no tool call")]))

    with pytest.raises(RuntimeError):
        client.generate_report("BA", Level.KID, None, lambda q: None, [])


def test_follow_up_sends_prior_turns_as_history():
    client = AnthropicClient()
    client._client = MagicMock()
    client._client.messages.create.return_value = _response("end_turn", [SimpleNamespace(type="text", text="A2")])

    answer = client.answer_follow_up("BA", Level.KID, "REPORT", [Turn(question="Q1", answer="A1")], "Q2")

    assert answer == "A2"
    messages = client._client.messages.create.call_args.kwargs["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "REPORT" in messages[0]["content"] and messages[0]["content"].endswith("Question: Q1")
    assert messages[1]["content"] == "A1"
    assert messages[2]["content"] == "Question: Q2"


def test_generate_report_uses_real_prices_for_the_move():
    client = _client(_response("tool_use", [SimpleNamespace(type="tool_use", input=EXPLANATION)]))
    closes = [(2, 100), (5, 102), (6, 101), (7, 103), (8, 99), (9, 104)]
    prices = [Price(date=f"2026-10-0{d}", close=c) for d, c in closes]

    explanation = client.generate_report("BA", Level.KID, None, lambda q: None, prices)

    assert (explanation.direction, explanation.pctMove) == ("up", 4.0)
    user = client._client.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "Thu 2026-10-08: close 99.0 (-3.88%)" in user
    assert "especially 2026-10-09 (+5.05%) and 2026-10-08 (-3.88%)" in user


@pytest.mark.parametrize("reply, expected", [('{"symbol": "brk.b"}', "BRK.B"), ('{"symbol": null}', None)])
def test_resolve_ticker_reads_structured_symbol(reply, expected):
    client = AnthropicClient()
    client._client = MagicMock()
    client._client.messages.create.return_value = _response("end_turn", [SimpleNamespace(type="text", text=reply)])
    assert client.resolve_ticker("berkshire") == expected
