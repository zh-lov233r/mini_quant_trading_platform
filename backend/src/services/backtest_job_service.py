from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
import os
import socket
from threading import Event, Thread
import time
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core.db import SessionLocal
from src.models.tables import (
    BacktestJob,
    ExperimentCandidate,
    ExperimentTrial,
    PortfolioSnapshot,
    ResearchExperiment,
    Signal,
    StrategyRun,
    Transaction,
)
from src.services.backtest_engine import BacktestCancelledError, run_backtest

log = logging.getLogger(__name__)
UTC = timezone.utc
JobSource = Literal["manual", "research", "verification"]


class BacktestVerificationError(RuntimeError):
    pass


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def enqueue_backtest_job(
    db: Session,
    *,
    run: StrategyRun,
    payload: dict[str, Any],
    source: JobSource = "manual",
    experiment_trial_id: UUID | None = None,
    priority: int = 0,
    max_attempts: int = 2,
) -> BacktestJob:
    existing = db.execute(select(BacktestJob).where(BacktestJob.run_id == run.id)).scalar_one_or_none()
    if existing is not None:
        return existing
    job = BacktestJob(
        run_id=run.id,
        experiment_trial_id=experiment_trial_id,
        source=source,
        status="queued",
        priority=priority,
        max_attempts=max(1, max_attempts),
        payload=payload,
        progress={"percent": 0.0},
    )
    db.add(job)
    db.flush()
    return job


def request_backtest_cancel(db: Session, run_id: UUID) -> BacktestJob:
    job = db.execute(select(BacktestJob).where(BacktestJob.run_id == run_id)).scalar_one_or_none()
    if job is None:
        raise ValueError("backtest job not found")
    if job.status in {"completed", "failed", "cancelled"}:
        return job
    job.cancel_requested_at = datetime.now(UTC)
    experiment_id = None
    if job.status == "queued":
        job.status = "cancelled"
        run = db.get(StrategyRun, run_id)
        if run is not None:
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            run.error_message = "backtest cancelled before execution"
        if run is not None:
            experiment_id = _finalize_linked_trial(db, job, run)
    db.commit()
    _finalize_research_experiment(db, experiment_id)
    db.refresh(job)
    return job


def recover_expired_jobs(db: Session, *, now: datetime | None = None) -> int:
    observed_at = now or datetime.now(UTC)
    jobs = list(
        db.execute(
            select(BacktestJob).where(
                BacktestJob.status == "running",
                BacktestJob.lease_expires_at.is_not(None),
                BacktestJob.lease_expires_at < observed_at,
            )
        ).scalars()
    )
    experiment_ids: set[UUID] = set()
    for job in jobs:
        run = db.get(StrategyRun, job.run_id)
        if job.cancel_requested_at is not None:
            job.status = "cancelled"
            if run is not None:
                run.status = "cancelled"
                run.finished_at = observed_at
                run.error_message = "backtest cancelled after worker lease expired"
        elif job.attempt < job.max_attempts:
            job.status = "queued"
            job.available_at = observed_at
            if run is not None:
                run.status = "queued"
                run.started_at = None
                run.finished_at = None
                run.error_message = None
        else:
            job.status = "failed"
            job.error_message = "backtest worker lease expired and retry budget was exhausted"
            if run is not None:
                run.status = "failed"
                run.finished_at = observed_at
                run.error_message = job.error_message
        job.claimed_by = None
        job.claimed_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        if run is not None and job.status in {"failed", "cancelled"}:
            experiment_id = _finalize_linked_trial(db, job, run)
            if experiment_id is not None:
                experiment_ids.add(experiment_id)
    if jobs:
        db.commit()
        for experiment_id in experiment_ids:
            _finalize_research_experiment(db, experiment_id)
    return len(jobs)


def claim_next_backtest_job(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> BacktestJob | None:
    now = datetime.now(UTC)
    recover_expired_jobs(db, now=now)
    stmt = (
        select(BacktestJob)
        .where(BacktestJob.status == "queued", BacktestJob.available_at <= now)
        .order_by(BacktestJob.priority.desc(), BacktestJob.created_at, BacktestJob.id)
        .limit(1)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.execute(stmt).scalar_one_or_none()
    if job is None:
        db.rollback()
        return None
    if job.cancel_requested_at is not None:
        job.status = "cancelled"
        run = db.get(StrategyRun, job.run_id)
        if run is not None:
            run.status = "cancelled"
            run.finished_at = now
        db.commit()
        return None
    job.status = "running"
    job.attempt += 1
    job.claimed_by = worker_id
    job.claimed_at = now
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
    run = db.get(StrategyRun, job.run_id)
    if run is not None:
        run.status = "running"
    db.commit()
    db.refresh(job)
    return job


def _job_cancel_requested(job_id: UUID) -> bool:
    db = SessionLocal()
    try:
        value = db.execute(
            select(BacktestJob.cancel_requested_at).where(BacktestJob.id == job_id)
        ).scalar_one_or_none()
        return value is not None
    finally:
        db.close()


def _update_job_progress(
    job_id: UUID,
    worker_id: str,
    progress: dict[str, Any],
    *,
    lease_seconds: int,
) -> None:
    db = SessionLocal()
    try:
        job = db.get(BacktestJob, job_id)
        if job is None or job.status != "running" or job.claimed_by != worker_id:
            return
        now = datetime.now(UTC)
        job.progress = dict(progress)
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
        db.commit()
    finally:
        db.close()


def _heartbeat_job(job_id: UUID, worker_id: str, *, lease_seconds: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(BacktestJob, job_id)
        if job is None or job.status != "running" or job.claimed_by != worker_id:
            return
        now = datetime.now(UTC)
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
        db.commit()
    finally:
        db.close()


def _heartbeat_loop(
    stop_event: Event,
    job_id: UUID,
    worker_id: str,
    lease_seconds: int,
) -> None:
    interval = max(5.0, min(30.0, lease_seconds / 3.0))
    while not stop_event.wait(interval):
        try:
            _heartbeat_job(job_id, worker_id, lease_seconds=lease_seconds)
        except Exception:
            log.exception("Backtest heartbeat failed", extra={"job_id": str(job_id)})


def _finalize_linked_trial(db: Session, job: BacktestJob, run: StrategyRun) -> UUID | None:
    if job.experiment_trial_id is None:
        return None
    trial = db.get(ExperimentTrial, job.experiment_trial_id)
    if trial is None:
        return None
    if job.status == "completed":
        trial.status = "completed"
        trial.metrics = dict(run.summary_metrics or {})
        trial.error_code = None
        trial.error_message = None
    elif job.status == "cancelled":
        trial.status = "cancelled"
        trial.error_code = "cancelled"
        trial.error_message = job.error_message
    else:
        trial.status = "failed"
        trial.error_code = "trial_failed"
        trial.error_message = job.error_message
    trial.finished_at = datetime.now(UTC)
    return trial.experiment_id


def _finalize_research_experiment(db: Session, experiment_id: UUID | None) -> None:
    if experiment_id is None:
        return
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        return
    # Local import avoids coupling the queue claim path to research planning.
    from src.services.research_experiment_service import _commit_trial_and_finalize_experiment

    _commit_trial_and_finalize_experiment(db, experiment)


def _validate_verification_payload(db: Session, payload: dict[str, Any]) -> None:
    expected = payload.get("expected_data_fingerprint")
    if not expected:
        raise BacktestVerificationError("verification data fingerprint is missing")
    from src.services.research_experiment_service import calculate_data_fingerprint

    observed = calculate_data_fingerprint(
        db,
        symbols=list(payload.get("universe_symbols") or []),
        start_date=datetime.fromisoformat(payload["start_date"]).date(),
        end_date=datetime.fromisoformat(payload["end_date"]).date(),
        universe_policy=payload.get("universe_policy"),
    )
    if observed.get("sha256") != expected:
        raise BacktestVerificationError("verification data fingerprint changed")


def _complete_candidate_verification(
    db: Session,
    *,
    payload: dict[str, Any],
    run: StrategyRun,
) -> None:
    candidate_id = payload.get("candidate_id")
    if not candidate_id:
        raise BacktestVerificationError("verification candidate is missing")
    candidate = db.get(ExperimentCandidate, UUID(str(candidate_id)))
    if candidate is None:
        raise BacktestVerificationError("verification candidate no longer exists")
    expected = dict(payload.get("expected_metrics") or {})
    observed = dict(run.summary_metrics or {})
    tolerance = float(payload.get("metric_tolerance") or 1e-10)
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        if expected_value is None:
            if observed.get(key) is not None:
                mismatches.append(key)
            continue
        if not isinstance(expected_value, (int, float)) or isinstance(expected_value, bool):
            continue
        observed_value = observed.get(key)
        if not isinstance(observed_value, (int, float)) or not math.isclose(
            float(observed_value),
            float(expected_value),
            rel_tol=tolerance,
            abs_tol=tolerance,
        ):
            mismatches.append(key)
    if mismatches:
        raise BacktestVerificationError(
            "verification metrics differ: " + ", ".join(sorted(mismatches))
        )
    aggregate = dict(candidate.aggregate_metrics or {})
    verification = dict(aggregate.get("verification") or {})
    verification.update(
        {
            "status": "completed",
            "runId": str(run.id),
            "verifiedAt": datetime.now(UTC).isoformat(),
            "metricTolerance": tolerance,
        }
    )
    aggregate["verification"] = verification
    candidate.aggregate_metrics = aggregate


def _fail_candidate_verification(db: Session, payload: dict[str, Any], message: str) -> None:
    candidate_id = payload.get("candidate_id")
    if not candidate_id:
        return
    candidate = db.get(ExperimentCandidate, UUID(str(candidate_id)))
    if candidate is None:
        return
    aggregate = dict(candidate.aggregate_metrics or {})
    verification = dict(aggregate.get("verification") or {})
    verification.update(
        {"status": "failed", "error": message[:2000], "failedAt": datetime.now(UTC).isoformat()}
    )
    aggregate["verification"] = verification
    candidate.aggregate_metrics = aggregate


def execute_backtest_job(
    job_id: UUID,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> None:
    db = SessionLocal()
    last_progress_write = 0.0
    heartbeat_stop: Event | None = None
    heartbeat_thread: Thread | None = None
    try:
        job = db.get(BacktestJob, job_id)
        if job is None or job.status != "running" or job.claimed_by != worker_id:
            return
        heartbeat_stop = Event()
        heartbeat_thread = Thread(
            target=_heartbeat_loop,
            args=(heartbeat_stop, job.id, worker_id, lease_seconds),
            name=f"backtest-heartbeat-{job.id}",
            daemon=True,
        )
        heartbeat_thread.start()
        run = db.get(StrategyRun, job.run_id)
        if run is None:
            raise ValueError("backtest run not found")
        payload = dict(job.payload or {})
        if job.source == "verification":
            _validate_verification_payload(db, payload)

        # A retry starts from an empty run-scoped detail set. The shared
        # support/resistance materialization remains reusable.
        db.execute(delete(Signal).where(Signal.run_id == run.id))
        db.execute(delete(Transaction).where(Transaction.run_id == run.id))
        db.execute(delete(PortfolioSnapshot).where(PortfolioSnapshot.run_id == run.id))
        db.commit()

        def report(progress: dict[str, Any]) -> None:
            nonlocal last_progress_write
            now = time.monotonic()
            if now - last_progress_write < 5.0 and float(progress.get("percent") or 0) < 100:
                return
            last_progress_write = now
            _update_job_progress(
                job.id,
                worker_id,
                progress,
                lease_seconds=lease_seconds,
            )

        run_backtest(
            db=db,
            strategy_id=payload["strategy_id"],
            start_date=datetime.fromisoformat(payload["start_date"]).date(),
            end_date=datetime.fromisoformat(payload["end_date"]).date(),
            initial_cash=float(payload.get("initial_cash") or 100_000.0),
            benchmark_symbol=payload.get("benchmark_symbol"),
            commission_bps=payload.get("commission_bps"),
            commission_min=payload.get("commission_min"),
            slippage_bps=payload.get("slippage_bps"),
            universe_symbols=payload.get("universe_symbols"),
            universe_metadata=payload.get("universe_metadata"),
            universe_policy=payload.get("universe_policy"),
            existing_run_id=run.id,
            runtime_params_override=payload.get("runtime_params_override"),
            persist_level=payload.get("persist_level") or "full",
            cancel_check=lambda: _job_cancel_requested(job.id),
            progress_callback=report,
            engine_version=payload.get("engine_version"),
        )
        job = db.get(BacktestJob, job.id)
        run = db.get(StrategyRun, run.id)
        assert job is not None and run is not None
        if job.source == "verification":
            _complete_candidate_verification(db, payload=payload, run=run)
        job.status = "completed"
        job.progress = {"percent": 100.0}
        job.heartbeat_at = datetime.now(UTC)
        job.lease_expires_at = None
        job.error_message = None
        experiment_id = _finalize_linked_trial(db, job, run)
        db.commit()
        _finalize_research_experiment(db, experiment_id)
    except Exception as exc:
        db.rollback()
        job = db.get(BacktestJob, job_id)
        if job is None:
            raise
        run = db.get(StrategyRun, job.run_id)
        cancelled = isinstance(exc, BacktestCancelledError) or job.cancel_requested_at is not None
        if cancelled:
            job.status = "cancelled"
            job.error_message = str(exc)
        elif isinstance(exc, BacktestVerificationError):
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            _fail_candidate_verification(db, dict(job.payload or {}), job.error_message)
        elif job.attempt < job.max_attempts:
            job.status = "queued"
            job.available_at = datetime.now(UTC) + timedelta(seconds=5)
            job.error_message = str(exc)[:2000]
            if run is not None:
                run.status = "queued"
                run.started_at = None
                run.finished_at = None
                run.error_message = None
        else:
            job.status = "failed"
            job.error_message = str(exc)[:2000]
        job.claimed_by = None
        job.claimed_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        if run is not None and job.status in {"failed", "cancelled"}:
            run.status = job.status
            run.finished_at = datetime.now(UTC)
            run.error_message = job.error_message
            experiment_id = _finalize_linked_trial(db, job, run)
        else:
            experiment_id = None
        db.commit()
        _finalize_research_experiment(db, experiment_id)
        if job.status == "failed":
            log.exception("Backtest job exhausted retries", extra={"job_id": str(job_id)})
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5.0)
        db.close()
