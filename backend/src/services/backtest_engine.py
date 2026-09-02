from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import resource
import statistics
import sys
from typing import Any, Callable, Literal
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

PERSIST_LEVELS = {"summary", "trades", "full"}
PersistLevel = Literal["summary", "trades", "full"]
MAX_PREVIEW_POINTS = 1_500


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
    """Run the sole C++ daily backtest kernel and persist its typed result."""
    from src.services.native_backtest_service import run_backtest_native

    return run_backtest_native(
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
        universe_policy=universe_policy,
        existing_run_id=existing_run_id,
        runtime_params_override=runtime_params_override,
        persist_level=persist_level,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
        prepared_dataset=prepared_dataset,
    )


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
        "avg_entry_price": None,
        "entry_trade_date": None,
        "entry_signal_features": None,
        "position_holding_days": None,
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
