from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Ticker = Annotated[str, StringConstraints(to_upper=True, strip_whitespace=True, pattern=r"^[A-Za-z0-9.]{1,10}$")]
# Free-form user input (a ticker, company name, or typo) — resolved to a real Ticker before use.
Query = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 .&'-]+$")
]
# Market indexes, stored without Yahoo's "^" prefix, which doesn't fit tickers, S3 keys, or URLs.
INDEXES = {
    "IXIC": "Nasdaq Composite",
    "GSPC": "S&P 500",
    "DJI": "Dow Jones Industrial Average",
    "NDX": "Nasdaq-100",
    "RUT": "Russell 2000",
}


class Level(StrEnum):
    KID = "kid"
    BEGINNER = "beginner"
    PRO = "pro"


class Reason(BaseModel):
    title: str = Field(description="Short headline, without the date")
    detail: str
    date: str | None = Field(description="Trading day (YYYY-MM-DD) whose move this reason explains, or null")
    sources: list[int] = Field(description="Indexes into the top-level sources list")
    tickers: list[str] = Field([], description="Tickers of the companies this reason names, including the subject")


class Term(BaseModel):
    term: str
    definition: str


class Explanation(BaseModel):
    direction: Literal["up", "down", "flat", "unknown"]
    pctMove: float | None = Field(description="Recent % move, e.g. -4.2, or null if not found")
    summary: str = Field(description="One sentence stating the move from the price table and its main cause")
    reasons: list[Reason]
    bottomLine: str
    terms: list[Term]
    sources: list[str] = Field(description="News article URLs copied exactly from your web_search results")
    changes: str | None = Field(None, description="When updating a previous explanation: what's new since then")


class Price(BaseModel):
    date: str
    close: float


class Report(BaseModel):
    status: str
    ticker: Ticker
    createdAt: int
    report: str | None = None
    explanation: Explanation | None = None
    # Simpler rewrites of the (pro) explanation, same facts.
    versions: dict[Level, Explanation] = {}
    progress: list[str] = []
    prices: list[Price] = []
    changesSince: int | None = None
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
    model_config = ConfigDict(extra="forbid")  # keeps it distinct from FeedbackRequest on the shared POST /api
    ticker: Query
    force: bool = False


class FeedbackRequest(BaseModel):
    ticker: Ticker
    path: str
    rating: Literal["up", "down"]
    incremental: bool | None = None


class SubmitResponse(BaseModel):
    path: str


class FollowUpRequest(BaseModel):
    path: Annotated[str, StringConstraints(pattern=r"^/reports/[A-Za-z0-9.]+__[0-9a-f]+\.json$")]
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    level: Level = Level.PRO


class FollowUpResponse(BaseModel):
    answer: str


class QuoteRequest(BaseModel):
    quote: Ticker


class Quote(BaseModel):
    price: float
    previousClose: float
    index: bool


class Turn(BaseModel):
    question: str
    answer: str


class Chat(BaseModel):
    turns: list[Turn] = []


class PinsRequest(BaseModel):
    pins: list[Ticker]


class HistoryResponse(BaseModel):
    reports: list[HistoryItem]
    pins: list[str]
