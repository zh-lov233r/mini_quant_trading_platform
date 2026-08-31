from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.tables import BacktestJob, BacktestWorkerManager
from src.services.backtest_worker_config import (
    BACKTEST_EXECUTION_MODEL,
    resolve_backtest_worker_concurrency,
)

HEARTBEAT_STALE_AFTER_SECONDS = 15


def load_backtest_worker_status(
    db: Session,
    *,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = checked_at or datetime.now(timezone.utc)
    queued_jobs = int(
        db.execute(select(func.count()).select_from(BacktestJob).where(BacktestJob.status == "queued")).scalar_one()
    )
    active_jobs = int(
        db.execute(select(func.count()).select_from(BacktestJob).where(BacktestJob.status == "running")).scalar_one()
    )
    oldest_queued_at = db.execute(
        select(func.min(BacktestJob.created_at)).where(BacktestJob.status == "queued")
    ).scalar_one_or_none()
    try:
        live_managers = list(
            db.execute(
                select(BacktestWorkerManager)
                .where(
                    BacktestWorkerManager.heartbeat_at
                    >= observed_at - timedelta(seconds=HEARTBEAT_STALE_AFTER_SECONDS)
                )
                .order_by(
                    BacktestWorkerManager.is_leader.desc(),
                    BacktestWorkerManager.heartbeat_at.desc(),
                )
            ).scalars()
        )
    except SQLAlchemyError:
        db.rollback()
        live_managers = []
    manager = live_managers[0] if live_managers else None
    configured_concurrency = resolve_backtest_worker_concurrency()
    return {
        "execution_model": BACKTEST_EXECUTION_MODEL,
        "configured_concurrency": configured_concurrency,
        "available_slots": max(configured_concurrency - active_jobs, 0),
        "automation_available": any(item.is_leader for item in live_managers),
        "manager_state": manager.status if manager is not None else "unavailable",
        "live_managers": len(live_managers),
        "worker_active": (
            active_jobs > 0
            or bool(
                manager is not None
                and manager.worker_pid is not None
                and manager.status in {"starting", "running"}
            )
        ),
        "active_jobs": active_jobs,
        "queued_jobs": queued_jobs,
        "oldest_queued_at": oldest_queued_at,
        "next_worker_start_at": manager.next_worker_start_at if manager is not None else None,
        "last_worker_exit_code": manager.last_worker_exit_code if manager is not None else None,
        "heartbeat_stale_after_seconds": HEARTBEAT_STALE_AFTER_SECONDS,
        "checked_at": observed_at,
    }
