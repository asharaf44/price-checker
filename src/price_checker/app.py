from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()

@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict, context: LambdaContext) -> dict:
    logger.info("Processing price check request", extra={"event": event})
    return {
        "statusCode": 200,
        "body": "Price check completed"
    }