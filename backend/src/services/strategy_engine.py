from __future__ import annotations

"""Signal generation engine shared by daily runs, paper trading, and backtests.

The module is organized from top-level orchestration helpers down to individual
strategy handlers and their supporting pattern-detection utilities.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Literal

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from src.models.tables import Signal, Strategy, StrategyRun
from src.services.strategy_registry import build_runtime_payload


RuntimeStrategy = Dict[str, Any]
MarketSnapshot = Dict[str, Any]
MarketDataBySymbol = Dict[str, MarketSnapshot]
HistoryBar = Dict[str, Any]
StrategyHandler = Callable[[RuntimeStrategy, MarketDataBySymbol], list["SignalEvent"]]

RECENT_BAR_COUNT = 40
RECENT_BAR_LOOKBACK_DAYS = 90
ISLAND_REVERSAL_STOP_ATR_WINDOW = 20
DOUBLE_BOTTOM_STOP_ATR_WINDOW = 20

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
    """Normalized signal payload emitted by a strategy handler."""

    strategy_id: str
    ts: datetime
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    reason: str
    score: float | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PersistedSignalRun:
    """Summary of one strategy run that was written to the database."""

    strategy_id: str
    run_id: str
    mode: str
    trade_date: date
    signal_count: int


@dataclass(slots=True)
class IslandReversalPattern:
    """Compact representation of a detected island-reversal setup."""

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


@dataclass(slots=True)
class DoubleBottomPattern:
    """Compact representation of a confirmed conservative double-bottom setup."""

    left_bottom_idx: int
    neckline_idx: int
    right_bottom_idx: int
    breakout_idx: int
    left_bottom_low: float
    right_bottom_low: float
    neckline_price: float
    breakout_close: float
    breakout_volume: float
    breakout_volume_ratio: float
    bottom_distance_pct: float
    rebound_up_day_ratio: float


@dataclass(slots=True)
class DoubleBottomLeftCandidate:
    """Confirmed left-bottom pivot that can be paired with a later right-bottom."""

    left_bottom_idx: int
    left_bottom_low: float


# ============================================================================
# Public orchestration API
# ============================================================================

# Fetch strategies that are currently eligible to participate in signal runs.
# Input: active SQLAlchemy session.
# Output: active Strategy rows ordered deterministically by created_at/version.
def list_active_strategies(db: Session) -> list[Strategy]:
    return db.execute(
        select(Strategy)
        .where(Strategy.status == "active")
        .order_by(Strategy.created_at.asc(), Strategy.version.asc())
    ).scalars().all()


# Build the runtime market snapshot map for one NY trade date.
# Input: db session, trade date, and an optional canonical symbol filter.
# Output: symbol -> snapshot dict with OHLCV, indicators, prev indicators, and recent_bars.
def load_feature_market_data(
    db: Session,
    trade_date: date,
    symbols: list[str] | None = None,
    *,
    recent_bar_count: int = RECENT_BAR_COUNT,
    recent_bar_lookback_days: int = RECENT_BAR_LOOKBACK_DAYS,
) -> MarketDataBySymbol:
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
    snapshots: MarketDataBySymbol = {}
    for row in rows:
        snapshot = _build_feature_snapshot(row)
        snapshots[snapshot["symbol"]] = snapshot

    recent_history = _load_recent_bar_history(
        db,
        trade_date,
        symbols,
        recent_bar_count=recent_bar_count,
        recent_bar_lookback_days=recent_bar_lookback_days,
    )
    for symbol, bars in recent_history.items():
        if symbol in snapshots:
            snapshots[symbol]["recent_bars"] = bars
    return snapshots


# Convenience entrypoint for in-memory signal generation on a single trade date.
# Input: db session, trade date, and an optional canonical symbol filter.
# Output: flat SignalEvent list across all active engine-ready strategies.
def generate_signals_for_trade_date(
    db: Session,
    trade_date: date,
    symbols: list[str] | None = None,
) -> list[SignalEvent]:
    active_runtimes = _list_engine_ready_runtimes(db)
    recent_bar_count, recent_bar_lookback_days = _recent_history_window_for_runtimes(active_runtimes)
    snapshots = load_feature_market_data(
        db,
        trade_date,
        symbols,
        recent_bar_count=recent_bar_count,
        recent_bar_lookback_days=recent_bar_lookback_days,
    )
    return generate_signals(db, snapshots)


# Run all active engine-ready strategies and persist one StrategyRun per strategy/date.
# Input: db session, trade date, execution mode, and an optional canonical symbol filter.
# Output: persisted run summaries for strategies that were executed and committed.
def generate_and_persist_signals_for_trade_date(
    db: Session,
    trade_date: date,
    *,
    mode: Literal["paper", "live"] = "paper",
    symbols: list[str] | None = None,
) -> list[PersistedSignalRun]:
    active_strategies = list_active_strategies(db)
    active_runtimes = _list_engine_ready_runtimes_from_strategies(active_strategies)
    recent_bar_count, recent_bar_lookback_days = _recent_history_window_for_runtimes(active_runtimes)
    snapshots = load_feature_market_data(
        db,
        trade_date,
        symbols,
        recent_bar_count=recent_bar_count,
        recent_bar_lookback_days=recent_bar_lookback_days,
    )
    results: list[PersistedSignalRun] = []
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


# Run all active engine-ready strategies against an already-built snapshot map.
# Input: db session plus market_data_by_symbol snapshots that include indicators/position state.
# Output: combined SignalEvent list without creating StrategyRun or Signal database records.
def generate_signals(
    db: Session,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
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


# Backward-compatible wrapper for callers that still import the old public helper.
# Input: runtime strategy payload plus a symbol -> snapshot market map.
# Output: the same SignalEvent list produced by the trend-following handler.
def trend_following(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _trend_following_handler(runtime_strategy, market_data_by_symbol)


# Legacy compatibility wrapper kept for older imports/tests.
# Input: runtime strategy payload plus a symbol -> snapshot market map.
# Output: the same SignalEvent list produced by the trend-following handler.
def run_trend_following_strategy(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _trend_following_handler(runtime_strategy, market_data_by_symbol)


# ============================================================================
# Database and payload helpers
# ============================================================================

# Load recent daily history used by pattern-aware handlers such as island reversal.
# Input: db session, trade date, and an optional canonical symbol filter.
# Output: symbol -> last RECENT_BAR_COUNT history bars with OHLCV and context indicators.
def _load_recent_bar_history(
    db: Session,
    trade_date: date,
    symbols: list[str] | None = None,
    *,
    recent_bar_count: int = RECENT_BAR_COUNT,
    recent_bar_lookback_days: int = RECENT_BAR_LOOKBACK_DAYS,
) -> Dict[str, list[HistoryBar]]:
    sql = RECENT_BAR_HISTORY_SQL
    params: dict[str, Any] = {
        "trade_date": trade_date,
        "history_start": trade_date - timedelta(days=recent_bar_lookback_days),
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
    history_by_symbol: Dict[str, list[HistoryBar]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        history_by_symbol.setdefault(symbol, []).append(_build_history_bar(row))

    return {
        symbol: bars[-recent_bar_count:]
        for symbol, bars in history_by_symbol.items()
        if bars
    }


def _safe_positive_int(value: Any, fallback: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return fallback
    return normalized if normalized > 0 else fallback


def required_recent_bar_count_for_runtime(runtime_strategy: RuntimeStrategy) -> int:
    recent_bar_count = RECENT_BAR_COUNT
    strategy_type = runtime_strategy.get("strategy_type")
    signal_cfg = runtime_strategy.get("params", {}).get("signal", {}) or {}

    if strategy_type == "island_reversal":
        downtrend_lookback = _safe_positive_int(signal_cfg.get("downtrend_lookback"), 0)
        max_island_bars = _safe_positive_int(signal_cfg.get("max_island_bars"), 0)
        retest_window = _safe_positive_int(signal_cfg.get("retest_window"), 0)
        return max(
            recent_bar_count,
            downtrend_lookback + max_island_bars + retest_window + 2,
        )

    if strategy_type == "double_bottom":
        downtrend_lookback = _safe_positive_int(signal_cfg.get("downtrend_lookback"), 0)
        max_bottom_spacing = _safe_positive_int(signal_cfg.get("max_bottom_spacing"), 0)
        left_bottom_before_bars = _safe_positive_int(signal_cfg.get("left_bottom_before_bars"), 0)
        max_breakout_wait = _safe_positive_int(signal_cfg.get("max_breakout_bars_after_right_bottom"), 0)
        retest_window = _safe_positive_int(signal_cfg.get("retest_window"), 0)
        return max(
            recent_bar_count,
            downtrend_lookback + max_bottom_spacing + left_bottom_before_bars + max_breakout_wait + retest_window + 10,
        )

    return recent_bar_count


def required_recent_bar_lookback_days(recent_bar_count: int) -> int:
    estimated_calendar_days = int(recent_bar_count * 1.8) + 30
    return max(RECENT_BAR_LOOKBACK_DAYS, estimated_calendar_days)


def _recent_history_window_for_runtimes(
    runtimes: list[RuntimeStrategy],
) -> tuple[int, int]:
    if not runtimes:
        return RECENT_BAR_COUNT, RECENT_BAR_LOOKBACK_DAYS

    recent_bar_count = max(
        required_recent_bar_count_for_runtime(runtime)
        for runtime in runtimes
    )
    return recent_bar_count, required_recent_bar_lookback_days(recent_bar_count)


def _list_engine_ready_runtimes_from_strategies(strategies: list[Strategy]) -> list[RuntimeStrategy]:
    runtimes: list[RuntimeStrategy] = []
    for strategy in strategies:
        runtime = build_runtime_payload(strategy)
        if not runtime["engine_ready"]:
            continue
        if STRATEGY_HANDLERS.get(runtime["strategy_type"]) is None:
            continue
        runtimes.append(runtime)
    return runtimes


def _list_engine_ready_runtimes(db: Session) -> list[RuntimeStrategy]:
    return _list_engine_ready_runtimes_from_strategies(list_active_strategies(db))


# Reuse the existing one-day StrategyRun for this strategy/mode/date or create a fresh one.
# Input: strategy ORM row, execution mode, trade date, config snapshot, and run start timestamp.
# Output: StrategyRun ORM object left in "running" state inside the current transaction.
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


# Replace all persisted Signal rows for one run with the current event list.
# Input: StrategyRun row, owning Strategy row, and normalized SignalEvent objects.
# Output: None; the current transaction is mutated via DELETE + INSERT side effects.
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


# Normalize a strategy's universe config into the concrete symbols to scan today.
# Input: universe config and the current symbol -> snapshot market map.
# Output: sorted symbol list based on explicit symbols or the configured stock universe.
def _resolve_strategy_universe(
    universe_cfg: Dict[str, Any],
    market_data_by_symbol: MarketDataBySymbol,
) -> list[str]:
    if universe_cfg.get("selection_mode") == "all_common_stock" and not universe_cfg.get("symbols"):
        return sorted(
            symbol
            for symbol, snapshot in market_data_by_symbol.items()
            if str(snapshot.get("asset_type", "")).upper() == "CS"
        )
    return universe_cfg.get("symbols") or sorted(market_data_by_symbol.keys())


# Convert one FEATURE_SNAPSHOT_SQL row into the runtime snapshot shape used by handlers.
# Input: SQLAlchemy mapping row with current-day bars plus current/previous indicators.
# Output: one per-symbol snapshot dict with empty recent_bars ready to be backfilled.
def _build_feature_snapshot(row: Dict[str, Any]) -> MarketSnapshot:
    symbol = str(row["symbol"]).upper()
    return {
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
        "avg_entry_price": None,
        "entry_trade_date": None,
        "position_holding_days": None,
        "entry_signal_features": None,
        "recent_bars": [],
    }


# Best-effort scalar conversion when missing/non-numeric input should fall back to a number.
# Input: arbitrary value and an optional numeric default.
# Output: float(value) when possible, otherwise the provided default.
def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Best-effort scalar conversion when callers need to preserve "missing" as None.
# Input: arbitrary value.
# Output: float(value) when possible, otherwise None.
def _safe_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_date_or_none(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def _resolve_position_holding_days(snapshot: MarketSnapshot) -> int | None:
    raw_holding_days = snapshot.get("position_holding_days")
    if raw_holding_days is not None:
        try:
            holding_days = int(raw_holding_days)
        except (TypeError, ValueError):
            holding_days = None
        else:
            if holding_days >= 0:
                return holding_days

    entry_trade_date = _safe_date_or_none(snapshot.get("entry_trade_date"))
    current_trade_date = _safe_date_or_none(snapshot.get("dt_ny"))
    if entry_trade_date is None or current_trade_date is None or current_trade_date < entry_trade_date:
        return None

    recent_bars = snapshot.get("recent_bars")
    if isinstance(recent_bars, list):
        session_dates = sorted(
            {
                bar_date
                for bar in recent_bars
                if isinstance(bar, dict)
                for bar_date in [_safe_date_or_none(bar.get("dt_ny"))]
                if bar_date is not None and entry_trade_date <= bar_date <= current_trade_date
            }
        )
        if session_dates and session_dates[0] == entry_trade_date and session_dates[-1] == current_trade_date:
            return max(len(session_dates) - 1, 0)

    return max((current_trade_date - entry_trade_date).days, 0)


def _true_range(
    high_p: float | None,
    low_p: float | None,
    prev_close: float | None,
) -> float | None:
    if high_p is None or low_p is None:
        return None
    if prev_close is None:
        return high_p - low_p
    return max(
        high_p - low_p,
        abs(high_p - prev_close),
        abs(low_p - prev_close),
    )


def _compute_recent_atr(recent_bars: list[HistoryBar], window: int) -> float | None:
    if window <= 0 or len(recent_bars) < window:
        return None

    true_ranges: list[float] = []
    prev_close: float | None = None
    for idx, bar in enumerate(recent_bars):
        if idx > 0:
            prev_close = _safe_float_or_none(recent_bars[idx - 1].get("close"))
        tr = _true_range(
            _safe_float_or_none(bar.get("high")),
            _safe_float_or_none(bar.get("low")),
            prev_close,
        )
        if tr is not None:
            true_ranges.append(tr)

    if len(true_ranges) < window:
        return None

    window_true_ranges = true_ranges[-window:]
    return sum(window_true_ranges) / float(window)


# Convert one RECENT_BAR_HISTORY_SQL row into the history-bar shape used by pattern scanners.
# Input: SQLAlchemy mapping row with historical OHLCV plus a few trend/volume indicators.
# Output: compact per-day dict stored under snapshot["recent_bars"].
def _build_history_bar(row: Dict[str, Any]) -> HistoryBar:
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


# ============================================================================
# Strategy handlers
# ============================================================================

# Evaluate moving-average crossover signals with a volume confirmation filter.
# Input: runtime strategy payload plus the symbol -> snapshot market map.
# Output: BUY/SELL SignalEvent objects for symbols whose fast/slow crossover changed today.
def _trend_following_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    risk_cfg = params["risk"]
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

        position = float(snapshot.get("position", 0) or 0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))
        close_price = _safe_float_or_none(snapshot.get("close"))
        current_atr = _safe_float_or_none(snapshot.get("atr_14"))
        stop_loss_pct = float(risk_cfg["stop_loss_pct"])

        if (
            position > 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price <= avg_entry_price * (1.0 - stop_loss_pct)
        ):
            signals.append(
                SignalEvent(
                    strategy_id=runtime_strategy["strategy_id"],
                    ts=snapshot.get("ts") or datetime.now(timezone.utc),
                    symbol=symbol,
                    action="SELL",
                    reason="price fell below the fixed stop-loss threshold",
                    score=float(abs((avg_entry_price - close_price) / avg_entry_price)),
                    metadata={
                        "close": close_price,
                        "atr_14": current_atr,
                        "position": position,
                        "avg_entry_price": avg_entry_price,
                        "config": {
                            "volume_multiplier": signal_cfg["volume_multiplier"],
                            "atr_multiplier": signal_cfg["atr_multiplier"],
                            "stop_loss_pct": stop_loss_pct,
                            "stop_loss_atr": risk_cfg["stop_loss_atr"],
                            "take_profit_atr": risk_cfg["take_profit_atr"],
                        },
                    },
                )
            )
            continue

        if (
            position > 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and current_atr is not None
            and current_atr > 0
            and close_price <= avg_entry_price - (float(risk_cfg["stop_loss_atr"]) * current_atr)
        ):
            signals.append(
                SignalEvent(
                    strategy_id=runtime_strategy["strategy_id"],
                    ts=snapshot.get("ts") or datetime.now(timezone.utc),
                    symbol=symbol,
                    action="SELL",
                    reason="price hit the ATR stop-loss threshold",
                    score=float(abs((avg_entry_price - close_price) / avg_entry_price)),
                    metadata={
                        "close": close_price,
                        "atr_14": current_atr,
                        "position": position,
                        "avg_entry_price": avg_entry_price,
                        "config": {
                            "volume_multiplier": signal_cfg["volume_multiplier"],
                            "atr_multiplier": signal_cfg["atr_multiplier"],
                            "stop_loss_pct": stop_loss_pct,
                            "stop_loss_atr": risk_cfg["stop_loss_atr"],
                            "take_profit_atr": risk_cfg["take_profit_atr"],
                        },
                    },
                )
            )
            continue

        if (
            position > 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and current_atr is not None
            and current_atr > 0
            and close_price >= avg_entry_price + (float(risk_cfg["take_profit_atr"]) * current_atr)
        ):
            signals.append(
                SignalEvent(
                    strategy_id=runtime_strategy["strategy_id"],
                    ts=snapshot.get("ts") or datetime.now(timezone.utc),
                    symbol=symbol,
                    action="SELL",
                    reason="price reached the ATR take-profit threshold",
                    score=float(abs((close_price - avg_entry_price) / avg_entry_price)),
                    metadata={
                        "close": close_price,
                        "atr_14": current_atr,
                        "position": position,
                        "avg_entry_price": avg_entry_price,
                        "config": {
                            "volume_multiplier": signal_cfg["volume_multiplier"],
                            "atr_multiplier": signal_cfg["atr_multiplier"],
                            "stop_loss_pct": stop_loss_pct,
                            "stop_loss_atr": risk_cfg["stop_loss_atr"],
                            "take_profit_atr": risk_cfg["take_profit_atr"],
                        },
                    },
                )
            )
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
                    "position": position,
                    "avg_entry_price": avg_entry_price,
                    "config": {
                        "volume_multiplier": signal_cfg["volume_multiplier"],
                        "atr_multiplier": signal_cfg["atr_multiplier"],
                        "stop_loss_pct": stop_loss_pct,
                        "stop_loss_atr": risk_cfg["stop_loss_atr"],
                        "take_profit_atr": risk_cfg["take_profit_atr"],
                    },
                },
            )
        )

    return signals


# Evaluate z-score-based mean-reversion entry/exit rules across the configured universe.
# Input: runtime strategy payload plus the symbol -> snapshot market map.
# Output: BUY/SELL SignalEvent objects for entry or exit conditions triggered today.
def _mean_reversion_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    risk_cfg = params["risk"]
    universe_cfg = params["universe"]
    universe = _resolve_strategy_universe(universe_cfg, market_data_by_symbol)

    lookback = int(signal_cfg["lookback_window"])
    zscore_key = f"zscore_{lookback}"
    zscore_entry = float(signal_cfg["zscore_entry"])
    zscore_exit = float(signal_cfg["zscore_exit"])
    stop_loss_pct = float(risk_cfg["stop_loss_pct"])
    take_profit_pct = float(risk_cfg["take_profit_pct"])
    max_holding_days = int(risk_cfg.get("max_holding_days") or 0)

    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue

        zscore = _safe_float_or_none(snapshot.get(zscore_key))

        action: Literal["BUY", "SELL", "HOLD"] | None = None
        reason: str | None = None
        position = float(snapshot.get("position", 0) or 0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))
        close_price = _safe_float_or_none(snapshot.get("close"))
        position_holding_days = _resolve_position_holding_days(snapshot)

        if (
            position > 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price <= avg_entry_price * (1.0 - stop_loss_pct)
        ):
            action = "SELL"
            reason = f"price fell below the {stop_loss_pct:.1%} stop-loss threshold"
        elif (
            position > 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price >= avg_entry_price * (1.0 + take_profit_pct)
        ):
            action = "SELL"
            reason = f"price reached the {take_profit_pct:.1%} take-profit threshold"
        elif (
            position > 0
            and max_holding_days > 0
            and position_holding_days is not None
            and position_holding_days >= max_holding_days
        ):
            action = "SELL"
            reason = f"position reached the {max_holding_days}-day max holding period"
        elif position > 0 and zscore is not None and zscore >= -zscore_exit:
            action = "SELL"
            reason = f"{zscore_key} reverted above exit threshold"
        elif (
            position < 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price >= avg_entry_price * (1.0 + stop_loss_pct)
        ):
            action = "BUY"
            reason = f"price rose above the {stop_loss_pct:.1%} short stop-loss threshold"
        elif (
            position < 0
            and close_price is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price <= avg_entry_price * (1.0 - take_profit_pct)
        ):
            action = "BUY"
            reason = f"price reached the {take_profit_pct:.1%} short take-profit threshold"
        elif (
            position < 0
            and max_holding_days > 0
            and position_holding_days is not None
            and position_holding_days >= max_holding_days
        ):
            action = "BUY"
            reason = f"short position reached the {max_holding_days}-day max holding period"
        elif position < 0 and zscore is not None and zscore <= zscore_exit:
            action = "BUY"
            reason = f"{zscore_key} reverted below exit threshold"
        elif zscore is None:
            continue
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
                score=float(abs(zscore)) if zscore is not None else 0.0,
                metadata={
                    "close": snapshot.get("close"),
                    "atr_14": snapshot.get("atr_14"),
                    "rsi_14": snapshot.get("rsi_14"),
                        zscore_key: zscore,
                        "position": position,
                        "avg_entry_price": avg_entry_price,
                        "position_holding_days": position_holding_days,
                        "config": {
                            "lookback_window": lookback,
                            "zscore_entry": zscore_entry,
                            "zscore_exit": zscore_exit,
                            "stop_loss_pct": stop_loss_pct,
                            "take_profit_pct": take_profit_pct,
                            "max_holding_days": max_holding_days,
                        },
                    },
                )
            )

    return signals


# Evaluate island-reversal setups using recent OHLCV history and position-aware exit logic.
# Input: runtime strategy payload plus the symbol -> snapshot map with recent_bars populated.
# Output: BUY/SELL SignalEvent objects for breakout, retest, or exit conditions.
def _island_reversal_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
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
            avg_entry_price=_safe_float_or_none(snapshot.get("avg_entry_price")),
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
                    "avg_entry_price": snapshot.get("avg_entry_price"),
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
                        "max_loss_pct": risk_cfg["max_loss_pct"],
                        "take_profit_atr": risk_cfg["take_profit_atr"],
                    },
                },
            )
        )

    return signals


# ============================================================================
# Island reversal helpers
# ============================================================================

# Scan recent bars with breakout-gap priority to find the latest valid island-reversal setup.
# Input: ordered recent_bars history and the strategy's island-reversal signal config.
# Output: IslandReversalPattern when a complete setup exists, otherwise None.
def _find_latest_island_reversal_pattern(
    recent_bars: list[HistoryBar],
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

    earliest_breakout_idx = min_island_bars + 1
    for breakout_idx in range(len(recent_bars) - 1, earliest_breakout_idx - 1, -1):
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

        latest_left_gap_idx = breakout_idx - min_island_bars
        earliest_left_gap_idx = max(1, breakout_idx - max_island_bars)
        if latest_left_gap_idx < earliest_left_gap_idx:
            continue

        for left_gap_idx in range(latest_left_gap_idx, earliest_left_gap_idx - 1, -1):
            left_gap_bar = recent_bars[left_gap_idx]
            pre_gap_bar = recent_bars[left_gap_idx - 1]

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
                recent_bars,
                left_gap_idx=left_gap_idx,
                downtrend_lookback=downtrend_lookback,
                min_drop_pct=downtrend_min_drop_pct,
            ):
                continue

            island_bars = recent_bars[left_gap_idx:breakout_idx]
            if len(island_bars) < min_island_bars:
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
            if any(
                (_safe_float_or_none(bar.get("high")) or float("inf")) >= prev_low
                for bar in island_bars
            ):
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


# Check whether the left-gap bar sits inside the intended bearish/downtrend context.
# Input: one history bar plus configured lookback/drop thresholds.
# Output: True when return-based or moving-average-based downtrend evidence is present.
def _has_island_downtrend_context(
    recent_bars: list[HistoryBar],
    *,
    left_gap_idx: int,
    downtrend_lookback: int,
    min_drop_pct: float,
) -> bool:
    left_gap_bar = recent_bars[left_gap_idx]
    close = _safe_float_or_none(left_gap_bar.get("close"))
    lookback_return = None
    anchor_index = left_gap_idx - downtrend_lookback
    if close is not None and anchor_index >= 0:
        anchor_close = _safe_float_or_none(recent_bars[anchor_index].get("close"))
        if anchor_close is not None and anchor_close > 0:
            lookback_return = (close / anchor_close) - 1.0
    sma_50 = _safe_float_or_none(left_gap_bar.get("sma_50"))
    if lookback_return is not None and lookback_return <= -min_drop_pct:
        return True
    if close is not None and sma_50 is not None and close < sma_50:
        return True
    return False


# Turn a detected island pattern and current position state into a concrete trade action.
# Input: recent bars, detected pattern, island signal config, risk config, and current position size.
# Output: (action, reason, stage) where any field may be None when no trade should fire today.
def _resolve_island_reversal_action(
    *,
    recent_bars: list[HistoryBar],
    pattern: IslandReversalPattern,
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
    position: float,
    avg_entry_price: float | None = None,
) -> tuple[Literal["BUY", "SELL", "HOLD"] | None, str | None, str | None]:
    current_idx = len(recent_bars) - 1
    current_bar = recent_bars[current_idx]
    current_close = _safe_float_or_none(current_bar.get("close"))
    current_low = _safe_float_or_none(current_bar.get("low"))
    current_volume = _safe_float_or_none(current_bar.get("volume"))
    current_atr = _compute_recent_atr(recent_bars, ISLAND_REVERSAL_STOP_ATR_WINDOW)
    breakout_atr = _compute_recent_atr(
        recent_bars[:pattern.breakout_idx + 1],
        ISLAND_REVERSAL_STOP_ATR_WINDOW,
    )
    support_tolerance_pct = float(signal_cfg["support_tolerance_pct"])
    support_floor = pattern.island_high * (1.0 - support_tolerance_pct)
    hard_stop = pattern.island_low * (1.0 - support_tolerance_pct)

    if position > 0:
        if (
            current_close is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and current_close <= avg_entry_price * (1.0 - float(risk_cfg["max_loss_pct"]))
        ):
            return "SELL", "price fell more than the configured max-loss threshold from entry", "max_loss_stop"
        if current_low is not None and current_low < hard_stop:
            return "SELL", "price broke below the island base low", "base_break"
        if (
            current_close is not None
            and breakout_atr is not None
            and current_close >= pattern.breakout_close + (float(risk_cfg["take_profit_atr"]) * breakout_atr)
        ):
            return "SELL", "price reached the ATR take-profit target from the breakout confirmation", "take_profit"
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


# Evaluate conservative double-bottom setups using recent OHLCV history and position-aware exits.
# Input: runtime strategy payload plus the symbol -> snapshot map with recent_bars populated.
# Output: BUY/SELL SignalEvent objects for breakout, retest, or exit conditions.
def _double_bottom_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
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
        pattern = _find_latest_double_bottom_pattern(recent_bars, signal_cfg)
        position = float(snapshot.get("position", 0) or 0.0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))

        if position > 0:
            exit_setup = _extract_double_bottom_position_setup(snapshot)
            if exit_setup is None and pattern is not None:
                exit_setup = _build_double_bottom_setup_payload(recent_bars, pattern)
            if exit_setup is None:
                continue

            action, reason, stage = _resolve_double_bottom_exit_action(
                recent_bars=recent_bars,
                setup=exit_setup,
                signal_cfg=signal_cfg,
                risk_cfg=risk_cfg,
                avg_entry_price=avg_entry_price,
            )
            if action is None or reason is None or stage is None:
                continue

            setup_payload = dict(exit_setup)
            setup_payload["stage"] = stage
        else:
            if pattern is None:
                continue

            action, reason, stage = _resolve_double_bottom_action(
                recent_bars=recent_bars,
                pattern=pattern,
                signal_cfg=signal_cfg,
                risk_cfg=risk_cfg,
                position=position,
                avg_entry_price=avg_entry_price,
            )
            if action is None or reason is None or stage is None:
                continue

            setup_payload = _build_double_bottom_setup_payload(
                recent_bars,
                pattern,
                stage=stage,
            )

        score = _compute_double_bottom_signal_score(setup_payload)
        if action is None or reason is None:
            continue

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
                    "avg_entry_price": avg_entry_price,
                    "setup": setup_payload,
                    "config": {
                        "downtrend_min_drop_pct": signal_cfg["downtrend_min_drop_pct"],
                        "bottom_tolerance_pct": signal_cfg["bottom_tolerance_pct"],
                        "left_bottom_before_bars": signal_cfg.get("left_bottom_before_bars", 1),
                        "left_bottom_after_bars": signal_cfg.get("left_bottom_after_bars", 1),
                        "neckline_min_rebound_pct": signal_cfg["neckline_min_rebound_pct"],
                        "breakout_buffer_pct": signal_cfg["breakout_buffer_pct"],
                        "breakout_volume_ratio_min": signal_cfg["breakout_volume_ratio_min"],
                        "max_breakout_bars_after_right_bottom": signal_cfg.get("max_breakout_bars_after_right_bottom", 40),
                        "retest_window": signal_cfg["retest_window"],
                        "support_tolerance_pct": signal_cfg["support_tolerance_pct"],
                        "max_loss_pct": risk_cfg["max_loss_pct"],
                        "take_profit_atr": risk_cfg["take_profit_atr"],
                    },
                },
            )
        )

    return signals


# ============================================================================
# Double bottom helpers
# ============================================================================

def _build_double_bottom_setup_payload(
    recent_bars: list[HistoryBar],
    pattern: DoubleBottomPattern,
    *,
    stage: str | None = None,
) -> Dict[str, Any]:
    breakout_atr = _compute_recent_atr(
        recent_bars[:pattern.breakout_idx + 1],
        DOUBLE_BOTTOM_STOP_ATR_WINDOW,
    )
    payload: Dict[str, Any] = {
        "left_bottom_trade_date": str(recent_bars[pattern.left_bottom_idx]["dt_ny"]),
        "neckline_trade_date": str(recent_bars[pattern.neckline_idx]["dt_ny"]),
        "right_bottom_trade_date": str(recent_bars[pattern.right_bottom_idx]["dt_ny"]),
        "breakout_trade_date": str(recent_bars[pattern.breakout_idx]["dt_ny"]),
        "left_bottom_low": pattern.left_bottom_low,
        "right_bottom_low": pattern.right_bottom_low,
        "neckline_price": pattern.neckline_price,
        "breakout_close": pattern.breakout_close,
        "breakout_atr": breakout_atr,
        "breakout_wait_bars": pattern.breakout_idx - pattern.right_bottom_idx,
        "bottom_distance_pct": pattern.bottom_distance_pct,
        "breakout_volume_ratio": pattern.breakout_volume_ratio,
        "rebound_up_day_ratio": pattern.rebound_up_day_ratio,
    }
    if stage is not None:
        payload["stage"] = stage
    return payload


def _extract_double_bottom_position_setup(snapshot: MarketSnapshot) -> Dict[str, Any] | None:
    entry_signal_features = snapshot.get("entry_signal_features")
    if not isinstance(entry_signal_features, dict):
        return None

    raw_setup = entry_signal_features.get("setup")
    if not isinstance(raw_setup, dict):
        return None

    left_bottom_low = _safe_float_or_none(raw_setup.get("left_bottom_low"))
    right_bottom_low = _safe_float_or_none(raw_setup.get("right_bottom_low"))
    neckline_price = _safe_float_or_none(raw_setup.get("neckline_price"))
    breakout_close = _safe_float_or_none(raw_setup.get("breakout_close"))
    if (
        left_bottom_low is None
        or right_bottom_low is None
        or neckline_price is None
        or breakout_close is None
    ):
        return None

    setup = dict(raw_setup)
    setup["left_bottom_low"] = left_bottom_low
    setup["right_bottom_low"] = right_bottom_low
    setup["neckline_price"] = neckline_price
    setup["breakout_close"] = breakout_close
    setup["breakout_atr"] = _safe_float_or_none(raw_setup.get("breakout_atr"))
    setup["breakout_wait_bars"] = _safe_float_or_none(raw_setup.get("breakout_wait_bars"))
    setup["bottom_distance_pct"] = _safe_float_or_none(raw_setup.get("bottom_distance_pct"))
    setup["breakout_volume_ratio"] = _safe_float_or_none(raw_setup.get("breakout_volume_ratio"))
    setup["rebound_up_day_ratio"] = _safe_float_or_none(raw_setup.get("rebound_up_day_ratio"))
    return setup


def _compute_double_bottom_signal_score(setup: Dict[str, Any]) -> float:
    bottom_distance_pct = _safe_float_or_none(setup.get("bottom_distance_pct")) or 1.0
    breakout_volume_ratio = _safe_float_or_none(setup.get("breakout_volume_ratio")) or 0.0
    rebound_up_day_ratio = _safe_float_or_none(setup.get("rebound_up_day_ratio")) or 0.0
    return ((1.0 - bottom_distance_pct) * 100.0) + breakout_volume_ratio + (rebound_up_day_ratio * 10.0)


def _resolve_double_bottom_exit_action(
    *,
    recent_bars: list[HistoryBar],
    setup: Dict[str, Any],
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
    avg_entry_price: float | None = None,
) -> tuple[Literal["SELL"] | None, str | None, str | None]:
    if not recent_bars:
        return None, None, None

    current_bar = recent_bars[-1]
    current_close = _safe_float_or_none(current_bar.get("close"))
    current_low = _safe_float_or_none(current_bar.get("low"))
    current_atr = _compute_recent_atr(recent_bars, DOUBLE_BOTTOM_STOP_ATR_WINDOW)
    breakout_close = _safe_float_or_none(setup.get("breakout_close"))
    breakout_atr = _safe_float_or_none(setup.get("breakout_atr"))
    left_bottom_low = _safe_float_or_none(setup.get("left_bottom_low"))
    right_bottom_low = _safe_float_or_none(setup.get("right_bottom_low"))
    if (
        breakout_close is None
        or left_bottom_low is None
        or right_bottom_low is None
    ):
        return None, None, None

    support_tolerance_pct = float(signal_cfg["support_tolerance_pct"])
    hard_stop = min(left_bottom_low, right_bottom_low) * (1.0 - support_tolerance_pct)

    if current_close is not None and current_close < right_bottom_low:
        return "SELL", "price closed below the right bottom after confirmation", "right_bottom_break"

    if (
        current_close is not None
        and avg_entry_price is not None
        and avg_entry_price > 0
        and current_close <= avg_entry_price * (1.0 - float(risk_cfg["max_loss_pct"]))
    ):
        return "SELL", "price fell more than the configured max-loss threshold from entry", "max_loss_stop"
    if current_low is not None and current_low < hard_stop:
        return "SELL", "price broke below the double-bottom base", "base_break"
    if (
        current_close is not None
        and breakout_atr is not None
        and current_close >= breakout_close + (float(risk_cfg["take_profit_atr"]) * breakout_atr)
    ):
        return "SELL", "price reached the ATR take-profit target from the breakout confirmation", "take_profit"
    if (
        current_close is not None
        and current_atr is not None
        and current_close < breakout_close - (float(risk_cfg["stop_loss_atr"]) * current_atr)
    ):
        return "SELL", "price hit the ATR stop from the breakout confirmation", "atr_stop"
    return None, None, None

# Find the latest confirmed conservative double-bottom setup by collecting left-bottom
# candidates first, then pairing them with later right-bottom pivots and breakouts.
# Input: ordered recent_bars history and the strategy's double-bottom signal config.
# Output: DoubleBottomPattern when a complete setup exists, otherwise None.
def _find_latest_double_bottom_pattern(
    recent_bars: list[HistoryBar],
    signal_cfg: Dict[str, Any],
) -> DoubleBottomPattern | None:
    if len(recent_bars) < 6:
        return None

    min_bottom_spacing = int(signal_cfg["min_bottom_spacing"])
    max_bottom_spacing = int(signal_cfg["max_bottom_spacing"])
    left_bottom_before_bars = int(signal_cfg.get("left_bottom_before_bars", 1))
    left_bottom_after_bars = int(signal_cfg.get("left_bottom_after_bars", 1))
    downtrend_lookback = int(signal_cfg["downtrend_lookback"])
    downtrend_min_drop_pct = float(signal_cfg["downtrend_min_drop_pct"])
    bottom_tolerance_pct = float(signal_cfg["bottom_tolerance_pct"])
    neckline_min_rebound_pct = float(signal_cfg["neckline_min_rebound_pct"])
    rebound_up_day_ratio_min = float(signal_cfg["rebound_up_day_ratio_min"])
    second_bottom_volume_ratio_max = float(signal_cfg["second_bottom_volume_ratio_max"])
    breakout_volume_ratio_min = float(signal_cfg["breakout_volume_ratio_min"])
    max_breakout_bars_after_right_bottom = int(signal_cfg.get("max_breakout_bars_after_right_bottom", 40))
    breakout_buffer_pct = float(signal_cfg["breakout_buffer_pct"])

    left_candidates = _collect_double_bottom_left_candidates(
        recent_bars,
        min_bottom_spacing=min_bottom_spacing,
        downtrend_lookback=downtrend_lookback,
        downtrend_min_drop_pct=downtrend_min_drop_pct,
        left_bottom_before_bars=left_bottom_before_bars,
        left_bottom_after_bars=left_bottom_after_bars,
        bottom_volume_ratio_max=second_bottom_volume_ratio_max,
    )
    if not left_candidates:
        return None

    best_pattern: DoubleBottomPattern | None = None
    latest_right_bottom_idx = len(recent_bars) - 2

    for left_candidate in left_candidates:
        left_bottom_idx = left_candidate.left_bottom_idx
        left_bottom_low = left_candidate.left_bottom_low
        earliest_right_idx = left_bottom_idx + min_bottom_spacing
        latest_right_idx = min(latest_right_bottom_idx, left_bottom_idx + max_bottom_spacing)
        if earliest_right_idx > latest_right_idx:
            continue

        for right_bottom_idx in range(earliest_right_idx, latest_right_idx + 1):
            if not _is_local_minimum(recent_bars, right_bottom_idx):
                continue

            right_bottom_bar = recent_bars[right_bottom_idx]
            right_bottom_low = _safe_float_or_none(right_bottom_bar.get("low"))
            right_bottom_volume = _safe_float_or_none(right_bottom_bar.get("volume"))
            right_bottom_avg_volume = _safe_float_or_none(right_bottom_bar.get("volume_sma_20"))
            if (
                right_bottom_low is None
                or right_bottom_volume is None
                or right_bottom_avg_volume is None
                or right_bottom_avg_volume <= 0
            ):
                continue

            right_bottom_volume_ratio = right_bottom_volume / right_bottom_avg_volume
            if right_bottom_volume_ratio > second_bottom_volume_ratio_max:
                continue

            bottom_distance_pct = abs(right_bottom_low - left_bottom_low) / max(
                left_bottom_low,
                right_bottom_low,
            )
            if bottom_distance_pct > bottom_tolerance_pct:
                continue
            if right_bottom_low < left_bottom_low * (1.0 - bottom_tolerance_pct):
                continue
            if not _double_bottom_intermediate_lows_hold(
                recent_bars,
                left_bottom_idx=left_bottom_idx,
                right_bottom_idx=right_bottom_idx,
                floor_low=min(left_bottom_low, right_bottom_low),
            ):
                continue

            neckline_idx, neckline_price = _find_double_bottom_neckline(
                recent_bars,
                left_bottom_idx=left_bottom_idx,
                right_bottom_idx=right_bottom_idx,
            )
            if neckline_idx is None or neckline_price is None or neckline_price <= 0:
                continue
            if neckline_price < max(left_bottom_low, right_bottom_low) * (1.0 + neckline_min_rebound_pct):
                continue

            rebound_up_day_ratio = _compute_up_day_ratio(
                recent_bars,
                start_idx=left_bottom_idx,
                end_idx=right_bottom_idx,
            )
            if rebound_up_day_ratio is None or rebound_up_day_ratio < rebound_up_day_ratio_min:
                continue

            breakout_idx = _find_first_double_bottom_breakout_idx(
                recent_bars,
                right_bottom_idx=right_bottom_idx,
                neckline_price=neckline_price,
                breakout_buffer_pct=breakout_buffer_pct,
                breakout_volume_ratio_min=breakout_volume_ratio_min,
                max_breakout_bars_after_right_bottom=max_breakout_bars_after_right_bottom,
            )
            if breakout_idx is None:
                continue

            breakout_bar = recent_bars[breakout_idx]
            breakout_close = _safe_float_or_none(breakout_bar.get("close"))
            breakout_volume = _safe_float_or_none(breakout_bar.get("volume"))
            breakout_avg_volume = _safe_float_or_none(breakout_bar.get("volume_sma_20"))
            if (
                breakout_close is None
                or breakout_volume is None
                or breakout_avg_volume is None
                or breakout_avg_volume <= 0
            ):
                continue

            pattern = DoubleBottomPattern(
                left_bottom_idx=left_bottom_idx,
                neckline_idx=neckline_idx,
                right_bottom_idx=right_bottom_idx,
                breakout_idx=breakout_idx,
                left_bottom_low=left_bottom_low,
                right_bottom_low=right_bottom_low,
                neckline_price=neckline_price,
                breakout_close=breakout_close,
                breakout_volume=breakout_volume,
                breakout_volume_ratio=breakout_volume / breakout_avg_volume,
                bottom_distance_pct=bottom_distance_pct,
                rebound_up_day_ratio=rebound_up_day_ratio,
            )
            if (
                best_pattern is None
                or pattern.breakout_idx > best_pattern.breakout_idx
                or (
                    pattern.breakout_idx == best_pattern.breakout_idx
                    and pattern.right_bottom_idx > best_pattern.right_bottom_idx
                )
            ):
                best_pattern = pattern

    return best_pattern


def _collect_double_bottom_left_candidates(
    recent_bars: list[HistoryBar],
    *,
    min_bottom_spacing: int,
    downtrend_lookback: int,
    downtrend_min_drop_pct: float,
    left_bottom_before_bars: int,
    left_bottom_after_bars: int,
    bottom_volume_ratio_max: float,
) -> list[DoubleBottomLeftCandidate]:
    latest_left_idx = min(
        len(recent_bars) - left_bottom_after_bars - 1,
        len(recent_bars) - min_bottom_spacing - 2,
    )
    if latest_left_idx < left_bottom_before_bars:
        return []

    candidates: list[DoubleBottomLeftCandidate] = []
    for left_bottom_idx in range(left_bottom_before_bars, latest_left_idx + 1):
        if not _is_local_minimum(
            recent_bars,
            left_bottom_idx,
            before_span=left_bottom_before_bars,
            after_span=left_bottom_after_bars,
        ):
            continue

        left_bottom_bar = recent_bars[left_bottom_idx]
        left_bottom_low = _safe_float_or_none(left_bottom_bar.get("low"))
        left_bottom_volume = _safe_float_or_none(left_bottom_bar.get("volume"))
        left_bottom_avg_volume = _safe_float_or_none(left_bottom_bar.get("volume_sma_20"))
        if left_bottom_low is None or left_bottom_low <= 0:
            continue
        if (
            left_bottom_volume is None
            or left_bottom_avg_volume is None
            or left_bottom_avg_volume <= 0
        ):
            continue
        if left_bottom_volume / left_bottom_avg_volume > bottom_volume_ratio_max:
            continue
        if not _has_double_bottom_downtrend_context(
            recent_bars,
            left_bottom_idx=left_bottom_idx,
            downtrend_lookback=downtrend_lookback,
            min_drop_pct=downtrend_min_drop_pct,
        ):
            continue

        candidates.append(
            DoubleBottomLeftCandidate(
                left_bottom_idx=left_bottom_idx,
                left_bottom_low=left_bottom_low,
            )
        )

    return candidates


def _is_local_minimum(
    recent_bars: list[HistoryBar],
    idx: int,
    *,
    before_span: int = 1,
    after_span: int | None = None,
) -> bool:
    low = _safe_float_or_none(recent_bars[idx].get("low"))
    if low is None:
        return False

    after_span = before_span if after_span is None else after_span
    if before_span < 0 or after_span < 0:
        return False
    if idx - before_span < 0 or idx + after_span >= len(recent_bars):
        return False

    start_idx = idx - before_span
    end_idx = idx + after_span
    for neighbor_idx in range(start_idx, end_idx + 1):
        if neighbor_idx == idx:
            continue
        neighbor_low = _safe_float_or_none(recent_bars[neighbor_idx].get("low"))
        if neighbor_low is not None and neighbor_low < low:
            return False
    return True


def _double_bottom_intermediate_lows_hold(
    recent_bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    right_bottom_idx: int,
    floor_low: float,
) -> bool:
    if right_bottom_idx - left_bottom_idx <= 1:
        return False

    for idx in range(left_bottom_idx + 1, right_bottom_idx):
        low = _safe_float_or_none(recent_bars[idx].get("low"))
        if low is None or low <= floor_low:
            return False
    return True


def _compute_up_day_ratio(
    recent_bars: list[HistoryBar],
    *,
    start_idx: int,
    end_idx: int,
) -> float | None:
    if end_idx <= start_idx:
        return None

    previous_close = _safe_float_or_none(recent_bars[start_idx].get("close"))
    if previous_close is None:
        return None

    up_days = 0
    directional_days = 0
    for idx in range(start_idx + 1, end_idx + 1):
        close = _safe_float_or_none(recent_bars[idx].get("close"))
        if close is None:
            continue
        if close > previous_close:
            up_days += 1
            directional_days += 1
        elif close < previous_close:
            directional_days += 1
        previous_close = close

    if directional_days == 0:
        return None
    return up_days / float(directional_days)


def _has_double_bottom_downtrend_context(
    recent_bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    downtrend_lookback: int,
    min_drop_pct: float,
) -> bool:
    left_bottom_bar = recent_bars[left_bottom_idx]
    close = _safe_float_or_none(left_bottom_bar.get("close"))
    lookback_return = None
    anchor_idx = left_bottom_idx - downtrend_lookback
    if close is not None and anchor_idx >= 0:
        anchor_close = _safe_float_or_none(recent_bars[anchor_idx].get("close"))
        if anchor_close is not None and anchor_close > 0:
            lookback_return = (close / anchor_close) - 1.0

    ret_60d = _safe_float_or_none(left_bottom_bar.get("ret_60d"))
    sma_50 = _safe_float_or_none(left_bottom_bar.get("sma_50"))
    if lookback_return is not None and lookback_return <= -min_drop_pct:
        return True
    if ret_60d is not None and ret_60d <= -min_drop_pct:
        return True
    if close is not None and sma_50 is not None and close < sma_50:
        return True
    return False


def _find_double_bottom_neckline(
    recent_bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    right_bottom_idx: int,
) -> tuple[int | None, float | None]:
    if right_bottom_idx - left_bottom_idx <= 1:
        return None, None

    neckline_idx: int | None = None
    neckline_price: float | None = None
    for idx in range(left_bottom_idx + 1, right_bottom_idx):
        high = _safe_float_or_none(recent_bars[idx].get("high"))
        if high is None:
            continue
        if neckline_price is None or high > neckline_price:
            neckline_idx = idx
            neckline_price = high
    return neckline_idx, neckline_price


def _find_first_double_bottom_breakout_idx(
    recent_bars: list[HistoryBar],
    *,
    right_bottom_idx: int,
    neckline_price: float,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
    max_breakout_bars_after_right_bottom: int,
) -> int | None:
    breakout_threshold = neckline_price * (1.0 + breakout_buffer_pct)
    breakout_search_end = min(
        len(recent_bars),
        right_bottom_idx + max_breakout_bars_after_right_bottom + 1,
    )
    for idx in range(right_bottom_idx + 1, breakout_search_end):
        bar = recent_bars[idx]
        close = _safe_float_or_none(bar.get("close"))
        open_price = _safe_float_or_none(bar.get("open"))
        volume = _safe_float_or_none(bar.get("volume"))
        avg_volume = _safe_float_or_none(bar.get("volume_sma_20"))
        if (
            close is None
            or open_price is None
            or volume is None
            or avg_volume is None
            or avg_volume <= 0
        ):
            continue
        if close <= breakout_threshold:
            continue
        if close <= open_price:
            continue
        if volume / avg_volume < breakout_volume_ratio_min:
            continue
        return idx
    return None


# Turn a detected double-bottom pattern and current position state into a concrete trade action.
# Input: recent bars, detected pattern, double-bottom signal config, risk config, and current position size.
# Output: (action, reason, stage) where any field may be None when no trade should fire today.
def _resolve_double_bottom_action(
    *,
    recent_bars: list[HistoryBar],
    pattern: DoubleBottomPattern,
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
    position: float,
    avg_entry_price: float | None = None,
) -> tuple[Literal["BUY", "SELL", "HOLD"] | None, str | None, str | None]:
    current_idx = len(recent_bars) - 1
    current_bar = recent_bars[current_idx]
    current_close = _safe_float_or_none(current_bar.get("close"))
    current_low = _safe_float_or_none(current_bar.get("low"))
    current_volume = _safe_float_or_none(current_bar.get("volume"))
    support_tolerance_pct = float(signal_cfg["support_tolerance_pct"])
    neckline_support = pattern.neckline_price * (1.0 - support_tolerance_pct)

    if position > 0:
        return _resolve_double_bottom_exit_action(
            recent_bars=recent_bars,
            setup=_build_double_bottom_setup_payload(recent_bars, pattern),
            signal_cfg=signal_cfg,
            risk_cfg=risk_cfg,
            avg_entry_price=avg_entry_price,
        )

    if current_idx == pattern.breakout_idx:
        return "BUY", "confirmed the double bottom with a volume-backed neckline breakout", "breakout"

    if current_idx <= pattern.breakout_idx:
        return None, None, None

    retest_window = int(signal_cfg["retest_window"])
    if current_idx > pattern.breakout_idx + retest_window:
        return None, None, None

    if current_low is None or current_close is None or current_volume is None:
        return None, None, None

    if any(
        (_safe_float_or_none(bar.get("close")) or float("inf")) < neckline_support
        for bar in recent_bars[pattern.breakout_idx + 1:current_idx]
    ):
        return None, None, None

    touched_neckline = current_low <= pattern.neckline_price * (1.0 + support_tolerance_pct)
    held_support = current_low >= neckline_support and current_close >= pattern.neckline_price
    low_volume_retest = current_volume <= pattern.breakout_volume * float(signal_cfg["retest_volume_ratio_max"])
    if touched_neckline and held_support and low_volume_retest:
        return "BUY", "low-volume retest held the neckline after the double-bottom breakout", "retest"

    return None, None, None


# Registry consulted by paper/live trading and backtests to route runtime strategies to handlers.
STRATEGY_HANDLERS: dict[str, StrategyHandler] = {
    "trend": _trend_following_handler,
    "mean_reversion": _mean_reversion_handler,
    "island_reversal": _island_reversal_handler,
    "double_bottom": _double_bottom_handler,
}
