import json
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

    def _meta(self, key: str) -> tuple[str, str | None]:
        try:
            data = json.loads(self._s3.get_object(Bucket=settings.reports_bucket, Key=key)["Body"].read())
            return data.get("status", "COMPLETED"), data.get("error")
        except Exception:
            return "COMPLETED", None

    def list_history(self) -> list[HistoryItem]:
        objects = self._s3.list_objects_v2(Bucket=settings.reports_bucket, Prefix=PREFIX).get("Contents", [])
        matched = [
            (obj["Key"], match.group(1), obj["LastModified"].isoformat())
            for obj in objects
            if (match := _KEY_RE.fullmatch(obj["Key"]))
        ]
        matched.sort(key=lambda x: x[2], reverse=True)
        items = []
        for key, ticker, date in matched[:200]:
            status, error = self._meta(key)
            items.append(HistoryItem(ticker=ticker, path="/" + key, date=date, status=status, error=error))
        return items
