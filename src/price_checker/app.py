import time
import uuid

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler import LambdaFunctionUrlResolver, Response, content_types
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import ValidationError

from price_checker.clients.anthropic_client import AnthropicClient
from price_checker.clients.reports_client import ReportsClient
from price_checker.models import HistoryResponse, Report, SubmitRequest, SubmitResponse, WorkerEvent

logger = Logger()
tracer = Tracer()
app = LambdaFunctionUrlResolver()

lambda_client = boto3.client("lambda")
anthropic_client = AnthropicClient()
reports = ReportsClient()


@app.post("/api")
def submit() -> Response:
    try:
        req = SubmitRequest.model_validate_json(app.current_event.body or "{}")
    except ValidationError:
        raise BadRequestError("invalid ticker")

    job_id = uuid.uuid4()
    key = reports.key_for(job_id, req.ticker)
    reports.put(key, Report(status="PENDING", ticker=req.ticker, createdAt=int(time.time())))
    lambda_client.invoke(
        FunctionName=app.lambda_context.invoked_function_arn,
        InvocationType="Event",
        Payload=WorkerEvent(jobId=job_id, ticker=req.ticker, key=key).model_dump_json().encode(),
    )
    return Response(
        status_code=202,
        content_type=content_types.APPLICATION_JSON,
        body=SubmitResponse(jobId=job_id, path="/" + key).model_dump_json(),
    )


@app.get("/api")
def history() -> dict:
    return HistoryResponse(reports=reports.list_history()).model_dump(mode='json')


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext):
    if event.get("requestContext", {}).get("http"):
        return app.resolve(event, context)
    return worker_handler(event)


def worker_handler(event: dict) -> None:
    job = WorkerEvent.model_validate(event)
    try:
        report, sources = anthropic_client.generate_report(job.ticker)
        reports.put(
            job.key,
            Report(status="COMPLETED", ticker=job.ticker, createdAt=int(time.time()), report=report, sources=sources),
        )
    except Exception as e:
        logger.exception("report generation failed")
        reports.put(job.key, Report(status="FAILED", ticker=job.ticker, createdAt=int(time.time()), error=str(e)))
