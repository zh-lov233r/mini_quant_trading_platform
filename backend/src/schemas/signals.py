"""Signal Center trust boundary. Strategies are selected by immutable instance IDs."""
from datetime import date
from typing import Literal
from uuid import UUID
import re
from email.headerregistry import Address
from pydantic import BaseModel, Field, field_validator

Market = Literal["US", "CN"]
Language = Literal["zh-CN", "en-US"]

class ScanCreate(BaseModel):
    basket_id: UUID
    strategy_ids: list[UUID] = Field(min_length=1, max_length=50)
    market: Market
    session_date: date | None = None
    name: str = Field(default="Signal scan", min_length=1, max_length=128)

    @field_validator("strategy_ids")
    @classmethod
    def unique_strategies(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("duplicate strategy instance")
        return values

class PlanCreate(ScanCreate):
    language: Language = "zh-CN"
    recipients: list[str] = Field(default_factory=list, max_length=20)
    retention_sessions: int = Field(default=3, ge=1, le=365)
    enabled: bool = False

    @field_validator("recipients")
    @classmethod
    def emails(cls, values):
        if any(not re.fullmatch(r"[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+", v) for v in values):
            raise ValueError("invalid recipient email")
        for value in values:
            try:
                value.encode("ascii"); Address(addr_spec=value)
            except (ValueError,UnicodeError) as exc:
                raise ValueError("invalid recipient email") from exc
        return list(dict.fromkeys(values))

class ReportCreate(BaseModel):
    language: Language = "zh-CN"

class DeliveryCreate(BaseModel):
    recipients: list[str] = Field(min_length=1, max_length=20)
    _emails = field_validator("recipients")(PlanCreate.emails.__func__)

class PlanToggle(BaseModel):
    enabled: bool

# Explicit public read contracts; evidence remains versioned strategy-specific JSON.
from datetime import datetime
from pydantic import JsonValue

class CoverageOut(BaseModel):
    expected: int
    evaluated: int
    not_evaluated: int
    reasons: dict[str,int]

class ScanOut(BaseModel):
    id: UUID
    name: str
    market: Market
    session_date: date
    plan_id: UUID | None
    status: str
    attempts: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    coverage: dict[str,CoverageOut]
    purged_at: datetime | None

class ScanPage(BaseModel):
    items: list[ScanOut]
    total: int

class PlanOut(BaseModel):
    id: UUID
    name: str
    market: Market
    basket_id: UUID
    strategies: list[dict[str,JsonValue]]
    language: Language
    recipients: list[str]
    retention_sessions: int
    enabled: bool
    created_at: datetime
    updated_at: datetime

class PlanPage(BaseModel):
    items: list[PlanOut]
    total: int

class ObservationOut(BaseModel):
    id: UUID
    scan_id: UUID
    strategy_run_id: UUID
    instrument_id: int
    symbol_as_of: str
    session_date: date
    confirmed_at: datetime | None
    event_type: str
    event_kind: Literal["event","condition"]
    direction: Literal["bullish","bearish","neutral"] | None
    passes_signal_filters: bool
    strength: dict[str,JsonValue] | None
    strategy_rank: int | None
    snapshot_ref: str
    strategy_name: str | None = None
    strategy_type: str | None = None
    strategy_version: int | None = None
    reason: str | None = None
    evidence: dict[str,JsonValue] | None = None

class ObservationPage(BaseModel):
    items: list[ObservationOut]
    total: int

class ReportOut(BaseModel):
    id: UUID
    scan_id: UUID
    status: str
    language: Language
    automatic: bool
    summary: dict[str,JsonValue]
    published_at: datetime | None
    expires_at: datetime | None
    expired_at: datetime | None
    retention_reason: str | None
    retention_sessions: int | None
    render_errors: dict[str,str]
    render_status: str | None = None
    render_error: str | None = None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    formats: list[str] = Field(default_factory=list)
    document: dict[str,JsonValue] | None = None
    deliveries: list[dict[str,JsonValue]] = Field(default_factory=list)

class ReportPage(BaseModel):
    items: list[ReportOut]
    total: int

class ChartBarOut(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    session_index: int
    price_source: dict[str,str]

class IndicatorValueOut(BaseModel):
    time: date
    value: float | None

class IndicatorOut(BaseModel):
    key: str
    unit: str
    pane: str
    values: list[IndicatorValueOut]
    thresholds: list[float] = Field(default_factory=list)

class SignalChartOut(BaseModel):
    report_id: UUID | None
    scan_id: UUID
    snapshot_id: str
    instrument_id: int
    symbol_as_of: str
    market: Market
    session_timezone: str
    timeframe: Literal["1d"]
    price_semantics: str
    as_of_session: date
    available_from: date
    available_to: date
    bars: list[ChartBarOut]
    indicator_series: list[IndicatorOut]
    selected_signal_id: UUID
    observation: ObservationOut
    next_cursor: date | None


class ScanStrategyOut(BaseModel):
    id: UUID
    scan_id: UUID
    strategy_id: UUID
    strategy_type: str
    version: int
    params_hash: str
    algorithm_revision: str
    runtime: dict[str,JsonValue]
    status: str
    coverage: dict[str,JsonValue]
    error: str | None

class ScanDetailOut(ScanOut):
    manifest: dict[str,JsonValue]
    strategies: list[ScanStrategyOut]

class DeliveryOut(BaseModel):
    id: UUID
    report_id: UUID
    recipient: str
    status: str
    attempts: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None

class QueuedOut(BaseModel):
    status: Literal["queued"]
