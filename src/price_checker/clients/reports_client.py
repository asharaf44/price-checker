import json
import re
import uuid

import boto3

from price_checker.config import settings
from price_checker.models import Chat, HistoryItem, Report

PREFIX = "reports/"
PINS_KEY = "watchlist.json"  # outside reports/, so the 90-day lifecycle rule doesn't expire it
_KEY_RE = re.compile(rf"{PREFIX}([A-Za-z0-9.]+)__[0-9a-f]+\.json")


def _chat_key(key: str) -> str:
    return key.removesuffix(".json") + ".chat.json"


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

    def exists(self, ticker: str) -> bool:
        res = self._s3.list_objects_v2(Bucket=settings.reports_bucket, Prefix=f"{PREFIX}{ticker}__", MaxKeys=1)
        return res["KeyCount"] > 0

    def get(self, key: str) -> Report:
        return Report.model_validate_json(self._s3.get_object(Bucket=settings.reports_bucket, Key=key)["Body"].read())

    def get_pins(self) -> list[str]:
        try:
            return json.loads(self._s3.get_object(Bucket=settings.reports_bucket, Key=PINS_KEY)["Body"].read())
        except self._s3.exceptions.NoSuchKey:
            return []

    def put_pins(self, pins: list[str]) -> None:
        self._s3.put_object(
            Bucket=settings.reports_bucket, Key=PINS_KEY, Body=json.dumps(pins).encode(), ContentType="application/json"
        )

    # A report's follow-up chat lives beside it (not matched by _KEY_RE, so it stays out of history).
    def get_chat(self, key: str) -> Chat:
        try:
            body = self._s3.get_object(Bucket=settings.reports_bucket, Key=_chat_key(key))["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return Chat()
        return Chat.model_validate_json(body)

    def put_chat(self, key: str, chat: Chat) -> None:
        self._s3.put_object(
            Bucket=settings.reports_bucket,
            Key=_chat_key(key),
            Body=chat.model_dump_json().encode(),
            ContentType="application/json",
        )

    def latest_completed(self, ticker: str) -> tuple[str, int] | None:
        objects = self._s3.list_objects_v2(Bucket=settings.reports_bucket, Prefix=f"{PREFIX}{ticker}__").get(
            "Contents", []
        )
        for obj in sorted(objects, key=lambda o: o["LastModified"], reverse=True):
            try:
                data = json.loads(self._s3.get_object(Bucket=settings.reports_bucket, Key=obj["Key"])["Body"].read())
            except Exception:
                continue
            if data.get("status") == "COMPLETED" and data.get("explanation"):
                return json.dumps(data["explanation"]), data.get("createdAt", 0)
        return None

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
