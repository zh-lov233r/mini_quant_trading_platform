from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from src.models.tables import (
    BacktestJob,
    MarketDataMaintenanceState,
    ResearchExperiment,
    SupportResistanceMaterialization,
)


UTC = timezone.utc
MARKET_DATA_MAINTENANCE_STATE_ID = 1
MARKET_DATA_ADVISORY_LOCK_KEY = 7_314_582_019
TERMINAL_EXPERIMENT_STATUSES = {"completed", "partially_failed", "failed", "cancelled"}


class MarketDataMaintenanceError(ValueError):
    code = "market_data_maintenance"


def load_market_data_maintenance_state(
    db: Session,
    *,
    for_update: bool = False,
) -> MarketDataMaintenanceState:
    statement = select(MarketDataMaintenanceState).where(
        MarketDataMaintenanceState.id == MARKET_DATA_MAINTENANCE_STATE_ID
    )
    if for_update:
        statement = statement.with_for_update()
    state = db.execute(statement).scalar_one_or_none()
    if state is None:
        state = MarketDataMaintenanceState(id=MARKET_DATA_MAINTENANCE_STATE_ID, status="ready")
        db.add(state)
        db.flush()
    return state


def assert_market_data_submission_allowed(db: Session) -> None:
    state = load_market_data_maintenance_state(db, for_update=True)
    if state.status != "ready":
        raise MarketDataMaintenanceError(
            f"market data maintenance is {state.status}; new strategy work is temporarily disabled"
        )


def acquire_market_data_read_lock(db: Session, *, allow_draining: bool = False) -> None:
    """Hold the shared PostgreSQL lock until the caller's transaction ends."""

    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock_shared(:key)"),
            {"key": MARKET_DATA_ADVISORY_LOCK_KEY},
        )
    state = load_market_data_maintenance_state(db)
    if state.status != "ready" and not (allow_draining and state.status == "draining"):
        raise MarketDataMaintenanceError(
            f"market data maintenance is {state.status}; strategy execution is unavailable"
        )


def active_market_data_work_counts(db: Session) -> dict[str, int]:
    jobs = int(
        db.scalar(
            select(func.count()).select_from(BacktestJob).where(
                BacktestJob.status.in_(("queued", "running"))
            )
        )
        or 0
    )
    experiments = int(
        db.scalar(
            select(func.count()).select_from(ResearchExperiment).where(
                ResearchExperiment.status.not_in(TERMINAL_EXPERIMENT_STATUSES)
            )
        )
        or 0
    )
    return {"backtest_jobs": jobs, "research_experiments": experiments}


def begin_market_data_draining(db: Session, owner_token: UUID) -> MarketDataMaintenanceState:
    state = load_market_data_maintenance_state(db, for_update=True)
    if state.status in {"draining", "updating"} and state.owner_token != owner_token:
        raise MarketDataMaintenanceError("another market data maintenance run is active")
    state.status = "draining"
    state.owner_token = owner_token
    state.requested_at = datetime.now(UTC)
    state.started_at = None
    state.finished_at = None
    state.error_message = None
    db.flush()
    return state


def begin_market_data_update(db: Session, owner_token: UUID) -> MarketDataMaintenanceState:
    state = load_market_data_maintenance_state(db, for_update=True)
    if state.status != "draining" or state.owner_token != owner_token:
        raise MarketDataMaintenanceError("market data maintenance ownership changed while draining")
    counts = active_market_data_work_counts(db)
    if any(counts.values()):
        raise MarketDataMaintenanceError("market data work is still active")
    state.status = "updating"
    state.started_at = datetime.now(UTC)
    db.flush()
    return state


def invalidate_support_resistance_materializations(db: Session) -> int:
    result = db.execute(
        update(SupportResistanceMaterialization)
        .where(SupportResistanceMaterialization.invalidated_at.is_(None))
        .values(invalidated_at=datetime.now(UTC))
    )
    return int(result.rowcount or 0)


def finish_market_data_maintenance(
    db: Session,
    owner_token: UUID,
    *,
    error: BaseException | None = None,
) -> MarketDataMaintenanceState:
    state = load_market_data_maintenance_state(db, for_update=True)
    if state.owner_token != owner_token:
        raise MarketDataMaintenanceError("market data maintenance ownership changed")
    state.status = "failed" if error is not None else "ready"
    state.finished_at = datetime.now(UTC)
    state.error_message = str(error)[:2000] if error is not None else None
    state.owner_token = None
    db.flush()
    return state


def market_data_maintenance_snapshot(db: Session) -> dict[str, Any]:
    state = load_market_data_maintenance_state(db)
    return {
        "status": state.status,
        "requested_at": state.requested_at,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "error_message": state.error_message,
    }
