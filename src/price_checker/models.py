import uuid
from typing import Annotated

from pydantic import BaseModel, StringConstraints

Ticker = Annotated[str, StringConstraints(to_upper=True, strip_whitespace=True, pattern=r"^[A-Za-z0-9.]{1,10}$")]


class Report(BaseModel):
    status: str
    ticker: Ticker
    createdAt: int
    report: str | None = None
    sources: list[str] = []
    error: str | None = None


class HistoryItem(BaseModel):
    jobId: uuid.UUID
    # Parsed from S3 keys on read — kept lenient so one odd object can't break list_history().
    ticker: str
    path: str
    date: str


class WorkerEvent(BaseModel):
    jobId: uuid.UUID
    ticker: Ticker
    key: str


class SubmitRequest(BaseModel):
    ticker: Ticker


class SubmitResponse(BaseModel):
    jobId: uuid.UUID
    path: str


class HistoryResponse(BaseModel):
    reports: list[HistoryItem]
