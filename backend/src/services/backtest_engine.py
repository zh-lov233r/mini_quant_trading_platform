from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging
import math
import os
import resource
import statistics
import sys
from time import perf_counter
from typing import Any, Callable, Literal
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.models.tables import PortfolioSnapshot, Signal, Strategy, StrategyRun, Transaction
from src.services.data_service import get_historical_data
from src.services.backtest_universe_service import (
    normalize_point_in_time_policy,
    point_in_time_entry_eligible,
    resolve_backtest_universe,
    resolve_point_in_time_universe,
)
from src.services.backtest_repository import BacktestRepository
from src.services.market_data_loader import MarketDataLoader
from src.services.stateless_signal_kernel import vectorized_stateless_prefilter
from src.services.stock_basket_service import (
    DEFAULT_COMMON_STOCK_BASKET_NAME,
    load_default_common_stock_symbols,
)
from src.services.strategy_engine import (
    RECENT_BAR_COUNT,
    STRATEGY_HANDLERS,
    SignalEvent,
    build_stateful_backtest_signal_state,
    generate_stateful_backtest_signals,
    required_recent_bar_count_for_runtime,
    required_recent_bar_lookback_days,
)
from src.services.support_resistance_persistence_service import (
    SupportResistanceMaterializationBuildError,
    find_reusable_materialization,
    hydrate_state_from_materialization,
    persist_support_resistance_run,
    record_failed_materialization_after_rollback,
    source_data_fingerprint,
)
from src.services.support_resistance_service import SupportResistanceState
from src.services.strategy_registry import (
    build_runtime_payload,
    extract_description,
    is_engine_ready,
    normalize_strategy_params,
)

DEFAULT_COMPARISON_SYMBOLS = ("SPY", "QQQ")
PERSIST_LEVELS = {"summary", "trades", "full"}
PersistLevel = Literal["summary", "trades", "full"]
MAX_PREVIEW_POINTS = 1_500
log = logging.getLogger(__name__)


class BacktestCancelledError(RuntimeError):
    pass


# One SQL query loads the full daily snapshot needed by the backtest loop:
# adjusted close for pricing, volume, and all strategy features used by handlers.
FEATURE_RANGE_SQL = """
SELECT
    curr.instrument_id,
    COALESCE(identity_symbol.symbol, i.ticker_canonical) AS symbol,
    curr.dt_ny,
    bars.ts_utc AS ts,
    bars.close_u AS close_unadjusted,
    COALESCE(bars.open_fa, bars.open_u) AS open,
    COALESCE(bars.high_fa, bars.high_u) AS high,
    COALESCE(bars.low_fa, bars.low_u) AS low,
    COALESCE(bars.close_fa, bars.close_u) AS close,
    bars.volume,
    curr.atr_14,
    curr.adv_20 AS volume_sma_20,
    curr.dollar_volume_20,
    i.asset_type,
    i.exchange,
    i.listed_at,
    i.delisted_at,
    curr.ret_20d,
    curr.ret_60d,
    curr.sma_10,
    curr.sma_20,
    curr.sma_50,
    curr.sma_100,
    curr.sma_200,
    curr.ema_12,
    curr.ema_15,
    curr.ema_20,
    curr.ema_50,
    curr.rsi_2,
    curr.rsi_5,
    curr.rsi_14,
    curr.zscore_5,
    curr.zscore_10,
    curr.zscore_20,
    prev.sma_10 AS prev_sma_10,
    prev.sma_20 AS prev_sma_20,
    prev.sma_50 AS prev_sma_50,
    prev.sma_100 AS prev_sma_100,
    prev.sma_200 AS prev_sma_200,
    prev.ema_12 AS prev_ema_12,
    prev.ema_15 AS prev_ema_15,
    prev.ema_20 AS prev_ema_20,
    prev.ema_50 AS prev_ema_50
FROM daily_features curr
JOIN instruments i
  ON i.id = curr.instrument_id
JOIN eod_bars bars
  ON bars.instrument_id = curr.instrument_id
 AND bars.dt_ny = curr.dt_ny
LEFT JOIN LATERAL (
    SELECT sh.symbol
    FROM symbol_history sh
    WHERE sh.instrument_id = curr.instrument_id
      AND sh.is_primary = TRUE
      AND sh.valid_from <= curr.dt_ny
      AND (sh.valid_to IS NULL OR sh.valid_to >= curr.dt_ny)
    ORDER BY sh.valid_from DESC, sh.id DESC
    LIMIT 1
) identity_symbol ON TRUE
LEFT JOIN LATERAL (
    SELECT *
    FROM daily_features prev_df
    WHERE prev_df.instrument_id = curr.instrument_id
      AND prev_df.dt_ny < curr.dt_ny
    ORDER BY prev_df.dt_ny DESC
    LIMIT 1
) prev ON TRUE
WHERE curr.dt_ny BETWEEN :start_date AND :end_date
  AND i.is_active = TRUE
  AND i.ticker_canonical IN :symbols
ORDER BY curr.dt_ny, COALESCE(identity_symbol.symbol, i.ticker_canonical), curr.instrument_id;
"""

FEATURE_RANGE_V2_SQL = FEATURE_RANGE_SQL.replace(
    "  AND i.is_active = TRUE\n  AND i.ticker_canonical IN :symbols",
    "  AND curr.instrument_id IN :instrument_ids",
).replace(
    "ORDER BY curr.dt_ny, COALESCE(identity_symbol.symbol, i.ticker_canonical), curr.instrument_id;",
    "ORDER BY curr.dt_ny, curr.instrument_id;",
)

FEATURE_DAY_COUNT_V2_SQL = """
SELECT COUNT(DISTINCT curr.dt_ny)
FROM daily_features curr
JOIN eod_bars bars
  ON bars.instrument_id = curr.instrument_id
 AND bars.dt_ny = curr.dt_ny
WHERE curr.dt_ny BETWEEN :start_date AND :end_date
  AND curr.instrument_id IN :instrument_ids
"""

SPLIT_ACTION_RANGE_SQL = """
SELECT
    ca.instrument_id,
    i.ticker_canonical AS symbol,
    ca.ex_date,
    ca.action_type,
    ca.split_from,
    ca.split_to
FROM corporate_actions ca
JOIN instruments i
  ON i.id = ca.instrument_id
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


@dataclass(slots=True)
class ExecutionStats:
    """Accumulated execution stats for a batch of simulated orders."""

    trade_count: int = 0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    notional_by_symbol: dict[str, float] = field(default_factory=dict)
    net_cash_flow_by_symbol: dict[str, float] = field(default_factory=dict)


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


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


def _merge_execution_stats(
    notional_by_symbol: dict[str, float],
    net_cash_flow_by_symbol: dict[str, float],
    stats: ExecutionStats,
) -> None:
    for symbol, value in stats.notional_by_symbol.items():
        notional_by_symbol[symbol] = notional_by_symbol.get(symbol, 0.0) + value
    for symbol, value in stats.net_cash_flow_by_symbol.items():
        net_cash_flow_by_symbol[symbol] = net_cash_flow_by_symbol.get(symbol, 0.0) + value


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


def _load_close_maps_by_symbol(
    db: Session,
    symbols: list[str | None],
    start_date: date,
    end_date: date,
) -> dict[str, dict[date, float]]:
    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        return {}

    bars_by_symbol = get_historical_data(
        db,
        normalized_symbols,
        start_date,
        end_date,
        adjusted=True,
    )
    close_maps: dict[str, dict[date, float]] = {}
    for symbol in normalized_symbols:
        bars = bars_by_symbol.get(symbol, [])
        close_by_date = {
            bar.trade_date: float(bar.close_px)
            for bar in bars
            if bar.close_px is not None
        }
        if close_by_date:
            close_maps[symbol] = close_by_date
    return close_maps


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
    engine_version: str | None = None,
) -> BacktestResult:
    """Run a long-only daily backtest and persist signals, fills, and equity snapshots.

    The backtest reuses the same strategy handlers as the signal engine, but applies a
    simplified execution model:
    - signals are generated on day T using day-T close data
    - fills are executed on the next available session using day-(T+1) open data
    - BUY opens a new long position
    - SELL closes an existing long position
    - no shorting, no partial fills, no intraday execution

    Costs are modeled as:
    - per-trade commission in basis points with a minimum fee
    - symmetric slippage in basis points
    """
    total_started = perf_counter()
    performance: dict[str, Any] = {
        "load_market_data_ms": 0.0,
        "build_dataset_ms": 0.0,
        "history_state_ms": 0.0,
        "signal_generation_ms": 0.0,
        "execution_simulation_ms": 0.0,
        "persist_details_ms": 0.0,
        "persist_summary_ms": 0.0,
        "support_resistance_zone_versions_ms": 0.0,
        "support_resistance_run_events_ms": 0.0,
        "support_resistance_persist_total_ms": 0.0,
        "support_resistance_zone_versions": 0,
        "support_resistance_run_events": 0,
        "support_resistance_cache_reused": None,
    }
    resolved_persist_level = _normalize_persist_level(persist_level)
    resolved_engine_version = str(
        engine_version or os.getenv("BACKTEST_ENGINE_VERSION", "v1")
    ).strip().lower()
    if resolved_engine_version not in {"v1", "v2"}:
        raise ValueError("engine_version must be v1 or v2")
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")

    runtime = build_runtime_payload(strategy)
    if runtime_params_override is not None:
        normalized_override = normalize_strategy_params(
            strategy.strategy_type,
            runtime_params_override,
            extract_description(runtime_params_override),
        )
        runtime["params"] = normalized_override
        runtime["engine_ready"] = is_engine_ready(strategy.strategy_type, normalized_override)
    normalized_universe_policy = (
        normalize_point_in_time_policy(universe_policy)
        if universe_policy is not None
        else None
    )
    if universe_symbols is not None and normalized_universe_policy is not None:
        raise ValueError("provide universe_symbols or universe_policy, not both")
    if normalized_universe_policy is not None:
        if resolved_engine_version != "v2":
            raise ValueError("universe_policy requires engine_version=v2")
        runtime["params"]["universe"]["symbols"] = []
        runtime["params"]["universe"]["selection_mode"] = "point_in_time_liquid"
        runtime["params"]["universe"]["policy"] = normalized_universe_policy
    elif universe_symbols is not None:
        normalized_symbols = _normalize_symbol_universe(universe_symbols)
        runtime["params"]["universe"]["symbols"] = normalized_symbols
        runtime["params"]["universe"]["selection_mode"] = "stock_basket"
        if universe_metadata:
            runtime["params"]["universe"]["basket"] = universe_metadata
    elif (
        not runtime["params"]["universe"].get("symbols")
        and runtime["params"]["universe"].get("selection_mode") == "all_common_stock"
    ):
        normalized_symbols = _normalize_symbol_universe(load_default_common_stock_symbols(db))
        runtime["params"]["universe"]["symbols"] = normalized_symbols
        runtime["params"]["universe"]["default_label"] = DEFAULT_COMMON_STOCK_BASKET_NAME
    if not runtime["engine_ready"]:
        raise ValueError("strategy is not engine-ready")
    cost_config = _resolve_backtest_cost_config(
        runtime,
        commission_bps=commission_bps,
        commission_min=commission_min,
        slippage_bps=slippage_bps,
    )

    handler = STRATEGY_HANDLERS.get(runtime["strategy_type"])
    if handler is None:
        raise ValueError(f"unsupported strategy_type for backtest: {runtime['strategy_type']}")

    symbols = runtime["params"]["universe"]["symbols"]
    if not symbols and normalized_universe_policy is None:
        raise ValueError("backtest currently requires a non-empty manual symbol universe")

    resolved_universe = None
    if normalized_universe_policy is not None:
        resolved_universe = resolve_point_in_time_universe(
            db,
            normalized_universe_policy,
            start_date=start_date,
            end_date=end_date,
        )
        symbols = [
            item.canonical_symbol or f"instrument-{item.instrument_id}"
            for item in resolved_universe.instruments
        ]
        runtime["params"]["universe"]["symbols"] = symbols
    elif resolved_engine_version == "v2":
        resolved_universe = resolve_backtest_universe(
            db,
            symbols,
            start_date=start_date,
            end_date=end_date,
        )
    config_snapshot = dict(runtime["params"])
    config_snapshot["run_options"] = {
        "persist_level": resolved_persist_level,
        "engine_version": resolved_engine_version,
        "universe_membership_semantics": (
            resolved_universe.membership_semantics
            if resolved_universe is not None
            else "current_active_snapshot"
        ),
        "survivorship_bias_warning": (
            resolved_universe.membership_semantics == "current_active_snapshot"
            if resolved_universe is not None
            else True
        ),
    }
    if resolved_universe is not None:
        config_snapshot["universe_resolution"] = resolved_universe.manifest()

    if existing_run_id is not None:
        run = db.get(StrategyRun, existing_run_id)
        if run is None:
            raise ValueError("backtest run not found")
        run.strategy_id = strategy.id
        run.strategy_version = strategy.version
        run.mode = "backtest"
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
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
            started_at=datetime.now(timezone.utc),
            window_start=start_date,
            window_end=end_date,
            initial_cash=initial_cash,
            benchmark_symbol=benchmark_symbol,
            config_snapshot=config_snapshot,
        )
        db.add(run)
    db.commit()
    db.refresh(run)

    streaming_loader: MarketDataLoader | None = None
    try:
        repository = BacktestRepository(db) if resolved_engine_version == "v2" else None
        recent_bar_count = required_recent_bar_count_for_runtime(runtime)
        recent_bar_lookback_days = required_recent_bar_lookback_days(recent_bar_count)
        history_start_date = start_date - timedelta(days=recent_bar_lookback_days)
        support_resistance_source_fingerprint = (
            source_data_fingerprint(db)
            if runtime["strategy_type"] == "support_resistance"
            else None
        )
        load_started = perf_counter()
        if resolved_universe is not None:
            feature_stmt = text(FEATURE_RANGE_V2_SQL).bindparams(
                bindparam("instrument_ids", expanding=True)
            )
            feature_params = {
                "instrument_ids": resolved_universe.instrument_ids,
                "start_date": history_start_date,
                "end_date": end_date,
            }
            streaming_loader = MarketDataLoader(
                db,
                statement=feature_stmt,
                params=feature_params,
                row_factory=_feature_snapshot_from_row,
                performance=performance,
            )
            snapshots_by_date: dict[date, dict[str, dict[str, Any]]] = {}
        else:
            snapshots_by_date = _load_feature_snapshots_by_date(
                db,
                symbols,
                history_start_date,
                end_date,
                performance=performance,
            )
        performance["data_prepare_ms"] = _elapsed_ms(load_started)
        split_adjustments_by_date = _load_split_adjustments_by_date(
            db,
            symbols,
            start_date,
            end_date,
            instrument_ids=(resolved_universe.instrument_ids if resolved_universe else None),
        )
        if streaming_loader is None and not snapshots_by_date:
            raise ValueError("no feature snapshots found for the requested universe and window")
        benchmark_symbol_normalized = _normalize_symbols([benchmark_symbol])[0] if benchmark_symbol else None
        comparison_symbols = _normalize_symbols([*DEFAULT_COMPARISON_SYMBOLS, benchmark_symbol_normalized])
        comparison_close_maps = _load_close_maps_by_symbol(
            db,
            comparison_symbols,
            start_date,
            end_date,
        )
        benchmark_close_by_date = (
            comparison_close_maps.get(benchmark_symbol_normalized, {})
            if benchmark_symbol_normalized
            else {}
        )
        comparison_curve_states = {
            symbol: {
                "base_close": None,
                "last_close": None,
                "points": [],
            }
            for symbol in DEFAULT_COMPARISON_SYMBOLS
            if symbol in comparison_close_maps
        }

        risk_cfg = runtime["params"]["risk"]
        cash = float(initial_cash)
        peak_equity = float(initial_cash)
        holdings: dict[Any, float] = {}
        avg_entry_prices: dict[Any, float] = {}
        entry_trade_dates: dict[Any, date] = {}
        entry_day_indices: dict[Any, int] = {}
        entry_signal_features: dict[Any, dict[str, Any]] = {}
        last_prices: dict[Any, float] = {}
        display_symbol_by_position_key: dict[Any, str] = {}
        stable_instrument_identity = resolved_engine_version == "v2"
        trade_count = 0
        signal_count = 0
        max_drawdown = 0.0
        total_fees = 0.0
        total_slippage = 0.0
        notional_by_symbol: dict[str, float] = {}
        net_cash_flow_by_symbol: dict[str, float] = {}
        equity_points: list[float] = []
        drawdown_points: list[float] = []
        preview_snapshots: list[dict[str, Any]] = []
        pending_signals: list[SignalEvent] = []
        benchmark_base_close: float | None = None
        benchmark_last_close: float | None = None
        benchmark_last_equity: float | None = None
        benchmark_last_return: float | None = None
        benchmark_peak_equity: float | None = None
        benchmark_max_drawdown = 0.0
        benchmark_points = 0
        universe_membership_by_year: dict[int, dict[str, Any]] = {}
        delisting_zero_write_off = 0.0
        delisting_last_close_sensitivity = 0.0
        delisted_at_by_instrument = {
            item.instrument_id: item.delisted_at
            for item in (resolved_universe.instruments if resolved_universe is not None else ())
        }
        recent_history_by_symbol: dict[str, deque[dict[str, Any]]] = {}
        stateful_signal_state = build_stateful_backtest_signal_state(runtime)
        if isinstance(stateful_signal_state, SupportResistanceState):
            reusable_materialization = find_reusable_materialization(
                db,
                runtime=runtime,
                symbols=symbols,
                coverage_start=history_start_date,
                coverage_end=end_date,
                expected_data_fingerprint=support_resistance_source_fingerprint,
            )
            if reusable_materialization is not None:
                stateful_signal_state = hydrate_state_from_materialization(
                    db,
                    reusable_materialization,
                )
        if streaming_loader is not None:
            day_items = streaming_loader.iter_days()
            trading_day_count = _count_v2_trading_days(
                db,
                instrument_ids=resolved_universe.instrument_ids,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            ordered_trade_days = sorted(snapshots_by_date)
            day_items = ((trade_day, snapshots_by_date[trade_day]) for trade_day in ordered_trade_days)
            trading_day_count = sum(trade_day >= start_date for trade_day in ordered_trade_days)
        if trading_day_count <= 0:
            raise ValueError("no feature snapshots found inside the requested backtest window")

        trading_days_seen = 0
        for trade_day, day_snapshots in day_items:
            if normalized_universe_policy is not None:
                eligible_count = 0
                exclusion_counts: dict[str, int] = {}
                for snapshot in day_snapshots.values():
                    eligible, reason = point_in_time_entry_eligible(
                        snapshot,
                        normalized_universe_policy,
                    )
                    snapshot["entry_eligible"] = eligible
                    snapshot["entry_exclusion_reason"] = reason
                    if eligible:
                        eligible_count += 1
                    elif reason:
                        exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
                if trade_day >= start_date:
                    annual = universe_membership_by_year.setdefault(
                        trade_day.year,
                        {"sessions": 0, "eligible_sum": 0, "eligible_min": None, "eligible_max": 0, "exclusions": {}},
                    )
                    annual["sessions"] += 1
                    annual["eligible_sum"] += eligible_count
                    annual["eligible_min"] = (
                        eligible_count
                        if annual["eligible_min"] is None
                        else min(int(annual["eligible_min"]), eligible_count)
                    )
                    annual["eligible_max"] = max(int(annual["eligible_max"]), eligible_count)
                    for reason, count in exclusion_counts.items():
                        annual["exclusions"][reason] = annual["exclusions"].get(reason, 0) + count
            if trade_day < start_date:
                if stateful_signal_state is not None:
                    generate_stateful_backtest_signals(
                        runtime,
                        day_snapshots,
                        stateful_signal_state,
                        emit_signals=False,
                    )
                if recent_bar_count > 0:
                    history_started = perf_counter()
                    _attach_recent_history(
                        day_snapshots,
                        recent_history_by_symbol,
                        recent_bar_count=recent_bar_count,
                    )
                    performance["history_state_ms"] += _elapsed_ms(history_started)
                continue
            trade_day_index = trading_days_seen
            trading_days_seen += 1
            if cancel_check is not None and cancel_check():
                raise BacktestCancelledError("backtest cancellation requested")
            for display_symbol, snapshot in day_snapshots.items():
                position_key = _snapshot_position_key(
                    display_symbol,
                    snapshot,
                    stable_instrument_identity=stable_instrument_identity,
                )
                display_symbol_by_position_key[position_key] = display_symbol
            _apply_split_adjustments(
                trade_day,
                split_adjustments_by_date,
                holdings,
                avg_entry_prices,
            )
            snapshot_ts = _snapshot_ts(day_snapshots)
            execution_prices = _snapshot_price_map(
                day_snapshots,
                "open",
                stable_instrument_identity=stable_instrument_identity,
            )
            close_prices = _snapshot_price_map(
                day_snapshots,
                "close",
                stable_instrument_identity=stable_instrument_identity,
            )
            execution_snapshots = _snapshot_identity_map(
                day_snapshots,
                stable_instrument_identity=stable_instrument_identity,
            )
            execution_marks = dict(last_prices)
            execution_marks.update(execution_prices)

            if normalized_universe_policy is not None:
                for position_key in list(holdings):
                    if position_key in execution_snapshots:
                        continue
                    delisted_at = delisted_at_by_instrument.get(int(position_key))
                    if delisted_at is None or trade_day <= delisted_at:
                        continue
                    stale_value = float(holdings.get(position_key, 0.0)) * float(
                        last_prices.get(position_key, 0.0)
                    )
                    delisting_zero_write_off += stale_value
                    delisting_last_close_sensitivity += stale_value
                    holdings.pop(position_key, None)
                    avg_entry_prices.pop(position_key, None)
                    entry_trade_dates.pop(position_key, None)
                    entry_day_indices.pop(position_key, None)
                    entry_signal_features.pop(position_key, None)
                    last_prices[position_key] = 0.0

            cash_state = {"cash": cash}
            execution_started = perf_counter()
            sell_stats = _apply_sell_signals(
                db=db,
                strategy=strategy,
                run=run,
                signals=pending_signals,
                holdings=holdings,
                avg_entry_prices=avg_entry_prices,
                entry_trade_dates=entry_trade_dates,
                entry_day_indices=entry_day_indices,
                entry_signal_features=entry_signal_features,
                execution_prices=execution_prices,
                execution_snapshots=execution_snapshots,
                cash_ref=cash_state,
                cost_config=cost_config,
                persist_transactions=resolved_persist_level in {"trades", "full"},
                repository=repository,
                stable_instrument_identity=stable_instrument_identity,
            )
            trade_count += sell_stats.trade_count
            total_fees += sell_stats.total_fees
            total_slippage += sell_stats.total_slippage
            _merge_execution_stats(notional_by_symbol, net_cash_flow_by_symbol, sell_stats)

            equity_before = _portfolio_equity(float(cash_state["cash"]), holdings, execution_marks)
            max_positions = int(risk_cfg["max_positions"])
            position_size_pct = float(risk_cfg["position_size_pct"])

            buy_stats = _apply_buy_signals(
                db=db,
                strategy=strategy,
                run=run,
                signals=pending_signals,
                holdings=holdings,
                avg_entry_prices=avg_entry_prices,
                entry_trade_dates=entry_trade_dates,
                entry_day_indices=entry_day_indices,
                entry_signal_features=entry_signal_features,
                execution_prices=execution_prices,
                execution_snapshots=execution_snapshots,
                cash_ref=cash_state,
                equity_before=equity_before,
                max_positions=max_positions,
                position_size_pct=position_size_pct,
                cost_config=cost_config,
                trade_day=trade_day,
                trade_day_index=trade_day_index,
                persist_transactions=resolved_persist_level in {"trades", "full"},
                repository=repository,
                stable_instrument_identity=stable_instrument_identity,
            )
            trade_count += buy_stats.trade_count
            total_fees += buy_stats.total_fees
            total_slippage += buy_stats.total_slippage
            _merge_execution_stats(notional_by_symbol, net_cash_flow_by_symbol, buy_stats)
            cash = float(cash_state["cash"])
            performance["execution_simulation_ms"] += _elapsed_ms(execution_started)

            _inject_backtest_positions(
                day_snapshots,
                holdings,
                avg_entry_prices,
                entry_trade_dates,
                entry_day_indices,
                entry_signal_features,
                trade_day,
                trade_day_index,
                stable_instrument_identity=stable_instrument_identity,
            )
            if recent_bar_count > 0:
                history_started = perf_counter()
                _attach_recent_history(
                    day_snapshots,
                    recent_history_by_symbol,
                    recent_bar_count=recent_bar_count,
                )
                performance["history_state_ms"] += _elapsed_ms(history_started)

            signal_started = perf_counter()
            if stateful_signal_state is not None:
                signals = generate_stateful_backtest_signals(
                    runtime,
                    day_snapshots,
                    stateful_signal_state,
                    emit_signals=True,
                )
            else:
                handler_snapshots = (
                    vectorized_stateless_prefilter(runtime, day_snapshots)
                    if resolved_engine_version == "v2"
                    else day_snapshots
                )
                signals = handler(runtime, handler_snapshots)
            if normalized_universe_policy is not None:
                signals = [
                    event
                    for event in signals
                    if event.action != "BUY"
                    or bool((day_snapshots.get(event.symbol) or {}).get("entry_eligible"))
                ]
            for event in signals:
                event_snapshot = day_snapshots.get(event.symbol)
                if event_snapshot is not None and event_snapshot.get("instrument_id") is not None:
                    event.instrument_id = int(event_snapshot["instrument_id"])
            performance["signal_generation_ms"] += _elapsed_ms(signal_started)
            pending_signals = signals
            signal_count += len(signals)
            if resolved_persist_level == "full":
                for event in signals:
                    signal_values = {
                        "run_id": run.id,
                        "strategy_id": strategy.id,
                        "instrument_id": event.instrument_id,
                        "ts": event.ts,
                        "symbol": event.symbol,
                        "signal": event.action,
                        "score": event.score,
                        "reason": event.reason,
                        "features": event.metadata,
                    }
                    if repository is not None:
                        repository.add_signal(signal_values)
                    else:
                        db.add(Signal(**signal_values))

            signal_by_position_key = {
                _event_position_key(
                    event,
                    stable_instrument_identity=stable_instrument_identity,
                ): event
                for event in signals
            }
            last_prices.update(close_prices)
            equity = _portfolio_equity(cash, holdings, last_prices)
            peak_equity = max(peak_equity, equity)
            drawdown = 0.0 if peak_equity <= 0 else (peak_equity - equity) / peak_equity
            max_drawdown = max(max_drawdown, drawdown)
            equity_points.append(equity)
            drawdown_points.append(drawdown)

            for symbol, curve_state in comparison_curve_states.items():
                comparison_close = comparison_close_maps[symbol].get(trade_day, curve_state["last_close"])
                if comparison_close is None:
                    continue
                curve_state["last_close"] = comparison_close
                if curve_state["base_close"] is None:
                    curve_state["base_close"] = comparison_close
                if curve_state["base_close"] <= 0:
                    continue

                comparison_equity = initial_cash * (comparison_close / curve_state["base_close"])
                curve_state["points"].append(
                    {
                        "ts": snapshot_ts.isoformat(),
                        "symbol": symbol,
                        "close": comparison_close,
                        "equity": comparison_equity,
                        "return": (comparison_equity / initial_cash) - 1 if initial_cash else 0.0,
                    }
                )

            benchmark_close = benchmark_close_by_date.get(trade_day, benchmark_last_close)
            benchmark_equity: float | None = None
            benchmark_return: float | None = None
            benchmark_excess_return: float | None = None
            if benchmark_close is not None:
                benchmark_last_close = benchmark_close
                if benchmark_base_close is None:
                    benchmark_base_close = benchmark_close
                if benchmark_base_close and benchmark_base_close > 0:
                    benchmark_equity = initial_cash * (benchmark_close / benchmark_base_close)
                    benchmark_return = (benchmark_equity / initial_cash) - 1 if initial_cash else 0.0
                    strategy_return_to_date = (equity / initial_cash) - 1 if initial_cash else 0.0
                    benchmark_excess_return = strategy_return_to_date - benchmark_return
                    benchmark_last_equity = benchmark_equity
                    benchmark_last_return = benchmark_return
                    benchmark_points += 1
                    benchmark_peak_equity = max(
                        benchmark_peak_equity or benchmark_equity,
                        benchmark_equity,
                    )
                    if benchmark_peak_equity > 0:
                        benchmark_max_drawdown = max(
                            benchmark_max_drawdown,
                            (benchmark_peak_equity - benchmark_equity) / benchmark_peak_equity,
                        )

            gross_exposure = _gross_exposure(holdings, last_prices)
            snapshot_metrics = {
                "holdings_count": len(holdings),
                "signal_count_cumulative": signal_count,
                "trade_count_cumulative": trade_count,
                "fees_cumulative": total_fees,
                "slippage_cumulative": total_slippage,
                "benchmark_symbol": benchmark_symbol_normalized,
                "benchmark_close": benchmark_close,
                "benchmark_equity": benchmark_equity,
                "benchmark_return": benchmark_return,
                "benchmark_excess_return": benchmark_excess_return,
            }
            if resolved_persist_level == "full":
                snapshot_values = {
                    "run_id": run.id,
                    "ts": snapshot_ts,
                    "cash": cash,
                    "equity": equity,
                    "gross_exposure": gross_exposure,
                    "net_exposure": gross_exposure,
                    "drawdown": drawdown,
                    "positions": _serialize_positions(
                            holdings,
                            avg_entry_prices,
                            entry_trade_dates,
                            entry_signal_features,
                            last_prices,
                            signal_by_position_key,
                            display_symbol_by_position_key,
                        ),
                    "metrics": snapshot_metrics,
                }
                if repository is not None:
                    repository.add_snapshot(snapshot_values)
                else:
                    db.add(PortfolioSnapshot(**snapshot_values))
            else:
                preview_snapshots.append(
                    {
                        "ts": snapshot_ts,
                        "cash": cash,
                        "equity": equity,
                        "gross_exposure": gross_exposure,
                        "net_exposure": gross_exposure,
                        "drawdown": drawdown,
                        "positions": {},
                        "metrics": {**snapshot_metrics, "downsampled": True},
                    }
                )
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "running",
                        "trade_date": trade_day.isoformat(),
                        "completed_days": trade_day_index + 1,
                        "total_days": trading_day_count,
                        "percent": round(
                            ((trade_day_index + 1) / trading_day_count) * 85.0,
                            3,
                        ),
                    }
                )

        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing",
                    "trade_date": trade_day.isoformat(),
                    "completed_days": trading_day_count,
                    "total_days": trading_day_count,
                    "percent": 85.0,
                    "finalizing_stage": "backtest_details",
                    "completed_items": None,
                    "total_items": None,
                }
            )

        final_equity = _portfolio_equity(cash, holdings, last_prices)
        ending_positions = _serialize_positions(
            holdings,
            avg_entry_prices,
            entry_trade_dates,
            entry_signal_features,
            last_prices,
            {},
            display_symbol_by_position_key,
        )
        if resolved_persist_level != "full":
            for row in _downsample_snapshots(preview_snapshots):
                snapshot_values = {"run_id": run.id, **row}
                if repository is not None:
                    repository.add_snapshot(snapshot_values)
                else:
                    db.add(PortfolioSnapshot(**snapshot_values))
        comparison_curves = {
            symbol: (
                curve_state["points"]
                if resolved_persist_level == "full"
                else _downsample_snapshots(curve_state["points"])
            )
            for symbol, curve_state in comparison_curve_states.items()
            if curve_state["points"]
        }
        support_resistance_materialization = None
        persist_started = perf_counter()
        if isinstance(stateful_signal_state, SupportResistanceState):
            def report_support_resistance_persistence(
                stage: str,
                completed_items: int,
                total_items: int,
            ) -> None:
                if progress_callback is None:
                    return
                item_ratio = completed_items / total_items if total_items else 0.0
                progress_callback(
                    {
                        "phase": "finalizing",
                        "trade_date": trade_day.isoformat(),
                        "completed_days": trading_day_count,
                        "total_days": trading_day_count,
                        "percent": min(99.0, round(85.0 + (item_ratio * 14.0), 3)),
                        "finalizing_stage": stage,
                        "completed_items": completed_items,
                        "total_items": total_items,
                    }
                )

            support_resistance_materialization = persist_support_resistance_run(
                db,
                run=run,
                runtime=runtime,
                state=stateful_signal_state,
                symbols=symbols,
                coverage_start=history_start_date,
                coverage_end=end_date,
                expected_data_fingerprint=support_resistance_source_fingerprint,
                persist_run_events=resolved_persist_level == "full",
                performance=performance,
                progress_callback=report_support_resistance_persistence,
            )
        if repository is not None:
            repository.flush()
        flush = getattr(db, "flush", None)
        if callable(flush):
            flush()
        performance["persist_details_ms"] = _elapsed_ms(persist_started)
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing",
                    "trade_date": trade_day.isoformat(),
                    "completed_days": trading_day_count,
                    "total_days": trading_day_count,
                    "percent": 99.0,
                    "finalizing_stage": "committing",
                    "completed_items": None,
                    "total_items": None,
                }
            )
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.final_equity = final_equity
        research_metrics = _research_metrics_from_simulation(
            equity_points=equity_points,
            drawdown_points=drawdown_points,
            notional_by_symbol=notional_by_symbol,
            net_cash_flow_by_symbol=net_cash_flow_by_symbol,
            ending_positions=ending_positions,
            initial_cash=initial_cash,
        )
        performance.update(
            {
                "rows_loaded": (
                    streaming_loader.rows_loaded
                    if streaming_loader is not None
                    else sum(len(items) for items in snapshots_by_date.values())
                ),
                "trading_days": trading_days_seen,
                "signals_generated": signal_count,
                "trades_generated": trade_count,
                "snapshots_persisted": trading_days_seen if resolved_persist_level == "full" else len(_downsample_snapshots(preview_snapshots)),
                "detail_rows_inserted": repository.rows_inserted if repository is not None else None,
                "peak_rss_mb": round(_peak_rss_mb(), 3),
            }
        )
        run.summary_metrics = {
            "initial_cash": initial_cash,
            "final_equity": final_equity,
            "total_return": (final_equity / initial_cash) - 1 if initial_cash else 0.0,
            "max_drawdown": max_drawdown,
            "signal_count": signal_count,
            "trade_count": trade_count,
            "total_fees": total_fees,
            "total_slippage": total_slippage,
            "total_transaction_cost": total_fees + total_slippage,
            "pending_signal_count": len(pending_signals),
            "execution_lag": "next_session_open",
            "universe_size": len(symbols),
            "symbols_loaded": (
                sorted(streaming_loader.loaded_symbols)
                if streaming_loader is not None
                else sorted(snapshots_by_date[next(iter(snapshots_by_date))].keys())
            ),
            "strategy_type": runtime["strategy_type"],
            "engine_version": resolved_engine_version,
            "cost_model": {
                "commission_bps": cost_config.commission_bps,
                "commission_min": cost_config.commission_min,
                "slippage_bps": cost_config.slippage_bps,
            },
            "benchmark_symbol": benchmark_symbol_normalized,
            "benchmark_points": benchmark_points,
            "benchmark_initial_close": benchmark_base_close,
            "benchmark_final_close": benchmark_last_close,
            "benchmark_final_equity": benchmark_last_equity,
            "benchmark_total_return": benchmark_last_return,
            "benchmark_max_drawdown": benchmark_max_drawdown if benchmark_points else None,
            "excess_return": (
                ((final_equity / initial_cash) - 1) - benchmark_last_return
                if initial_cash and benchmark_last_return is not None
                else None
            ),
            "comparison_curves": comparison_curves,
            "persist_level": resolved_persist_level,
            "available_details": _available_details(resolved_persist_level),
            "performance": dict(performance),
            "support_resistance_materialization_id": (
                str(support_resistance_materialization.id)
                if support_resistance_materialization is not None
                else None
            ),
            "support_resistance_cache_key": (
                support_resistance_materialization.cache_key
                if support_resistance_materialization is not None
                else None
            ),
            "universe_membership": {
                "policy": normalized_universe_policy,
                "annual": {
                    str(year): {
                        **values,
                        "eligible_average": (
                            values["eligible_sum"] / values["sessions"]
                            if values["sessions"]
                            else 0.0
                        ),
                    }
                    for year, values in sorted(universe_membership_by_year.items())
                },
            } if normalized_universe_policy is not None else None,
            "delisting_zero_write_off": delisting_zero_write_off,
            "delisting_last_close_sensitivity": delisting_last_close_sensitivity,
            **research_metrics,
        }
        summary_persist_started = perf_counter()
        db.commit()
        performance["persist_summary_ms"] = _elapsed_ms(summary_persist_started)
        response_started = perf_counter()
        result = BacktestResult(
            run_id=str(run.id),
            strategy_id=str(strategy.id),
            status=run.status,
            initial_cash=float(initial_cash),
            final_equity=float(final_equity),
            total_return=float(run.summary_metrics["total_return"]),
            max_drawdown=float(max_drawdown),
            signal_count=signal_count,
            trade_count=trade_count,
            total_fees=float(total_fees),
            total_slippage=float(total_slippage),
        )
        performance["response_serialization_ms"] = _elapsed_ms(response_started)
        performance["total_ms"] = _elapsed_ms(total_started)
        # JSON mutation tracking is not enabled, so assign a fresh mapping before the final commit.
        run.summary_metrics = {**dict(run.summary_metrics or {}), "performance": dict(performance)}
        db.commit()
        db.refresh(run)
        log.info(
            "Backtest completed run_id=%s strategy_type=%s persist_level=%s performance=%s",
            run.id,
            runtime["strategy_type"],
            resolved_persist_level,
            performance,
        )

        return result
    except Exception as exc:
        if streaming_loader is not None:
            streaming_loader.close()
        db.rollback()
        if isinstance(exc, SupportResistanceMaterializationBuildError):
            try:
                record_failed_materialization_after_rollback(db, exc)
            except Exception:
                db.rollback()
        failed_run = db.get(StrategyRun, run.id)
        if failed_run is not None:
            failed_run.status = "cancelled" if isinstance(exc, BacktestCancelledError) else "failed"
            failed_run.finished_at = datetime.now(timezone.utc)
            failed_run.error_message = str(exc)
            db.commit()
        raise


def run_backtest_trend(
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
) -> BacktestResult:
    """Convenience wrapper that narrows ``run_backtest`` to trend strategies."""
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if strategy.strategy_type != "trend":
        raise ValueError("run_backtest_trend only supports trend strategies")
    return run_backtest(
        db,
        strategy_id,
        start_date,
        end_date,
        initial_cash=initial_cash,
        benchmark_symbol=benchmark_symbol,
        commission_bps=commission_bps,
        commission_min=commission_min,
        slippage_bps=slippage_bps,
        universe_symbols=universe_symbols,
        universe_metadata=universe_metadata,
    )


def run_backtest_mean_reversion(
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
) -> BacktestResult:
    """Convenience wrapper that narrows ``run_backtest`` to mean-reversion strategies."""
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if strategy.strategy_type != "mean_reversion":
        raise ValueError("run_backtest_mean_reversion only supports mean_reversion strategies")
    return run_backtest(
        db,
        strategy_id,
        start_date,
        end_date,
        initial_cash=initial_cash,
        benchmark_symbol=benchmark_symbol,
        commission_bps=commission_bps,
        commission_min=commission_min,
        slippage_bps=slippage_bps,
        universe_symbols=universe_symbols,
        universe_metadata=universe_metadata,
    )


def _count_v2_trading_days(
    db: Session,
    *,
    instrument_ids: list[int],
    start_date: date,
    end_date: date,
) -> int:
    stmt = text(FEATURE_DAY_COUNT_V2_SQL).bindparams(
        bindparam("instrument_ids", expanding=True)
    )
    return int(
        db.execute(
            stmt,
            {
                "instrument_ids": instrument_ids,
                "start_date": start_date,
                "end_date": end_date,
            },
        ).scalar_one()
    )


def _load_feature_snapshots_by_date(
    db: Session,
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    performance: dict[str, Any] | None = None,
    instrument_ids: list[int] | None = None,
) -> dict[date, dict[str, dict[str, Any]]]:
    """Load all per-symbol daily inputs once, then group them by trade date.

    The backtest loop works on one in-memory snapshot per day. Most handlers remain
    stateless, while strategy-specific backtest state can be layered on top when needed.
    """
    if instrument_ids is not None:
        stmt = text(FEATURE_RANGE_V2_SQL).bindparams(bindparam("instrument_ids", expanding=True))
        filter_params: dict[str, Any] = {"instrument_ids": instrument_ids}
    else:
        stmt = text(FEATURE_RANGE_SQL).bindparams(bindparam("symbols", expanding=True))
        filter_params = {"symbols": [symbol.upper() for symbol in symbols]}
    sql_started = perf_counter()
    rows = db.execute(
        stmt,
        {**filter_params, "start_date": start_date, "end_date": end_date},
    ).mappings().all()
    if performance is not None:
        performance["load_market_data_ms"] = _elapsed_ms(sql_started)

    build_started = perf_counter()
    snapshots_by_date: dict[date, dict[str, dict[str, Any]]] = {}
    for row in rows:
        trade_date, symbol, snapshot = _feature_snapshot_from_row(dict(row))
        snapshots_by_date.setdefault(trade_date, {})[symbol] = snapshot
    if performance is not None:
        performance["build_dataset_ms"] = _elapsed_ms(build_started)
    return snapshots_by_date


def _feature_snapshot_from_row(
    row: dict[str, Any],
) -> tuple[date, str, dict[str, Any]]:
    symbol = str(row["symbol"]).upper()
    trade_date = row["dt_ny"]
    snapshot = {
        "instrument_id": int(row["instrument_id"]),
        "symbol": symbol,
        "dt_ny": trade_date,
        "ts": row["ts"] or datetime.now(timezone.utc),
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "close_unadjusted": row.get("close_unadjusted"),
        "volume": row["volume"],
        "atr_14": row["atr_14"],
        "volume_sma_20": row["volume_sma_20"],
        "dollar_volume_20": row.get("dollar_volume_20"),
        "asset_type": row.get("asset_type"),
        "exchange": row.get("exchange"),
        "listed_at": row.get("listed_at"),
        "delisted_at": row.get("delisted_at"),
        "ret_20d": row["ret_20d"],
        "ret_60d": row["ret_60d"],
        "sma_10": row["sma_10"],
        "sma_20": row["sma_20"],
        "sma_50": row["sma_50"],
        "sma_100": row["sma_100"],
        "sma_200": row["sma_200"],
        "ema_12": row["ema_12"],
        "ema_15": row["ema_15"],
        "ema_20": row["ema_20"],
        "ema_50": row["ema_50"],
        "rsi_2": row["rsi_2"],
        "rsi_5": row["rsi_5"],
        "rsi_14": row["rsi_14"],
        "zscore_5": row["zscore_5"],
        "zscore_10": row["zscore_10"],
        "zscore_20": row["zscore_20"],
        "prev_sma_10": row["prev_sma_10"],
        "prev_sma_20": row["prev_sma_20"],
        "prev_sma_50": row["prev_sma_50"],
        "prev_sma_100": row["prev_sma_100"],
        "prev_sma_200": row["prev_sma_200"],
        "prev_ema_12": row["prev_ema_12"],
        "prev_ema_15": row["prev_ema_15"],
        "prev_ema_20": row["prev_ema_20"],
        "prev_ema_50": row["prev_ema_50"],
        "position": 0.0,
        "recent_bars": [],
    }
    return trade_date, symbol, snapshot


def _load_split_adjustments_by_date(
    db: Session,
    symbols: list[str],
    start_date: date,
    end_date: date,
    *,
    instrument_ids: list[int] | None = None,
) -> dict[date, dict[Any, float]]:
    """Load per-symbol quantity adjustment factors for split-style corporate actions."""
    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        return {}

    if instrument_ids is not None:
        stmt = text(SPLIT_ACTION_RANGE_V2_SQL).bindparams(bindparam("instrument_ids", expanding=True))
        filter_params: dict[str, Any] = {"instrument_ids": instrument_ids}
    else:
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

        quantity_factor = split_to / split_from
        day_adjustments = adjustments_by_date.setdefault(trade_date, {})
        position_key: Any = int(row["instrument_id"]) if instrument_ids is not None else symbol
        day_adjustments[position_key] = (
            float(day_adjustments.get(position_key, 1.0)) * quantity_factor
        )

    return adjustments_by_date


def _apply_split_adjustments(
    trade_day: date,
    split_adjustments_by_date: dict[date, dict[Any, float]],
    holdings: dict[Any, float],
    avg_entry_prices: dict[Any, float],
) -> None:
    """Adjust open positions for split-style corporate actions effective on trade_day."""
    day_adjustments = split_adjustments_by_date.get(trade_day)
    if not day_adjustments:
        return

    for position_key, quantity_factor in day_adjustments.items():
        current_qty = float(holdings.get(position_key, 0.0))
        if current_qty <= 0 or quantity_factor <= 0:
            continue
        if abs(quantity_factor - 1.0) <= 1e-12:
            continue

        holdings[position_key] = current_qty * quantity_factor

        avg_entry_price = avg_entry_prices.get(position_key)
        if avg_entry_price is not None:
            avg_entry_prices[position_key] = float(avg_entry_price) / quantity_factor


def _inject_backtest_positions(
    day_snapshots: dict[str, dict[str, Any]],
    holdings: dict[Any, float],
    avg_entry_prices: dict[Any, float],
    entry_trade_dates: dict[Any, date],
    entry_day_indices: dict[Any, int],
    entry_signal_features: dict[Any, dict[str, Any]],
    trade_day: date,
    trade_day_index: int,
    *,
    stable_instrument_identity: bool = False,
) -> None:
    """Expose current position size to handlers that need state-aware exits."""
    for symbol, snapshot in day_snapshots.items():
        position_key = _snapshot_position_key(
            symbol,
            snapshot,
            stable_instrument_identity=stable_instrument_identity,
        )
        snapshot["position"] = float(holdings.get(position_key, 0.0))
        snapshot["avg_entry_price"] = avg_entry_prices.get(position_key)
        snapshot["entry_trade_date"] = entry_trade_dates.get(position_key)
        snapshot["entry_signal_features"] = entry_signal_features.get(position_key)
        entry_day_index = entry_day_indices.get(position_key)
        snapshot["position_holding_days"] = (
            max(trade_day_index - entry_day_index, 0) if entry_day_index is not None else None
        )


def _attach_recent_history(
    day_snapshots: dict[str, dict[str, Any]],
    history_by_symbol: dict[str, deque[dict[str, Any]]],
    *,
    recent_bar_count: int = RECENT_BAR_COUNT,
) -> None:
    if recent_bar_count <= 0:
        return
    for symbol, snapshot in day_snapshots.items():
        history = history_by_symbol.setdefault(symbol, deque(maxlen=recent_bar_count))
        history.append(
            {
                "dt_ny": snapshot.get("dt_ny"),
                "ts": snapshot.get("ts") or datetime.now(timezone.utc),
                "open": _to_float_or_none(snapshot.get("open")),
                "high": _to_float_or_none(snapshot.get("high")),
                "low": _to_float_or_none(snapshot.get("low")),
                "close": _to_float_or_none(snapshot.get("close")),
                "volume": _to_float_or_none(snapshot.get("volume")),
                "atr_14": _to_float_or_none(snapshot.get("atr_14")),
                "volume_sma_20": _to_float_or_none(snapshot.get("volume_sma_20")),
                "ret_20d": _to_float_or_none(snapshot.get("ret_20d")),
                "ret_60d": _to_float_or_none(snapshot.get("ret_60d")),
                "sma_20": _to_float_or_none(snapshot.get("sma_20")),
                "sma_50": _to_float_or_none(snapshot.get("sma_50")),
            }
        )
        snapshot["recent_bars"] = list(history)


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


def _normalize_symbol_universe(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    if not normalized:
        raise ValueError("universe_symbols must contain at least one non-empty ticker")
    return normalized


def _commission_for_notional(notional: float, cost_config: BacktestCostConfig) -> float:
    """Return commission for one trade notional, honoring the minimum fee."""
    if notional <= 0:
        return 0.0
    proportional = notional * (cost_config.commission_bps / 10_000.0)
    return max(proportional, cost_config.commission_min)


def _buy_execution_price(mark_price: float, cost_config: BacktestCostConfig) -> float:
    """Apply adverse slippage to a buy fill."""
    return mark_price * (1.0 + (cost_config.slippage_bps / 10_000.0))


def _sell_execution_price(mark_price: float, cost_config: BacktestCostConfig) -> float:
    """Apply adverse slippage to a sell fill."""
    return mark_price * (1.0 - (cost_config.slippage_bps / 10_000.0))


def _estimate_buy_order(
    available_cash: float,
    mark_price: float,
    cost_config: BacktestCostConfig,
) -> tuple[float, float, float, float]:
    """Solve the largest affordable buy order after fees and slippage.

    Returns:
        qty, execution_price, fee, gross_notional
    """
    execution_price = _buy_execution_price(mark_price, cost_config)
    if available_cash <= 0 or execution_price <= 0:
        return 0.0, execution_price, 0.0, 0.0

    if cost_config.commission_bps <= 0:
        qty = max((available_cash - cost_config.commission_min) / execution_price, 0.0)
        if qty <= 0:
            return 0.0, execution_price, 0.0, 0.0
        notional = qty * execution_price
        fee = _commission_for_notional(notional, cost_config)
        if notional + fee > available_cash:
            qty = max((available_cash - fee) / execution_price, 0.0)
            notional = qty * execution_price
            fee = _commission_for_notional(notional, cost_config)
        return qty, execution_price, fee, notional

    proportional_qty = available_cash / (
        execution_price * (1.0 + (cost_config.commission_bps / 10_000.0))
    )
    proportional_notional = proportional_qty * execution_price
    proportional_fee = _commission_for_notional(proportional_notional, cost_config)
    if proportional_notional > 0 and proportional_fee >= cost_config.commission_min:
        return proportional_qty, execution_price, proportional_fee, proportional_notional

    if available_cash <= cost_config.commission_min:
        return 0.0, execution_price, 0.0, 0.0

    min_fee_qty = (available_cash - cost_config.commission_min) / execution_price
    min_fee_notional = max(min_fee_qty, 0.0) * execution_price
    min_fee_commission = _commission_for_notional(min_fee_notional, cost_config)
    if min_fee_notional + min_fee_commission > available_cash:
        adjusted_qty = max((available_cash - min_fee_commission) / execution_price, 0.0)
        min_fee_notional = adjusted_qty * execution_price
        min_fee_commission = _commission_for_notional(min_fee_notional, cost_config)
        return adjusted_qty, execution_price, min_fee_commission, min_fee_notional
    return max(min_fee_qty, 0.0), execution_price, min_fee_commission, min_fee_notional


def _apply_sell_signals(
    *,
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    signals: list[SignalEvent],
    holdings: dict[Any, float],
    avg_entry_prices: dict[Any, float],
    entry_trade_dates: dict[Any, date],
    entry_day_indices: dict[Any, int],
    entry_signal_features: dict[Any, dict[str, Any]],
    execution_prices: dict[Any, float],
    execution_snapshots: dict[Any, dict[str, Any]],
    cash_ref: dict[str, float],
    cost_config: BacktestCostConfig,
    persist_transactions: bool = True,
    repository: BacktestRepository | None = None,
    stable_instrument_identity: bool = False,
) -> ExecutionStats:
    """Close existing long positions for queued SELL signals on the next session open."""
    stats = ExecutionStats()
    for event in signals:
        if event.action != "SELL":
            continue
        position_key = _event_position_key(
            event,
            stable_instrument_identity=stable_instrument_identity,
        )
        qty = holdings.get(position_key, 0.0)
        price = execution_prices.get(position_key)
        execution_snapshot = execution_snapshots.get(position_key)
        if qty <= 0 or price is None or execution_snapshot is None:
            continue
        # SELL receives a worse price than the mark because of slippage.
        execution_price = _sell_execution_price(float(price), cost_config)
        notional = qty * execution_price
        fee = _commission_for_notional(notional, cost_config)
        proceeds = max(notional - fee, 0.0)
        slippage_cost = qty * max(float(price) - execution_price, 0.0)
        cash_ref["cash"] += proceeds
        del holdings[position_key]
        avg_entry_prices.pop(position_key, None)
        entry_trade_dates.pop(position_key, None)
        entry_day_indices.pop(position_key, None)
        entry_signal_features.pop(position_key, None)
        display_symbol = str(execution_snapshot.get("symbol") or event.symbol).upper()
        stats.trade_count += 1
        stats.total_fees += fee
        stats.total_slippage += slippage_cost
        stats.notional_by_symbol[display_symbol] = (
            stats.notional_by_symbol.get(display_symbol, 0.0) + abs(notional)
        )
        stats.net_cash_flow_by_symbol[display_symbol] = (
            stats.net_cash_flow_by_symbol.get(display_symbol, 0.0) + proceeds
        )
        if persist_transactions:
            transaction_values = {
                "strategy_id": strategy.id,
                "run_id": run.id,
                "instrument_id": event.instrument_id,
                "ts": _execution_ts(execution_snapshot),
                "symbol": display_symbol,
                "side": "SELL",
                "qty": qty,
                "price": execution_price,
                "fee": fee,
                "order_id": None,
                "meta": {
                        "reason": event.reason,
                        "source": "backtest",
                        "signal_ts": event.ts.isoformat(),
                        "execution_trade_date": _execution_trade_date(execution_snapshot),
                        "reference_price": float(price),
                        "slippage_bps": cost_config.slippage_bps,
                        "slippage_cost": slippage_cost,
                        "gross_notional": notional,
                        "net_cash_flow": proceeds,
                },
            }
            if repository is not None:
                repository.add_transaction(transaction_values)
            else:
                db.add(Transaction(**transaction_values))
    return stats


def _apply_buy_signals(
    *,
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    signals: list[SignalEvent],
    holdings: dict[Any, float],
    avg_entry_prices: dict[Any, float],
    entry_trade_dates: dict[Any, date],
    entry_day_indices: dict[Any, int],
    entry_signal_features: dict[Any, dict[str, Any]],
    execution_prices: dict[Any, float],
    execution_snapshots: dict[Any, dict[str, Any]],
    cash_ref: dict[str, float],
    equity_before: float,
    max_positions: int,
    position_size_pct: float,
    cost_config: BacktestCostConfig,
    trade_day: date,
    trade_day_index: int,
    persist_transactions: bool = True,
    repository: BacktestRepository | None = None,
    stable_instrument_identity: bool = False,
) -> ExecutionStats:
    """Open new long positions for queued BUY signals on the next session open."""
    stats = ExecutionStats()
    for event in signals:
        if event.action != "BUY":
            continue
        position_key = _event_position_key(
            event,
            stable_instrument_identity=stable_instrument_identity,
        )
        if position_key in holdings:
            continue
        if len(holdings) >= max_positions:
            continue

        price = execution_prices.get(position_key)
        execution_snapshot = execution_snapshots.get(position_key)
        if price is None or price <= 0 or execution_snapshot is None:
            continue

        # Size each entry off current equity, but never spend more cash than we have.
        target_value = min(float(cash_ref["cash"]), float(equity_before) * position_size_pct)
        qty, execution_price, fee, gross_notional = _estimate_buy_order(
            target_value,
            float(price),
            cost_config,
        )
        if qty <= 0:
            continue

        total_cash_out = gross_notional + fee
        if total_cash_out <= 0 or total_cash_out > cash_ref["cash"]:
            continue

        slippage_cost = qty * max(execution_price - float(price), 0.0)
        cash_ref["cash"] -= total_cash_out
        holdings[position_key] = qty
        avg_entry_prices[position_key] = execution_price
        entry_trade_dates[position_key] = trade_day
        entry_day_indices[position_key] = trade_day_index
        entry_signal_features[position_key] = event.metadata if isinstance(event.metadata, dict) else {}
        display_symbol = str(execution_snapshot.get("symbol") or event.symbol).upper()
        stats.trade_count += 1
        stats.total_fees += fee
        stats.total_slippage += slippage_cost
        stats.notional_by_symbol[display_symbol] = (
            stats.notional_by_symbol.get(display_symbol, 0.0) + abs(gross_notional)
        )
        stats.net_cash_flow_by_symbol[display_symbol] = (
            stats.net_cash_flow_by_symbol.get(display_symbol, 0.0) - total_cash_out
        )
        if persist_transactions:
            transaction_values = {
                "strategy_id": strategy.id,
                "run_id": run.id,
                "instrument_id": event.instrument_id,
                "ts": _execution_ts(execution_snapshot),
                "symbol": display_symbol,
                "side": "BUY",
                "qty": qty,
                "price": execution_price,
                "fee": fee,
                "order_id": None,
                "meta": {
                        "reason": event.reason,
                        "source": "backtest",
                        "signal_ts": event.ts.isoformat(),
                        "entry_signal_features": event.metadata if isinstance(event.metadata, dict) else {},
                        "execution_trade_date": _execution_trade_date(execution_snapshot),
                        "reference_price": float(price),
                        "slippage_bps": cost_config.slippage_bps,
                        "slippage_cost": slippage_cost,
                        "gross_notional": gross_notional,
                        "net_cash_flow": -total_cash_out,
                },
            }
            if repository is not None:
                repository.add_transaction(transaction_values)
            else:
                db.add(Transaction(**transaction_values))
    return stats


def _event_position_key(
    event: SignalEvent,
    *,
    stable_instrument_identity: bool,
) -> Any:
    if stable_instrument_identity:
        if event.instrument_id is None:
            raise ValueError(f"signal {event.symbol} is missing instrument identity")
        return int(event.instrument_id)
    return event.symbol


def _snapshot_position_key(
    symbol: str,
    snapshot: dict[str, Any],
    *,
    stable_instrument_identity: bool,
) -> Any:
    if stable_instrument_identity:
        instrument_id = snapshot.get("instrument_id")
        if instrument_id is None:
            raise ValueError(f"snapshot {symbol} is missing instrument identity")
        return int(instrument_id)
    return symbol


def _portfolio_equity(cash: float, holdings: dict[Any, float], last_prices: dict[Any, float]) -> float:
    """Mark the portfolio to market using the latest daily close snapshot."""
    return cash + _gross_exposure(holdings, last_prices)


def _gross_exposure(holdings: dict[Any, float], last_prices: dict[Any, float]) -> float:
    """Compute gross long exposure from current holdings."""
    return sum(float(qty) * float(last_prices.get(symbol, 0.0)) for symbol, qty in holdings.items())


def _snapshot_price_map(
    day_snapshots: dict[str, dict[str, Any]],
    field: Literal["open", "close"],
    *,
    stable_instrument_identity: bool = False,
) -> dict[Any, float]:
    prices: dict[Any, float] = {}
    for symbol, snapshot in day_snapshots.items():
        price = snapshot.get(field)
        if price is None:
            continue
        position_key = _snapshot_position_key(
            symbol,
            snapshot,
            stable_instrument_identity=stable_instrument_identity,
        )
        prices[position_key] = float(price)
    return prices


def _snapshot_identity_map(
    day_snapshots: dict[str, dict[str, Any]],
    *,
    stable_instrument_identity: bool,
) -> dict[Any, dict[str, Any]]:
    return {
        _snapshot_position_key(
            symbol,
            snapshot,
            stable_instrument_identity=stable_instrument_identity,
        ): snapshot
        for symbol, snapshot in day_snapshots.items()
    }


def _serialize_positions(
    holdings: dict[Any, float],
    avg_entry_prices: dict[Any, float],
    entry_trade_dates: dict[Any, date],
    entry_signal_features: dict[Any, dict[str, Any]],
    last_prices: dict[Any, float],
    signal_by_position_key: dict[Any, Any],
    display_symbol_by_position_key: dict[Any, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Persist a lightweight position snapshot for later review and debugging."""
    payload: dict[str, dict[str, float | str | None]] = {}
    for position_key, qty in holdings.items():
        close_px = float(last_prices.get(position_key, 0.0))
        event = signal_by_position_key.get(position_key)
        symbol = (display_symbol_by_position_key or {}).get(position_key, str(position_key))
        payload[symbol] = {
            "qty": float(qty),
            "instrument_id": position_key if isinstance(position_key, int) else None,
            "avg_entry_price": avg_entry_prices.get(position_key),
            "entry_trade_date": (
                entry_trade_dates[position_key].isoformat()
                if entry_trade_dates.get(position_key) is not None
                else None
            ),
            "entry_signal_features": entry_signal_features.get(position_key),
            "close": close_px,
            "market_value": float(qty) * close_px,
            "latest_signal": getattr(event, "action", None),
        }
    return payload


def _snapshot_ts(day_snapshots: dict[str, dict[str, Any]]) -> datetime:
    """Use the day's market timestamp as the portfolio snapshot timestamp."""
    first_snapshot = next(iter(day_snapshots.values()))
    return first_snapshot.get("ts") or datetime.now(timezone.utc)


def _execution_ts(execution_snapshot: dict[str, Any]) -> datetime:
    return execution_snapshot.get("ts") or datetime.now(timezone.utc)


def _execution_trade_date(execution_snapshot: dict[str, Any]) -> str | None:
    trade_date = execution_snapshot.get("dt_ny")
    return str(trade_date) if trade_date is not None else None


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
