from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from src.models.tables import Signal, Strategy, StrategyRun
from src.services.strategy_registry import build_runtime_payload


StrategyHandler = Callable[[Dict[str, Any], Dict[str, Dict[str, Any]]], list["SignalEvent"]]

RECENT_BAR_COUNT = 40
RECENT_BAR_LOOKBACK_DAYS = 90

FEATURE_SNAPSHOT_SQL = """
SELECT
    i.ticker_canonical AS symbol,
    i.asset_type,
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
WHERE curr.dt_ny = :trade_date
"""

RECENT_BAR_HISTORY_SQL = """
SELECT
    i.ticker_canonical AS symbol,
    bars.dt_ny,
    bars.ts_utc AS ts,
    COALESCE(bars.open_fa, bars.open_u) AS open,
    COALESCE(bars.high_fa, bars.high_u) AS high,
    COALESCE(bars.low_fa, bars.low_u) AS low,
    COALESCE(bars.close_fa, bars.close_u) AS close,
    bars.volume,
    feat.atr_14,
    feat.adv_20 AS volume_sma_20,
    feat.ret_20d,
    feat.ret_60d,
    feat.sma_20,
    feat.sma_50
FROM eod_bars bars
JOIN instruments i
  ON i.id = bars.instrument_id
LEFT JOIN daily_features feat
  ON feat.instrument_id = bars.instrument_id
 AND feat.dt_ny = bars.dt_ny
WHERE bars.dt_ny BETWEEN :history_start AND :trade_date
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


@dataclass(slots=True)
class IslandReversalPattern:
    left_gap_idx: int
    breakout_idx: int
    island_low: float
    island_high: float
    breakout_gap_low: float
    breakout_close: float
    breakout_volume: float
    breakout_volume_ratio: float
    left_gap_pct: float
    breakout_gap_pct: float


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
        sql += " AND i.asset_type = 'CS'"
        stmt = text(sql)

    rows = db.execute(stmt, params).mappings().all()
    snapshots: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        snapshots[symbol] = {
            "symbol": symbol,
            "asset_type": row["asset_type"],
            "dt_ny": row["dt_ny"],
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
            "position": 0,
            "recent_bars": [],
        }
    recent_history = _load_recent_bar_history(db, trade_date, symbols)
    for symbol, bars in recent_history.items():
        if symbol in snapshots:
            snapshots[symbol]["recent_bars"] = bars
    return snapshots


def _load_recent_bar_history(
    db: Session,
    trade_date: date,
    symbols: list[str] | None = None,
) -> Dict[str, list[Dict[str, Any]]]:
    sql = RECENT_BAR_HISTORY_SQL
    params: dict[str, Any] = {
        "trade_date": trade_date,
        "history_start": trade_date - timedelta(days=RECENT_BAR_LOOKBACK_DAYS),
    }

    if symbols:
        sql += " AND i.ticker_canonical IN :symbols"
        stmt = text(sql + " ORDER BY i.ticker_canonical, bars.dt_ny").bindparams(
            bindparam("symbols", expanding=True)
        )
        params["symbols"] = [symbol.upper() for symbol in symbols]
    else:
        sql += " AND i.asset_type = 'CS'"
        stmt = text(sql + " ORDER BY i.ticker_canonical, bars.dt_ny")

    rows = db.execute(stmt, params).mappings().all()
    history_by_symbol: Dict[str, list[Dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        history_by_symbol.setdefault(symbol, []).append(_build_history_bar(row))

    return {
        symbol: bars[-RECENT_BAR_COUNT:]
        for symbol, bars in history_by_symbol.items()
        if bars
    }


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


def _resolve_strategy_universe(
    universe_cfg: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[str]:
    if universe_cfg.get("selection_mode") == "all_common_stock" and not universe_cfg.get("symbols"):
        return sorted(
            symbol
            for symbol, snapshot in market_data_by_symbol.items()
            if str(snapshot.get("asset_type", "")).upper() == "CS"
        )
    return universe_cfg.get("symbols") or sorted(market_data_by_symbol.keys())


def _trend_following_handler(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    universe_cfg = params["universe"]
    universe = _resolve_strategy_universe(universe_cfg, market_data_by_symbol)

    fast = signal_cfg["fast_indicator"]
    slow = signal_cfg["slow_indicator"]
    fast_key = f"{fast['kind']}_{fast['window']}"
    slow_key = f"{slow['kind']}_{slow['window']}"

    signals: list[SignalEvent] = []

    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue

        volume = _safe_float(snapshot.get("volume"))
        avg_volume = _safe_float(snapshot.get("volume_sma_20"))
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_history_bar(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "dt_ny": row["dt_ny"],
        "ts": row["ts"] or datetime.now(timezone.utc),
        "open": _safe_float_or_none(row.get("open")),
        "high": _safe_float_or_none(row.get("high")),
        "low": _safe_float_or_none(row.get("low")),
        "close": _safe_float_or_none(row.get("close")),
        "volume": _safe_float_or_none(row.get("volume")),
        "atr_14": _safe_float_or_none(row.get("atr_14")),
        "volume_sma_20": _safe_float_or_none(row.get("volume_sma_20")),
        "ret_20d": _safe_float_or_none(row.get("ret_20d")),
        "ret_60d": _safe_float_or_none(row.get("ret_60d")),
        "sma_20": _safe_float_or_none(row.get("sma_20")),
        "sma_50": _safe_float_or_none(row.get("sma_50")),
    }


def _mean_reversion_handler(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    universe_cfg = params["universe"]
    universe = _resolve_strategy_universe(universe_cfg, market_data_by_symbol)

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


def _island_reversal_handler(
    runtime_strategy: Dict[str, Any],
    market_data_by_symbol: Dict[str, Dict[str, Any]],
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    risk_cfg = params["risk"]
    universe = _resolve_strategy_universe(params["universe"], market_data_by_symbol)

    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue

        recent_bars = snapshot.get("recent_bars") or []
        pattern = _find_latest_island_reversal_pattern(recent_bars, signal_cfg)
        if pattern is None:
            continue

        position = float(snapshot.get("position", 0) or 0.0)
        action, reason, stage = _resolve_island_reversal_action(
            recent_bars=recent_bars,
            pattern=pattern,
            signal_cfg=signal_cfg,
            risk_cfg=risk_cfg,
            position=position,
        )
        if action is None or reason is None:
            continue

        score = (
            pattern.left_gap_pct * 100.0
            + pattern.breakout_gap_pct * 100.0
            + pattern.breakout_volume_ratio
        )
        signals.append(
            SignalEvent(
                strategy_id=runtime_strategy["strategy_id"],
                ts=snapshot.get("ts") or datetime.now(timezone.utc),
                symbol=symbol,
                action=action,
                reason=reason,
                score=score,
                metadata={
                    "close": snapshot.get("close"),
                    "open": snapshot.get("open"),
                    "high": snapshot.get("high"),
                    "low": snapshot.get("low"),
                    "atr_14": snapshot.get("atr_14"),
                    "position": position,
                    "setup": {
                        "stage": stage,
                        "left_gap_trade_date": str(recent_bars[pattern.left_gap_idx]["dt_ny"]),
                        "breakout_trade_date": str(recent_bars[pattern.breakout_idx]["dt_ny"]),
                        "island_low": pattern.island_low,
                        "island_high": pattern.island_high,
                        "breakout_gap_low": pattern.breakout_gap_low,
                        "left_gap_pct": pattern.left_gap_pct,
                        "breakout_gap_pct": pattern.breakout_gap_pct,
                        "breakout_volume_ratio": pattern.breakout_volume_ratio,
                    },
                    "config": {
                        "downtrend_min_drop_pct": signal_cfg["downtrend_min_drop_pct"],
                        "left_gap_min_pct": signal_cfg["left_gap_min_pct"],
                        "right_gap_min_pct": signal_cfg["right_gap_min_pct"],
                        "retest_window": signal_cfg["retest_window"],
                        "support_tolerance_pct": signal_cfg["support_tolerance_pct"],
                    },
                },
            )
        )

    return signals


def _find_latest_island_reversal_pattern(
    recent_bars: list[Dict[str, Any]],
    signal_cfg: Dict[str, Any],
) -> IslandReversalPattern | None:
    if len(recent_bars) < 4:
        return None

    min_island_bars = int(signal_cfg["min_island_bars"])
    max_island_bars = int(signal_cfg["max_island_bars"])
    downtrend_lookback = int(signal_cfg["downtrend_lookback"])
    downtrend_min_drop_pct = float(signal_cfg["downtrend_min_drop_pct"])
    left_gap_min_pct = float(signal_cfg["left_gap_min_pct"])
    right_gap_min_pct = float(signal_cfg["right_gap_min_pct"])
    left_volume_ratio_max = float(signal_cfg["left_volume_ratio_max"])
    right_volume_ratio_min = float(signal_cfg["right_volume_ratio_min"])

    for breakout_idx in range(len(recent_bars) - 1, 1, -1):
        breakout_bar = recent_bars[breakout_idx]
        breakout_open = _safe_float_or_none(breakout_bar.get("open"))
        breakout_close = _safe_float_or_none(breakout_bar.get("close"))
        breakout_low = _safe_float_or_none(breakout_bar.get("low"))
        breakout_volume = _safe_float_or_none(breakout_bar.get("volume"))
        breakout_avg_volume = _safe_float_or_none(breakout_bar.get("volume_sma_20"))
        if (
            breakout_open is None
            or breakout_close is None
            or breakout_low is None
            or breakout_volume is None
            or breakout_avg_volume is None
            or breakout_avg_volume <= 0
            or breakout_close <= breakout_open
        ):
            continue

        breakout_volume_ratio = breakout_volume / breakout_avg_volume
        if breakout_volume_ratio < right_volume_ratio_min:
            continue

        left_start = max(1, breakout_idx - max_island_bars)
        left_end = breakout_idx - min_island_bars + 1
        if left_end <= left_start:
            continue

        for left_gap_idx in range(left_end - 1, left_start - 1, -1):
            left_gap_bar = recent_bars[left_gap_idx]
            pre_gap_bar = recent_bars[left_gap_idx - 1]
            island_bars = recent_bars[left_gap_idx:breakout_idx]
            if len(island_bars) < min_island_bars:
                continue

            left_gap_high = _safe_float_or_none(left_gap_bar.get("high"))
            left_gap_open = _safe_float_or_none(left_gap_bar.get("open"))
            left_gap_close = _safe_float_or_none(left_gap_bar.get("close"))
            left_gap_volume = _safe_float_or_none(left_gap_bar.get("volume"))
            left_gap_avg_volume = _safe_float_or_none(left_gap_bar.get("volume_sma_20"))
            prev_low = _safe_float_or_none(pre_gap_bar.get("low"))
            if (
                left_gap_high is None
                or left_gap_open is None
                or left_gap_close is None
                or left_gap_volume is None
                or left_gap_avg_volume is None
                or left_gap_avg_volume <= 0
                or prev_low is None
                or left_gap_close >= left_gap_open
            ):
                continue

            left_gap_pct = (prev_low - left_gap_high) / prev_low if prev_low > 0 else 0.0
            if left_gap_pct < left_gap_min_pct:
                continue
            if left_gap_volume / left_gap_avg_volume > left_volume_ratio_max:
                continue
            if not _has_island_downtrend_context(
                left_gap_bar,
                downtrend_lookback=downtrend_lookback,
                min_drop_pct=downtrend_min_drop_pct,
            ):
                continue

            island_high = max(
                _safe_float_or_none(bar.get("high")) or float("-inf")
                for bar in island_bars
            )
            island_low = min(
                _safe_float_or_none(bar.get("low")) or float("inf")
                for bar in island_bars
            )
            if island_high == float("-inf") or island_low == float("inf"):
                continue
            if any((_safe_float_or_none(bar.get("high")) or float("inf")) >= prev_low for bar in island_bars):
                continue

            breakout_gap_pct = (breakout_low - island_high) / island_high if island_high > 0 else 0.0
            if breakout_gap_pct < right_gap_min_pct:
                continue

            return IslandReversalPattern(
                left_gap_idx=left_gap_idx,
                breakout_idx=breakout_idx,
                island_low=island_low,
                island_high=island_high,
                breakout_gap_low=breakout_low,
                breakout_close=breakout_close,
                breakout_volume=breakout_volume,
                breakout_volume_ratio=breakout_volume_ratio,
                left_gap_pct=left_gap_pct,
                breakout_gap_pct=breakout_gap_pct,
            )
    return None


def _has_island_downtrend_context(
    left_gap_bar: Dict[str, Any],
    *,
    downtrend_lookback: int,
    min_drop_pct: float,
) -> bool:
    ret_key = "ret_20d" if downtrend_lookback <= 20 else "ret_60d"
    lookback_return = _safe_float_or_none(left_gap_bar.get(ret_key))
    close = _safe_float_or_none(left_gap_bar.get("close"))
    sma_50 = _safe_float_or_none(left_gap_bar.get("sma_50"))
    if lookback_return is not None and lookback_return <= -min_drop_pct:
        return True
    if close is not None and sma_50 is not None and close < sma_50:
        return True
    return False


def _resolve_island_reversal_action(
    *,
    recent_bars: list[Dict[str, Any]],
    pattern: IslandReversalPattern,
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
    position: float,
) -> tuple[Literal["BUY", "SELL", "HOLD"] | None, str | None, str | None]:
    current_idx = len(recent_bars) - 1
    current_bar = recent_bars[current_idx]
    current_close = _safe_float_or_none(current_bar.get("close"))
    current_low = _safe_float_or_none(current_bar.get("low"))
    current_volume = _safe_float_or_none(current_bar.get("volume"))
    current_atr = _safe_float_or_none(current_bar.get("atr_14"))
    support_tolerance_pct = float(signal_cfg["support_tolerance_pct"])
    support_floor = pattern.island_high * (1.0 - support_tolerance_pct)
    hard_stop = pattern.island_low * (1.0 - support_tolerance_pct)

    if position > 0:
        if current_close is not None and current_close < support_floor:
            return "SELL", "price closed below the island gap support", "support_break"
        if current_low is not None and current_low < hard_stop:
            return "SELL", "price broke below the island base low", "base_break"
        if (
            current_close is not None
            and current_atr is not None
            and current_close < pattern.breakout_close - (float(risk_cfg["stop_loss_atr"]) * current_atr)
        ):
            return "SELL", "price hit the ATR stop from the breakout confirmation", "atr_stop"
        return None, None, None

    if current_idx == pattern.breakout_idx:
        return "BUY", "confirmed the island reversal with a volume-backed upside gap", "breakout"

    if current_idx <= pattern.breakout_idx:
        return None, None, None

    retest_window = int(signal_cfg["retest_window"])
    if current_idx > pattern.breakout_idx + retest_window:
        return None, None, None

    if current_low is None or current_close is None or current_volume is None:
        return None, None, None

    if any(
        (_safe_float_or_none(bar.get("close")) or float("inf")) < support_floor
        for bar in recent_bars[pattern.breakout_idx + 1:current_idx]
    ):
        return None, None, None

    touched_gap = current_low <= pattern.breakout_gap_low * (1.0 + support_tolerance_pct)
    held_support = current_low >= support_floor and current_close >= pattern.island_high
    low_volume_retest = current_volume <= pattern.breakout_volume * float(signal_cfg["retest_volume_ratio_max"])
    if touched_gap and held_support and low_volume_retest:
        return "BUY", "low-volume retest held the upside gap after the island reversal", "retest"

    return None, None, None


STRATEGY_HANDLERS: dict[str, StrategyHandler] = {
    "trend": _trend_following_handler,
    "mean_reversion": _mean_reversion_handler,
    "island_reversal": _island_reversal_handler,
}
