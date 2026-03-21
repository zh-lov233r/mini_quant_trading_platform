from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from src.models.tables import Signal, Strategy, StrategyRun
from src.services.strategy_registry import build_runtime_payload


StrategyHandler = Callable[[Dict[str, Any], Dict[str, Dict[str, Any]]], list["SignalEvent"]]


FEATURE_SNAPSHOT_SQL = """
SELECT
    i.ticker_canonical AS symbol,
    curr.dt_ny,
    bars.ts_utc AS ts,
    COALESCE(bars.close_fa, bars.close_u) AS close,
    bars.volume,
    curr.atr_14,
    curr.adv_20 AS volume_sma_20,
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
WHERE curr.dt_ny = :trade_date
"""


@dataclass(slots=True)
class SignalEvent:
    strategy_id: str
    ts: datetime
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    reason: str
    score: float | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PersistedSignalRun:
    strategy_id: str
    run_id: str
    mode: str
    trade_date: date
    signal_count: int


def list_active_strategies(db: Session) -> list[Strategy]:
    return db.execute(
        select(Strategy)
        .where(Strategy.status == "active")
        .order_by(Strategy.created_at.asc(), Strategy.version.asc())
    ).scalars().all()


def load_feature_market_data(
    db: Session,
    trade_date: date,
    symbols: list[str] | None = None,
) -> Dict[str, Dict[str, Any]]:
    sql = FEATURE_SNAPSHOT_SQL
    params: dict[str, Any] = {"trade_date": trade_date}

    if symbols:
        sql += " AND i.ticker_canonical IN :symbols"
        stmt = text(sql).bindparams(bindparam("symbols", expanding=True))
        params["symbols"] = [symbol.upper() for symbol in symbols]
    else:
        stmt = text(sql)

    rows = db.execute(stmt, params).mappings().all()
    snapshots: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        snapshots[symbol] = {
            "symbol": symbol,
            "dt_ny": row["dt_ny"],
            "ts": row["ts"] or datetime.now(timezone.utc),
            "close": row["close"],
            "volume": row["volume"],
            "atr_14": row["atr_14"],
            "volume_sma_20": row["volume_sma_20"],
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
            "position": 0,
        }
    return snapshots


def generate_signals_for_trade_date(
    db: Session,
    trade_date: date,
    symbols: list[str] | None = None,
) -> list[SignalEvent]:
    snapshots = load_feature_market_data(db, trade_date, symbols)
    return generate_signals(db, snapshots)


def generate_and_persist_signals_for_trade_date(
    db: Session,
    trade_date: date,
    *,
    mode: Literal["paper", "live"] = "paper",
    symbols: list[str] | None = None,
) -> list[PersistedSignalRun]:
    snapshots = load_feature_market_data(db, trade_date, symbols)
    results: list[PersistedSignalRun] = []

    active_strategies = list_active_strategies(db)
    started_at = datetime.now(timezone.utc)

    for strategy in active_strategies:
        runtime = build_runtime_payload(strategy)
        if not runtime["engine_ready"]:
            continue

        handler = STRATEGY_HANDLERS.get(runtime["strategy_type"])
        if handler is None:
            continue

        strategy_signals = handler(runtime, snapshots)
        run = _get_or_create_signal_run(
            db=db,
            strategy=strategy,
            mode=mode,
            trade_date=trade_date,
            config_snapshot=runtime["params"],
            started_at=started_at,
        )
        _replace_signals_for_run(db, run, strategy, strategy_signals)

        run.status = "completed"
        run.started_at = run.started_at or started_at
        run.finished_at = datetime.now(timezone.utc)
        run.summary_metrics = {
            "signal_count": len(strategy_signals),
            "symbols_requested": runtime["params"]["universe"].get("symbols", []),
            "symbols_signaled": sorted({event.symbol for event in strategy_signals}),
        }
        db.flush()

        results.append(
            PersistedSignalRun(
                strategy_id=str(strategy.id),
                run_id=str(run.id),
                mode=mode,
                trade_date=trade_date,
                signal_count=len(strategy_signals),
            )
        )

    db.commit()
    return results


def run_trend_following_strategy(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    return _trend_following_handler(runtime_strategy, market_data_by_symbol)


def _get_or_create_signal_run(
    db: Session,
    strategy: Strategy,
    mode: Literal["paper", "live"],
    trade_date: date,
    config_snapshot: Dict[str, Any],
    started_at: datetime,
) -> StrategyRun:
    existing = db.execute(
        select(StrategyRun)
        .where(StrategyRun.strategy_id == strategy.id)
        .where(StrategyRun.strategy_version == strategy.version)
        .where(StrategyRun.mode == mode)
        .where(StrategyRun.window_start == trade_date)
        .where(StrategyRun.window_end == trade_date)
        .order_by(StrategyRun.requested_at.desc())
    ).scalars().first()

    if existing is not None:
        existing.status = "running"
        existing.started_at = started_at
        existing.finished_at = None
        existing.config_snapshot = config_snapshot
        existing.error_message = None
        db.flush()
        return existing

    run = StrategyRun(
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        mode=mode,
        status="running",
        started_at=started_at,
        window_start=trade_date,
        window_end=trade_date,
        config_snapshot=config_snapshot,
    )
    db.add(run)
    db.flush()
    return run


def _replace_signals_for_run(
    db: Session,
    run: StrategyRun,
    strategy: Strategy,
    events: list[SignalEvent],
) -> None:
    db.execute(delete(Signal).where(Signal.run_id == run.id))
    for event in events:
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


def generate_signals(
    db: Session,
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    """
    market_data_by_symbol 的建议结构：
    {
        "AAPL": {
            "close": 212.3,
            "ema_15": 210.1,
            "sma_200": 205.8,
            "prev_fast": 204.5,
            "prev_slow": 205.1,
            "volume": 1500000,
            "volume_sma_20": 900000,
            "atr_14": 4.2,
            "position": 0
        }
    }
    """
    signals: list[SignalEvent] = []

    for strategy in list_active_strategies(db):
        runtime = build_runtime_payload(strategy)
        if not runtime["engine_ready"]:
            continue

        handler = STRATEGY_HANDLERS.get(runtime["strategy_type"])
        if handler is None:
            continue

        signals.extend(handler(runtime, market_data_by_symbol))

    return signals


def trend_following(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    return _trend_following_handler(runtime_strategy, market_data_by_symbol)


def _trend_following_handler(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    universe = params["universe"]["symbols"] or sorted(market_data_by_symbol.keys())

    fast = signal_cfg["fast_indicator"]
    slow = signal_cfg["slow_indicator"]
    fast_key = f"{fast['kind']}_{fast['window']}"
    slow_key = f"{slow['kind']}_{slow['window']}"

    signals: list[SignalEvent] = []

    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue

        volume = float(snapshot.get("volume", 0))
        avg_volume = float(snapshot.get("volume_sma_20", 0))
        if avg_volume <= 0 or volume < signal_cfg["volume_multiplier"] * avg_volume:
            continue

        fast_now = snapshot.get(fast_key)
        slow_now = snapshot.get(slow_key)
        prev_fast = snapshot.get(f"prev_{fast_key}", snapshot.get("prev_fast"))
        prev_slow = snapshot.get(f"prev_{slow_key}", snapshot.get("prev_slow"))
        if None in {fast_now, slow_now, prev_fast, prev_slow}:
            continue

        action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
        reason = "trend unchanged"

        if prev_fast <= prev_slow and fast_now > slow_now:
            action = "BUY"
            reason = f"{fast_key} crossed above {slow_key}"
        elif prev_fast >= prev_slow and fast_now < slow_now:
            action = "SELL"
            reason = f"{fast_key} crossed below {slow_key}"
        else:
            continue

        signals.append(
            SignalEvent(
                strategy_id=runtime_strategy["strategy_id"],
                ts=snapshot.get("ts") or datetime.now(timezone.utc),
                symbol=symbol,
                action=action,
                reason=reason,
                score=float(abs(fast_now - slow_now)),
                metadata={
                    "close": snapshot.get("close"),
                    "atr_14": snapshot.get("atr_14"),
                    "position": snapshot.get("position", 0),
                    "config": {
                        "volume_multiplier": signal_cfg["volume_multiplier"],
                        "atr_multiplier": signal_cfg["atr_multiplier"],
                    },
                },
            )
        )

    return signals


def _mean_reversion_handler(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    universe = params["universe"]["symbols"] or sorted(market_data_by_symbol.keys())

    lookback = int(signal_cfg["lookback_window"])
    zscore_key = f"zscore_{lookback}"
    zscore_entry = float(signal_cfg["zscore_entry"])
    zscore_exit = float(signal_cfg["zscore_exit"])

    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue

        zscore = snapshot.get(zscore_key)
        if zscore is None:
            continue

        action: Literal["BUY", "SELL", "HOLD"] | None = None
        reason: str | None = None
        position = float(snapshot.get("position", 0) or 0)

        if position > 0 and zscore >= -zscore_exit:
            action = "SELL"
            reason = f"{zscore_key} reverted above exit threshold"
        elif position < 0 and zscore <= zscore_exit:
            action = "BUY"
            reason = f"{zscore_key} reverted below exit threshold"
        elif zscore <= -zscore_entry:
            action = "BUY"
            reason = f"{zscore_key} below negative entry threshold"
        elif zscore >= zscore_entry:
            action = "SELL"
            reason = f"{zscore_key} above positive entry threshold"

        if action is None or reason is None:
            continue

        signals.append(
            SignalEvent(
                strategy_id=runtime_strategy["strategy_id"],
                ts=snapshot.get("ts") or datetime.now(timezone.utc),
                symbol=symbol,
                action=action,
                reason=reason,
                score=float(abs(zscore)),
                metadata={
                    "close": snapshot.get("close"),
                    "atr_14": snapshot.get("atr_14"),
                    "rsi_14": snapshot.get("rsi_14"),
                    zscore_key: zscore,
                    "position": position,
                    "config": {
                        "lookback_window": lookback,
                        "zscore_entry": zscore_entry,
                        "zscore_exit": zscore_exit,
                    },
                },
            )
        )

    return signals


STRATEGY_HANDLERS: dict[str, StrategyHandler] = {
    "trend": _trend_following_handler,
    "mean_reversion": _mean_reversion_handler,
}
