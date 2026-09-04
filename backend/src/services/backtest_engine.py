from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import resource
import statistics
import sys
from time import perf_counter
from typing import Any, Callable, Literal
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

import quant_kernel

from src.models.tables import Strategy, StrategyRun
from src.services.backtest_universe_service import (
    normalize_point_in_time_policy,
    resolve_backtest_universe,
    resolve_point_in_time_universe,
)
from src.services.backtest_worker_config import (
    resolve_backtest_intra_run_threads,
    resolve_effective_backtest_intra_run_threads,
)
from src.services.columnar_market_data_loader import (
    MarketDatasetPipeline,
    annual_chunk_manifests,
    close_market_dataset,
)
from src.services.support_resistance_risk_service import load_support_risk_context
from src.services.market_data_maintenance_service import acquire_market_data_read_lock
from src.services.native_result_repository import (
    NativePersistenceCancelledError,
    persist_native_result,
)
from src.services.native_support_state import NativeSupportHistory, NativeSupportState
from src.services.prepared_dataset_service import (
    PREPARED_DATASET_SCHEMA_VERSION,
    PREPARED_INTEGER_INDEX,
    PreparedDatasetCache,
    build_prepared_dataset_manifest,
    prepared_dataset_key,
)
from src.services.stock_basket_service import (
    DEFAULT_COMMON_STOCK_BASKET_NAME,
    load_default_common_stock_symbols,
)
from src.services.strategy_registry import (
    build_runtime_payload,
    build_strategy_catalog,
    extract_description,
    is_engine_ready,
    normalize_strategy_params,
    strategy_data_requirements,
)
from src.services.support_resistance_persistence_service import (
    SupportResistancePersistenceCancelledError,
    find_reusable_materialization,
    hydrate_state_from_materialization,
    persist_support_resistance_run,
)

PERSIST_LEVELS = {"summary", "trades", "full"}
PersistLevel = Literal["summary", "trades", "full"]
MAX_PREVIEW_POINTS = 1_500
UTC = timezone.utc


class BacktestCancelledError(RuntimeError):
    pass


SPLIT_ACTION_RANGE_SQL = """
SELECT
    ca.instrument_id,
    i.ticker_canonical AS symbol,
    ca.ex_date,
    ca.action_type,
    ca.split_from,
    ca.split_to,
    price_coverage.bar_count,
    price_coverage.adjusted_value_count
FROM corporate_actions ca
JOIN instruments i
  ON i.id = ca.instrument_id
JOIN LATERAL (
    SELECT
        COUNT(*) AS bar_count,
        COUNT(bars.open_fa)
            + COUNT(bars.high_fa)
            + COUNT(bars.low_fa)
            + COUNT(bars.close_fa) AS adjusted_value_count
    FROM eod_bars bars
    WHERE bars.instrument_id = ca.instrument_id
      AND bars.dt_ny BETWEEN :start_date AND ca.ex_date
) price_coverage ON TRUE
WHERE ca.ex_date BETWEEN :start_date AND :end_date
  AND ca.action_type IN ('split', 'reverse_split', 'stock_dividend')
  AND ca.split_from IS NOT NULL
  AND ca.split_to IS NOT NULL
  AND i.is_active = TRUE
  AND i.ticker_canonical IN :symbols
ORDER BY ca.ex_date, i.ticker_canonical, ca.id;
"""

SPLIT_ACTION_RANGE_V2_SQL = SPLIT_ACTION_RANGE_SQL.replace(
    "  AND i.is_active = TRUE\n  AND i.ticker_canonical IN :symbols",
    "  AND ca.instrument_id IN :instrument_ids",
).replace(
    "ORDER BY ca.ex_date, i.ticker_canonical, ca.id;",
    "ORDER BY ca.ex_date, ca.instrument_id, ca.id;",
)


@dataclass(slots=True)
class BacktestResult:
    """Compact summary returned to callers after one backtest run finishes."""

    run_id: str
    strategy_id: str
    status: str
    initial_cash: float
    final_equity: float
    total_return: float
    max_drawdown: float
    signal_count: int
    trade_count: int
    total_fees: float
    total_slippage: float


@dataclass(slots=True)
class BacktestCostConfig:
    """Execution-cost assumptions applied to every simulated trade."""

    commission_bps: float
    commission_min: float
    slippage_bps: float


def _normalize_persist_level(value: str | None) -> PersistLevel:
    normalized = str(value or "full").strip().lower()
    if normalized not in PERSIST_LEVELS:
        raise ValueError("persist_level must be one of: summary, trades, full")
    return normalized  # type: ignore[return-value]


def _available_details(persist_level: PersistLevel) -> list[str]:
    details = ["summary", "equity"]
    if persist_level in {"trades", "full"}:
        details.append("transactions")
    if persist_level == "full":
        details.extend(["signals", "positions", "support_resistance_events"])
    return details


def _peak_rss_mb() -> float:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    return peak / (1024.0 * 1024.0) if sys.platform == "darwin" else peak / 1024.0


def _finalize_engine_performance(
    performance: dict[str, Any],
    *,
    engine_total_ms: float,
    setup_wall_ms: float,
    loop_wall_ms: float,
    finalization_wall_ms: float,
    streaming_data: bool,
) -> None:
    data_ms = sum(
        float(performance.get(key) or 0.0)
        for key in ("sql_execute_ms", "sql_fetch_ms", "row_decode_ms", "day_grouping_ms")
    )
    loop_subphases_ms = sum(
        float(performance.get(key) or 0.0)
        for key in (
            "history_state_ms",
            "signal_generation_ms",
            "execution_simulation_ms",
            "detail_build_ms",
        )
    )
    finalization_subphases_ms = sum(
        float(performance.get(key) or 0.0)
        for key in (
            "persist_details_ms",
            "persist_summary_ms",
            "response_serialization_ms",
        )
    )
    performance["setup_ms"] = round(
        max(setup_wall_ms - (0.0 if streaming_data else data_ms), 0.0),
        3,
    )
    performance["portfolio_loop_ms"] = round(
        max(loop_wall_ms - (data_ms if streaming_data else 0.0) - loop_subphases_ms, 0.0),
        3,
    )
    performance["finalization_ms"] = round(
        max(finalization_wall_ms - finalization_subphases_ms, 0.0),
        3,
    )
    performance["engine_total_ms"] = round(engine_total_ms, 3)
    performance["total_ms"] = round(engine_total_ms, 3)
    performance["data_prepare_ms"] = round(data_ms, 3)
    rows_loaded = int(performance.get("rows_loaded") or 0)
    trading_days = int(performance.get("trading_days") or 0)
    signals = int(performance.get("signals_generated") or 0)
    trades = int(performance.get("trades_generated") or 0)
    seconds = engine_total_ms / 1000.0
    performance["rows_per_second"] = round(rows_loaded / seconds, 3) if seconds > 0 else 0.0
    performance["trading_days_per_second"] = round(trading_days / seconds, 3) if seconds > 0 else 0.0
    performance["signals_per_second"] = round(signals / seconds, 3) if seconds > 0 else 0.0
    performance["trades_per_second"] = round(trades / seconds, 3) if seconds > 0 else 0.0
    performance["microseconds_per_input_row"] = (
        round(engine_total_ms * 1000.0 / rows_loaded, 3) if rows_loaded else None
    )

    exclusive_phase_keys = (
        "setup_ms",
        "sql_execute_ms",
        "sql_fetch_ms",
        "row_decode_ms",
        "day_grouping_ms",
        "history_state_ms",
        "signal_generation_ms",
        "execution_simulation_ms",
        "detail_build_ms",
        "portfolio_loop_ms",
        "persist_details_ms",
        "persist_summary_ms",
        "response_serialization_ms",
        "finalization_ms",
    )
    accounted_ms = sum(float(performance.get(key) or 0.0) for key in exclusive_phase_keys)
    performance["unaccounted_ms"] = round(engine_total_ms - accounted_ms, 3)
    performance["phase_percent"] = {
        key: round(float(performance.get(key) or 0.0) / engine_total_ms * 100.0, 3)
        if engine_total_ms > 0
        else 0.0
        for key in exclusive_phase_keys
    }


def _research_metrics_from_simulation(
    *,
    equity_points: list[float],
    drawdown_points: list[float],
    notional_by_symbol: dict[str, float],
    net_cash_flow_by_symbol: dict[str, float],
    ending_positions: dict[str, dict[str, Any]],
    initial_cash: float,
) -> dict[str, Any]:
    returns = [
        (current / previous) - 1.0
        for previous, current in zip(equity_points, equity_points[1:])
        if previous > 0
    ]
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

    current_duration = 0
    max_duration = 0
    for drawdown in drawdown_points:
        if drawdown > 0:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0

    annualized_return = None
    if equity_points and initial_cash > 0:
        periods = max(len(equity_points) - 1, 1)
        annualized_return = (equity_points[-1] / initial_cash) ** (252 / periods) - 1

    total_notional = sum(notional_by_symbol.values())
    average_equity = statistics.mean(equity_points) if equity_points else None
    turnover = total_notional / average_equity if average_equity and average_equity > 0 else None
    activity = (
        {symbol: value / total_notional for symbol, value in sorted(notional_by_symbol.items())}
        if total_notional > 0
        else {}
    )
    symbols_with_pnl = sorted(set(net_cash_flow_by_symbol) | set(ending_positions))
    pnl_by_symbol = {
        symbol: net_cash_flow_by_symbol.get(symbol, 0.0)
        + float((ending_positions.get(symbol) or {}).get("market_value") or 0.0)
        for symbol in symbols_with_pnl
    }
    total_absolute_pnl = sum(abs(value) for value in pnl_by_symbol.values())
    return {
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_duration_sessions": max_duration,
        "turnover": turnover,
        "symbol_activity_share": activity,
        "activity_concentration": max(activity.values()) if activity else None,
        "symbol_return_contribution": {
            symbol: pnl / initial_cash for symbol, pnl in pnl_by_symbol.items() if initial_cash > 0
        },
        "pnl_concentration": (
            max(abs(value) for value in pnl_by_symbol.values()) / total_absolute_pnl
            if total_absolute_pnl > 0
            else None
        ),
    }


def _downsample_snapshots(
    rows: list[dict[str, Any]],
    max_points: int = MAX_PREVIEW_POINTS,
) -> list[dict[str, Any]]:
    """Deterministic min/max bucket preview preserving the first and last rows."""
    if len(rows) <= max_points:
        return rows
    if max_points < 3:
        return [rows[0], rows[-1]][:max_points]
    interior = rows[1:-1]
    bucket_count = max(1, (max_points - 2) // 2)
    bucket_size = max(1, math.ceil(len(interior) / bucket_count))
    selected: list[dict[str, Any]] = [rows[0]]
    for start in range(0, len(interior), bucket_size):
        bucket = interior[start : start + bucket_size]
        if not bucket:
            continue
        low = min(bucket, key=lambda item: (float(item["equity"]), item["ts"]))
        high = max(bucket, key=lambda item: (float(item["equity"]), item["ts"]))
        for item in sorted({id(low): low, id(high): high}.values(), key=lambda value: value["ts"]):
            if len(selected) < max_points - 1:
                selected.append(item)
    selected.append(rows[-1])
    return selected[: max_points - 1] + [rows[-1]] if len(selected) > max_points else selected


def _normalize_symbols(symbols: list[str | None] | None) -> list[str]:
    normalized_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols or []:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol or normalized_symbol in seen:
            continue
        seen.add(normalized_symbol)
        normalized_symbols.append(normalized_symbol)
    return normalized_symbols




def _load_split_adjustments_by_date(
    db: Session,
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    instrument_ids: list[int] | None = None,
) -> dict[date, dict[Any, float]]:
    """Load quantity adjustments only for price series that are entirely unadjusted."""
    normalized_symbols = _normalize_symbols(symbols)
    if instrument_ids is not None:
        normalized_instrument_ids = sorted({int(value) for value in instrument_ids})
        if not normalized_instrument_ids:
            return {}
        stmt = text(SPLIT_ACTION_RANGE_V2_SQL).bindparams(bindparam("instrument_ids", expanding=True))
        filter_params: dict[str, Any] = {"instrument_ids": normalized_instrument_ids}
    else:
        if not normalized_symbols:
            return {}
        stmt = text(SPLIT_ACTION_RANGE_SQL).bindparams(bindparam("symbols", expanding=True))
        filter_params = {"symbols": normalized_symbols}
    rows = db.execute(
        stmt,
        {**filter_params, "start_date": start_date, "end_date": end_date},
    ).mappings().all()

    adjustments_by_date: dict[date, dict[Any, float]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        trade_date = row["ex_date"]
        split_from = float(row["split_from"])
        split_to = float(row["split_to"])
        if split_from <= 0 or split_to <= 0:
            continue

        bar_count = int(row.get("bar_count") or 0)
        adjusted_value_count = int(row.get("adjusted_value_count") or 0)
        if adjusted_value_count == bar_count * 4 and bar_count > 0:
            continue
        if adjusted_value_count:
            raise ValueError(
                f"mixed adjusted and unadjusted OHLC before {symbol} corporate action "
                f"on {trade_date.isoformat()}"
            )

        quantity_factor = split_to / split_from
        day_adjustments = adjustments_by_date.setdefault(trade_date, {})
        position_key: Any = int(row["instrument_id"]) if instrument_ids is not None else symbol
        day_adjustments[position_key] = (
            float(day_adjustments.get(position_key, 1.0)) * quantity_factor
        )

    return adjustments_by_date


def _resolve_backtest_cost_config(
    runtime: dict[str, Any],
    *,
    commission_bps: float | None,
    commission_min: float | None,
    slippage_bps: float | None,
) -> BacktestCostConfig:
    """Resolve cost assumptions from runtime config, with call-site overrides first."""
    execution_cfg = runtime.get("params", {}).get("execution", {}) or {}
    backtest_cfg = execution_cfg.get("backtest", {}) or {}

    resolved_commission_bps = float(
        commission_bps
        if commission_bps is not None
        else backtest_cfg.get("commission_bps", 1.0)
    )
    resolved_commission_min = float(
        commission_min
        if commission_min is not None
        else backtest_cfg.get("commission_min", 1.0)
    )
    resolved_slippage_bps = float(
        slippage_bps
        if slippage_bps is not None
        else backtest_cfg.get("slippage_bps", 5.0)
    )

    if resolved_commission_bps < 0:
        raise ValueError("commission_bps must be non-negative")
    if resolved_commission_min < 0:
        raise ValueError("commission_min must be non-negative")
    if resolved_slippage_bps < 0:
        raise ValueError("slippage_bps must be non-negative")

    return BacktestCostConfig(
        commission_bps=resolved_commission_bps,
        commission_min=resolved_commission_min,
        slippage_bps=resolved_slippage_bps,
    )


def _split_adjustments(
    db: Session,
    *,
    instrument_ids: list[int],
    start_date: date,
    end_date: date,
) -> list[list[Any]]:
    values = _load_split_adjustments_by_date(
        db,
        [],
        start_date,
        end_date,
        instrument_ids=instrument_ids,
    )
    return [
        [trade_date.isoformat(), int(instrument_id), float(factor)]
        for trade_date, items in sorted(values.items())
        for instrument_id, factor in sorted(items.items())
    ]


def _support_hydration(
    db: Session,
    *,
    runtime: dict[str, Any],
    symbols: list[str],
    coverage_start: date,
    coverage_end: date,
) -> tuple[dict[str, Any], Any | None]:
    if runtime["strategy_type"] != "support_resistance":
        return {}, None
    materialization = find_reusable_materialization(
        db,
        runtime=runtime,
        symbols=symbols,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    if materialization is None:
        return {}, None
    state = hydrate_state_from_materialization(db, materialization)
    return {
        symbol: {
            "zone_timeline": list(symbol_state.cached_zone_timeline),
            "regime_timeline": list(symbol_state.cached_regime_timeline),
            "lifecycle_events": [list(item) for item in sorted(symbol_state.cached_lifecycle_events)],
        }
        for symbol, symbol_state in state.symbols.items()
    }, materialization


def _coverage_start(strategy_type: str, start: date, policy: dict[str, Any] | None) -> date:
    # Stateless descriptors consume precomputed features, including exact prev_*.
    # Stateful warmup and PIT history eligibility retain the established window.
    return start - timedelta(days=400 if policy or strategy_data_requirements(strategy_type).history_length else 0)


def _load_prepared_dataset(
    db: Session,
    *,
    runtime: dict[str, Any],
    symbols: list[str],
    resolved_universe: Any,
    start_date: date,
    end_date: date,
    universe_policy: dict[str, Any] | None,
    supplied: dict[str, Any] | None,
    performance: dict[str, Any],
) -> tuple[MarketDatasetPipeline, dict[str, Any], dict[str, Any], Any | None]:
    sql_read_ms = 0.0
    if supplied is None:
        manifest = build_prepared_dataset_manifest(
            strategy_type=runtime["strategy_type"],
            universe=resolved_universe.manifest(),
            instrument_ids=resolved_universe.instrument_ids,
            coverage_date_range=(_coverage_start(runtime["strategy_type"], start_date, universe_policy), end_date),
            requested_date_range=(start_date, end_date),
            universe_policy=universe_policy,
        )
    else:
        manifest = dict(supplied.get("manifest") or {})
        expected_key = str(supplied.get("key") or "")
        if prepared_dataset_key(manifest) != expected_key:
            raise ValueError("prepared dataset key does not match its manifest")
        request_range = list(manifest.get("requested_date_range") or [])
        if len(request_range) != 2:
            raise ValueError("prepared dataset request range is missing")
        if not (
            date.fromisoformat(str(request_range[0])) <= start_date
            and end_date <= date.fromisoformat(str(request_range[1]))
        ):
            raise ValueError("backtest window is outside the prepared dataset range")
    manifest_instrument_ids = sorted(int(value) for value in manifest.get("instrument_ids") or [])
    if not manifest_instrument_ids:
        raise ValueError("prepared dataset instrument set is empty")

    cache = PreparedDatasetCache()
    build_start = date.fromisoformat(str(manifest["date_range"][0]))
    build_end = date.fromisoformat(str(manifest["date_range"][1]))
    corporate_actions: list[list[Any]] = []
    if not all(cache.metadata(chunk) is not None for chunk in annual_chunk_manifests(manifest)):
        actions_started = perf_counter()
        corporate_actions = _split_adjustments(
            db, instrument_ids=manifest_instrument_ids, start_date=build_start, end_date=build_end,
        )
        sql_read_ms += (perf_counter() - actions_started) * 1000.0
    pipeline = MarketDatasetPipeline(
        db, cache, manifest, performance, corporate_actions
    )
    performance.update(
        sql_read_ms=round(
            sql_read_ms
            + float(performance.get("sql_read_ms", 0.0)),
            3,
        ),
        row_conversion_ms=round(
            float(performance.get("row_conversion_ms", 0.0)),
            3,
        ),
        array_write_ms=round(
            float(performance.get("array_write_ms", 0.0)), 3
        ),
    )
    try:
        hydration, materialization = _support_hydration(
            db,
            runtime=runtime,
            symbols=symbols,
            coverage_start=build_start,
            coverage_end=build_end,
        )
    except Exception:
        pipeline.close()
        raise
    return pipeline, manifest, hydration, materialization


def _native_support_state(
    result: Any, dataset: Any, check_cancel: Callable[[], None] | None = None,
) -> NativeSupportState:
    return NativeSupportState(result, dataset, check_cancel)


def _native_research_metrics(result: Any, initial_cash: float) -> dict[str, Any]:
    symbols = list(result.symbols)
    trades = result.trades
    equity = result.equity
    notional_by_symbol: dict[str, float] = {}
    net_cash_flow_by_symbol: dict[str, float] = {}
    for symbol_id, notional, cash_flow in zip(
        trades["symbol_id"],
        trades["gross_notional"],
        trades["net_cash_flow"],
        strict=True,
    ):
        symbol = symbols[int(symbol_id)]
        notional_by_symbol[symbol] = notional_by_symbol.get(symbol, 0.0) + abs(
            float(notional)
        )
        net_cash_flow_by_symbol[symbol] = net_cash_flow_by_symbol.get(symbol, 0.0) + float(
            cash_flow
        )
    positions_json = list(equity["positions_json"])
    ending_positions = json.loads(positions_json[-1]) if positions_json else {}
    return _research_metrics_from_simulation(
        equity_points=[float(value) for value in equity["equity"]],
        drawdown_points=[float(value) for value in equity["drawdown"]],
        notional_by_symbol=notional_by_symbol,
        net_cash_flow_by_symbol=net_cash_flow_by_symbol,
        ending_positions=ending_positions,
        initial_cash=initial_cash,
    )


def _native_universe_membership(result: Any, policy: dict[str, Any] | None) -> dict[str, Any] | None:
    values = result.universe_membership
    if policy is None or values is None:
        return None
    annual: dict[int, dict[str, Any]] = {}
    exclusion_columns = {
        "asset_type": "excluded_asset_type",
        "exchange": "excluded_exchange",
        "before_listing": "excluded_before_listing",
        "after_delisting": "excluded_after_delisting",
        "price": "excluded_price",
        "liquidity": "excluded_liquidity",
        "history": "excluded_history",
    }
    for index, ordinal in enumerate(values["date_ordinal"]):
        year = date.fromordinal(int(ordinal)).year
        eligible = int(values["eligible_count"][index])
        item = annual.setdefault(
            year,
            {
                "sessions": 0,
                "eligible_sum": 0,
                "eligible_min": None,
                "eligible_max": 0,
                "exclusions": {},
            },
        )
        item["sessions"] += 1
        item["eligible_sum"] += eligible
        item["eligible_min"] = (
            eligible
            if item["eligible_min"] is None
            else min(int(item["eligible_min"]), eligible)
        )
        item["eligible_max"] = max(int(item["eligible_max"]), eligible)
        for reason, column in exclusion_columns.items():
            count = int(values[column][index])
            if count:
                item["exclusions"][reason] = item["exclusions"].get(reason, 0) + count
    return {
        "policy": policy,
        "annual": {
            str(year): {
                **item,
                "eligible_average": item["eligible_sum"] / item["sessions"],
            }
            for year, item in sorted(annual.items())
        },
    }


def run_backtest(
    db: Session,
    strategy_id: UUID | str,
    start_date: date,
    end_date: date,
    *,
    initial_cash: float = 100_000.0,
    benchmark_symbol: str | None = None,
    commission_bps: float | None = None,
    commission_min: float | None = None,
    slippage_bps: float | None = None,
    universe_symbols: list[str] | None = None,
    universe_metadata: dict[str, Any] | None = None,
    universe_policy: dict[str, Any] | None = None,
    existing_run_id: UUID | str | None = None,
    runtime_params_override: dict[str, Any] | None = None,
    persist_level: PersistLevel | str = "full",
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    prepared_dataset: dict[str, Any] | None = None,
) -> BacktestResult:
    started = perf_counter()
    performance: dict[str, Any] = {}
    configured_intra_run_threads = resolve_backtest_intra_run_threads()
    effective_intra_run_threads = resolve_effective_backtest_intra_run_threads(
        configured_intra_run_threads
    )
    level = _normalize_persist_level(persist_level)
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    runtime = build_runtime_payload(strategy)
    if runtime_params_override is not None:
        normalized = normalize_strategy_params(
            strategy.strategy_type,
            runtime_params_override,
            extract_description(runtime_params_override),
        )
        runtime["params"] = normalized
        runtime["engine_ready"] = is_engine_ready(strategy.strategy_type, normalized)
    if not runtime["engine_ready"]:
        raise ValueError("strategy is not engine-ready")

    universe_started = perf_counter()
    policy = normalize_point_in_time_policy(universe_policy) if universe_policy else None
    if universe_symbols is not None and policy is not None:
        raise ValueError("provide universe_symbols or universe_policy, not both")
    if policy is not None:
        runtime["params"]["universe"] = {
            **runtime["params"]["universe"],
            "symbols": [],
            "selection_mode": "point_in_time_liquid",
            "policy": policy,
        }
        resolved = resolve_point_in_time_universe(
            db, policy, start_date=start_date - timedelta(days=400), end_date=end_date
        )
        symbols = [
            item.canonical_symbol or f"instrument-{item.instrument_id}"
            for item in resolved.instruments
        ]
    else:
        symbols = _normalize_symbols(
            universe_symbols if universe_symbols is not None else runtime["params"]["universe"].get("symbols")
        )
        if not symbols and runtime["params"]["universe"].get("selection_mode") == "all_common_stock":
            symbols = _normalize_symbols(load_default_common_stock_symbols(db))
            runtime["params"]["universe"]["default_label"] = DEFAULT_COMMON_STOCK_BASKET_NAME
        if not symbols:
            raise ValueError("backtest requires a non-empty universe")
        runtime["params"]["universe"]["symbols"] = symbols
        if universe_metadata:
            runtime["params"]["universe"]["basket"] = universe_metadata
            runtime["params"]["universe"]["selection_mode"] = "stock_basket"
        resolved = resolve_backtest_universe(
            db, symbols, start_date=start_date - timedelta(days=400), end_date=end_date
        )

    performance["universe_resolution_ms"] = round((perf_counter() - universe_started) * 1000.0, 3)
    cost = _resolve_backtest_cost_config(
        runtime,
        commission_bps=commission_bps,
        commission_min=commission_min,
        slippage_bps=slippage_bps,
    )
    descriptor = next(
        item for item in build_strategy_catalog() if item["strategy_type"] == strategy.strategy_type
    )
    config_snapshot = {
        **dict(runtime["params"]),
        "run_options": {
            "persist_level": level,
            "intra_run_threads": effective_intra_run_threads,
            "universe_membership_semantics": resolved.membership_semantics,
            "survivorship_bias_warning": resolved.membership_semantics == "current_active_snapshot",
        },
        "universe_resolution": resolved.manifest(),
    }
    if existing_run_id is not None:
        run = db.get(StrategyRun, existing_run_id)
        if run is None:
            raise ValueError("backtest run not found")
        run.strategy_id = strategy.id
        run.strategy_version = strategy.version
        run.mode = "backtest"
        run.status = "running"
        run.started_at = datetime.now(UTC)
        run.finished_at = None
        run.window_start = start_date
        run.window_end = end_date
        run.initial_cash = initial_cash
        run.final_equity = None
        run.benchmark_symbol = benchmark_symbol
        run.config_snapshot = config_snapshot
        run.summary_metrics = {}
        run.error_message = None
    else:
        run = StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode="backtest",
            status="running",
            started_at=datetime.now(UTC),
            window_start=start_date,
            window_end=end_date,
            initial_cash=initial_cash,
            benchmark_symbol=benchmark_symbol,
            config_snapshot=config_snapshot,
        )
        db.add(run)
    db.commit()
    db.refresh(run)

    try:
        acquire_market_data_read_lock(db, allow_draining=True)
        pipeline, manifest, support_hydration, reusable_materialization = _load_prepared_dataset(
            db,
            runtime=runtime,
            symbols=symbols,
            resolved_universe=resolved,
            start_date=start_date,
            end_date=end_date,
            universe_policy=policy,
            supplied=prepared_dataset,
            performance=performance,
        )
        session_dates: list[date] = []
        support_history = NativeSupportHistory() if runtime["strategy_type"] == "support_resistance" else None
        chunk_compute_ms: list[dict[str, Any]] = []
        performance["chunk_compute_ms"] = chunk_compute_ms
        coverage_start = date.fromisoformat(str(manifest["date_range"][0]))
        coverage_end = date.fromisoformat(str(manifest["date_range"][1]))
        support_context: dict[str, Any] | None = None
        try:
            if runtime["strategy_type"] == "support_resistance":
                context = load_support_risk_context(
                    db, runtime["params"]["risk"], coverage_start, end_date
                )
                support_context = context
                run.config_snapshot = {**run.config_snapshot, "support_risk_context": context}
        except Exception:
            pipeline.close()
            raise

        def control(completed: int, total: int) -> bool:
            cancelled = bool(cancel_check and cancel_check())
            if progress_callback is not None and not cancelled:
                trade_date = session_dates[min(max(completed - 1, 0), len(session_dates) - 1)]
                day_span = max((end_date - start_date).days, 1)
                elapsed_days = max((trade_date - start_date).days, 0)
                progress_callback(
                    {
                        "phase": "running",
                        "trade_date": trade_date.isoformat(),
                        "completed_days": completed,
                        "total_days": max(total, completed),
                        "percent": round(min(elapsed_days / day_span, 1.0) * 85.0, 3),
                    }
                )
            return cancelled

        last_cancel_check = 0.0

        def check_finalization_cancel() -> None:
            nonlocal last_cancel_check
            now = perf_counter()
            if now - last_cancel_check < 0.25:
                return
            last_cancel_check = now
            if cancel_check is not None and cancel_check():
                raise SupportResistancePersistenceCancelledError(
                    "backtest cancellation requested during finalization"
                )

        def report_persistence(stage: str, completed: int, total: int) -> None:
            if progress_callback is not None:
                progress_callback({
                    "phase": "finalizing",
                    "trade_date": end_date.isoformat(),
                    "completed_days": len(session_dates),
                    "total_days": len(session_dates),
                    "percent": round(90.0 + 8.0 * completed / total, 3),
                    "finalizing_stage": stage,
                    "completed_items": completed,
                    "total_items": total,
                })

        def finalize_native(completed: int, total: int) -> bool:
            check_finalization_cancel()
            if progress_callback is not None:
                progress_callback({
                    "phase": "finalizing",
                    "trade_date": end_date.isoformat(),
                    "completed_days": len(session_dates),
                    "total_days": len(session_dates),
                    "percent": round(85.0 + 3.0 * completed / total, 3),
                    "finalizing_stage": "backtest_details",
                    "completed_items": completed,
                    "total_items": total,
                })
            return False

        try:
            native_session = quant_kernel.create_backtest_session(
                runtime,
                {
                    "initial_cash": initial_cash,
                    "commission_bps": cost.commission_bps,
                    "commission_min": cost.commission_min,
                    "slippage_bps": cost.slippage_bps,
                    "thread_count": effective_intra_run_threads,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                control,
                finalize_native,
            )
            for chunk in pipeline:
                dataset = chunk.dataset
                if support_hydration:
                    dataset.sidecar = {
                        **dict(dataset.sidecar),
                        "support_resistance_hydration": support_hydration,
                    }
                if support_context is not None:
                    dataset.sidecar = {
                        **dict(dataset.sidecar),
                        "support_risk_context": support_context,
                    }
                session_dates.extend(sorted({
                    date.fromordinal(int(value))
                    for value in dataset.integers[:, PREPARED_INTEGER_INDEX["dt_ordinal"]]
                    if start_date.toordinal() <= int(value) <= end_date.toordinal()
                }))
                compute_started = perf_counter()
                try:
                    native_session.consume(dataset)
                    if support_history is not None:
                        support_history.consume(dataset)
                finally:
                    compute_finished = perf_counter()
                    pipeline.record_compute(compute_started, compute_finished)
                    close_market_dataset(dataset)
                chunk_compute_ms.append({
                    "date_range": chunk.manifest["date_range"],
                    "compute_ms": round((compute_finished - compute_started) * 1000.0, 3),
                    "rows": len(dataset),
                })
            finish_started = perf_counter()
            native_result = native_session.finish()
            finish_ms = (perf_counter() - finish_started) * 1000.0
            performance["native_finish_ms"] = round(finish_ms, 3)
            performance["native_kernel_ms"] = round(
                sum(float(item["compute_ms"]) for item in chunk_compute_ms) + finish_ms,
                3,
            )
        except quant_kernel.BacktestCancelledError as exc:
            raise BacktestCancelledError(str(exc)) from exc
        finally:
            pipeline.close()
        cache_status = (
            "warm"
            if performance.get("chunk_load_ms")
            and all(bool(item["cache_hit"]) for item in performance["chunk_load_ms"])
            else "cold"
        )
        if cancel_check is not None and cancel_check():
            raise BacktestCancelledError("backtest cancellation requested before persistence")
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing",
                    "trade_date": end_date.isoformat(),
                    "completed_days": len(session_dates),
                    "total_days": len(session_dates),
                    "percent": 88.0,
                    "finalizing_stage": "backtest_details",
                    "completed_items": None,
                    "total_items": None,
                }
            )
        persist_stats = persist_native_result(
            db,
            run_id=run.id,
            strategy_id=strategy.id,
            result=native_result,
            persist_level=level,
            cancel_check=cancel_check,
            performance=performance,
        )
        materialization = reusable_materialization
        if runtime["strategy_type"] == "support_resistance":
            assert support_history is not None
            native_state = _native_support_state(native_result, support_history, check_finalization_cancel)
            materialization = persist_support_resistance_run(
                db,
                run=run,
                runtime=runtime,
                state=native_state,
                symbols=symbols,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                persist_run_events=level == "full",
                cancel_check=cancel_check,
                progress_callback=report_persistence,
                performance=performance,
            )
        summary = dict(native_result.summary)
        native_performance = dict(native_result.performance)
        research_metrics = _native_research_metrics(native_result, initial_cash)
        final_equity = float(summary["final_equity"])
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        run.final_equity = final_equity
        run.summary_metrics = {
            **summary,
            "total_transaction_cost": float(summary["total_fees"]) + float(summary["total_slippage"]),
            "pending_signal_count": 0,
            "execution_lag": "next_session_open",
            "universe_size": len(symbols),
            "symbols_loaded": sorted(symbols),
            "strategy_type": runtime["strategy_type"],
            "cost_model": {
                "commission_bps": cost.commission_bps,
                "commission_min": cost.commission_min,
                "slippage_bps": cost.slippage_bps,
            },
            "benchmark_symbol": str(benchmark_symbol).upper() if benchmark_symbol else None,
            "persist_level": level,
            "available_details": _available_details(level),
            "support_resistance_materialization_id": str(materialization.id) if materialization else None,
            "support_resistance_cache_key": materialization.cache_key if materialization else None,
            "universe_membership": _native_universe_membership(native_result, policy),
            "delisting_zero_write_off": float(summary["delisting_zero_write_off"]),
            "delisting_last_close_sensitivity": 0.0,
            "kernel": {
                "version": quant_kernel.KERNEL_VERSION,
                "abi": quant_kernel.ABI_VERSION,
                "build_id": quant_kernel.BUILD_ID,
                "dataset_schema": PREPARED_DATASET_SCHEMA_VERSION,
                "strategy_revision": descriptor["algorithm_revision"],
            },
            "performance": {
                **performance,
                "configured_intra_run_threads": configured_intra_run_threads,
                "effective_intra_run_threads": effective_intra_run_threads,
                "intra_run_threads": int(native_performance["thread_count"]),
                "parallel_trading_days": int(native_performance["parallel_sessions"]),
                "serial_trading_days": int(native_performance["serial_sessions"]),
                "native_warmup_ms": round(float(native_performance["warmup_ms"]), 3),
                "native_signal_generation_ms": round(
                    float(native_performance["signal_generation_ms"]),
                    3,
                ),
                "prepared_dataset_status": cache_status,
                "prepared_dataset_key": prepared_dataset_key(manifest),
                "rows_loaded": int(performance.get("rows_loaded", 0)),
                "trading_days": int(summary["trading_days"]),
                "signals_generated": int(summary["signal_count"]),
                "trades_generated": int(summary["trade_count"]),
                "detail_rows_inserted": persist_stats.total,
                "peak_rss_mb": round(_peak_rss_mb(), 3),
                "engine_total_ms": round((perf_counter() - started) * 1000.0, 3),
            },
            **research_metrics,
        }
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing",
                    "trade_date": end_date.isoformat(),
                    "completed_days": len(session_dates),
                    "total_days": len(session_dates),
                    "percent": 99.0,
                    "finalizing_stage": "committing",
                    "completed_items": None,
                    "total_items": None,
                }
            )
        db.commit()
        db.refresh(run)
        return BacktestResult(
            run_id=str(run.id),
            strategy_id=str(strategy.id),
            status=run.status,
            initial_cash=float(initial_cash),
            final_equity=final_equity,
            total_return=float(summary["total_return"]),
            max_drawdown=float(summary["max_drawdown"]),
            signal_count=int(summary["signal_count"]),
            trade_count=int(summary["trade_count"]),
            total_fees=float(summary["total_fees"]),
            total_slippage=float(summary["total_slippage"]),
        )
    except Exception as exc:
        db.rollback()
        persistence_cancelled = isinstance(
            exc,
            (
                NativePersistenceCancelledError,
                SupportResistancePersistenceCancelledError,
            ),
        )
        failed = db.get(StrategyRun, run.id)
        if failed is not None:
            failed.status = (
                "cancelled"
                if isinstance(exc, BacktestCancelledError) or persistence_cancelled
                else "failed"
            )
            failed.finished_at = datetime.now(UTC)
            failed.error_message = str(exc)
            db.commit()
        if persistence_cancelled:
            raise BacktestCancelledError(str(exc)) from exc
        raise
