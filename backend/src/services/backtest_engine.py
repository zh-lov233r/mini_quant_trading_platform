from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.models.tables import PortfolioSnapshot, Signal, Strategy, StrategyRun, Transaction
from src.services.data_service import get_historical_data
from src.services.stock_basket_service import (
    DEFAULT_COMMON_STOCK_BASKET_NAME,
    load_default_common_stock_symbols,
)
from src.services.strategy_engine import (
    RECENT_BAR_COUNT,
    STRATEGY_HANDLERS,
    SignalEvent,
    required_recent_bar_count_for_runtime,
    required_recent_bar_lookback_days,
)
from src.services.strategy_registry import build_runtime_payload

DEFAULT_COMPARISON_SYMBOLS = ("SPY", "QQQ")


# One SQL query loads the full daily snapshot needed by the backtest loop:
# adjusted close for pricing, volume, and all strategy features used by handlers.
FEATURE_RANGE_SQL = """
SELECT
    i.ticker_canonical AS symbol,
    curr.dt_ny,
    bars.ts_utc AS ts,
    COALESCE(bars.open_fa, bars.open_u) AS open,
    COALESCE(bars.high_fa, bars.high_u) AS high,
    COALESCE(bars.low_fa, bars.low_u) AS low,
    COALESCE(bars.close_fa, bars.close_u) AS close,
    bars.volume,
    curr.atr_14,
    curr.adv_20 AS volume_sma_20,
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
ORDER BY curr.dt_ny, i.ticker_canonical;
"""


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
    existing_run_id: UUID | str | None = None,
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
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")

    runtime = build_runtime_payload(strategy)
    if universe_symbols is not None:
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
    if not symbols:
        raise ValueError("backtest currently requires a non-empty manual symbol universe")

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
        run.config_snapshot = runtime["params"]
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
            config_snapshot=runtime["params"],
        )
        db.add(run)
    db.commit()
    db.refresh(run)

    try:
        recent_bar_count = required_recent_bar_count_for_runtime(runtime)
        recent_bar_lookback_days = required_recent_bar_lookback_days(recent_bar_count)
        history_start_date = start_date - timedelta(days=recent_bar_lookback_days)
        snapshots_by_date = _load_feature_snapshots_by_date(
            db,
            symbols,
            history_start_date,
            end_date,
        )
        if not snapshots_by_date:
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
        holdings: dict[str, float] = {}
        avg_entry_prices: dict[str, float] = {}
        last_prices: dict[str, float] = {}
        trade_count = 0
        signal_count = 0
        max_drawdown = 0.0
        total_fees = 0.0
        total_slippage = 0.0
        pending_signals: list[SignalEvent] = []
        benchmark_base_close: float | None = None
        benchmark_last_close: float | None = None
        benchmark_last_equity: float | None = None
        benchmark_last_return: float | None = None
        benchmark_points = 0
        recent_history_by_symbol: dict[str, deque[dict[str, Any]]] = {}
        ordered_trade_days = sorted(snapshots_by_date)
        warmup_days = [trade_day for trade_day in ordered_trade_days if trade_day < start_date]
        trading_days = [trade_day for trade_day in ordered_trade_days if trade_day >= start_date]
        if not trading_days:
            raise ValueError("no feature snapshots found inside the requested backtest window")

        for trade_day in warmup_days:
            _attach_recent_history(
                snapshots_by_date[trade_day],
                recent_history_by_symbol,
                recent_bar_count=recent_bar_count,
            )

        for trade_day in trading_days:
            day_snapshots = snapshots_by_date[trade_day]
            snapshot_ts = _snapshot_ts(day_snapshots)
            execution_prices = _snapshot_price_map(day_snapshots, "open")
            close_prices = _snapshot_price_map(day_snapshots, "close")
            execution_marks = dict(last_prices)
            execution_marks.update(execution_prices)

            cash_state = {"cash": cash}
            sell_stats = _apply_sell_signals(
                db=db,
                strategy=strategy,
                run=run,
                signals=pending_signals,
                holdings=holdings,
                avg_entry_prices=avg_entry_prices,
                execution_prices=execution_prices,
                execution_snapshots=day_snapshots,
                cash_ref=cash_state,
                cost_config=cost_config,
            )
            trade_count += sell_stats.trade_count
            total_fees += sell_stats.total_fees
            total_slippage += sell_stats.total_slippage

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
                execution_prices=execution_prices,
                execution_snapshots=day_snapshots,
                cash_ref=cash_state,
                equity_before=equity_before,
                max_positions=max_positions,
                position_size_pct=position_size_pct,
                cost_config=cost_config,
            )
            trade_count += buy_stats.trade_count
            total_fees += buy_stats.total_fees
            total_slippage += buy_stats.total_slippage
            cash = float(cash_state["cash"])

            _inject_backtest_positions(day_snapshots, holdings, avg_entry_prices)
            _attach_recent_history(
                day_snapshots,
                recent_history_by_symbol,
                recent_bar_count=recent_bar_count,
            )

            signals = handler(runtime, day_snapshots)
            pending_signals = signals
            signal_count += len(signals)
            for event in signals:
                db.add(
                    Signal(
                        run_id=run.id,
                        strategy_id=strategy.id,
                        ts=event.ts,
                        symbol=event.symbol,
                        signal=event.action,
                        score=event.score,
                        reason=event.reason,
                        features=event.metadata,
                    )
                )

            signal_by_symbol = {event.symbol: event for event in signals}
            last_prices.update(close_prices)
            equity = _portfolio_equity(cash, holdings, last_prices)
            peak_equity = max(peak_equity, equity)
            drawdown = 0.0 if peak_equity <= 0 else (peak_equity - equity) / peak_equity
            max_drawdown = max(max_drawdown, drawdown)

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

            db.add(
                PortfolioSnapshot(
                    run_id=run.id,
                    ts=snapshot_ts,
                    cash=cash,
                    equity=equity,
                    gross_exposure=_gross_exposure(holdings, last_prices),
                    net_exposure=_gross_exposure(holdings, last_prices),
                    drawdown=drawdown,
                    positions=_serialize_positions(holdings, avg_entry_prices, last_prices, signal_by_symbol),
                    metrics={
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
                    },
                )
            )

        final_equity = _portfolio_equity(cash, holdings, last_prices)
        comparison_curves = {
            symbol: curve_state["points"]
            for symbol, curve_state in comparison_curve_states.items()
            if curve_state["points"]
        }
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.final_equity = final_equity
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
            "symbols_loaded": sorted(snapshots_by_date[next(iter(snapshots_by_date))].keys()),
            "strategy_type": runtime["strategy_type"],
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
            "excess_return": (
                ((final_equity / initial_cash) - 1) - benchmark_last_return
                if initial_cash and benchmark_last_return is not None
                else None
            ),
            "comparison_curves": comparison_curves,
        }
        db.commit()
        db.refresh(run)

        return BacktestResult(
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
    except Exception as exc:
        db.rollback()
        failed_run = db.get(StrategyRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
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


def _load_feature_snapshots_by_date(
    db: Session,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict[date, dict[str, dict[str, Any]]]:
    """Load all per-symbol daily inputs once, then group them by trade date.

    The backtest loop works on one in-memory snapshot per day so handlers can stay
    stateless and identical to the live signal-generation path.
    """
    stmt = text(FEATURE_RANGE_SQL).bindparams(bindparam("symbols", expanding=True))
    rows = db.execute(
        stmt,
        {
            "symbols": [symbol.upper() for symbol in symbols],
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().all()

    snapshots_by_date: dict[date, dict[str, dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        trade_date = row["dt_ny"]
        snapshots_by_date.setdefault(trade_date, {})[symbol] = {
            "symbol": symbol,
            "dt_ny": trade_date,
            "ts": row["ts"] or datetime.now(timezone.utc),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "atr_14": row["atr_14"],
            "volume_sma_20": row["volume_sma_20"],
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
    return snapshots_by_date


def _inject_backtest_positions(
    day_snapshots: dict[str, dict[str, Any]],
    holdings: dict[str, float],
    avg_entry_prices: dict[str, float],
) -> None:
    """Expose current position size to handlers that need state-aware exits."""
    for symbol, snapshot in day_snapshots.items():
        snapshot["position"] = float(holdings.get(symbol, 0.0))
        snapshot["avg_entry_price"] = avg_entry_prices.get(symbol)


def _attach_recent_history(
    day_snapshots: dict[str, dict[str, Any]],
    history_by_symbol: dict[str, deque[dict[str, Any]]],
    *,
    recent_bar_count: int = RECENT_BAR_COUNT,
) -> None:
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
    holdings: dict[str, float],
    avg_entry_prices: dict[str, float],
    execution_prices: dict[str, float],
    execution_snapshots: dict[str, dict[str, Any]],
    cash_ref: dict[str, float],
    cost_config: BacktestCostConfig,
) -> ExecutionStats:
    """Close existing long positions for queued SELL signals on the next session open."""
    stats = ExecutionStats()
    for event in signals:
        if event.action != "SELL":
            continue
        qty = holdings.get(event.symbol, 0.0)
        price = execution_prices.get(event.symbol)
        execution_snapshot = execution_snapshots.get(event.symbol)
        if qty <= 0 or price is None or execution_snapshot is None:
            continue
        # SELL receives a worse price than the mark because of slippage.
        execution_price = _sell_execution_price(float(price), cost_config)
        notional = qty * execution_price
        fee = _commission_for_notional(notional, cost_config)
        proceeds = max(notional - fee, 0.0)
        slippage_cost = qty * max(float(price) - execution_price, 0.0)
        cash_ref["cash"] += proceeds
        del holdings[event.symbol]
        avg_entry_prices.pop(event.symbol, None)
        stats.trade_count += 1
        stats.total_fees += fee
        stats.total_slippage += slippage_cost
        db.add(
            Transaction(
                strategy_id=strategy.id,
                run_id=run.id,
                ts=_execution_ts(execution_snapshot),
                symbol=event.symbol,
                side="SELL",
                qty=qty,
                price=execution_price,
                fee=fee,
                order_id=None,
                meta={
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
            )
        )
    return stats


def _apply_buy_signals(
    *,
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    signals: list[SignalEvent],
    holdings: dict[str, float],
    avg_entry_prices: dict[str, float],
    execution_prices: dict[str, float],
    execution_snapshots: dict[str, dict[str, Any]],
    cash_ref: dict[str, float],
    equity_before: float,
    max_positions: int,
    position_size_pct: float,
    cost_config: BacktestCostConfig,
) -> ExecutionStats:
    """Open new long positions for queued BUY signals on the next session open."""
    stats = ExecutionStats()
    for event in signals:
        if event.action != "BUY":
            continue
        if event.symbol in holdings:
            continue
        if len(holdings) >= max_positions:
            continue

        price = execution_prices.get(event.symbol)
        execution_snapshot = execution_snapshots.get(event.symbol)
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
        holdings[event.symbol] = qty
        avg_entry_prices[event.symbol] = execution_price
        stats.trade_count += 1
        stats.total_fees += fee
        stats.total_slippage += slippage_cost
        db.add(
            Transaction(
                strategy_id=strategy.id,
                run_id=run.id,
                ts=_execution_ts(execution_snapshot),
                symbol=event.symbol,
                side="BUY",
                qty=qty,
                price=execution_price,
                fee=fee,
                order_id=None,
                meta={
                    "reason": event.reason,
                    "source": "backtest",
                    "signal_ts": event.ts.isoformat(),
                    "execution_trade_date": _execution_trade_date(execution_snapshot),
                    "reference_price": float(price),
                    "slippage_bps": cost_config.slippage_bps,
                    "slippage_cost": slippage_cost,
                    "gross_notional": gross_notional,
                    "net_cash_flow": -total_cash_out,
                },
            )
        )
    return stats


def _portfolio_equity(cash: float, holdings: dict[str, float], last_prices: dict[str, float]) -> float:
    """Mark the portfolio to market using the latest daily close snapshot."""
    return cash + _gross_exposure(holdings, last_prices)


def _gross_exposure(holdings: dict[str, float], last_prices: dict[str, float]) -> float:
    """Compute gross long exposure from current holdings."""
    return sum(float(qty) * float(last_prices.get(symbol, 0.0)) for symbol, qty in holdings.items())


def _snapshot_price_map(
    day_snapshots: dict[str, dict[str, Any]],
    field: Literal["open", "close"],
) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, snapshot in day_snapshots.items():
        price = snapshot.get(field)
        if price is None:
            continue
        prices[symbol] = float(price)
    return prices


def _serialize_positions(
    holdings: dict[str, float],
    avg_entry_prices: dict[str, float],
    last_prices: dict[str, float],
    signal_by_symbol: dict[str, Any],
) -> dict[str, dict[str, float | str | None]]:
    """Persist a lightweight position snapshot for later review and debugging."""
    payload: dict[str, dict[str, float | str | None]] = {}
    for symbol, qty in holdings.items():
        close_px = float(last_prices.get(symbol, 0.0))
        event = signal_by_symbol.get(symbol)
        payload[symbol] = {
            "qty": float(qty),
            "avg_entry_price": avg_entry_prices.get(symbol),
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
