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
    ticker: str
    path: str
    date: str
    status: str
    error: str | None = None


class WorkerEvent(BaseModel):
    ticker: Ticker
    key: str


class SubmitRequest(BaseModel):
    ticker: Ticker


class SubmitResponse(BaseModel):
    path: str


class HistoryResponse(BaseModel):
    reports: list[HistoryItem]
