from __future__ import annotations

import asyncio
import copy
import hashlib
import itertools
import json
import logging
import math
import os
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.core.db import SessionLocal
from src.models.tables import (
    BacktestJob,
    ExperimentCandidate,
    ExperimentRound,
    ExperimentTrial,
    PortfolioSnapshot,
    ResearchExperiment,
    Signal,
    StockBasket,
    Strategy,
    StrategyRun,
    Transaction,
)
from src.schemas.research import ExperimentSpec, ExperimentTokenUsageUpdate
from src.services.backtest_job_service import enqueue_backtest_job, request_backtest_cancel
from src.services.backtest_universe_service import resolve_point_in_time_universe
from src.services.strategy_registry import extract_description, is_engine_ready, normalize_strategy_params
from src.services.strategy_service import validate_strategy_params


log = logging.getLogger(__name__)
MAX_EXPERIMENT_TRIALS = min(50, max(1, int(os.getenv("RESEARCH_MAX_TRIALS", "50"))))
RESEARCH_JOB_QUEUE_LIMIT = max(1, int(os.getenv("RESEARCH_WORKER_CONCURRENCY", "2")))
TERMINAL_STATUSES = {"completed", "partially_failed", "failed", "cancelled"}
_ALLOWED_GRID_PREFIXES = ("signal.", "risk.")


class ExperimentConflictError(RuntimeError):
    pass


class ExperimentNotFoundError(LookupError):
    pass


class ExperimentDataIncompleteError(RuntimeError):
    pass


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_symbols(symbols: list[str]) -> list[str]:
    return sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})


def _path_exists(value: dict[str, Any], path: str) -> bool:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = copy.deepcopy(replacement)


def _load_universe(db: Session, spec: ExperimentSpec) -> tuple[list[str], dict[str, Any]]:
    if spec.universe_policy is not None:
        if spec.in_sample is None or spec.out_of_sample is None:
            raise ValueError("dynamic universe resolution requires inSample and outOfSample")
        start_date = min(spec.in_sample.start_date, spec.out_of_sample.start_date)
        end_date = max(spec.in_sample.end_date, spec.out_of_sample.end_date)
        policy = spec.universe_policy.model_dump(mode="json", by_alias=True)
        resolved = resolve_point_in_time_universe(
            db,
            policy,
            start_date=start_date,
            end_date=end_date,
        )
        symbols = [
            item.canonical_symbol or f"instrument-{item.instrument_id}"
            for item in resolved.instruments
        ]
        metadata = {
            "basketId": None,
            "basketName": None,
            "symbols": [],
            "universePolicy": policy,
            "membershipSemantics": "point_in_time_liquid",
            "instrumentCount": len(resolved.instruments),
            "instrumentIds": resolved.instrument_ids,
            "instrumentSetHash": resolved.manifest()["instrument_set_hash"],
        }
    elif spec.basket_id is not None:
        basket = db.get(StockBasket, spec.basket_id)
        if basket is None:
            raise ValueError("stock basket not found")
        symbols = normalize_symbols(list(basket.symbols or []))
        metadata = {
            "basketId": str(basket.id),
            "basketName": basket.name,
            "symbols": symbols,
        }
    else:
        symbols = normalize_symbols(spec.symbols)
        metadata = {"basketId": None, "basketName": None, "symbols": symbols}
    if not symbols:
        raise ValueError("experiment universe is empty")
    return symbols, metadata


def expand_experiment(
    db: Session,
    spec: ExperimentSpec,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    strategy = db.get(Strategy, spec.strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    base_params = normalize_strategy_params(
        strategy.strategy_type,
        strategy.params,
        extract_description(strategy.params),
    )
    if not is_engine_ready(strategy.strategy_type, base_params):
        raise ValueError("strategy is not engine-ready")

    symbols, universe = _load_universe(db, spec)
    grid_keys = sorted(spec.parameter_grid)
    for path in grid_keys:
        if not path.startswith(_ALLOWED_GRID_PREFIXES):
            raise ValueError(f"parameter grid path is not allowed: {path}")
        if not _path_exists(base_params, path):
            raise ValueError(f"parameter grid path does not exist: {path}")

    grid_values = [spec.parameter_grid[key] for key in grid_keys]
    combinations = itertools.product(*grid_values) if grid_keys else [()]
    definitions: list[dict[str, Any]] = []
    ordinal = 0
    windows = (
        ("in_sample", spec.in_sample.start_date, spec.in_sample.end_date),
        ("out_of_sample", spec.out_of_sample.start_date, spec.out_of_sample.end_date),
    )
    for combination in combinations:
        params = copy.deepcopy(base_params)
        parameter_values = dict(zip(grid_keys, combination, strict=True))
        for path, value in parameter_values.items():
            _set_path(params, path, value)
        params = validate_strategy_params(
            db,
            strategy_type=strategy.strategy_type,
            params=params,
            description=extract_description(params),
        )
        params["universe"]["symbols"] = symbols
        params["universe"]["selection_mode"] = "stock_basket" if spec.basket_id else "manual"
        params_hash = canonical_hash(params)
        for sample_kind, start_date, end_date in windows:
            for scenario in spec.cost_scenarios:
                key_payload = {
                    "paramsHash": params_hash,
                    "sampleKind": sample_kind,
                    "window": [start_date.isoformat(), end_date.isoformat()],
                    "costScenario": scenario.model_dump(mode="json", by_alias=True),
                }
                definitions.append(
                    {
                        "trialKey": canonical_hash(key_payload),
                        "ordinal": ordinal,
                        "sampleKind": sample_kind,
                        "costScenario": scenario.name,
                        "params": params,
                        "paramsHash": params_hash,
                        "windowStart": start_date,
                        "windowEnd": end_date,
                        "costConfig": scenario.model_dump(mode="json", by_alias=True),
                    }
                )
                ordinal += 1
                if len(definitions) > MAX_EXPERIMENT_TRIALS:
                    raise ValueError(f"experiment expands to more than {MAX_EXPERIMENT_TRIALS} trials")
    return definitions, symbols, universe


def get_experiment(db: Session, experiment_id: UUID | str) -> ResearchExperiment:
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(str(experiment_id))
    return experiment


def cancel_experiment(db: Session, experiment_id: UUID | str) -> ResearchExperiment:
    experiment = get_experiment(db, experiment_id)
    if experiment.status in TERMINAL_STATUSES:
        return experiment
    if experiment.study_kind == "support_resistance_effectiveness_v3":
        child_ids = list(
            db.execute(
                select(ResearchExperiment.id).where(
                    ResearchExperiment.parent_experiment_id == experiment.id
                )
            ).scalars()
        )
        for child_id in child_ids:
            cancel_experiment(db, child_id)
        experiment = get_experiment(db, experiment_id)
    experiment.status = "cancel_requested"
    db.execute(
        ExperimentTrial.__table__.update()
        .where(ExperimentTrial.experiment_id == experiment.id, ExperimentTrial.status == "queued")
        .values(status="cancelled", finished_at=datetime.now(UTC))
    )
    now = datetime.now(UTC)
    active_jobs = list(
        db.execute(
            select(BacktestJob)
            .join(ExperimentTrial, ExperimentTrial.id == BacktestJob.experiment_trial_id)
            .where(
                ExperimentTrial.experiment_id == experiment.id,
                BacktestJob.status.in_({"queued", "running"}),
            )
        ).scalars()
    )
    for job in active_jobs:
        job.cancel_requested_at = now
        if job.status == "queued":
            job.status = "cancelled"
            trial = db.get(ExperimentTrial, job.experiment_trial_id)
            if trial is not None:
                trial.status = "cancelled"
                trial.finished_at = now
                trial.error_code = "cancelled"
            run = db.get(StrategyRun, job.run_id)
            if run is not None:
                run.status = "cancelled"
                run.finished_at = now
                run.error_message = "research experiment cancelled before execution"
    _refresh_progress(db, experiment)
    _finalize_if_ready(db, experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def update_experiment_token_usage(
    db: Session,
    experiment_id: UUID | str,
    payload: ExperimentTokenUsageUpdate,
) -> ResearchExperiment:
    experiment = get_experiment(db, experiment_id)
    if experiment.workflow_run_id != payload.workflow_run_id:
        raise ExperimentConflictError("workflow run does not own this experiment")
    manifest = dict(experiment.run_manifest or {})
    previous = manifest.get("tokenUsage") if isinstance(manifest.get("tokenUsage"), dict) else {}
    usage = payload.model_dump(mode="json", by_alias=True, exclude={"workflow_run_id"})
    manifest["tokenUsage"] = {
        key: max(int(previous.get(key) or 0), int(value or 0))
        for key, value in usage.items()
    }
    manifest["tokenUsageUpdatedAt"] = datetime.now(UTC).isoformat()
    experiment.run_manifest = manifest
    enforce_experiment_stop_policy(db, experiment)
    if experiment.status in TERMINAL_STATUSES:
        experiment.report = build_experiment_report(db, experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def list_experiments(db: Session, *, limit: int = 100) -> list[ResearchExperiment]:
    return list(
        db.execute(
            select(ResearchExperiment).order_by(ResearchExperiment.created_at.desc()).limit(limit)
        ).scalars()
    )


def list_trials(db: Session, experiment_id: UUID | str) -> list[ExperimentTrial]:
    experiment = get_experiment(db, experiment_id)
    return list(
        db.execute(
            select(ExperimentTrial)
            .where(ExperimentTrial.experiment_id == experiment.id)
            .order_by(ExperimentTrial.ordinal)
        ).scalars()
    )


def _refresh_progress(db: Session, experiment: ResearchExperiment) -> None:
    counts = {
        str(status): int(count)
        for status, count in db.execute(
            select(ExperimentTrial.status, func.count())
            .where(ExperimentTrial.experiment_id == experiment.id)
            .group_by(ExperimentTrial.status)
        ).all()
    }
    experiment.progress = {
        "total": sum(counts.values()),
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
    }


def build_experiment_report(db: Session, experiment: ResearchExperiment) -> dict[str, Any]:
    trials = list_trials(db, experiment.id)
    completed = [trial for trial in trials if trial.status == "completed"]
    out_of_sample = [trial for trial in completed if trial.sample_kind == "out_of_sample"]
    def metric_value(trial: ExperimentTrial, key: str) -> float | None:
        value = (trial.metrics or {}).get(key)
        return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None

    ranked = sorted(
        out_of_sample,
        key=lambda item: metric_value(item, "total_return") if metric_value(item, "total_return") is not None else float("-inf"),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    spec = experiment.spec or {}
    scenarios = [str(item.get("name")) for item in spec.get("costScenarios", []) if isinstance(item, dict)]
    base_scenario = next(
        (scenario for scenario in scenarios if scenario.strip().lower() == "base"),
        None,
    )
    grouped: dict[str, dict[tuple[str, str], ExperimentTrial]] = {}
    params_order: list[str] = []
    for trial in completed:
        if trial.params_hash not in grouped:
            grouped[trial.params_hash] = {}
            params_order.append(trial.params_hash)
        grouped[trial.params_hash][(trial.sample_kind, trial.cost_scenario)] = trial
    sample_gaps: list[dict[str, Any]] = []
    cost_decay: list[dict[str, Any]] = []
    for params_hash in params_order:
        items = grouped[params_hash]
        in_sample_trial = items.get(("in_sample", base_scenario or ""))
        out_sample_trial = items.get(("out_of_sample", base_scenario or ""))
        in_return = metric_value(in_sample_trial, "total_return") if in_sample_trial else None
        out_return = metric_value(out_sample_trial, "total_return") if out_sample_trial else None
        sample_gaps.append({
            "paramsHash": params_hash,
            "inSampleReturn": in_return,
            "outOfSampleReturn": out_return,
            "outMinusIn": out_return - in_return if in_return is not None and out_return is not None else None,
        })
        if out_return is not None:
            for scenario in (item for item in scenarios if item != base_scenario):
                stress_trial = items.get(("out_of_sample", scenario))
                stress_return = metric_value(stress_trial, "total_return") if stress_trial else None
                cost_decay.append({
                    "paramsHash": params_hash,
                    "scenario": scenario,
                    "baseReturn": out_return,
                    "stressReturn": stress_return,
                    "decay": stress_return - out_return if stress_return is not None else None,
                })
    adjacent_stability: list[dict[str, Any]] = []
    ordered_base_oos = [grouped[item].get(("out_of_sample", base_scenario or "")) for item in params_order]
    for left, right in zip(ordered_base_oos, ordered_base_oos[1:]):
        if left is None or right is None:
            continue
        left_return = metric_value(left, "total_return")
        right_return = metric_value(right, "total_return")
        adjacent_stability.append({
            "leftParamsHash": left.params_hash,
            "rightParamsHash": right.params_hash,
            "returnDifference": abs(right_return - left_return) if left_return is not None and right_return is not None else None,
        })
    manifest = experiment.run_manifest or {}
    termination = manifest.get("termination") if isinstance(manifest.get("termination"), dict) else None
    return {
        "disclaimer": "Research evidence only; this is not a profitability or live-trading safety guarantee.",
        "status": experiment.status,
        "termination": termination or {
            "reason": "all_trials_completed",
            "earlyStopped": False,
            "triggeredConditions": [],
        },
        "tokenUsage": manifest.get("tokenUsage") or {},
        "counts": dict(experiment.progress or {}),
        "bestOutOfSampleTrial": (
            {
                "trialId": str(best.id),
                "backtestRunId": str(best.backtest_run_id) if best.backtest_run_id else None,
                "paramsHash": best.params_hash,
                "costScenario": best.cost_scenario,
                "metrics": best.metrics,
            }
            if best
            else None
        ),
        "outOfSampleRanking": [
            {
                "trialId": str(trial.id),
                "backtestRunId": str(trial.backtest_run_id) if trial.backtest_run_id else None,
                "paramsHash": trial.params_hash,
                "costScenario": trial.cost_scenario,
                "totalReturn": (trial.metrics or {}).get("total_return"),
                "maxDrawdown": (trial.metrics or {}).get("max_drawdown"),
                "sharpe": (trial.metrics or {}).get("sharpe"),
                "sortino": (trial.metrics or {}).get("sortino"),
                "excessReturn": (trial.metrics or {}).get("excess_return"),
                "turnover": (trial.metrics or {}).get("turnover"),
                "symbolReturnContribution": (trial.metrics or {}).get("symbol_return_contribution"),
                "pnlConcentration": (trial.metrics or {}).get("pnl_concentration"),
            }
            for trial in ranked
        ],
        "sampleGeneralization": sample_gaps,
        "costDecay": cost_decay,
        "adjacentParameterStability": adjacent_stability,
        "parameterSensitivity": {
            "completedParameterSets": len(grouped),
            "baseScenario": base_scenario,
        },
        "strategy": {
            "id": manifest.get("strategyId"),
            "version": manifest.get("strategyVersion"),
            "type": manifest.get("strategyType"),
        },
        "parameterSets": [
            {
                "paramsHash": params_hash,
                "params": next(
                    (trial.params for trial in completed if trial.params_hash == params_hash),
                    {},
                ),
            }
            for params_hash in params_order
        ],
        "lineage": [
            {
                "trialId": str(trial.id),
                "backtestRunId": str(trial.backtest_run_id) if trial.backtest_run_id else None,
                "paramsHash": trial.params_hash,
                "sampleKind": trial.sample_kind,
                "costScenario": trial.cost_scenario,
                "status": trial.status,
            }
            for trial in trials
        ],
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _finalize_if_ready(db: Session, experiment: ResearchExperiment) -> None:
    # Effectiveness parents are orchestration containers with no direct trials.
    # Their child-state machine owns completion and report generation.
    if experiment.study_kind == "support_resistance_effectiveness_v3":
        return
    _refresh_progress(db, experiment)
    progress = experiment.progress
    if progress["queued"] or progress["running"]:
        return
    from src.services.adaptive_research_service import (
        build_adaptive_report,
        finalize_adaptive_round_if_ready,
    )

    report_builder = (
        build_adaptive_report
        if (experiment.spec or {}).get("researchMode") == "adaptive_category"
        else build_experiment_report
    )
    if finalize_adaptive_round_if_ready(db, experiment):
        return
    termination = (experiment.run_manifest or {}).get("termination")
    if isinstance(termination, dict) and termination.get("earlyStopped"):
        if progress["completed"] and progress["failed"]:
            experiment.status = "partially_failed"
        elif progress["failed"]:
            experiment.status = "failed"
        else:
            experiment.status = "completed"
        experiment.error_code = None
        experiment.error_message = None
    elif experiment.status == "cancel_requested":
        experiment.status = "cancelled"
    elif progress["completed"] and progress["failed"]:
        experiment.status = "partially_failed"
    elif progress["completed"]:
        experiment.status = "completed"
    else:
        experiment.status = "failed"
    experiment.finished_at = datetime.now(UTC)
    experiment.report = report_builder(db, experiment)


def _target_condition_matches(
    db: Session,
    experiment: ResearchExperiment,
    condition: dict[str, Any],
) -> dict[str, Any] | None:
    metric = str(condition.get("metric") or "")
    operator = str(condition.get("operator") or "gte")
    expected = condition.get("value")
    if not isinstance(expected, (int, float)):
        return None
    trials = db.execute(
        select(ExperimentTrial)
        .where(
            ExperimentTrial.experiment_id == experiment.id,
            ExperimentTrial.status == "completed",
            ExperimentTrial.sample_kind == str(condition.get("sampleKind") or "out_of_sample"),
            ExperimentTrial.cost_scenario == str(condition.get("costScenario") or "base"),
        )
        .order_by(ExperimentTrial.ordinal)
    ).scalars()
    for trial in trials:
        observed = (trial.metrics or {}).get(metric)
        if not isinstance(observed, (int, float)) or not math.isfinite(float(observed)):
            continue
        matched = float(observed) >= float(expected) if operator == "gte" else float(observed) <= float(expected)
        if matched:
            return {
                "metric": metric,
                "operator": operator,
                "target": float(expected),
                "observed": float(observed),
                "trialId": str(trial.id),
                "backtestRunId": str(trial.backtest_run_id) if trial.backtest_run_id else None,
            }
    return None


def enforce_experiment_stop_policy(
    db: Session,
    experiment: ResearchExperiment,
    *,
    now: datetime | None = None,
) -> str | None:
    manifest = dict(experiment.run_manifest or {})
    if isinstance(manifest.get("termination"), dict):
        return None
    spec = experiment.spec or {}
    policy = spec.get("stopPolicy") if isinstance(spec.get("stopPolicy"), dict) else None
    if not policy or experiment.status in TERMINAL_STATUSES | {"cancel_requested"}:
        return None

    current_time = now or datetime.now(UTC)
    triggered: list[dict[str, Any]] = []
    target = policy.get("targetMetric") if isinstance(policy.get("targetMetric"), dict) else None
    target_match = _target_condition_matches(db, experiment, target) if target else None
    if target_match:
        triggered.append({"reason": "target_reached", **target_match})

    token_budget = policy.get("tokenBudget")
    token_usage = manifest.get("tokenUsage") if isinstance(manifest.get("tokenUsage"), dict) else {}
    total_tokens = int(token_usage.get("totalTokens") or 0)
    if isinstance(token_budget, int) and total_tokens >= token_budget:
        triggered.append(
            {
                "reason": "token_budget_reached",
                "tokenBudget": token_budget,
                "totalTokens": total_tokens,
            }
        )

    max_duration = policy.get("maxDurationSeconds")
    started_raw = manifest.get("policyStartedAt") or manifest.get("createdAt")
    try:
        policy_started_at = datetime.fromisoformat(str(started_raw))
        if policy_started_at.tzinfo is None:
            policy_started_at = policy_started_at.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        policy_started_at = current_time
    elapsed_seconds = max(0, int((current_time - policy_started_at).total_seconds()))
    if isinstance(max_duration, int) and elapsed_seconds >= max_duration:
        triggered.append(
            {
                "reason": "time_limit_reached",
                "maxDurationSeconds": max_duration,
                "elapsedSeconds": elapsed_seconds,
            }
        )

    if not triggered:
        return None
    reason = str(triggered[0]["reason"])
    manifest["termination"] = {
        "reason": reason,
        "earlyStopped": True,
        "triggeredConditions": triggered,
        "stoppedAt": current_time.isoformat(),
    }
    experiment.run_manifest = manifest
    db.execute(
        ExperimentTrial.__table__.update()
        .where(
            ExperimentTrial.experiment_id == experiment.id,
            ExperimentTrial.status == "queued",
        )
        .values(
            status="cancelled",
            error_code="policy_stopped",
            error_message=f"Experiment stopped after {reason}.",
            finished_at=current_time,
        )
    )
    db.flush()
    _refresh_progress(db, experiment)
    _finalize_if_ready(db, experiment)
    log.info(
        "Research stop policy triggered workflow_run_id=%s experiment_id=%s reason=%s",
        experiment.workflow_run_id,
        experiment.id,
        reason,
    )
    return reason


def enforce_active_experiment_stop_policies(db: Session) -> int:
    experiments = list(
        db.execute(
            select(ResearchExperiment).where(
                ResearchExperiment.status.in_({"queued", "running", "waiting_agent"})
            )
        ).scalars()
    )
    stopped = sum(bool(enforce_experiment_stop_policy(db, experiment)) for experiment in experiments)
    if stopped:
        db.commit()
    return stopped


def _commit_trial_and_finalize_experiment(db: Session, experiment: ResearchExperiment) -> None:
    """Make this worker's trial terminal before computing cross-worker progress."""
    experiment_id = experiment.id
    db.commit()
    db.expire_all()
    current = db.get(ResearchExperiment, experiment_id)
    if current is not None:
        enforce_experiment_stop_policy(db, current)
        _finalize_if_ready(db, current)
    current_id = getattr(current, "id", None)
    parent_id = getattr(current, "parent_experiment_id", None)
    db.commit()
    if current_id is not None and parent_id is not None:
        from src.services.support_resistance_validation_service import (
            advance_effectiveness_study,
        )

        advance_effectiveness_study(db, current_id)


def cancel_trial(
    db: Session,
    experiment_id: UUID | str,
    trial_id: UUID | str,
) -> ExperimentTrial:
    trial = db.execute(
        select(ExperimentTrial)
        .where(
            ExperimentTrial.id == UUID(str(trial_id)),
            ExperimentTrial.experiment_id == UUID(str(experiment_id)),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if trial is None:
        raise ExperimentNotFoundError(str(trial_id))
    if trial.status in {"completed", "failed", "cancelled"}:
        return trial

    now = datetime.now(UTC)
    trial.cancel_requested_at = trial.cancel_requested_at or now
    job = db.execute(
        select(BacktestJob).where(BacktestJob.experiment_trial_id == trial.id)
    ).scalar_one_or_none()
    if job is not None:
        db.flush()
        request_backtest_cancel(db, job.run_id)
        db.refresh(trial)
        return trial

    if trial.status == "queued":
        trial.status = "cancelled"
        trial.error_code = "cancelled"
        trial.error_message = "research trial cancelled before backtest queueing"
        trial.finished_at = now
        if trial.backtest_run_id is not None:
            run = db.get(StrategyRun, trial.backtest_run_id)
            if run is not None:
                run.status = "cancelled"
                run.finished_at = now
                run.error_message = trial.error_message
        experiment = db.get(ResearchExperiment, trial.experiment_id)
        assert experiment is not None
        _commit_trial_and_finalize_experiment(db, experiment)
    else:
        db.commit()
    db.refresh(trial)
    return trial


def _recovery_stop_code(experiment: ResearchExperiment | None) -> str | None:
    if experiment is None:
        return None
    if experiment.status == "cancel_requested":
        return experiment.status
    termination = (experiment.run_manifest or {}).get("termination")
    if isinstance(termination, dict) and termination.get("earlyStopped"):
        return "policy_stopped"
    return None


def recover_orphaned_trials() -> int:
    db = SessionLocal()
    try:
        affected_experiment_ids = set(
            db.execute(
                select(ResearchExperiment.id).where(
                    ResearchExperiment.status.in_(
                        {"queued", "running", "cancel_requested"}
                    )
                )
            ).scalars()
        )
        trials = list(
            db.execute(select(ExperimentTrial).where(ExperimentTrial.status == "running")).scalars()
        )
        recovered = 0
        for trial in trials:
            durable_job = db.execute(
                select(BacktestJob).where(BacktestJob.experiment_trial_id == trial.id)
            ).scalar_one_or_none()
            if durable_job is not None and durable_job.status in {"queued", "running"}:
                continue
            experiment = db.get(ResearchExperiment, trial.experiment_id)
            affected_experiment_ids.add(trial.experiment_id)
            stop_code = _recovery_stop_code(experiment)
            if stop_code is not None:
                trial.status = "cancelled"
                trial.finished_at = datetime.now(UTC)
                trial.error_code = stop_code
                trial.error_message = "The trial was stopped while the research worker restarted."
            else:
                trial.status = "queued"
                trial.error_code = "worker_restarted"
                trial.error_message = "The worker restarted; this trial was safely re-queued."
            if trial.backtest_run_id:
                run = db.get(StrategyRun, trial.backtest_run_id)
                if run is not None and run.status == "running":
                    run.status = "failed"
                    run.finished_at = datetime.now(UTC)
                    run.error_message = "research worker restarted"
            recovered += 1
        db.flush()
        for experiment_id in affected_experiment_ids:
            experiment = db.get(ResearchExperiment, experiment_id)
            if experiment is not None:
                active_job_count = int(
                    db.execute(
                        select(func.count())
                        .select_from(BacktestJob)
                        .join(ExperimentTrial, ExperimentTrial.id == BacktestJob.experiment_trial_id)
                        .where(
                            ExperimentTrial.experiment_id == experiment.id,
                            BacktestJob.status.in_({"queued", "running"}),
                        )
                    ).scalar_one()
                )
                if experiment.status == "running" and active_job_count == 0:
                    experiment.status = "queued"
                _finalize_if_ready(db, experiment)
        db.commit()
        return recovered
    finally:
        db.close()


def _claim_trial(db: Session) -> ExperimentTrial | None:
    enforce_active_experiment_stop_policies(db)
    running_count = int(
        db.execute(
            select(func.count()).select_from(ExperimentTrial).where(ExperimentTrial.status == "running")
        ).scalar_one()
    )
    if running_count >= RESEARCH_JOB_QUEUE_LIMIT:
        db.rollback()
        return None
    trial = db.execute(
        select(ExperimentTrial)
        .join(ResearchExperiment, ResearchExperiment.id == ExperimentTrial.experiment_id)
        .where(
            ExperimentTrial.status == "queued",
            ResearchExperiment.status.in_(["queued", "running"]),
        )
        .order_by(ResearchExperiment.created_at, ExperimentTrial.ordinal)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if trial is None:
        return None
    experiment = db.get(ResearchExperiment, trial.experiment_id)
    assert experiment is not None
    trial.status = "running"
    trial.attempt += 1
    trial.started_at = datetime.now(UTC)
    trial.error_code = None
    trial.error_message = None
    experiment.status = "running"
    experiment.started_at = experiment.started_at or datetime.now(UTC)
    if trial.candidate_id:
        candidate = db.get(ExperimentCandidate, trial.candidate_id)
        if candidate is not None:
            round_row = db.get(ExperimentRound, candidate.round_id)
            if round_row is not None and round_row.status == "queued":
                round_row.status = "running"
                round_row.started_at = round_row.started_at or datetime.now(UTC)
    _refresh_progress(db, experiment)
    db.commit()
    db.refresh(trial)
    log.info(
        "Research trial claimed experiment_id=%s trial_id=%s ordinal=%s attempt=%s",
        trial.experiment_id,
        trial.id,
        trial.ordinal,
        trial.attempt,
    )
    return trial


def _prepare_backtest_run(db: Session, trial: ExperimentTrial, experiment: ResearchExperiment) -> StrategyRun:
    strategy_id = UUID(str(experiment.spec["strategyId"]))
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if trial.backtest_run_id:
        run = db.get(StrategyRun, trial.backtest_run_id)
        if run is None:
            trial.backtest_run_id = None
        else:
            db.execute(delete(Signal).where(Signal.run_id == run.id))
            db.execute(delete(Transaction).where(Transaction.run_id == run.id))
            db.execute(delete(PortfolioSnapshot).where(PortfolioSnapshot.run_id == run.id))
            run.status = "queued"
            run.started_at = None
            run.finished_at = None
            run.summary_metrics = {}
            run.error_message = None
            db.commit()
            return run
    run = StrategyRun(
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        mode="backtest",
        status="queued",
        window_start=trial.window_start,
        window_end=trial.window_end,
        initial_cash=float(experiment.spec["initialCash"]),
        benchmark_symbol=experiment.spec.get("benchmarkSymbol"),
        config_snapshot=trial.params,
    )
    db.add(run)
    db.flush()
    trial.backtest_run_id = run.id
    db.commit()
    db.refresh(run)
    return run


def _finish_cancelled_claim(
    db: Session,
    trial: ExperimentTrial,
    experiment: ResearchExperiment,
) -> bool:
    if trial.cancel_requested_at is None:
        return False
    now = datetime.now(UTC)
    trial.status = "cancelled"
    trial.error_code = "cancelled"
    trial.error_message = "research trial cancelled before backtest queueing"
    trial.finished_at = now
    if trial.backtest_run_id is not None:
        run = db.get(StrategyRun, trial.backtest_run_id)
        if run is not None:
            run.status = "cancelled"
            run.finished_at = now
            run.error_message = trial.error_message
    _commit_trial_and_finalize_experiment(db, experiment)
    return True


def process_next_trial() -> bool:
    db = SessionLocal()
    try:
        trial = _claim_trial(db)
        if trial is None:
            return False
        experiment = db.get(ResearchExperiment, trial.experiment_id)
        assert experiment is not None
        try:
            db.refresh(trial)
            if _finish_cancelled_claim(db, trial, experiment):
                return True
            manifest = experiment.run_manifest or {}
            universe = manifest.get("universe") or {}
            symbols = list(universe.get("symbols") or [])
            spec = experiment.spec or {}
            run = _prepare_backtest_run(db, trial, experiment)
            trial = db.execute(
                select(ExperimentTrial)
                .where(ExperimentTrial.id == trial.id)
                .with_for_update()
            ).scalar_one()
            if _finish_cancelled_claim(db, trial, experiment):
                return True
            log.info(
                "Research backtest queued workflow_run_id=%s experiment_id=%s trial_id=%s backtest_id=%s",
                experiment.workflow_run_id,
                experiment.id,
                trial.id,
                run.id,
            )
            costs = trial.cost_config or {}
            persist_level = "full" if experiment.parent_experiment_id is not None else "summary"
            enqueue_backtest_job(
                db,
                run=run,
                source="research",
                experiment_trial_id=trial.id,
                priority=10,
                payload={
                    "strategy_id": str(spec["strategyId"]),
                    "start_date": trial.window_start.isoformat(),
                    "end_date": trial.window_end.isoformat(),
                    "initial_cash": float(spec["initialCash"]),
                    "benchmark_symbol": spec.get("benchmarkSymbol"),
                    "commission_bps": float(costs.get("commissionBps", 0)),
                    "commission_min": float(costs.get("commissionMin", 0)),
                    "slippage_bps": float(costs.get("slippageBps", 0)),
                    "universe_symbols": None if spec.get("universePolicy") else symbols,
                    "universe_metadata": universe,
                    "universe_policy": spec.get("universePolicy"),
                    "runtime_params_override": trial.params,
                    "persist_level": persist_level,
                    "prepared_dataset": manifest.get("preparedDataset"),
                    "parameter_hash": canonical_hash(trial.params),
                },
            )
            run.config_snapshot = {
                **dict(trial.params or {}),
                "run_options": {
                    "persist_level": persist_level,
                    "source": "research",
                },
            }
            db.commit()
            return True
        except Exception as exc:
            db.rollback()
            trial = db.get(ExperimentTrial, trial.id)
            experiment = db.get(ResearchExperiment, trial.experiment_id) if trial else None
            error_code = "data_incomplete" if isinstance(exc, ExperimentDataIncompleteError) else "trial_failed"
            if trial is not None:
                trial.status = "failed"
                trial.error_code = error_code
                trial.error_message = str(exc)[:2000]
                trial.finished_at = datetime.now(UTC)
            if experiment is not None:
                experiment.error_code = error_code
                experiment.error_message = "One or more research trials failed."
            log.exception("Research trial failed", extra={"trial_id": str(trial.id) if trial else None})
        if experiment is not None:
            _commit_trial_and_finalize_experiment(db, experiment)
        else:
            db.commit()
        return True
    finally:
        db.close()


class ResearchExperimentWorker:
    def __init__(self) -> None:
        self._loop_task: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[bool]] = set()
        self._stopping = False
        self._concurrency = max(1, int(os.getenv("RESEARCH_WORKER_CONCURRENCY", "2")))

    def status_snapshot(self, *, enabled: bool) -> dict[str, Any]:
        active = sum(not task.done() for task in self._active)
        if not enabled:
            state = "disabled"
        elif self._stopping:
            state = "stopping"
        elif self._loop_task is None or self._loop_task.done():
            state = "failed"
        else:
            state = "running" if active else "idle"
        return {
            "enabled": enabled,
            "state": state,
            "configured_concurrency": self._concurrency,
            "active_trials": active,
            "available_slots": max(self._concurrency - active, 0) if state in {"idle", "running"} else 0,
        }

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        recovered = await asyncio.to_thread(recover_orphaned_trials)
        if recovered:
            log.warning("Re-queued %s orphaned research trial(s).", recovered)
        self._stopping = False
        self._loop_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._loop_task is not None:
            await self._loop_task
            self._loop_task = None
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    async def _run(self) -> None:
        while not self._stopping:
            finished = {task for task in self._active if task.done()}
            for task in finished:
                try:
                    task.result()
                except Exception:
                    log.exception("Research worker task failed outside trial handling")
            self._active.difference_update(finished)
            while len(self._active) < self._concurrency:
                task = asyncio.create_task(asyncio.to_thread(process_next_trial))
                self._active.add(task)
                await asyncio.sleep(0)
                if task.done():
                    try:
                        claimed = task.result()
                    except Exception:
                        log.exception("Research worker could not claim a trial")
                        claimed = False
                    self._active.discard(task)
                    if not claimed:
                        break
            await asyncio.sleep(1)
