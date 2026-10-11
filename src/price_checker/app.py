import time

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import LambdaFunctionUrlResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import TypeAdapter, ValidationError

from price_checker.clients.anthropic_client import AnthropicClient
from price_checker.clients.prices_client import fetch_prices, recent_moves
from price_checker.clients.reports_client import ReportsClient
from price_checker.models import (
    Chat,
    FeedbackRequest,
    FollowUpRequest,
    FollowUpResponse,
    HistoryResponse,
    Report,
    SubmitRequest,
    SubmitResponse,
    Turn,
    WorkerEvent,
)

logger = Logger()
tracer = Tracer()
app = LambdaFunctionUrlResolver()

lambda_client = boto3.client("lambda")
anthropic_client = AnthropicClient()
reports = ReportsClient()
# Feedback and follow-ups share POST /api, the only path CloudFront routes to the Lambda.
api_request = TypeAdapter(SubmitRequest | FollowUpRequest | FeedbackRequest)


@app.post("/api")
def submit() -> Response:
    try:
        req = api_request.validate_json(app.current_event.body or "{}")
    except ValidationError:
        raise BadRequestError("invalid input")
    if isinstance(req, FollowUpRequest):
        return follow_up(req)

    if isinstance(req, FeedbackRequest):
        logger.info("report feedback", extra=req.model_dump())
        return Response(status_code=204)

    # A ticker we've already reported on was resolved before, so skip the Opus lookup.
    query = req.ticker.upper()
    ticker = query if reports.exists(query) else anthropic_client.resolve_ticker(req.ticker)
    if not ticker:
        raise BadRequestError(f"Couldn't find a stock ticker for '{req.ticker}'.")

    key = reports.key_for(ticker)
    reports.put(key, Report(status="PENDING", ticker=ticker, createdAt=int(time.time()), level=req.level))
    lambda_client.invoke(
        FunctionName=app.lambda_context.invoked_function_arn,
        InvocationType="Event",
        Payload=WorkerEvent(ticker=ticker, key=key, force=req.force, level=req.level).model_dump_json().encode(),
    )
    return Response(
        status_code=202,
        content_type=content_types.APPLICATION_JSON,
        body=SubmitResponse(path="/" + key).model_dump_json(),
    )


def follow_up(req: FollowUpRequest) -> Response:
    key = req.path.lstrip("/")
    report = reports.get(key)
    text = report.explanation.model_dump_json() if report.explanation else report.report
    if text and len(report.prices) > 1:
        text += "\n\n" + recent_moves(report.prices)[0]
    if not text:
        raise BadRequestError("That report isn't ready yet.")
    chat = reports.get_chat(key)
    answer = anthropic_client.answer_follow_up(report.ticker, report.level, text, chat.turns, req.question)
    reports.put_chat(key, Chat(turns=[*chat.turns, Turn(question=req.question, answer=answer)]))
    return Response(
        status_code=200,
        content_type=content_types.APPLICATION_JSON,
        body=FollowUpResponse(answer=answer).model_dump_json(),
    )


@app.get("/api")
def history() -> dict:
    return HistoryResponse(reports=reports.list_history()).model_dump(mode="json")


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext):
    if event.get("requestContext", {}).get("http"):
        return app.resolve(event, context)
    return worker_handler(event)


def worker_handler(event: dict) -> None:
    job = WorkerEvent.model_validate(event)
    try:
        prior = None if job.force else reports.latest_completed(job.ticker, job.level)
        progress: list[str] = []

        def on_search(query: str) -> None:
            step = f"Searching: {query}"
            if step in progress:
                return
            progress.append(step)
            reports.put(
                job.key,
                Report(
                    status="PENDING", ticker=job.ticker, createdAt=int(time.time()), level=job.level, progress=progress
                ),
            )

        prices = fetch_prices(job.ticker)
        explanation = anthropic_client.generate_report(job.ticker, job.level, prior, on_search, prices)
        reports.put(
            job.key,
            Report(
                status="COMPLETED",
                ticker=job.ticker,
                createdAt=int(time.time()),
                level=job.level,
                explanation=explanation,
                prices=prices,
                changesSince=prior[1] if prior else None,
            ),
        )
    except Exception as e:
        logger.exception("report generation failed")
        reports.put(job.key, Report(status="FAILED", ticker=job.ticker, createdAt=int(time.time()), error=str(e)))
