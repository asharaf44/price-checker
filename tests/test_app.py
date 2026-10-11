import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from price_checker import app as app_module
from price_checker.app import app, worker_handler
from price_checker.clients.anthropic_client import _system
from price_checker.models import Chat, Explanation, Level, Report, Turn


def _event(method="POST", path="/api", body=None):
    return {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": "",
        "requestContext": {"stage": "$default", "http": {"method": method, "path": path}},
        "headers": {},
        "queryStringParameters": {},
        "body": body,
        "isBase64Encoded": False,
    }


def test_rejects_blank_ticker():
    response = app.resolve(_event(body='{"ticker": "!!!"}'), None)
    assert response["statusCode"] == 400


def test_unknown_route_404():
    response = app.resolve(_event(method="GET", path="/nope"), None)
    assert response["statusCode"] == 404


def test_rejects_unknown_level():
    body = '{"path": "/reports/AAPL__abc.json", "question": "Why?", "level": "expert"}'
    response = app.resolve(_event(body=body), None)
    assert response["statusCode"] == 400


def test_research_is_written_for_pros():
    assert "for an experienced investor." in _system()


def test_worker_records_search_progress(monkeypatch):
    def generate_report(ticker, prior, on_search, prices):
        on_search("DDOG earnings")
        on_search("DDOG earnings")
        return Explanation(
            direction="up", pctMove=None, summary="s", reasons=[], bottomLine="b", terms=[], sources=[]
        )

    puts = []
    monkeypatch.setattr(app_module.anthropic_client, "generate_report", generate_report)
    monkeypatch.setattr(app_module.anthropic_client, "simplify", lambda explanation, level: explanation)
    monkeypatch.setattr(app_module, "fetch_prices", lambda ticker: [])
    monkeypatch.setattr(app_module.reports, "put", lambda key, report: puts.append(report.model_copy(deep=True)))

    worker_handler({"ticker": "DDOG", "key": "reports/DDOG__abc.json", "force": True})

    assert [(r.status, r.progress) for r in puts] == [
        ("PENDING", ["Searching: DDOG earnings"]),
        ("PENDING", ["Searching: DDOG earnings", "Writing the Beginner and Kid versions"]),
        ("COMPLETED", []),
    ]
    assert list(puts[-1].versions) == [Level.BEGINNER, Level.KID]


@pytest.mark.parametrize("known", [True, False])
def test_known_ticker_skips_resolve(monkeypatch, known):
    reports, anthropic = MagicMock(), MagicMock()
    reports.exists.return_value = known
    reports.key_for.return_value = "reports/DDOG__abc.json"
    anthropic.resolve_ticker.return_value = "DDOG"
    monkeypatch.setattr(app_module, "reports", reports)
    monkeypatch.setattr(app_module, "anthropic_client", anthropic)
    monkeypatch.setattr(app_module, "lambda_client", MagicMock())

    response = app.resolve(_event(body='{"ticker": "ddog"}'), SimpleNamespace(invoked_function_arn="arn"))

    assert response["statusCode"] == 202
    reports.exists.assert_called_once_with("DDOG")
    assert anthropic.resolve_ticker.called is not known


def test_follow_up_answers_from_report(monkeypatch):
    reports = MagicMock()
    reports.get.return_value = Report(status="COMPLETED", ticker="DDOG", createdAt=1, report="It went up.")
    reports.get_chat.return_value = Chat()
    client = MagicMock()
    client.answer_follow_up.return_value = "Because of earnings."
    monkeypatch.setattr(app_module, "reports", reports)
    monkeypatch.setattr(app_module, "anthropic_client", client)

    body = json.dumps({"path": "/reports/DDOG__abc123.json", "question": "What's guidance?"})
    response = app.resolve(_event(body=body), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"answer": "Because of earnings."}
    reports.get.assert_called_once_with("reports/DDOG__abc123.json")
    client.answer_follow_up.assert_called_once_with("DDOG", Level.PRO, "It went up.", [], "What's guidance?")
    reports.put_chat.assert_called_once_with(
        "reports/DDOG__abc123.json", Chat(turns=[Turn(question="What's guidance?", answer="Because of earnings.")])
    )


def test_follow_up_rejects_non_report_path():
    body = json.dumps({"path": "/index.html", "question": "Why?"})
    response = app.resolve(_event(body=body), None)
    assert response["statusCode"] == 400


def test_feedback_logged(caplog):
    body = '{"ticker": "aapl", "path": "/reports/AAPL__abc.json", "rating": "up", "incremental": true}'
    response = app.resolve(_event(body=body), None)
    assert response["statusCode"] == 204
    assert "report feedback" in caplog.text


def test_invalid_feedback_rejected():
    body = '{"ticker": "AAPL", "path": "/reports/AAPL__abc.json", "rating": "meh"}'
    response = app.resolve(_event(body=body), None)
    assert response["statusCode"] == 400
