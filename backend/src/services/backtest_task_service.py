from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import (
    BacktestJob,
    ExperimentCandidate,
    ExperimentTrial,
    ResearchExperiment,
    Strategy,
    StrategyRun,
)
from src.services.backtest_job_service import TERMINAL_BACKTEST_STATUSES, normalize_backtest_progress


TaskSource = Literal["manual", "research", "verification"]
TaskStage = Literal[
    "waiting_research",
    "queued",
    "preparing",
    "running",
    "finalizing",
    "cancel_requested",
    "completed",
    "failed",
    "cancelled",
]
ACTIVE_TASK_STAGES = {
    "waiting_research",
    "queued",
    "preparing",
    "running",
    "finalizing",
    "cancel_requested",
}


def _job_progress(job: BacktestJob | None, run: StrategyRun | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return normalize_backtest_progress(
        dict(job.progress or {}),
        status=job.status,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        updated_at=job.updated_at or (run.updated_at if run is not None else None),
    )


def _stage(
    *,
    job: BacktestJob | None,
    run: StrategyRun | None,
    trial: ExperimentTrial | None = None,
) -> TaskStage:
    if (job is not None and job.cancel_requested_at is not None) or (
        trial is not None and trial.cancel_requested_at is not None and trial.status not in {"completed", "failed", "cancelled"}
    ):
        return "cancel_requested"
    if job is not None:
        progress = _job_progress(job, run)
        phase = str((progress or {}).get("phase") or job.status)
        if phase in {
            "queued",
            "preparing",
            "running",
            "finalizing",
            "completed",
            "failed",
            "cancelled",
        }:
            return phase  # type: ignore[return-value]
    if trial is not None:
        if trial.status == "queued" and trial.backtest_run_id is None:
            return "waiting_research"
        return trial.status  # type: ignore[return-value]
    return str(run.status if run is not None else "failed")  # type: ignore[return-value]


def _experiment_name(experiment: ResearchExperiment | None) -> str | None:
    if experiment is None:
        return None
    value = (experiment.spec or {}).get("name")
    return str(value) if value else str(experiment.id)


def _uuid_or_none(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _sort_time(item: dict[str, Any]) -> datetime:
    for key in ("updated_at", "started_at", "requested_at", "created_at"):
        value = item.get(key)
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def list_backtest_tasks(
    db: Session,
    *,
    source: TaskSource | None = None,
    stage: TaskStage | Literal["active"] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    trial_rows = db.execute(
        select(
            ExperimentTrial,
            ResearchExperiment,
            ExperimentCandidate,
            StrategyRun,
            BacktestJob,
            Strategy,
        )
        .join(ResearchExperiment, ResearchExperiment.id == ExperimentTrial.experiment_id)
        .outerjoin(ExperimentCandidate, ExperimentCandidate.id == ExperimentTrial.candidate_id)
        .outerjoin(StrategyRun, StrategyRun.id == ExperimentTrial.backtest_run_id)
        .outerjoin(BacktestJob, BacktestJob.run_id == StrategyRun.id)
        .outerjoin(Strategy, Strategy.id == StrategyRun.strategy_id)
    ).all()
    trial_run_ids = {run.id for _trial, _experiment, _candidate, run, _job, _strategy in trial_rows if run is not None}
    experiment_strategy_ids = {
        strategy_id
        for _trial, experiment, _candidate, run, _job, _strategy in trial_rows
        if run is None
        if (strategy_id := _uuid_or_none((experiment.spec or {}).get("strategyId"))) is not None
    }
    experiment_strategies = {
        strategy.id: strategy
        for strategy in db.scalars(select(Strategy).where(Strategy.id.in_(experiment_strategy_ids)))
    } if experiment_strategy_ids else {}

    for trial, experiment, candidate, run, job, strategy in trial_rows:
        task_stage = _stage(job=job, run=run, trial=trial)
        strategy_id = run.strategy_id if run is not None else _uuid_or_none((experiment.spec or {}).get("strategyId"))
        displayed_strategy = strategy or experiment_strategies.get(strategy_id)
        tasks.append(
            {
                "task_key": f"trial:{trial.id}",
                "source": "research",
                "stage": task_stage,
                "job_id": job.id if job is not None else None,
                "run_id": run.id if run is not None else trial.backtest_run_id,
                "trial_id": trial.id,
                "experiment_id": trial.experiment_id,
                "candidate_id": trial.candidate_id,
                "strategy_id": strategy_id,
                "strategy_name": displayed_strategy.name if displayed_strategy is not None else None,
                "experiment_name": _experiment_name(experiment),
                "trial_ordinal": trial.ordinal,
                "sample_kind": trial.sample_kind,
                "cost_scenario": trial.cost_scenario,
                "window_start": trial.window_start,
                "window_end": trial.window_end,
                "progress": _job_progress(job, run),
                "attempt": job.attempt if job is not None else trial.attempt,
                "max_attempts": job.max_attempts if job is not None else None,
                "requested_at": run.requested_at if run is not None else trial.created_at,
                "started_at": run.started_at if run is not None else trial.started_at,
                "finished_at": run.finished_at if run is not None else trial.finished_at,
                "updated_at": max(
                    (value for value in (trial.updated_at, job.updated_at if job is not None else None, run.updated_at if run is not None else None) if value is not None),
                    default=trial.updated_at,
                ),
                "cancel_requested_at": trial.cancel_requested_at or (job.cancel_requested_at if job is not None else None),
                "error_code": trial.error_code,
                "error_message": trial.error_message or (job.error_message if job is not None else None) or (run.error_message if run is not None else None),
                "cancellable": trial.status in {"queued", "running"} and trial.cancel_requested_at is None,
                "retryable": False,
                "deletable": False,
            }
        )

    run_rows = db.execute(
        select(StrategyRun, BacktestJob, Strategy)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .outerjoin(BacktestJob, BacktestJob.run_id == StrategyRun.id)
        .where(StrategyRun.mode == "backtest")
    ).all()
    for run, job, strategy in run_rows:
        if run.id in trial_run_ids:
            continue
        task_source = str(job.source) if job is not None else "manual"
        if task_source not in {"manual", "research", "verification"}:
            continue
        candidate = None
        experiment = None
        if task_source == "verification" and job is not None and (job.payload or {}).get("candidate_id"):
            candidate = db.get(ExperimentCandidate, UUID(str((job.payload or {})["candidate_id"])))
            experiment = db.get(ResearchExperiment, candidate.experiment_id) if candidate is not None else None
        task_stage = _stage(job=job, run=run)
        tasks.append(
            {
                "task_key": f"job:{job.id}" if job is not None else f"run:{run.id}",
                "source": task_source,
                "stage": task_stage,
                "job_id": job.id if job is not None else None,
                "run_id": run.id,
                "trial_id": None,
                "experiment_id": experiment.id if experiment is not None else None,
                "candidate_id": candidate.id if candidate is not None else None,
                "strategy_id": run.strategy_id,
                "strategy_name": strategy.name,
                "experiment_name": _experiment_name(experiment),
                "trial_ordinal": None,
                "sample_kind": None,
                "cost_scenario": None,
                "window_start": run.window_start,
                "window_end": run.window_end,
                "progress": _job_progress(job, run),
                "attempt": job.attempt if job is not None else 0,
                "max_attempts": job.max_attempts if job is not None else None,
                "requested_at": run.requested_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "updated_at": max(
                    (value for value in (run.updated_at, job.updated_at if job is not None else None) if value is not None),
                    default=run.updated_at,
                ),
                "cancel_requested_at": job.cancel_requested_at if job is not None else None,
                "error_code": None,
                "error_message": (job.error_message if job is not None else None) or run.error_message,
                "cancellable": job is not None and job.status in {"queued", "running"} and job.cancel_requested_at is None,
                "retryable": (
                    job is not None
                    and job.status == "failed"
                    and task_source in {"manual", "verification"}
                    and not (job.payload or {}).get("retried_by_run_id")
                ),
                "deletable": (
                    task_source == "manual"
                    and run.status in TERMINAL_BACKTEST_STATUSES
                    and (job is None or job.status in TERMINAL_BACKTEST_STATUSES)
                ),
            }
        )

    if source is not None:
        tasks = [item for item in tasks if item["source"] == source]
    counts: dict[str, int] = {}
    for item in tasks:
        counts[item["stage"]] = counts.get(item["stage"], 0) + 1
    if stage == "active":
        tasks = [item for item in tasks if item["stage"] in ACTIVE_TASK_STAGES]
    elif stage is not None:
        tasks = [item for item in tasks if item["stage"] == stage]

    # ponytail: paginate the small local research history in memory; replace with a SQL UNION when task volume warrants it.
    tasks.sort(key=lambda item: (0 if item["stage"] in ACTIVE_TASK_STAGES else 1, -_sort_time(item).timestamp(), item["task_key"]))
    total = len(tasks)
    return {"items": tasks[offset : offset + limit], "total": total, "counts": counts}
