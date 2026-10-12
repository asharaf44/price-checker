import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import LambdaFunctionUrlResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import TypeAdapter, ValidationError

from price_checker.clients.anthropic_client import AnthropicClient
from price_checker.clients.prices_client import fetch_prices, fetch_quote, recent_moves
from price_checker.clients.reports_client import ReportsClient
from price_checker.models import (
    Chat,
    FeedbackRequest,
    FollowUpRequest,
    FollowUpResponse,
    HistoryResponse,
    Level,
    PinsRequest,
    QuoteRequest,
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
# Follow-ups, feedback, quotes and pins share POST /api, the only path CloudFront routes to the Lambda.
api_request = TypeAdapter(SubmitRequest | FollowUpRequest | FeedbackRequest | QuoteRequest | PinsRequest)


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
    if isinstance(req, QuoteRequest):
        quote = fetch_quote(req.quote)
        return Response(status_code=200, content_type=content_types.APPLICATION_JSON, body=quote.model_dump_json())
    if isinstance(req, PinsRequest):
        reports.put_pins(req.pins)
        return Response(status_code=204)

    # A ticker we've already reported on was resolved before, so skip the Opus lookup.
    query = req.ticker.upper()
    ticker = query if reports.exists(query) else anthropic_client.resolve_ticker(req.ticker)
    if not ticker:
        raise BadRequestError(f"Couldn't find a stock ticker for '{req.ticker}'.")

    key = reports.key_for(ticker)
    reports.put(key, Report(status="PENDING", ticker=ticker, createdAt=int(time.time())))
    lambda_client.invoke(
        FunctionName=app.lambda_context.invoked_function_arn,
        InvocationType="Event",
        Payload=WorkerEvent(ticker=ticker, key=key, force=req.force).model_dump_json().encode(),
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
    answer = anthropic_client.answer_follow_up(report.ticker, req.level, text, chat.turns, req.question)
    reports.put_chat(key, Chat(turns=[*chat.turns, Turn(question=req.question, answer=answer)]))
    return Response(
        status_code=200,
        content_type=content_types.APPLICATION_JSON,
        body=FollowUpResponse(answer=answer).model_dump_json(),
    )


@app.get("/api")
def history() -> dict:
    return HistoryResponse(reports=reports.list_history(), pins=reports.get_pins()).model_dump(mode="json")


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext):
    if event.get("requestContext", {}).get("http"):
        return app.resolve(event, context)
    return worker_handler(event)


def worker_handler(event: dict) -> None:
    job = WorkerEvent.model_validate(event)
    try:
        prior = None if job.force else reports.latest_completed(job.ticker)
        progress: list[str] = []

        def add_step(step: str) -> None:
            if step in progress:
                return
            progress.append(step)
            pending = Report(status="PENDING", ticker=job.ticker, createdAt=int(time.time()), progress=progress)
            reports.put(job.key, pending)

        prices = fetch_prices(job.ticker)
        explanation = anthropic_client.generate_report(job.ticker, prior, lambda q: add_step(f"Searching: {q}"), prices)
        # Research once at the pro level, then rewrite it simpler in parallel; the facts stay the same.
        add_step("Writing the Beginner and Kid versions")
        simpler = [Level.BEGINNER, Level.KID]
        with ThreadPoolExecutor() as pool:
            versions = dict(zip(simpler, pool.map(lambda lvl: anthropic_client.simplify(explanation, lvl), simpler)))
        reports.put(
            job.key,
            Report(
                status="COMPLETED",
                ticker=job.ticker,
                createdAt=int(time.time()),
                explanation=explanation,
                versions=versions,
                prices=prices,
                changesSince=prior[1] if prior else None,
            ),
        )
    except Exception as e:
        logger.exception("report generation failed")
        reports.put(job.key, Report(status="FAILED", ticker=job.ticker, createdAt=int(time.time()), error=str(e)))
