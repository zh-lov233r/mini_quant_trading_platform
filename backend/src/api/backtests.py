from __future__ import annotations

import base64
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
import json
import logging
from time import perf_counter
from typing import Any, Literal, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from src.core.db import get_db
from src.models.tables import (
    BacktestJob,
    ExperimentTrial,
    PortfolioSnapshot,
    Signal,
    StockBasket,
    Strategy,
    StrategyRun,
    SupportResistanceMaterialization,
    SupportResistanceRegimeVersion,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    SupportResistanceZoneVersion,
    Transaction,
)
from src.services.backtest_engine import (
    _available_details,
    _downsample_snapshots,
)
from src.services.backtest_equity_service import (
    load_downsampled_chart_points,
)
from src.services.backtest_job_service import (
    delete_terminal_backtest_run,
    enqueue_backtest_job,
    normalize_backtest_progress,
    request_backtest_cancel,
    retry_failed_backtest_job,
)
from src.services.backtest_worker_status_service import load_backtest_worker_status
from src.services.backtest_task_service import TaskSource, TaskStage, list_backtest_tasks
from src.services.data_service import get_historical_data
from src.schemas.research import PointInTimeUniversePolicy
from src.services.stock_basket_service import DEFAULT_COMMON_STOCK_BASKET_NAME

NEW_YORK = ZoneInfo("America/New_York")
US_COMPARISON_SYMBOLS = ("SPY", "QQQ")
A_SHARE_COMPARISON_SYMBOLS = ("000001.SH", "399001.SZ")
A_SHARE_EXCHANGES = {"XSHG", "XSHE", "XBSE"}
A_SHARE_BASKET_NAME = "All A Shares (Tushare)"
log = logging.getLogger(__name__)


class BacktestCreate(BaseModel):
    strategy_id: UUID = Field(..., description="策略 ID")
    basket_id: Optional[UUID] = Field(default=None, description="股票组合 ID，用于覆盖策略 universe")
    universe_policy: Optional[PointInTimeUniversePolicy] = Field(
        default=None,
        description="历史动态入场股票池；与 basket_id 互斥",
    )
    start_date: date = Field(..., description="回测开始日期")
    end_date: date = Field(..., description="回测结束日期")
    initial_cash: float = Field(default=100_000.0, gt=0, description="初始资金")
    benchmark_symbol: Optional[str] = Field(
        default=None,
        description="对标基准；A 股股票池的空值、SPY 或 QQQ 自动使用上证指数",
    )
    commission_bps: Optional[float] = Field(default=None, ge=0)
    commission_min: Optional[float] = Field(default=None, ge=0)
    slippage_bps: Optional[float] = Field(default=None, ge=0)
    persist_level: Literal["summary", "trades", "full"] = "full"


class BacktestProgressOut(BaseModel):
    phase: Literal["queued", "preparing", "running", "finalizing", "completed", "failed", "cancelled"]
    percent: float = Field(ge=0, le=100)
    completed_days: Optional[int] = None
    total_days: Optional[int] = None
    trade_date: Optional[str] = None
    finalizing_stage: Optional[
        Literal["zone_versions", "regime_versions", "run_events", "backtest_details", "committing"]
    ] = None
    completed_items: Optional[int] = Field(default=None, ge=0)
    total_items: Optional[int] = Field(default=None, ge=0)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    updated_at: datetime


class BacktestWorkerStatusOut(BaseModel):
    execution_model: Literal["process"]
    configured_concurrency: int = Field(ge=1, le=2)
    intra_run_execution_model: Literal["thread"]
    configured_intra_run_threads: int = Field(ge=1, le=16)
    effective_intra_run_threads: int = Field(ge=1, le=16)
    available_slots: int = Field(ge=0)
    automation_available: bool
    manager_state: Literal["idle", "starting", "running", "backoff", "standby", "stopping", "unavailable"]
    live_managers: int
    worker_active: bool
    active_jobs: int
    queued_jobs: int
    oldest_queued_at: Optional[datetime] = None
    next_worker_start_at: Optional[datetime] = None
    last_worker_exit_code: Optional[int] = None
    heartbeat_stale_after_seconds: int
    checked_at: datetime


class BacktestTaskOut(BaseModel):
    task_key: str
    source: TaskSource
    stage: TaskStage
    job_id: Optional[UUID] = None
    run_id: Optional[UUID] = None
    trial_id: Optional[UUID] = None
    experiment_id: Optional[UUID] = None
    candidate_id: Optional[UUID] = None
    strategy_id: Optional[UUID] = None
    strategy_name: Optional[str] = None
    experiment_name: Optional[str] = None
    trial_ordinal: Optional[int] = None
    sample_kind: Optional[str] = None
    cost_scenario: Optional[str] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    progress: Optional[BacktestProgressOut] = None
    attempt: int = 0
    max_attempts: Optional[int] = None
    requested_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    cancellable: bool = False
    retryable: bool = False
    deletable: bool = False


class BacktestTaskPageOut(BaseModel):
    items: list[BacktestTaskOut]
    total: int
    counts: dict[str, int]


class BacktestRunOut(BaseModel):
    id: UUID
    strategy_id: UUID
    strategy_name: Optional[str] = None
    basket_id: Optional[str] = None
    basket_name: Optional[str] = None
    strategy_version: int
    mode: str
    status: str
    requested_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    runtime_ms: Optional[int] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    initial_cash: Optional[float] = None
    final_equity: Optional[float] = None
    benchmark_symbol: Optional[str] = None
    summary_metrics: dict[str, Any]
    persist_level: Literal["summary", "trades", "full"] = "full"
    available_details: list[str] = Field(default_factory=list)
    progress: Optional[BacktestProgressOut] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BacktestDeleteOut(BaseModel):
    run_id: UUID
    deleted: bool = True


class BacktestDetailOut(BacktestRunOut):
    latest_snapshot: Optional[dict[str, Any]] = None
    transaction_count: int
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    comparison_curves: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    transactions: list[dict[str, Any]] = Field(default_factory=list)


class BacktestSummaryOut(BacktestRunOut):
    latest_snapshot: Optional[dict[str, Any]] = None
    transaction_count: int = 0


class BacktestComparisonCurvesOut(BaseModel):
    run_id: UUID
    comparison_curves: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class BacktestPageOut(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    next_cursor: Optional[str] = None


class SupportResistanceBacktestOut(BaseModel):
    run_id: UUID
    materialization: Optional[dict[str, Any]] = None
    zone_versions: list[dict[str, Any]] = Field(default_factory=list)
    regime_intervals: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


def _serialize_snapshot(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    metrics = snapshot.metrics or {}
    return {
        "ts": snapshot.ts.isoformat() if snapshot.ts else None,
        "cash": float(snapshot.cash),
        "equity": float(snapshot.equity),
        "gross_exposure": float(snapshot.gross_exposure or 0),
        "net_exposure": float(snapshot.net_exposure or 0),
        "drawdown": float(snapshot.drawdown) if snapshot.drawdown is not None else None,
        "positions": snapshot.positions or {},
        "metrics": metrics,
        "benchmark_symbol": metrics.get("benchmark_symbol"),
        "benchmark_close": (
            float(metrics["benchmark_close"])
            if metrics.get("benchmark_close") is not None
            else None
        ),
        "benchmark_equity": (
            float(metrics["benchmark_equity"])
            if metrics.get("benchmark_equity") is not None
            else None
        ),
        "benchmark_return": (
            float(metrics["benchmark_return"])
            if metrics.get("benchmark_return") is not None
            else None
        ),
        "benchmark_excess_return": (
            float(metrics["benchmark_excess_return"])
            if metrics.get("benchmark_excess_return") is not None
            else None
        ),
    }


def _normalize_symbols(symbols: list[str | None]) -> list[str]:
    normalized_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol or normalized_symbol in seen:
            continue
        seen.add(normalized_symbol)
        normalized_symbols.append(normalized_symbol)
    return normalized_symbols


def _symbols_are_a_share(symbols: list[Any]) -> bool:
    normalized = _normalize_symbols([str(symbol) for symbol in symbols])
    return bool(normalized) and all(
        symbol.endswith((".SH", ".SZ", ".BJ")) for symbol in normalized
    )


def _is_a_share_run(run: StrategyRun) -> bool:
    config = dict(getattr(run, "config_snapshot", None) or {})
    universe = config.get("universe") or {}
    basket = universe.get("basket") if isinstance(universe, dict) else {}
    submit_payload = config.get("submit_payload") or {}
    basket_name = (
        basket.get("name") if isinstance(basket, dict) else None
    ) or (submit_payload.get("basket_name") if isinstance(submit_payload, dict) else None)
    if basket_name == A_SHARE_BASKET_NAME:
        return True

    resolution = config.get("universe_resolution") or {}
    policy = resolution.get("policy") if isinstance(resolution, dict) else {}
    exchanges = set(policy.get("exchanges") or []) if isinstance(policy, dict) else set()
    if exchanges and exchanges <= A_SHARE_EXCHANGES:
        return True

    instruments = resolution.get("instruments") if isinstance(resolution, dict) else []
    resolved_symbols = [
        item.get("canonical_symbol")
        for item in instruments or []
        if isinstance(item, dict)
    ]
    if _symbols_are_a_share(resolved_symbols):
        return True
    metrics = dict(getattr(run, "summary_metrics", None) or {})
    return _symbols_are_a_share(list(metrics.get("symbols_loaded") or []))


def _comparison_symbols_for_run(run: StrategyRun) -> tuple[str, str]:
    return A_SHARE_COMPARISON_SYMBOLS if _is_a_share_run(run) else US_COMPARISON_SYMBOLS


def _benchmark_symbol_for_run(run: StrategyRun) -> str | None:
    normalized = _normalize_symbols([getattr(run, "benchmark_symbol", None)])
    benchmark_symbol = normalized[0] if normalized else None
    if _is_a_share_run(run) and benchmark_symbol in {None, *US_COMPARISON_SYMBOLS}:
        return A_SHARE_COMPARISON_SYMBOLS[0]
    return benchmark_symbol


def _extract_cached_comparison_curves(summary_metrics: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw_curves = summary_metrics.get("comparison_curves")
    if not isinstance(raw_curves, dict):
        return {}

    cached_curves: dict[str, list[dict[str, Any]]] = {}
    for symbol, points in raw_curves.items():
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol or not isinstance(points, list):
            continue

        normalized_points: list[dict[str, Any]] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            ts = point.get("ts")
            close = point.get("close")
            equity = point.get("equity")
            curve_return = point.get("return")
            if not isinstance(ts, str):
                continue
            normalized_points.append(
                {
                    "ts": ts,
                    "symbol": normalized_symbol,
                    "close": float(close) if close is not None else None,
                    "equity": float(equity) if equity is not None else None,
                    "return": float(curve_return) if curve_return is not None else None,
                }
            )

        if normalized_points:
            cached_curves[normalized_symbol] = normalized_points

    return cached_curves


def _build_comparison_curves_from_bars(
    initial_cash: float | None,
    snapshots: list[PortfolioSnapshot],
    bars_by_symbol: dict[str, Any],
    symbols: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not initial_cash or initial_cash <= 0:
        return {}

    ordered_snapshots = [snapshot for snapshot in snapshots if snapshot.ts is not None]
    if not ordered_snapshots:
        return {}

    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        return {}

    curves: dict[str, list[dict[str, Any]]] = {}
    for symbol in normalized_symbols:
        bars = bars_by_symbol.get(symbol, [])
        close_by_date = {
            bar.trade_date: float(bar.close_px)
            for bar in bars
            if bar.close_px is not None
        }
        if not close_by_date:
            continue

        base_close: float | None = None
        last_close: float | None = None
        points: list[dict[str, Any]] = []
        for snapshot in ordered_snapshots:
            trade_date = snapshot.ts.astimezone(NEW_YORK).date()
            close = close_by_date.get(trade_date, last_close)
            if close is None:
                continue
            last_close = close
            if base_close is None:
                base_close = close
            if base_close <= 0:
                continue

            equity = float(initial_cash) * (close / base_close)
            points.append(
                {
                    "ts": snapshot.ts.isoformat(),
                    "symbol": symbol,
                    "close": close,
                    "equity": equity,
                    "return": (equity / float(initial_cash)) - 1,
                }
            )

        if points:
            curves[symbol] = points

    return curves


def _load_comparison_curves_read_only(
    db: Session,
    run: StrategyRun,
    *,
    max_points: int,
) -> dict[str, list[dict[str, Any]]]:
    """Return display comparison curves without mutating the run cache."""
    display_symbols = _comparison_symbols_for_run(run)
    cached_curves = _extract_cached_comparison_curves(dict(run.summary_metrics or {}))
    missing_symbols = [symbol for symbol in display_symbols if symbol not in cached_curves]
    computed_curves: dict[str, list[dict[str, Any]]] = {}

    if missing_symbols and run.initial_cash is not None and float(run.initial_cash) > 0:
        snapshots = db.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.run_id == run.id)
            .order_by(PortfolioSnapshot.ts.asc())
        ).scalars().all()
        ordered_snapshots = [snapshot for snapshot in snapshots if snapshot.ts is not None]
        if ordered_snapshots:
            start_date = ordered_snapshots[0].ts.astimezone(NEW_YORK).date()
            end_date = ordered_snapshots[-1].ts.astimezone(NEW_YORK).date()
            bars_by_symbol = get_historical_data(
                db,
                missing_symbols,
                start_date,
                end_date,
                adjusted=True,
            )
            computed_curves = _build_comparison_curves_from_bars(
                float(run.initial_cash),
                ordered_snapshots,
                bars_by_symbol,
                missing_symbols,
            )

    all_curves = {**cached_curves, **computed_curves}
    result: dict[str, list[dict[str, Any]]] = {}
    for symbol in display_symbols:
        valid_points = [
            point
            for point in all_curves.get(symbol, [])
            if isinstance(point.get("equity"), (int, float))
        ]
        if valid_points:
            result[symbol] = _downsample_snapshots(valid_points, max_points=max_points)
    return result


def _build_benchmark_snapshot_overrides(
    benchmark_symbol: str | None,
    initial_cash: float | None,
    snapshots: list[PortfolioSnapshot],
    curve_points: list[dict[str, Any]] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not benchmark_symbol or not initial_cash or initial_cash <= 0 or not snapshots or not curve_points:
        return {}, {}

    normalized_symbol = str(benchmark_symbol).strip().upper()
    if not normalized_symbol:
        return {}, {}

    ordered_snapshots = [snapshot for snapshot in snapshots if snapshot.ts is not None]
    if not ordered_snapshots:
        return {}, {}

    point_by_ts = {
        str(point["ts"]): point
        for point in curve_points
        if isinstance(point, dict) and isinstance(point.get("ts"), str)
    }
    if not point_by_ts:
        return {}, {}

    overrides: dict[str, dict[str, Any]] = {}
    benchmark_base_close = next(
        (
            float(point["close"])
            for point in curve_points
            if point.get("close") is not None
        ),
        None,
    )
    benchmark_last_close: float | None = None
    benchmark_last_equity: float | None = None
    benchmark_last_return: float | None = None
    benchmark_points = 0

    for snapshot in ordered_snapshots:
        snapshot_ts = snapshot.ts.isoformat()
        point = point_by_ts.get(snapshot_ts)
        if point is None:
            continue

        benchmark_close = float(point["close"]) if point.get("close") is not None else None
        benchmark_equity = float(point["equity"]) if point.get("equity") is not None else None
        benchmark_return = float(point["return"]) if point.get("return") is not None else None
        if benchmark_close is None or benchmark_equity is None or benchmark_return is None:
            continue

        strategy_return = (float(snapshot.equity) / float(initial_cash)) - 1
        overrides[snapshot_ts] = {
            "benchmark_symbol": normalized_symbol,
            "benchmark_close": benchmark_close,
            "benchmark_equity": benchmark_equity,
            "benchmark_return": benchmark_return,
            "benchmark_excess_return": strategy_return - benchmark_return,
        }
        benchmark_last_close = benchmark_close
        benchmark_last_equity = benchmark_equity
        benchmark_last_return = benchmark_return
        benchmark_points += 1

    summary = {
        "benchmark_symbol": normalized_symbol,
        "benchmark_points": benchmark_points,
        "benchmark_initial_close": benchmark_base_close,
        "benchmark_final_close": benchmark_last_close,
        "benchmark_final_equity": benchmark_last_equity,
        "benchmark_total_return": benchmark_last_return,
    }
    return overrides, summary


def _resolve_comparison_curves_and_summary(
    db: Session,
    run: StrategyRun,
    equity_curve: list[PortfolioSnapshot],
    summary_metrics: dict[str, Any],
    has_stored_benchmark: bool,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, dict[str, Any]], bool]:
    initial_cash = float(run.initial_cash) if run.initial_cash is not None else None
    updated_summary_metrics = dict(summary_metrics)
    benchmark_overrides: dict[str, dict[str, Any]] = {}
    cache_updated = False

    cached_comparison_curves = _extract_cached_comparison_curves(updated_summary_metrics)
    benchmark_symbol = _benchmark_symbol_for_run(run)
    benchmark_symbol_normalized = _normalize_symbols([benchmark_symbol])[0] if benchmark_symbol else None
    display_symbols = list(_comparison_symbols_for_run(run))
    required_symbols = _normalize_symbols([*display_symbols, benchmark_symbol_normalized])
    missing_symbols = [symbol for symbol in required_symbols if symbol not in cached_comparison_curves]

    computed_comparison_curves: dict[str, list[dict[str, Any]]] = {}
    ordered_snapshots = [snapshot for snapshot in equity_curve if snapshot.ts is not None]
    if missing_symbols and initial_cash and ordered_snapshots:
        start_date = ordered_snapshots[0].ts.astimezone(NEW_YORK).date()
        end_date = ordered_snapshots[-1].ts.astimezone(NEW_YORK).date()
        bars_by_symbol = get_historical_data(
            db,
            missing_symbols,
            start_date,
            end_date,
            adjusted=True,
        )
        computed_comparison_curves = _build_comparison_curves_from_bars(
            initial_cash,
            equity_curve,
            bars_by_symbol,
            missing_symbols,
        )
        if computed_comparison_curves:
            updated_summary_metrics["comparison_curves"] = {
                **cached_comparison_curves,
                **computed_comparison_curves,
            }
            cache_updated = True

    all_comparison_curves = {
        **cached_comparison_curves,
        **computed_comparison_curves,
    }
    comparison_curves = {
        symbol: all_comparison_curves[symbol]
        for symbol in display_symbols
        if symbol in all_comparison_curves
    }

    if benchmark_symbol and not has_stored_benchmark:
        benchmark_overrides, benchmark_summary = _build_benchmark_snapshot_overrides(
            benchmark_symbol,
            initial_cash,
            equity_curve,
            all_comparison_curves.get(benchmark_symbol_normalized),
        )
        for key, value in benchmark_summary.items():
            if updated_summary_metrics.get(key) != value:
                updated_summary_metrics[key] = value
                cache_updated = True
        if (
            updated_summary_metrics.get("benchmark_total_return") is not None
            and updated_summary_metrics.get("total_return") is not None
            and updated_summary_metrics.get("excess_return") is None
        ):
            updated_summary_metrics["excess_return"] = (
                float(updated_summary_metrics["total_return"])
                - float(updated_summary_metrics["benchmark_total_return"])
            )
            cache_updated = True

    return comparison_curves, updated_summary_metrics, benchmark_overrides, cache_updated


def _merge_benchmark_fields(
    serialized_snapshot: dict[str, Any],
    benchmark_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    if not benchmark_fields:
        return serialized_snapshot

    metrics = dict(serialized_snapshot.get("metrics") or {})
    merged = dict(serialized_snapshot)
    for key, value in benchmark_fields.items():
        merged[key] = value
        metrics[key] = value
    merged["metrics"] = metrics
    return merged


def _serialize_transaction(txn: Transaction) -> dict[str, Any]:
    return {
        "id": str(txn.id),
        "run_id": str(txn.run_id) if txn.run_id is not None else None,
        "strategy_id": str(txn.strategy_id),
        "instrument_id": int(txn.instrument_id) if txn.instrument_id is not None else None,
        "ts": txn.ts.isoformat() if txn.ts else None,
        "symbol": txn.symbol,
        "side": txn.side,
        "qty": float(txn.qty),
        "price": float(txn.price),
        "fee": float(txn.fee or 0),
        "order_id": txn.order_id,
        "meta": txn.meta or {},
    }


def _serialize_signal(signal: Signal) -> dict[str, Any]:
    features = signal.features or {}
    strength = features.get("strength") if isinstance(features, dict) else None
    return {
        "id": str(signal.id),
        "run_id": str(signal.run_id),
        "strategy_id": str(signal.strategy_id),
        "instrument_id": int(signal.instrument_id) if signal.instrument_id is not None else None,
        "ts": signal.ts.isoformat() if signal.ts else None,
        "symbol": signal.symbol,
        "signal": signal.signal,
        "score": float(signal.score) if signal.score is not None else None,
        "strength": strength if isinstance(strength, dict) else None,
        "reason": signal.reason,
        "features": features,
    }


def _compact_summary_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: value
        for key, value in (metrics or {}).items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _incremental_summary_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Keep exact metrics and telemetry while excluding large response arrays."""
    return {
        key: value
        for key, value in (metrics or {}).items()
        if key not in {"comparison_curves", "symbols_loaded"}
    }


def _encode_cursor(*values: Any) -> str:
    payload = json.dumps([str(value) for value in values], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, expected_items: int) -> list[str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        values = json.loads(decoded.decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid pagination cursor") from exc
    if not isinstance(values, list) or len(values) != expected_items or not all(
        isinstance(value, str) for value in values
    ):
        raise HTTPException(status_code=422, detail="invalid pagination cursor")
    return values


def _run_persist_level(run: StrategyRun) -> Literal["summary", "trades", "full"]:
    metrics_level = (run.summary_metrics or {}).get("persist_level")
    run_options = (run.config_snapshot or {}).get("run_options") or {}
    value = str(metrics_level or run_options.get("persist_level") or "full")
    return value if value in {"summary", "trades", "full"} else "full"  # type: ignore[return-value]


router = APIRouter(prefix="/api/backtests", tags=["backtests"])


def _to_backtest_run_out(
    run: StrategyRun,
    strategy_name: str | None = None,
    *,
    compact_metrics: bool = False,
) -> BacktestRunOut:
    universe = (run.config_snapshot or {}).get("universe") or {}
    basket = universe.get("basket") if isinstance(universe, dict) else {}
    selection_mode = universe.get("selection_mode") if isinstance(universe, dict) else None
    default_label = universe.get("default_label") if isinstance(universe, dict) else None
    persist_level = _run_persist_level(run)
    metrics = run.summary_metrics or {}
    job = getattr(run, "backtest_job", None)
    return BacktestRunOut(
        id=run.id,
        strategy_id=run.strategy_id,
        strategy_name=strategy_name or getattr(run.strategy, "name", None),
        basket_id=str(basket.get("id")) if isinstance(basket, dict) and basket.get("id") else None,
        basket_name=(
            str(basket.get("name"))
            if isinstance(basket, dict) and basket.get("name")
            else (
                str(default_label)
                if selection_mode == "all_common_stock" and default_label
                else (DEFAULT_COMMON_STOCK_BASKET_NAME if selection_mode == "all_common_stock" else None)
            )
        ),
        strategy_version=run.strategy_version,
        mode=run.mode,
        status=run.status,
        requested_at=run.requested_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        runtime_ms=_compute_run_runtime_ms(run.started_at, run.finished_at),
        window_start=run.window_start,
        window_end=run.window_end,
        initial_cash=float(run.initial_cash) if run.initial_cash is not None else None,
        final_equity=float(run.final_equity) if run.final_equity is not None else None,
        benchmark_symbol=_benchmark_symbol_for_run(run),
        summary_metrics=_compact_summary_metrics(metrics) if compact_metrics else metrics,
        persist_level=persist_level,
        available_details=list(metrics.get("available_details") or _available_details(persist_level)),
        progress=(
            normalize_backtest_progress(
                dict(job.progress or {}),
                status=job.status,
                attempt=job.attempt,
                max_attempts=job.max_attempts,
                updated_at=job.updated_at or run.updated_at,
            )
            if job is not None
            else None
        ),
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _compute_run_runtime_ms(
    started_at: datetime | None,
    finished_at: datetime | None,
) -> int | None:
    if started_at is None or finished_at is None:
        return None
    elapsed_ms = int((finished_at - started_at).total_seconds() * 1000)
    return max(elapsed_ms, 0)


def _to_backtest_detail_out(
    db: Session,
    run: StrategyRun,
    strategy_name: str | None,
    latest_snapshot: PortfolioSnapshot | None,
    transaction_count: int,
    equity_curve: list[PortfolioSnapshot],
    signals: list[Signal],
    transactions: list[Transaction],
) -> BacktestDetailOut:
    base = _to_backtest_run_out(run, strategy_name)
    dump = base.model_dump() if hasattr(base, "model_dump") else base.dict()
    summary_metrics = dict(dump.get("summary_metrics") or {})
    has_stored_benchmark = any(
        (snapshot.metrics or {}).get("benchmark_equity") is not None
        for snapshot in equity_curve
    )
    comparison_curves, summary_metrics, benchmark_overrides, cache_updated = _resolve_comparison_curves_and_summary(
        db,
        run,
        equity_curve,
        summary_metrics,
        has_stored_benchmark,
    )
    if cache_updated:
        run.summary_metrics = summary_metrics
        db.add(run)
        db.commit()

    serialized_equity_curve = [
        _merge_benchmark_fields(
            _serialize_snapshot(snapshot),
            benchmark_overrides.get(snapshot.ts.isoformat()) if snapshot.ts else None,
        )
        for snapshot in equity_curve
    ]
    serialized_latest_snapshot = (
        _merge_benchmark_fields(
            _serialize_snapshot(latest_snapshot),
            benchmark_overrides.get(latest_snapshot.ts.isoformat()) if latest_snapshot and latest_snapshot.ts else None,
        )
        if latest_snapshot is not None
        else None
    )
    return BacktestDetailOut(
        **{**dump, "summary_metrics": summary_metrics},
        latest_snapshot=serialized_latest_snapshot,
        transaction_count=transaction_count,
        equity_curve=serialized_equity_curve,
        comparison_curves=comparison_curves,
        signals=[_serialize_signal(signal) for signal in signals],
        transactions=[_serialize_transaction(txn) for txn in transactions],
    )


@router.post("", response_model=BacktestRunOut, status_code=status.HTTP_201_CREATED)
def create_backtest(
    payload: BacktestCreate,
    db: Session = Depends(get_db),
):
    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if (
        strategy.strategy_type == "support_resistance"
        and str(((strategy.params or {}).get("metadata") or {}).get("algorithm_version"))
        != "pivot-slope-regime-v3"
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "historical_strategy_read_only",
                "message": "pivot-slope-atr-v2 is retained for audit and cannot start a new backtest",
            },
        )
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    if payload.basket_id is not None and payload.universe_policy is not None:
        raise HTTPException(status_code=422, detail="basket_id and universe_policy are mutually exclusive")
    basket = None
    basket_symbols = None
    basket_metadata = None
    if payload.basket_id is not None:
        basket = db.get(StockBasket, payload.basket_id)
        if basket is None:
            raise HTTPException(status_code=404, detail="stock basket not found")
        basket_symbols = list(basket.symbols or [])
        if not basket_symbols:
            raise HTTPException(status_code=422, detail="stock basket is empty")
        basket_metadata = {
            "id": str(basket.id),
            "name": basket.name,
            "description": basket.description,
            "status": basket.status,
            "symbol_count": len(basket_symbols),
        }

    requested_benchmark = _normalize_symbols([payload.benchmark_symbol])
    benchmark_symbol = requested_benchmark[0] if requested_benchmark else None
    strategy_symbols = list(((strategy.params or {}).get("universe") or {}).get("symbols") or [])
    if _symbols_are_a_share(basket_symbols or strategy_symbols) and benchmark_symbol in {
        None,
        *US_COMPARISON_SYMBOLS,
    }:
        benchmark_symbol = A_SHARE_COMPARISON_SYMBOLS[0]

    run = StrategyRun(
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        mode="backtest",
        status="queued",
        window_start=payload.start_date,
        window_end=payload.end_date,
        initial_cash=payload.initial_cash,
        benchmark_symbol=benchmark_symbol,
        config_snapshot={
            "submit_payload": {
                "basket_id": str(payload.basket_id) if payload.basket_id else None,
                "basket_name": basket.name if basket is not None else None,
                "universe_policy": (
                    payload.universe_policy.model_dump(mode="json", by_alias=True)
                    if payload.universe_policy
                    else None
                ),
                "commission_bps": payload.commission_bps,
                "commission_min": payload.commission_min,
                "slippage_bps": payload.slippage_bps,
            },
            "run_options": {"persist_level": payload.persist_level},
        },
    )
    db.add(run)
    db.flush()
    enqueue_backtest_job(
        db,
        run=run,
        source="manual",
        payload={
            "strategy_id": str(strategy.id),
            "start_date": payload.start_date.isoformat(),
            "end_date": payload.end_date.isoformat(),
            "initial_cash": payload.initial_cash,
            "benchmark_symbol": benchmark_symbol,
            "commission_bps": payload.commission_bps,
            "commission_min": payload.commission_min,
            "slippage_bps": payload.slippage_bps,
            "universe_symbols": basket_symbols,
            "universe_metadata": basket_metadata,
            "universe_policy": (
                payload.universe_policy.model_dump(mode="json", by_alias=True)
                if payload.universe_policy
                else None
            ),
            "persist_level": payload.persist_level,
        },
    )
    db.commit()
    db.refresh(run)

    return _to_backtest_run_out(run, strategy.name)


@router.get("", response_model=list[BacktestRunOut])
def list_backtests(
    db: Session = Depends(get_db),
    strategy_id: Optional[UUID] = Query(default=None),
    mode: Optional[str] = Query(default="backtest"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(StrategyRun, Strategy.name)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .outerjoin(BacktestJob, BacktestJob.run_id == StrategyRun.id)
        .options(selectinload(StrategyRun.backtest_job))
        .where(
            or_(BacktestJob.source == "manual", BacktestJob.id.is_(None)),
            ~select(ExperimentTrial.id)
            .where(ExperimentTrial.backtest_run_id == StrategyRun.id)
            .exists(),
        )
    )

    if strategy_id:
        stmt = stmt.where(StrategyRun.strategy_id == strategy_id)
    if mode:
        stmt = stmt.where(StrategyRun.mode == mode)
    if status_filter:
        stmt = stmt.where(StrategyRun.status == status_filter)

    stmt = stmt.order_by(StrategyRun.requested_at.desc(), StrategyRun.created_at.desc())
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    return [
        _to_backtest_run_out(run, strategy_name, compact_metrics=True)
        for run, strategy_name in rows
    ]


@router.get("/worker-status", response_model=BacktestWorkerStatusOut)
def get_backtest_worker_status(db: Session = Depends(get_db)):
    return BacktestWorkerStatusOut(**load_backtest_worker_status(db))


@router.get("/tasks", response_model=BacktestTaskPageOut)
def get_backtest_tasks(
    db: Session = Depends(get_db),
    source: Optional[TaskSource] = Query(default=None),
    stage: Optional[TaskStage | Literal["active"]] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return BacktestTaskPageOut(
        **list_backtest_tasks(db, source=source, stage=stage, limit=limit, offset=offset)
    )


@router.delete("/{run_id}", response_model=BacktestDeleteOut)
def delete_backtest(run_id: UUID, db: Session = Depends(get_db)):
    try:
        delete_terminal_backtest_run(db, run_id, expected_source="manual")
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BacktestDeleteOut(run_id=run_id)


@router.get("/{run_id}", response_model=BacktestDetailOut)
def get_backtest(run_id: UUID, db: Session = Depends(get_db)):
    serialize_started = perf_counter()
    row = db.execute(
        select(StrategyRun, Strategy.name)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == run_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="backtest not found")

    run, strategy_name = row
    latest_snapshot = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.ts.desc())
        .limit(1)
    ).scalars().first()
    equity_curve = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.ts.asc())
    ).scalars().all()
    signals = db.execute(
        select(Signal)
        .where(Signal.run_id == run_id)
        .order_by(Signal.ts.asc(), Signal.symbol.asc())
    ).scalars().all()
    transactions = db.execute(
        select(Transaction)
        .where(Transaction.run_id == run_id)
        .order_by(Transaction.ts.desc())
    ).scalars().all()
    transaction_count = db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.run_id == run_id)
    ).scalar_one()

    response = _to_backtest_detail_out(
        db=db,
        run=run,
        strategy_name=strategy_name,
        latest_snapshot=latest_snapshot,
        transaction_count=int(transaction_count),
        equity_curve=equity_curve,
        signals=signals,
        transactions=transactions,
    )
    log.info(
        "Serialized legacy backtest detail run_id=%s elapsed_ms=%.3f snapshots=%s signals=%s transactions=%s",
        run_id,
        (perf_counter() - serialize_started) * 1000.0,
        len(equity_curve),
        len(signals),
        len(transactions),
    )
    return response


def _get_run_with_strategy(db: Session, run_id: UUID) -> tuple[StrategyRun, str | None]:
    row = db.execute(
        select(StrategyRun, Strategy.name)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == run_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return row


@router.get("/{run_id}/summary", response_model=BacktestSummaryOut)
def get_backtest_summary(run_id: UUID, db: Session = Depends(get_db)):
    run, strategy_name = _get_run_with_strategy(db, run_id)
    latest_snapshot = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.ts.desc())
        .limit(1)
    ).scalars().first()
    transaction_count = db.execute(
        select(func.count()).select_from(Transaction).where(Transaction.run_id == run_id)
    ).scalar_one()
    base = _to_backtest_run_out(run, strategy_name)
    dump = base.model_dump() if hasattr(base, "model_dump") else base.dict()
    return BacktestSummaryOut(
        **{**dump, "summary_metrics": _incremental_summary_metrics(dump.get("summary_metrics"))},
        latest_snapshot=_serialize_snapshot(latest_snapshot) if latest_snapshot else None,
        transaction_count=int(transaction_count),
    )


@router.get("/{run_id}/equity", response_model=list[dict[str, Any]])
def get_backtest_equity(
    run_id: UUID,
    max_points: int = Query(default=1500, ge=2, le=5000),
    shape: Literal["chart", "snapshot"] = Query(default="snapshot"),
    db: Session = Depends(get_db),
):
    if db.get(StrategyRun, run_id) is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    if shape == "chart":
        return load_downsampled_chart_points(db, run_id, max_points=max_points)
    snapshots = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.ts.asc())
    ).scalars().all()
    rows = [_serialize_snapshot(snapshot) for snapshot in snapshots]
    return _downsample_snapshots(rows, max_points=max_points)


@router.get("/{run_id}/comparison-curves", response_model=BacktestComparisonCurvesOut)
def get_backtest_comparison_curves(
    run_id: UUID,
    max_points: int = Query(default=1500, ge=2, le=5000),
    db: Session = Depends(get_db),
):
    run = db.get(StrategyRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return BacktestComparisonCurvesOut(
        run_id=run.id,
        comparison_curves=_load_comparison_curves_read_only(
            db,
            run,
            max_points=max_points,
        ),
    )


@router.get("/{run_id}/signals", response_model=BacktestPageOut)
def list_backtest_signals(
    run_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    run = db.get(StrategyRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    stmt = select(Signal).where(Signal.run_id == run_id)
    count_stmt = select(func.count()).select_from(Signal).where(Signal.run_id == run_id)
    if symbol:
        normalized_symbol = symbol.strip().upper()
        stmt = stmt.where(Signal.symbol == normalized_symbol)
        count_stmt = count_stmt.where(Signal.symbol == normalized_symbol)
    if cursor:
        ts_raw, symbol_raw, id_raw = _decode_cursor(cursor, 3)
        try:
            cursor_ts = datetime.fromisoformat(ts_raw)
            cursor_id = UUID(id_raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid pagination cursor") from exc
        stmt = stmt.where(
            or_(
                Signal.ts > cursor_ts,
                and_(Signal.ts == cursor_ts, Signal.symbol > symbol_raw),
                and_(Signal.ts == cursor_ts, Signal.symbol == symbol_raw, Signal.id > cursor_id),
            )
        )
    items = db.execute(
        stmt.order_by(Signal.ts.asc(), Signal.symbol.asc(), Signal.id.asc()).limit(limit + 1)
    ).scalars().all()
    page_items = items[:limit]
    next_cursor = None
    if len(items) > limit and page_items:
        last = page_items[-1]
        next_cursor = _encode_cursor(last.ts.isoformat(), last.symbol, last.id)
    return BacktestPageOut(
        items=[_serialize_signal(item) for item in page_items],
        total=int(db.execute(count_stmt).scalar_one()),
        next_cursor=next_cursor,
    )


@router.get("/{run_id}/transactions", response_model=BacktestPageOut)
def list_backtest_transactions(
    run_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    run = db.get(StrategyRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    stmt = select(Transaction).where(Transaction.run_id == run_id)
    count_stmt = select(func.count()).select_from(Transaction).where(Transaction.run_id == run_id)
    if symbol:
        normalized_symbol = symbol.strip().upper()
        stmt = stmt.where(Transaction.symbol == normalized_symbol)
        count_stmt = count_stmt.where(Transaction.symbol == normalized_symbol)
    if cursor:
        ts_raw, id_raw = _decode_cursor(cursor, 2)
        try:
            cursor_ts = datetime.fromisoformat(ts_raw)
            cursor_id = UUID(id_raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid pagination cursor") from exc
        stmt = stmt.where(
            or_(
                Transaction.ts < cursor_ts,
                and_(Transaction.ts == cursor_ts, Transaction.id < cursor_id),
            )
        )
    items = db.execute(
        stmt.order_by(Transaction.ts.desc(), Transaction.id.desc()).limit(limit + 1)
    ).scalars().all()
    page_items = items[:limit]
    next_cursor = None
    if len(items) > limit and page_items:
        last = page_items[-1]
        next_cursor = _encode_cursor(last.ts.isoformat(), last.id)
    return BacktestPageOut(
        items=[_serialize_transaction(item) for item in page_items],
        total=int(db.execute(count_stmt).scalar_one()),
        next_cursor=next_cursor,
    )


@router.post("/{run_id}/cancel", response_model=BacktestRunOut, status_code=status.HTTP_202_ACCEPTED)
def cancel_backtest(run_id: UUID, db: Session = Depends(get_db)):
    run, strategy_name = _get_run_with_strategy(db, run_id)
    try:
        request_backtest_cancel(db, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(run)
    return _to_backtest_run_out(run, strategy_name)


@router.post("/{run_id}/retry", response_model=BacktestRunOut, status_code=status.HTTP_202_ACCEPTED)
def retry_backtest(run_id: UUID, db: Session = Depends(get_db)):
    _run, strategy_name = _get_run_with_strategy(db, run_id)
    try:
        retry_run = retry_failed_backtest_job(db, run_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_backtest_run_out(retry_run, strategy_name)


@router.get("/{run_id}/support-resistance", response_model=SupportResistanceBacktestOut)
def get_backtest_support_resistance(
    run_id: UUID,
    db: Session = Depends(get_db),
    symbol: Optional[str] = Query(default=None),
    zone_key: Optional[str] = Query(default=None),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
):
    if end_date is not None and start_date is not None and end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    run = db.get(StrategyRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest not found")

    linked = db.execute(
        select(SupportResistanceRunMaterialization, SupportResistanceMaterialization)
        .join(
            SupportResistanceMaterialization,
            SupportResistanceMaterialization.id
            == SupportResistanceRunMaterialization.materialization_id,
        )
        .where(SupportResistanceRunMaterialization.run_id == run_id)
    ).first()
    if linked is None:
        return SupportResistanceBacktestOut(run_id=run_id)

    _, materialization = linked
    normalized_symbol = str(symbol).strip().upper() if symbol else None
    zone_stmt = select(SupportResistanceZoneVersion).where(
        SupportResistanceZoneVersion.materialization_id == materialization.id
    )
    event_stmt = select(SupportResistanceRunEvent).where(
        SupportResistanceRunEvent.run_id == run_id
    )
    regime_stmt = select(SupportResistanceRegimeVersion).where(
        SupportResistanceRegimeVersion.materialization_id == materialization.id
    )
    if normalized_symbol:
        zone_stmt = zone_stmt.where(SupportResistanceZoneVersion.symbol == normalized_symbol)
        event_stmt = event_stmt.where(SupportResistanceRunEvent.symbol == normalized_symbol)
        regime_stmt = regime_stmt.where(SupportResistanceRegimeVersion.symbol == normalized_symbol)
    if zone_key:
        zone_stmt = zone_stmt.where(SupportResistanceZoneVersion.zone_key == zone_key)
        event_stmt = event_stmt.where(SupportResistanceRunEvent.zone_key == zone_key)
    if start_date:
        zone_stmt = zone_stmt.where(
            (SupportResistanceZoneVersion.effective_to.is_(None))
            | (SupportResistanceZoneVersion.effective_to >= start_date)
        )
        event_stmt = event_stmt.where(SupportResistanceRunEvent.event_date >= start_date)
    if end_date:
        zone_stmt = zone_stmt.where(SupportResistanceZoneVersion.effective_from <= end_date)
        event_stmt = event_stmt.where(SupportResistanceRunEvent.event_date <= end_date)

    versions = db.execute(
        zone_stmt.order_by(
            SupportResistanceZoneVersion.symbol,
            SupportResistanceZoneVersion.zone_key,
            SupportResistanceZoneVersion.effective_from,
            SupportResistanceZoneVersion.version,
        )
    ).scalars().all()
    events = db.execute(
        event_stmt.order_by(
            SupportResistanceRunEvent.event_date,
            SupportResistanceRunEvent.symbol,
            SupportResistanceRunEvent.created_at,
        )
    ).scalars().all()
    regime_versions = db.execute(
        regime_stmt.order_by(
            SupportResistanceRegimeVersion.symbol,
            SupportResistanceRegimeVersion.effective_from,
            SupportResistanceRegimeVersion.version,
        )
    ).scalars().all()
    geometry_by_version = _build_clipped_zone_geometry(
        db,
        versions,
        start_date=start_date,
        end_date=end_date,
    )
    if normalized_symbol and not regime_versions and normalized_symbol not in (materialization.symbols or []):
        regime_intervals = []
    else:
        try:
            regime_intervals = _build_regime_intervals(
                db,
                materialization,
                regime_versions,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "regime_timeline_integrity_error", "message": str(exc)},
            ) from exc
    regime_intervals = [
        interval
        for interval in regime_intervals
        if (normalized_symbol is None or interval["symbol"] == normalized_symbol)
        and (zone_key is None or zone_key in {interval["lower_zone_key"], interval["upper_zone_key"]})
        and (start_date is None or date.fromisoformat(interval["end_date"]) >= start_date)
        and (end_date is None or date.fromisoformat(interval["start_date"]) <= end_date)
    ]

    return SupportResistanceBacktestOut(
        run_id=run_id,
        materialization={
            "id": str(materialization.id),
            "cache_key": materialization.cache_key,
            "algorithm_version": materialization.algorithm_version,
            "detector_params": materialization.detector_params,
            "symbols": materialization.symbols,
            "coverage_start": materialization.coverage_start.isoformat(),
            "coverage_end": materialization.coverage_end.isoformat(),
            "source_data_fingerprint": materialization.source_data_fingerprint,
            "price_semantics": materialization.price_semantics,
            "status": materialization.status,
            "statistics": materialization.statistics,
            "completed_at": (
                materialization.completed_at.isoformat() if materialization.completed_at else None
            ),
        },
        zone_versions=[
            {
                "id": str(version.id),
                "symbol": version.symbol,
                "zone_key": version.zone_key,
                "version": version.version,
                "effective_from": version.effective_from.isoformat(),
                "effective_to": version.effective_to.isoformat() if version.effective_to else None,
                "role": version.role,
                "status": version.status,
                "center_price": float(version.center_price),
                "lower_price": float(version.lower_price),
                "upper_price": float(version.upper_price),
                "atr_width": float(version.atr_width),
                "anchor_session_index": version.anchor_session_index,
                "slope_per_session": float(version.slope_per_session),
                "fit_residual_atr": float(version.fit_residual_atr),
                "projection_end": version.projection_end.isoformat(),
                "end_center_price": float(version.end_center_price),
                "end_lower_price": float(version.end_lower_price),
                "end_upper_price": float(version.end_upper_price),
                "pivot_count": version.pivot_count,
                "touch_count": version.touch_count,
                "source_metadata": version.source_metadata,
                "geometry": geometry_by_version.get(version.id),
            }
            for version in versions
        ],
        regime_intervals=regime_intervals,
        events=[
            {
                "id": str(event.id),
                "symbol": event.symbol,
                "event_date": event.event_date.isoformat(),
                "event_type": event.event_type,
                "zone_key": event.zone_key,
                "setup": event.setup,
                "selected": event.selected,
                "score": float(event.score) if event.score is not None else None,
                "posterior_sample_count": event.posterior_sample_count,
                "lower_price": float(event.lower_price) if event.lower_price is not None else None,
                "upper_price": float(event.upper_price) if event.upper_price is not None else None,
                "payload": event.payload,
            }
            for event in events
        ],
    )


def _build_regime_intervals(
    db: Session,
    materialization: SupportResistanceMaterialization,
    versions: list[SupportResistanceRegimeVersion],
) -> list[dict[str, Any]]:
    if not versions:
        if materialization.algorithm_version == "pivot-slope-regime-v3":
            raise ValueError("v3 materialization is missing its regime timeline")
        return []
    grouped: dict[tuple[str, int | None], list[SupportResistanceRegimeVersion]] = {}
    for version in versions:
        instrument_id = int(version.instrument_id) if version.instrument_id is not None else None
        grouped.setdefault((version.symbol, instrument_id), []).append(version)

    dates_by_instrument: dict[int, list[date]] = {}
    instrument_ids = sorted({key[1] for key in grouped if key[1] is not None})
    if instrument_ids and db.bind is not None and db.bind.dialect.name == "postgresql":
        rows = db.execute(
            text(
                """
                SELECT bars.instrument_id, bars.dt_ny
                FROM eod_bars bars
                JOIN daily_features features
                  ON features.instrument_id = bars.instrument_id
                 AND features.dt_ny = bars.dt_ny
                WHERE bars.instrument_id = ANY(:instrument_ids)
                  AND bars.dt_ny BETWEEN :coverage_start AND :coverage_end
                ORDER BY bars.instrument_id, bars.dt_ny
                """
            ),
            {
                "instrument_ids": instrument_ids,
                "coverage_start": materialization.coverage_start,
                "coverage_end": materialization.coverage_end,
            },
        ).all()
        for instrument_id, trade_date in rows:
            dates_by_instrument.setdefault(int(instrument_id), []).append(trade_date)

    output: list[dict[str, Any]] = []
    for (symbol, instrument_id), items in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1] if item[0][1] is not None else -1),
    ):
        ordered = sorted(items, key=lambda item: (item.effective_from, item.version, str(item.id)))
        starts = [item.effective_from for item in ordered]
        if len(starts) != len(set(starts)) or any(left >= right for left, right in zip(starts, starts[1:])):
            raise ValueError(f"{symbol}: regime effective dates are not strictly increasing")
        if any(left.regime == right.regime for left, right in zip(ordered, ordered[1:])):
            raise ValueError(f"{symbol}: adjacent regime intervals have the same state")
        session_dates = [
            trade_date
            for trade_date in dates_by_instrument.get(instrument_id or -1, [])
            if trade_date >= starts[0]
        ]
        if not session_dates:
            session_dates = [
                materialization.coverage_start + timedelta(days=offset)
                for offset in range((materialization.coverage_end - materialization.coverage_start).days + 1)
            ]
        if not session_dates:
            continue
        if starts and starts[0] != session_dates[0]:
            raise ValueError(f"{symbol}: regime timeline does not cover the first market session")
        session_set = set(session_dates)
        if any(start not in session_set for start in starts):
            raise ValueError(f"{symbol}: regime transition is not aligned to a market session")
        index_by_date = {trade_date: index for index, trade_date in enumerate(session_dates)}
        covered_sessions = 0
        previous_end_index = -1
        for index, version in enumerate(ordered):
            start_index = index_by_date[version.effective_from]
            next_start_index = (
                index_by_date[ordered[index + 1].effective_from]
                if index + 1 < len(ordered)
                else len(session_dates)
            )
            end_index = next_start_index - 1
            if start_index != previous_end_index + 1 or end_index < start_index:
                raise ValueError(f"{symbol}: regime intervals contain a gap or overlap")
            session_count = end_index - start_index + 1
            covered_sessions += session_count
            previous_end_index = end_index
            output.append(
                {
                    "version_id": str(version.id),
                    "symbol": symbol,
                    "regime": version.regime,
                    "start_date": session_dates[start_index].isoformat(),
                    "end_date": session_dates[end_index].isoformat(),
                    "session_count": session_count,
                    "lower_zone_key": version.lower_zone_key,
                    "upper_zone_key": version.upper_zone_key,
                    "reason_code": version.reason_code,
                    "evidence": version.evidence or {},
                }
            )
        if ordered and covered_sessions != len(session_dates):
            raise ValueError(f"{symbol}: regime timeline does not cover every market session")
    return sorted(output, key=lambda item: (item["symbol"], item["start_date"], item["version_id"]))


def _build_clipped_zone_geometry(
    db: Session,
    versions: list[SupportResistanceZoneVersion],
    *,
    start_date: date | None,
    end_date: date | None,
) -> dict[UUID, dict[str, Any]]:
    """Project each version onto the nearest sessions inside the requested window."""
    usable = [version for version in versions if version.instrument_id is not None]
    if not usable:
        return {}
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return {
            version.id: {
                "start_date": version.effective_from.isoformat(),
                "end_date": version.projection_end.isoformat(),
                "start_center_price": float(version.center_price),
                "start_lower_price": float(version.lower_price),
                "start_upper_price": float(version.upper_price),
                "end_center_price": float(version.end_center_price),
                "end_lower_price": float(version.end_lower_price),
                "end_upper_price": float(version.end_upper_price),
                "slope_per_session": float(version.slope_per_session),
            }
            for version in usable
        }
    earliest = min(version.effective_from for version in usable)
    latest = max(version.projection_end for version in usable)
    rows = db.execute(
        text(
            """
            SELECT instrument_id, dt_ny
            FROM eod_bars
            WHERE instrument_id = ANY(:instrument_ids)
              AND dt_ny BETWEEN :earliest AND :latest
            ORDER BY instrument_id, dt_ny
            """
        ),
        {
            "instrument_ids": sorted({version.instrument_id for version in usable}),
            "earliest": earliest,
            "latest": latest,
        },
    ).all()
    dates_by_instrument: dict[int, list[date]] = {}
    for instrument_id, trade_date in rows:
        dates_by_instrument.setdefault(int(instrument_id), []).append(trade_date)

    output: dict[UUID, dict[str, Any]] = {}
    for version in usable:
        dates = dates_by_instrument.get(int(version.instrument_id), [])
        if not dates:
            continue
        lower_date = max(version.effective_from, start_date or version.effective_from)
        upper_date = min(version.projection_end, end_date or version.projection_end)
        start_index = bisect_left(dates, lower_date)
        end_index = bisect_right(dates, upper_date) - 1
        base_index = bisect_left(dates, version.effective_from)
        if start_index >= len(dates) or end_index < start_index or base_index >= len(dates):
            continue
        start_offset = start_index - base_index
        end_offset = end_index - base_index
        slope = float(version.slope_per_session)
        start_center = float(version.center_price) + slope * start_offset
        end_center = float(version.center_price) + slope * end_offset
        half_width = (float(version.upper_price) - float(version.lower_price)) / 2.0
        output[version.id] = {
            "start_date": dates[start_index].isoformat(),
            "end_date": dates[end_index].isoformat(),
            "start_center_price": start_center,
            "start_lower_price": start_center - half_width,
            "start_upper_price": start_center + half_width,
            "end_center_price": end_center,
            "end_lower_price": end_center - half_width,
            "end_upper_price": end_center + half_width,
            "slope_per_session": slope,
        }
    return output
