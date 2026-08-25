from __future__ import annotations

import asyncio
import copy
import hashlib
import itertools
import json
import logging
import math
import os
import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.orm import Session

from src.core.db import SessionLocal
from src.models.tables import (
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
from src.services.backtest_engine import run_backtest
from src.services.strategy_registry import extract_description, is_engine_ready, normalize_strategy_params
from src.services.strategy_service import validate_strategy_params


log = logging.getLogger(__name__)
MAX_EXPERIMENT_TRIALS = min(50, max(1, int(os.getenv("RESEARCH_MAX_TRIALS", "50"))))
TERMINAL_STATUSES = {"completed", "partially_failed", "failed", "cancelled", "data_changed"}
_ALLOWED_GRID_PREFIXES = ("signal.", "risk.")


class ExperimentConflictError(RuntimeError):
    pass


class ExperimentNotFoundError(LookupError):
    pass


class ExperimentDataChangedError(RuntimeError):
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
    if spec.basket_id is not None:
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


def calculate_data_fingerprint(
    db: Session,
    *,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    lookback_start = start_date - timedelta(days=400)
    statement = text(
        """
        SELECT
            i.ticker_canonical AS symbol,
            df.*,
            bars.ts_utc AS bar_ts_utc,
            bars.open_u,
            bars.high_u,
            bars.low_u,
            bars.close_u,
            bars.open_fa,
            bars.high_fa,
            bars.low_fa,
            bars.close_fa,
            bars.volume AS bar_volume,
            bars.asof AS bar_asof
        FROM daily_features df
        JOIN instruments i ON i.id = df.instrument_id
        JOIN eod_bars bars
          ON bars.instrument_id = df.instrument_id
         AND bars.dt_ny = df.dt_ny
        WHERE i.ticker_canonical IN :symbols
          AND df.dt_ny BETWEEN :start_date AND :end_date
        ORDER BY i.ticker_canonical, df.dt_ny
        """
    ).bindparams(bindparam("symbols", expanding=True))
    rows = db.execute(
        statement,
        {"symbols": symbols, "start_date": lookback_start, "end_date": end_date},
    ).mappings()
    digest = hashlib.sha256()
    row_count = 0
    max_asof: datetime | None = None
    min_date: date | None = None
    max_date: date | None = None
    symbol_counts: dict[str, int] = {}
    for row in rows:
        payload = {key: row[key] for key in sorted(row.keys())}
        digest.update(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
        row_count += 1
        symbol = str(row.get("symbol") or "")
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        trade_date = row.get("dt_ny")
        if isinstance(trade_date, date):
            min_date = trade_date if min_date is None else min(min_date, trade_date)
            max_date = trade_date if max_date is None else max(max_date, trade_date)
        for asof in (row.get("asof"), row.get("bar_asof")):
            if isinstance(asof, datetime) and (max_asof is None or asof > max_asof):
                max_asof = asof
    if row_count == 0:
        raise ExperimentDataIncompleteError(
            "no daily feature data found for the experiment universe and window"
        )
    action_statement = text(
        """
        SELECT
            i.ticker_canonical AS symbol,
            ca.action_type,
            ca.ex_date,
            ca.split_from,
            ca.split_to,
            ca.cash_amount,
            ca.currency,
            ca.updated_at
        FROM corporate_actions ca
        JOIN instruments i ON i.id = ca.instrument_id
        WHERE i.ticker_canonical IN :symbols
          AND ca.ex_date BETWEEN :start_date AND :end_date
        ORDER BY i.ticker_canonical, ca.ex_date, ca.id
        """
    ).bindparams(bindparam("symbols", expanding=True))
    action_count = 0
    for row in db.execute(
        action_statement,
        {"symbols": symbols, "start_date": start_date, "end_date": end_date},
    ).mappings():
        payload = {key: row[key] for key in sorted(row.keys())}
        digest.update(b"corporate-action:")
        digest.update(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
        action_count += 1
        updated_at = row.get("updated_at")
        if isinstance(updated_at, datetime) and (max_asof is None or updated_at > max_asof):
            max_asof = updated_at
    return {
        "sha256": digest.hexdigest(),
        "rowCount": row_count,
        "corporateActionCount": action_count,
        "symbolCounts": {symbol: symbol_counts[symbol] for symbol in sorted(symbol_counts)},
        "startDate": lookback_start.isoformat(),
        "endDate": end_date.isoformat(),
        "minDate": min_date.isoformat() if min_date else None,
        "maxDate": max_date.isoformat() if max_date else None,
        "maxAsof": max_asof.isoformat() if max_asof else None,
    }


def validate_experiment(db: Session, spec: ExperimentSpec) -> dict[str, Any]:
    definitions, symbols, _universe = expand_experiment(db, spec)
    return {
        "valid": True,
        "trialCount": len(definitions),
        "normalizedSpec": spec.model_dump(mode="json", by_alias=True),
        "universeSymbols": symbols,
        "warnings": [],
        "estimatedCost": {
            "backtestRuns": len(definitions),
            "maxConcurrentTrials": max(1, int(os.getenv("RESEARCH_WORKER_CONCURRENCY", "2"))),
            "llmAnalysisCalls": 1,
            "maxDurationSeconds": spec.stop_policy.max_duration_seconds if spec.stop_policy else None,
            "tokenBudget": spec.stop_policy.token_budget if spec.stop_policy else None,
        },
    }


def create_experiment(
    db: Session,
    *,
    workflow_run_id: str,
    spec: ExperimentSpec,
    idempotency_key: str,
) -> ResearchExperiment:
    payload_hash = canonical_hash(
        {"workflowRunId": workflow_run_id, "spec": spec.model_dump(mode="json", by_alias=True)}
    )
    existing = db.execute(
        select(ResearchExperiment).where(ResearchExperiment.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.run_manifest.get("requestHash") != payload_hash:
            raise ExperimentConflictError("idempotency key was already used with a different request")
        return existing

    definitions, symbols, universe = expand_experiment(db, spec)
    overall_start = min(spec.in_sample.start_date, spec.out_of_sample.start_date)
    overall_end = max(spec.in_sample.end_date, spec.out_of_sample.end_date)
    fingerprint = calculate_data_fingerprint(
        db,
        symbols=symbols,
        start_date=overall_start,
        end_date=overall_end,
    )
    strategy = db.get(Strategy, spec.strategy_id)
    assert strategy is not None
    policy_started_at = datetime.now(UTC)
    experiment = ResearchExperiment(
        workflow_run_id=workflow_run_id,
        idempotency_key=idempotency_key,
        status="queued",
        spec=spec.model_dump(mode="json", by_alias=True),
        run_manifest={
            "requestHash": payload_hash,
            "specHash": canonical_hash(spec.model_dump(mode="json", by_alias=True)),
            "strategyId": str(strategy.id),
            "strategyVersion": strategy.version,
            "strategyType": strategy.strategy_type,
            "universe": universe,
            "dataFingerprint": fingerprint,
            "quantBuildVersion": os.getenv("APP_VERSION", "development"),
            "createdAt": policy_started_at.isoformat(),
            "policyStartedAt": policy_started_at.isoformat(),
            "tokenUsage": {
                "inputTokens": 0,
                "cachedInputTokens": 0,
                "outputTokens": 0,
                "reasoningOutputTokens": 0,
                "totalTokens": 0,
            },
        },
        progress={"total": len(definitions), "queued": len(definitions), "running": 0, "completed": 0, "failed": 0, "cancelled": 0},
    )
    db.add(experiment)
    db.flush()
    for definition in definitions:
        db.add(
            ExperimentTrial(
                experiment_id=experiment.id,
                trial_key=definition["trialKey"],
                ordinal=definition["ordinal"],
                status="queued",
                sample_kind=definition["sampleKind"],
                cost_scenario=definition["costScenario"],
                params=definition["params"],
                params_hash=definition["paramsHash"],
                window_start=definition["windowStart"],
                window_end=definition["windowEnd"],
                cost_config=definition["costConfig"],
                data_fingerprint=fingerprint["sha256"],
            )
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        concurrent = db.execute(
            select(ResearchExperiment).where(ResearchExperiment.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if concurrent is not None and concurrent.run_manifest.get("requestHash") == payload_hash:
            return concurrent
        raise
    db.refresh(experiment)
    log.info(
        "Research experiment created workflow_run_id=%s experiment_id=%s trials=%s",
        workflow_run_id,
        experiment.id,
        len(definitions),
    )
    return experiment


def get_experiment(db: Session, experiment_id: UUID | str) -> ResearchExperiment:
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(str(experiment_id))
    return experiment


def cancel_experiment(db: Session, experiment_id: UUID | str) -> ResearchExperiment:
    experiment = get_experiment(db, experiment_id)
    if experiment.status in TERMINAL_STATUSES:
        return experiment
    experiment.status = "cancel_requested"
    db.execute(
        ExperimentTrial.__table__.update()
        .where(ExperimentTrial.experiment_id == experiment.id, ExperimentTrial.status == "queued")
        .values(status="cancelled", finished_at=datetime.now(UTC))
    )
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


def _portfolio_metrics(db: Session, run: StrategyRun) -> dict[str, Any]:
    snapshots = list(
        db.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.run_id == run.id)
            .order_by(PortfolioSnapshot.ts)
        ).scalars()
    )
    returns: list[float] = []
    previous: float | None = None
    max_drawdown_duration = 0
    current_drawdown_duration = 0
    for snapshot in snapshots:
        equity = float(snapshot.equity)
        if previous and previous > 0:
            returns.append((equity / previous) - 1)
        previous = equity
        drawdown = float(snapshot.drawdown or 0)
        if drawdown > 0:
            current_drawdown_duration += 1
            max_drawdown_duration = max(max_drawdown_duration, current_drawdown_duration)
        else:
            current_drawdown_duration = 0
    sharpe = None
    sortino = None
    if len(returns) > 1:
        deviation = statistics.pstdev(returns)
        if deviation > 0:
            sharpe = statistics.mean(returns) / deviation * math.sqrt(252)
        downside = [value for value in returns if value < 0]
        if len(downside) > 1:
            downside_deviation = statistics.pstdev(downside)
            if downside_deviation > 0:
                sortino = statistics.mean(returns) / downside_deviation * math.sqrt(252)
    annualized_return = None
    if snapshots and run.initial_cash and float(run.initial_cash) > 0:
        periods = max(len(snapshots) - 1, 1)
        final_equity = float(snapshots[-1].equity)
        annualized_return = (final_equity / float(run.initial_cash)) ** (252 / periods) - 1
    transactions = list(
        db.execute(
            select(Transaction).where(Transaction.run_id == run.id).order_by(Transaction.ts, Transaction.symbol)
        ).scalars()
    )
    notional_by_symbol: dict[str, float] = {}
    net_cash_flow_by_symbol: dict[str, float] = {}
    total_notional = 0.0
    for transaction in transactions:
        notional = abs(float(transaction.qty) * float(transaction.price))
        total_notional += notional
        notional_by_symbol[transaction.symbol] = notional_by_symbol.get(transaction.symbol, 0.0) + notional
        metadata = transaction.meta if isinstance(transaction.meta, dict) else {}
        net_cash_flow_by_symbol[transaction.symbol] = (
            net_cash_flow_by_symbol.get(transaction.symbol, 0.0)
            + float(metadata.get("net_cash_flow") or 0)
        )
    average_equity = statistics.mean(float(item.equity) for item in snapshots) if snapshots else None
    turnover = total_notional / average_equity if average_equity and average_equity > 0 else None
    symbol_activity = {
        symbol: notional / total_notional
        for symbol, notional in sorted(notional_by_symbol.items())
    } if total_notional > 0 else {}
    ending_positions = snapshots[-1].positions if snapshots and isinstance(snapshots[-1].positions, dict) else {}
    symbols_with_pnl = sorted(set(net_cash_flow_by_symbol) | set(ending_positions))
    pnl_by_symbol = {
        symbol: net_cash_flow_by_symbol.get(symbol, 0.0)
        + float((ending_positions.get(symbol) or {}).get("market_value") or 0)
        for symbol in symbols_with_pnl
    }
    initial_cash = float(run.initial_cash or 0)
    symbol_return_contribution = {
        symbol: pnl / initial_cash
        for symbol, pnl in pnl_by_symbol.items()
        if initial_cash > 0
    }
    total_absolute_pnl = sum(abs(value) for value in pnl_by_symbol.values())
    metrics = dict(run.summary_metrics or {})
    metrics.update(
        {
            "annualized_return": annualized_return,
            "sharpe": sharpe,
            "sortino": sortino,
            "max_drawdown_duration_sessions": max_drawdown_duration,
            "turnover": turnover,
            "symbol_activity_share": symbol_activity,
            "activity_concentration": max(symbol_activity.values()) if symbol_activity else None,
            "symbol_return_contribution": symbol_return_contribution,
            "pnl_concentration": (
                max(abs(value) for value in pnl_by_symbol.values()) / total_absolute_pnl
                if total_absolute_pnl > 0
                else None
            ),
        }
    )
    return metrics


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
                "dataFingerprint": trial.data_fingerprint,
                "sampleKind": trial.sample_kind,
                "costScenario": trial.cost_scenario,
                "status": trial.status,
            }
            for trial in trials
        ],
        "dataFingerprint": manifest.get("dataFingerprint"),
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _finalize_if_ready(db: Session, experiment: ResearchExperiment) -> None:
    _refresh_progress(db, experiment)
    progress = experiment.progress
    if progress["queued"] or progress["running"]:
        return
    if experiment.status == "data_changed":
        previous_report = dict(experiment.report or {})
        experiment.finished_at = experiment.finished_at or datetime.now(UTC)
        experiment.report = {
            **build_experiment_report(db, experiment),
            **{
                key: value
                for key, value in previous_report.items()
                if key in {"observedDataFingerprint"}
            },
        }
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
    experiment.report = build_experiment_report(db, experiment)


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
            select(ResearchExperiment).where(ResearchExperiment.status.in_({"queued", "running"}))
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
    db.commit()


def _recovery_stop_code(experiment: ResearchExperiment | None) -> str | None:
    if experiment is None:
        return None
    if experiment.status in {"cancel_requested", "data_changed"}:
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
                        {"queued", "running", "cancel_requested", "data_changed"}
                    )
                )
            ).scalars()
        )
        trials = list(
            db.execute(select(ExperimentTrial).where(ExperimentTrial.status == "running")).scalars()
        )
        for trial in trials:
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
        db.execute(
            ResearchExperiment.__table__.update()
            .where(ResearchExperiment.status == "running")
            .values(status="queued")
        )
        db.flush()
        for experiment_id in affected_experiment_ids:
            experiment = db.get(ResearchExperiment, experiment_id)
            if experiment is not None:
                _finalize_if_ready(db, experiment)
        db.commit()
        return len(trials)
    finally:
        db.close()


def _claim_trial(db: Session) -> ExperimentTrial | None:
    enforce_active_experiment_stop_policies(db)
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


def process_next_trial() -> bool:
    db = SessionLocal()
    try:
        trial = _claim_trial(db)
        if trial is None:
            return False
        experiment = db.get(ResearchExperiment, trial.experiment_id)
        assert experiment is not None
        try:
            manifest = experiment.run_manifest or {}
            universe = manifest.get("universe") or {}
            symbols = list(universe.get("symbols") or [])
            spec = experiment.spec or {}
            start_date = min(date.fromisoformat(spec["inSample"]["startDate"]), date.fromisoformat(spec["outOfSample"]["startDate"]))
            end_date = max(date.fromisoformat(spec["inSample"]["endDate"]), date.fromisoformat(spec["outOfSample"]["endDate"]))
            current_fingerprint = calculate_data_fingerprint(
                db,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
            )
            expected = (manifest.get("dataFingerprint") or {}).get("sha256")
            if current_fingerprint["sha256"] != expected:
                experiment.status = "data_changed"
                experiment.error_code = "data_changed"
                experiment.error_message = "Daily feature data changed after the experiment was created."
                experiment.finished_at = datetime.now(UTC)
                trial.status = "failed"
                trial.error_code = "data_changed"
                trial.error_message = experiment.error_message
                trial.finished_at = datetime.now(UTC)
                db.execute(
                    ExperimentTrial.__table__.update()
                    .where(
                        ExperimentTrial.experiment_id == experiment.id,
                        ExperimentTrial.status == "queued",
                    )
                    .values(
                        status="cancelled",
                        error_code="data_changed",
                        error_message=experiment.error_message,
                        finished_at=datetime.now(UTC),
                    )
                )
                db.flush()
                _refresh_progress(db, experiment)
                experiment.report = {
                    "disclaimer": "Research evidence only; this is not a profitability or live-trading safety guarantee.",
                    "status": "data_changed",
                    "counts": dict(experiment.progress or {}),
                    "dataFingerprint": manifest.get("dataFingerprint"),
                    "observedDataFingerprint": current_fingerprint,
                    "generatedAt": datetime.now(UTC).isoformat(),
                }
                db.commit()
                raise ExperimentDataChangedError(experiment.error_message)

            run = _prepare_backtest_run(db, trial, experiment)
            log.info(
                "Research backtest starting workflow_run_id=%s experiment_id=%s trial_id=%s backtest_id=%s",
                experiment.workflow_run_id,
                experiment.id,
                trial.id,
                run.id,
            )
            costs = trial.cost_config or {}
            result = run_backtest(
                db,
                spec["strategyId"],
                trial.window_start,
                trial.window_end,
                initial_cash=float(spec["initialCash"]),
                benchmark_symbol=spec.get("benchmarkSymbol"),
                commission_bps=float(costs.get("commissionBps", 0)),
                commission_min=float(costs.get("commissionMin", 0)),
                slippage_bps=float(costs.get("slippageBps", 0)),
                universe_symbols=symbols,
                universe_metadata=universe,
                existing_run_id=run.id,
                runtime_params_override=trial.params,
            )
            completed_run = db.get(StrategyRun, UUID(result.run_id))
            assert completed_run is not None
            trial = db.get(ExperimentTrial, trial.id)
            assert trial is not None
            db.refresh(experiment)
            if experiment.status == "data_changed":
                trial.status = "cancelled"
                trial.error_code = "data_changed"
                trial.error_message = "Experiment stopped because another worker detected data drift."
            else:
                trial.status = "completed"
                trial.metrics = _portfolio_metrics(db, completed_run)
            trial.finished_at = datetime.now(UTC)
            log.info(
                "Research trial finished workflow_run_id=%s experiment_id=%s trial_id=%s backtest_id=%s status=%s",
                experiment.workflow_run_id,
                experiment.id,
                trial.id,
                completed_run.id,
                trial.status,
            )
        except ExperimentDataChangedError:
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
