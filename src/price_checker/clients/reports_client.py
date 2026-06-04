import re
import uuid

import boto3

from price_checker.config import settings
from price_checker.models import HistoryItem, Report

PREFIX = "reports/"
_KEY_RE = re.compile(rf"{PREFIX}([A-Za-z0-9.]+)__[0-9a-f]+\.json")


class ReportsClient:
    def __init__(self) -> None:
        self._s3 = boto3.client("s3")

    def key_for(self, ticker: str) -> str:
        return f"{PREFIX}{ticker}__{uuid.uuid4().hex}.json"

    def put(self, key: str, report: Report) -> None:
        self._s3.put_object(
            Bucket=settings.reports_bucket,
            Key=key,
            Body=report.model_dump_json().encode(),
            ContentType="application/json",
        )

    def list_history(self) -> list[HistoryItem]:
        objects = self._s3.list_objects_v2(Bucket=settings.reports_bucket, Prefix=PREFIX).get("Contents", [])
        items = []
        for obj in objects:
            match = _KEY_RE.fullmatch(obj["Key"])
            if match:
                items.append(
                    HistoryItem(ticker=match.group(1), path="/" + obj["Key"], date=obj["LastModified"].isoformat())
                )
        items.sort(key=lambda i: i.date, reverse=True)
        return items[:200]
