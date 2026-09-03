"""Read-only dashboard contract. No broker-derived financial values."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


HealthStatus = Literal["healthy", "degraded", "failed", "unknown", "disabled"]


class DashboardSystemItem(BaseModel):
    key: Literal["market_data", "backtest", "research", "scheduler", "broker"]
    status: HealthStatus
    reason: str
    checked_at: datetime
    observed_at: datetime | None = None
    enabled: bool | None = None
    submit_orders: bool | None = None
    active: int | None = None
    capacity: int | None = None
    queued: int | None = None
    last_run_status: str | None = None


class DashboardKpis(BaseModel):
    active_strategies: int
    running_experiments: int
    running_backtests: int
    queued_backtests: int


class DashboardTaskSummary(BaseModel):
    waiting_research: int
    completed_last_24h: int
    failed_backtests_last_24h: int
    failed_research_last_24h: int


class DashboardResearchProgress(BaseModel):
    experiments: int
    evaluated_candidates: int
    verified_candidates: int
    promoted_strategies: int
    paper_strategies: int


class DashboardStrategyEvidence(BaseModel):
    strategy_id: str
    name: str
    strategy_type: str
    version: int
    engine_ready: bool
    backtest_id: str | None = None
    evidence_status: Literal["available", "missing", "configuration_changed", "invalid"]
    total_return: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    trade_count: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    finished_at: datetime | None = None
    experiment_id: str | None = None
    candidate_id: str | None = None
    verification_status: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None
    issues: list[str] = Field(default_factory=list)


class DashboardPaperPortfolio(BaseModel):
    portfolio_id: str
    account_name: str
    name: str
    allocation_count: int
    allocation_total: float
    auto_run_eligible: bool
    latest_run_id: str | None = None
    latest_strategy_name: str | None = None
    latest_run_status: str | None = None
    latest_run_at: datetime | None = None
    submit_orders: bool | None = None


class DashboardPaperSummary(BaseModel):
    account_count: int
    portfolio_count: int
    portfolios: list[DashboardPaperPortfolio]


class DashboardAlert(BaseModel):
    id: str
    code: str
    severity: Literal["critical", "warning", "info"]
    count: int
    occurred_at: datetime | None
    href: str


class DashboardActivity(BaseModel):
    strategy_type: str | None = None
    id: str
    category: Literal["backtest", "research", "strategy", "paper"]
    status: str
    name: str
    occurred_at: datetime
    href: str


class DashboardOverview(BaseModel):
    generated_at: datetime
    system: list[DashboardSystemItem]
    research_kpis: DashboardKpis
    task_summary: DashboardTaskSummary
    research_progress: DashboardResearchProgress
    strategy_evidence: list[DashboardStrategyEvidence]
    paper_summary: DashboardPaperSummary
    alerts: list[DashboardAlert]
    activity: list[DashboardActivity]
