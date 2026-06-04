from typing import Annotated

from pydantic import BaseModel, StringConstraints

Ticker = Annotated[str, StringConstraints(to_upper=True, strip_whitespace=True, pattern=r"^[A-Za-z0-9.]{1,10}$")]
# Free-form user input (a ticker, company name, or typo) — resolved to a real Ticker before use.
Query = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 .&'-]+$")
]


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
    force: bool = False


class SubmitRequest(BaseModel):
    ticker: Query
    force: bool = False


class SubmitResponse(BaseModel):
    path: str


class HistoryResponse(BaseModel):
    reports: list[HistoryItem]
