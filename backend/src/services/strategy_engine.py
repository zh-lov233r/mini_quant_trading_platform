from __future__ import annotations

"""Signal generation engine shared by daily runs, paper trading, and backtests.

The module is organized from top-level orchestration helpers down to individual
strategy handlers and their supporting pattern-detection utilities.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from src.models.tables import Signal, Strategy, StrategyRun
from src.services.bottom_reversal_service import evaluate_bottom_reversal
from src.services.signal_strength_service import annotate_and_rank_signals
from src.services.staged_entry_service import build_pattern_setup, pattern_setup_from_metadata
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
    advance_symbol as advance_support_resistance_symbol,
)
from src.services.support_resistance_persistence_service import (
    SupportResistanceMaterializationBuildError,
    find_reusable_materialization,
    hydrate_state_from_materialization,
    persist_support_resistance_run,
    record_failed_materialization_after_rollback,
    source_data_fingerprint,
)
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
NEW_YORK_TZ = ZoneInfo("America/New_York")

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
    instrument_id: int | None = None


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


@dataclass(slots=True)
class DoubleBottomRightCandidate:
    """Validated double-bottom base waiting for a breakout confirmation."""

    left_bottom_idx: int
    neckline_idx: int
    right_bottom_idx: int
    left_bottom_low: float
    right_bottom_low: float
    neckline_price: float
    bottom_distance_pct: float
    rebound_up_day_ratio: float


@dataclass(slots=True)
class DoubleBottomBacktestSymbolState:
    """Per-symbol state carried across the whole backtest lifecycle."""

    history_bars: list[HistoryBar] = field(default_factory=list)
    left_candidates: list[DoubleBottomLeftCandidate] = field(default_factory=list)
    right_candidates: list[DoubleBottomRightCandidate] = field(default_factory=list)
    best_pattern: DoubleBottomPattern | None = None


@dataclass(slots=True)
class DoubleBottomBacktestState:
    """Backtest-only double-bottom state keyed by symbol."""

    symbols: dict[str, DoubleBottomBacktestSymbolState] = field(default_factory=dict)


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
    support_resistance_source_fingerprint = (
        source_data_fingerprint(db)
        if any(runtime["strategy_type"] == "support_resistance" for runtime in active_runtimes)
        else None
    )
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

        replay_state: SupportResistanceState | None = None
        replay_symbols: list[str] = []
        replay_dates: list[date] = []
        if runtime["strategy_type"] == "support_resistance":
            replay_symbols = _resolve_strategy_universe(runtime["params"]["universe"], snapshots)
            for symbol in replay_symbols:
                for bar in (snapshots.get(symbol) or {}).get("recent_bars") or []:
                    value = bar.get("dt_ny")
                    if isinstance(value, datetime):
                        replay_dates.append(value.date())
                    elif isinstance(value, date):
                        replay_dates.append(value)
            coverage_start = min(replay_dates) if replay_dates else trade_date
            reusable = find_reusable_materialization(
                db,
                runtime=runtime,
                symbols=replay_symbols,
                coverage_start=coverage_start,
                coverage_end=trade_date,
                expected_data_fingerprint=support_resistance_source_fingerprint,
            )
            replay_state = (
                hydrate_state_from_materialization(db, reusable)
                if reusable is not None
                else SupportResistanceState()
            )
            strategy_signals = _support_resistance_replay_handler_with_state(
                runtime,
                snapshots,
                replay_state,
            )
        else:
            strategy_signals = handler(runtime, snapshots)
        annotate_and_rank_signals(runtime, strategy_signals)
        run = _get_or_create_signal_run(
            db=db,
            strategy=strategy,
            mode=mode,
            trade_date=trade_date,
            config_snapshot=runtime["params"],
            started_at=started_at,
        )
        _replace_signals_for_run(db, run, strategy, strategy_signals)

        support_resistance_materialization = None
        if runtime["strategy_type"] == "support_resistance":
            assert replay_state is not None
            try:
                support_resistance_materialization = persist_support_resistance_run(
                    db,
                    run=run,
                    runtime=runtime,
                    state=replay_state,
                    symbols=replay_symbols,
                    coverage_start=min(replay_dates) if replay_dates else trade_date,
                    coverage_end=trade_date,
                    expected_data_fingerprint=support_resistance_source_fingerprint,
                )
            except SupportResistanceMaterializationBuildError as exc:
                db.rollback()
                record_failed_materialization_after_rollback(db, exc)
                failed_run = _get_or_create_signal_run(
                    db=db,
                    strategy=strategy,
                    mode=mode,
                    trade_date=trade_date,
                    config_snapshot=runtime["params"],
                    started_at=started_at,
                )
                _replace_signals_for_run(db, failed_run, strategy, [])
                failed_run.status = "failed"
                failed_run.finished_at = datetime.now(timezone.utc)
                failed_run.error_message = str(exc)
                db.commit()
                raise

        run.status = "completed"
        run.started_at = run.started_at or started_at
        run.finished_at = datetime.now(timezone.utc)
        run.summary_metrics = {
            "signal_count": len(strategy_signals),
            "symbols_requested": runtime["params"]["universe"].get("symbols", []),
            "symbols_signaled": sorted({event.symbol for event in strategy_signals}),
            "support_resistance_materialization_id": (
                str(support_resistance_materialization.id)
                if support_resistance_materialization is not None
                else None
            ),
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

        strategy_signals = handler(runtime, market_data_by_symbol)
        annotate_and_rank_signals(runtime, strategy_signals)
        signals.extend(strategy_signals)

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
    risk_cfg = runtime_strategy.get("params", {}).get("risk", {}) or {}

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

    if strategy_type == "head_shoulders_bottom":
        downtrend_lookback = _safe_positive_int(signal_cfg.get("downtrend_lookback"), 60)
        max_segment_bars = _safe_positive_int(signal_cfg.get("max_segment_bars"), 40)
        pivot_right_bars = _safe_positive_int(signal_cfg.get("pivot_right_bars"), 2)
        return max(recent_bar_count, downtrend_lookback + max_segment_bars * 2 + pivot_right_bars + 10)

    if strategy_type == "rounded_bottom":
        max_lookback = _safe_positive_int(signal_cfg.get("max_lookback"), 240)
        pivot_right_bars = _safe_positive_int(signal_cfg.get("pivot_right_bars"), 2)
        return max(recent_bar_count, max_lookback + pivot_right_bars + 10)

    if strategy_type == "v_reversal":
        downtrend_lookback = _safe_positive_int(signal_cfg.get("downtrend_lookback"), 60)
        continuation_window = _safe_positive_int(signal_cfg.get("continuation_window"), 5)
        consolidation_max_bars = _safe_positive_int(signal_cfg.get("consolidation_max_bars"), 10)
        retest_window = _safe_positive_int(signal_cfg.get("retest_window"), 5)
        return max(recent_bar_count, downtrend_lookback + continuation_window + consolidation_max_bars + retest_window + 10)

    if strategy_type == "support_resistance":
        detection_window = _safe_positive_int(signal_cfg.get("detection_window"), 120)
        pivot_right = _safe_positive_int(signal_cfg.get("pivot_right_bars"), 3)
        score_window = _safe_positive_int(signal_cfg.get("score_outcome_window"), 20)
        retest_window = _safe_positive_int(signal_cfg.get("retest_window"), 10)
        max_holding_days = _safe_positive_int(risk_cfg.get("max_holding_days"), 40)
        return max(
            recent_bar_count,
            detection_window + pivot_right + score_window + retest_window + 10,
            max_holding_days + 5,
        )

    if strategy_type in {"trend", "mean_reversion", "momentum_breakout"}:
        return 0

    return recent_bar_count


def required_recent_bar_lookback_days(recent_bar_count: int) -> int:
    if recent_bar_count <= 0:
        return 0
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


def _signal_timestamp_utc(snapshot: MarketSnapshot) -> datetime:
    timestamp = snapshot.get("ts")
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    trade_date = _safe_date_or_none(snapshot.get("dt_ny"))
    if trade_date is None:
        raise ValueError("daily strategy snapshot requires ts or dt_ny")
    market_close_ny = datetime.combine(trade_date, time(hour=16), tzinfo=NEW_YORK_TZ)
    return market_close_ny.astimezone(timezone.utc)


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
                    "strength_inputs": {
                        "separation_atr": (
                            float(fast_now - slow_now) / current_atr
                            if current_atr is not None and current_atr > 0
                            else None
                        ),
                        "crossover_impulse_atr": (
                            float((fast_now - slow_now) - (prev_fast - prev_slow)) / current_atr
                            if current_atr is not None and current_atr > 0
                            else None
                        ),
                        "volume_ratio": volume / avg_volume,
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
                        "strength_inputs": {
                            "absolute_zscore": abs(zscore) if zscore is not None else None,
                        },
                    },
                )
            )

    return signals


# Evaluate daily momentum breakouts using only existing adjusted snapshot fields.
# Input: runtime strategy payload plus the symbol -> daily-feature snapshot map.
# Output: deterministically ordered BUY/SELL SignalEvent objects for day-T close evaluation.
def _momentum_breakout_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    signal_cfg = params["signal"]
    risk_cfg = params["risk"]
    universe = sorted(set(_resolve_strategy_universe(params["universe"], market_data_by_symbol)))

    minimum_return_20d = float(signal_cfg["minimum_return_20d"])
    breakout_buffer_pct = float(signal_cfg["breakout_buffer_pct"])
    volume_multiplier = float(signal_cfg["volume_multiplier"])
    exit_return_20d = float(signal_cfg["exit_return_20d"])
    stop_loss_pct = float(risk_cfg["stop_loss_pct"])
    take_profit_pct = float(risk_cfg["take_profit_pct"])

    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue

        close_price = _safe_float_or_none(snapshot.get("close"))
        sma_20 = _safe_float_or_none(snapshot.get("sma_20"))
        return_20d = _safe_float_or_none(snapshot.get("ret_20d"))
        volume = _safe_float_or_none(snapshot.get("volume"))
        average_volume = _safe_float_or_none(snapshot.get("volume_sma_20"))
        if (
            close_price is None
            or sma_20 is None
            or sma_20 <= 0
            or return_20d is None
            or volume is None
            or average_volume is None
            or average_volume <= 0
        ):
            continue

        position = float(snapshot.get("position", 0) or 0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))
        breakout_threshold = sma_20 * (1.0 + breakout_buffer_pct)
        volume_ratio = volume / average_volume
        action: Literal["BUY", "SELL", "HOLD"] | None = None
        reason: str | None = None

        if (
            position > 0
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price <= avg_entry_price * (1.0 - stop_loss_pct)
        ):
            action = "SELL"
            reason = f"price fell below the {stop_loss_pct:.1%} stop-loss threshold"
        elif (
            position > 0
            and avg_entry_price is not None
            and avg_entry_price > 0
            and close_price >= avg_entry_price * (1.0 + take_profit_pct)
        ):
            action = "SELL"
            reason = f"price reached the {take_profit_pct:.1%} take-profit threshold"
        elif position > 0 and (close_price < sma_20 or return_20d <= exit_return_20d):
            action = "SELL"
            reason = "20-day momentum or SMA20 support failed"
        elif (
            position <= 0
            and close_price >= breakout_threshold
            and return_20d >= minimum_return_20d
            and volume_ratio >= volume_multiplier
        ):
            action = "BUY"
            reason = "adjusted close confirmed a volume-backed 20-day momentum breakout"

        if action is None or reason is None:
            continue

        price_extension = (close_price / sma_20) - 1.0
        signals.append(
            SignalEvent(
                strategy_id=runtime_strategy["strategy_id"],
                ts=_signal_timestamp_utc(snapshot),
                symbol=symbol,
                action=action,
                reason=reason,
                score=return_20d + price_extension + volume_ratio,
                metadata={
                    "close": close_price,
                    "sma_20": sma_20,
                    "ret_20d": return_20d,
                    "volume": volume,
                    "volume_sma_20": average_volume,
                    "volume_ratio": volume_ratio,
                    "breakout_threshold": breakout_threshold,
                    "position": position,
                    "avg_entry_price": avg_entry_price,
                    "price_semantics": "forward_adjusted_fallback_unadjusted",
                    "config": {
                        "minimum_return_20d": minimum_return_20d,
                        "breakout_buffer_pct": breakout_buffer_pct,
                        "volume_multiplier": volume_multiplier,
                        "exit_return_20d": exit_return_20d,
                        "stop_loss_pct": stop_loss_pct,
                        "take_profit_pct": take_profit_pct,
                    },
                    "strength_inputs": {
                        "return_20d": return_20d,
                        "price_extension": price_extension,
                        "volume_ratio": volume_ratio,
                    },
                },
            )
        )

    return signals


# Evaluate island-reversal setups using recent OHLCV history and position-aware exit logic.
# Input: runtime strategy payload plus the symbol -> snapshot map with recent_bars populated.
# Output: BUY/SELL SignalEvent objects for retest-only entries or exit conditions.
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
        position = float(snapshot.get("position", 0) or 0.0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))
        stored_setup = pattern_setup_from_metadata(snapshot.get("entry_signal_features"))
        exit_decision = _resolve_standard_staged_exit(
            recent_bars=recent_bars,
            setup=stored_setup,
            risk_cfg=risk_cfg,
            position=position,
            avg_entry_price=avg_entry_price,
        )
        pattern = _find_latest_island_reversal_pattern(recent_bars, signal_cfg)
        exhaustion = None if pattern is not None else _find_current_island_exhaustion_gap(recent_bars, signal_cfg)
        if exit_decision is not None:
            action, reason, stage = exit_decision
        elif pattern is not None:
            action, reason, stage = _resolve_island_reversal_action(
                recent_bars=recent_bars,
                pattern=pattern,
                signal_cfg=signal_cfg,
                risk_cfg=risk_cfg,
                position=0,
                avg_entry_price=avg_entry_price,
            )
        elif exhaustion is not None:
            action, reason, stage = "BUY", "confirmed a low-volume downside exhaustion gap", "exhaustion_gap"
        else:
            continue
        if action is None or reason is None:
            continue

        if pattern is not None:
            score = pattern.left_gap_pct * 100.0 + pattern.breakout_gap_pct * 100.0 + pattern.breakout_volume_ratio
        else:
            score = float(exhaustion["left_gap_pct"] * 100.0) if exhaustion is not None else None
        current_close = _safe_float_or_none(snapshot.get("close"))
        current_volume = _safe_float_or_none(snapshot.get("volume"))
        current_atr = _safe_float_or_none(snapshot.get("atr_14"))
        if action == "SELL" and stored_setup is not None:
            setup_payload = {**stored_setup, "exit_stage": stage}
        elif pattern is not None:
            stage_index = 2 if stage == "breakout" else 3
            stage_key = "upside_gap" if stage_index == 2 else "gap_retest"
            setup_payload = build_pattern_setup(
                pattern_type="island_reversal",
                symbol=symbol,
                stage_index=stage_index,
                stage_key=stage_key,
                risk_cfg=risk_cfg,
                anchors={
                    "left_gap_trade_date": str(recent_bars[pattern.left_gap_idx]["dt_ny"]),
                    "breakout_trade_date": str(recent_bars[pattern.breakout_idx]["dt_ny"]),
                    "left_gap_price": pattern.island_high,
                    "breakout_price": pattern.breakout_close,
                },
                invalidation_price=pattern.island_low * (1.0 - float(signal_cfg["support_tolerance_pct"])),
                setup_id_anchors=(recent_bars[pattern.left_gap_idx]["dt_ny"],),
                extra={
                    "island_low": pattern.island_low,
                    "island_high": pattern.island_high,
                    "breakout_gap_low": pattern.breakout_gap_low,
                    "left_gap_pct": pattern.left_gap_pct,
                    "breakout_gap_pct": pattern.breakout_gap_pct,
                    "breakout_volume": pattern.breakout_volume,
                    "breakout_volume_ratio": pattern.breakout_volume_ratio,
                },
            )
        else:
            assert exhaustion is not None
            setup_payload = build_pattern_setup(
                pattern_type="island_reversal",
                symbol=symbol,
                stage_index=1,
                stage_key="exhaustion_gap",
                risk_cfg=risk_cfg,
                anchors={
                    "left_gap_trade_date": str(exhaustion["trade_date"]),
                    "left_gap_price": exhaustion["high"],
                },
                invalidation_price=float(exhaustion["low"]) * (1.0 - float(signal_cfg["support_tolerance_pct"])),
                setup_id_anchors=(exhaustion["trade_date"],),
                extra={
                    "island_low": exhaustion["low"],
                    "island_high": exhaustion["high"],
                    "left_gap_pct": exhaustion["left_gap_pct"],
                    "left_volume_ratio": exhaustion["volume_ratio"],
                },
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
                    "volume": snapshot.get("volume"),
                    "atr_14": snapshot.get("atr_14"),
                    "position": position,
                    "avg_entry_price": snapshot.get("avg_entry_price"),
                    "setup": setup_payload,
                    "config": {
                        "downtrend_min_drop_pct": signal_cfg["downtrend_min_drop_pct"],
                        "left_gap_min_pct": signal_cfg["left_gap_min_pct"],
                        "right_gap_min_pct": signal_cfg["right_gap_min_pct"],
                        "retest_window": signal_cfg["retest_window"],
                        "support_tolerance_pct": signal_cfg["support_tolerance_pct"],
                        "max_loss_pct": risk_cfg["max_loss_pct"],
                        "take_profit_atr": risk_cfg["take_profit_atr"],
                    },
                    "strength_inputs": {
                        "stage": stage,
                        "left_gap_pct": setup_payload.get("left_gap_pct"),
                        "right_gap_pct": setup_payload.get("breakout_gap_pct"),
                        "breakout_volume_ratio": setup_payload.get("breakout_volume_ratio"),
                        "left_volume_ratio": setup_payload.get("left_volume_ratio"),
                        "retest_volume_ratio": (
                            current_volume / float(setup_payload["breakout_volume"])
                            if current_volume is not None and float(setup_payload.get("breakout_volume") or 0) > 0
                            else None
                        ),
                        "hold_margin_atr": (
                            (current_close - float(setup_payload["island_high"])) / current_atr
                            if current_close is not None and current_atr is not None and current_atr > 0
                            else None
                        ),
                    },
                },
            )
        )

    return signals


def _find_current_island_exhaustion_gap(
    recent_bars: list[HistoryBar],
    signal_cfg: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Detect only today's causally available first-stage island candidate."""
    if len(recent_bars) < 2:
        return None
    idx = len(recent_bars) - 1
    bar = recent_bars[idx]
    previous = recent_bars[idx - 1]
    high = _safe_float_or_none(bar.get("high"))
    low = _safe_float_or_none(bar.get("low"))
    open_price = _safe_float_or_none(bar.get("open"))
    close = _safe_float_or_none(bar.get("close"))
    volume = _safe_float_or_none(bar.get("volume"))
    average_volume = _safe_float_or_none(bar.get("volume_sma_20"))
    previous_low = _safe_float_or_none(previous.get("low"))
    if any(value is None for value in (high, low, open_price, close, volume, average_volume, previous_low)):
        return None
    assert high is not None and low is not None and open_price is not None and close is not None
    assert volume is not None and average_volume is not None and previous_low is not None
    if average_volume <= 0 or previous_low <= 0 or close >= open_price:
        return None
    gap_pct = (previous_low - high) / previous_low
    volume_ratio = volume / average_volume
    if gap_pct < float(signal_cfg["left_gap_min_pct"]) or volume_ratio > float(signal_cfg["left_volume_ratio_max"]):
        return None
    if not _has_island_downtrend_context(
        recent_bars,
        left_gap_idx=idx,
        downtrend_lookback=int(signal_cfg["downtrend_lookback"]),
        min_drop_pct=float(signal_cfg["downtrend_min_drop_pct"]),
    ):
        return None
    return {
        "trade_date": bar.get("dt_ny"),
        "high": high,
        "low": low,
        "left_gap_pct": gap_pct,
        "volume_ratio": volume_ratio,
    }


def _resolve_standard_staged_exit(
    *,
    recent_bars: list[HistoryBar],
    setup: Dict[str, Any] | None,
    risk_cfg: Dict[str, Any],
    position: float,
    avg_entry_price: float | None,
) -> tuple[Literal["SELL"] | None, str | None, str | None] | None:
    if position <= 0 or setup is None or not recent_bars:
        return None
    current = recent_bars[-1]
    close = _safe_float_or_none(current.get("close"))
    low = _safe_float_or_none(current.get("low"))
    atr = _safe_float_or_none(current.get("atr_14")) or _compute_recent_atr(recent_bars, 20)
    invalidation = _safe_float_or_none(setup.get("invalidation_price"))
    if invalidation is not None and low is not None and low < invalidation:
        return "SELL", "price broke the staged pattern invalidation level", "pattern_invalidation"
    if close is not None and avg_entry_price is not None and close <= avg_entry_price * (1.0 - float(risk_cfg["max_loss_pct"])):
        return "SELL", "price fell more than the configured max-loss threshold from entry", "max_loss_stop"
    if close is not None and atr is not None and avg_entry_price is not None and close <= avg_entry_price - float(risk_cfg["stop_loss_atr"]) * atr:
        return "SELL", "price hit the ATR stop", "atr_stop"
    if close is not None and atr is not None and avg_entry_price is not None and close >= avg_entry_price + float(risk_cfg["take_profit_atr"]) * atr:
        return "SELL", "price reached the ATR take-profit target", "take_profit"
    return None


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


def build_stateful_backtest_signal_state(
    runtime_strategy: RuntimeStrategy,
) -> DoubleBottomBacktestState | SupportResistanceState | None:
    if runtime_strategy.get("strategy_type") == "double_bottom":
        return DoubleBottomBacktestState()
    if runtime_strategy.get("strategy_type") == "support_resistance":
        return SupportResistanceState()
    return None


def generate_stateful_backtest_signals(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: DoubleBottomBacktestState | SupportResistanceState,
    *,
    emit_signals: bool = True,
) -> list[SignalEvent]:
    strategy_type = runtime_strategy.get("strategy_type")
    if strategy_type == "double_bottom" and isinstance(state, DoubleBottomBacktestState):
        return _double_bottom_backtest_handler(
            runtime_strategy,
            market_data_by_symbol,
            state,
            emit_signals=emit_signals,
        )
    if strategy_type == "support_resistance" and isinstance(state, SupportResistanceState):
        return _support_resistance_backtest_handler(
            runtime_strategy,
            market_data_by_symbol,
            state,
            emit_signals=emit_signals,
        )
    return []


def _support_resistance_backtest_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: SupportResistanceState,
    *,
    emit_signals: bool = True,
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    universe = _resolve_strategy_universe(params["universe"], market_data_by_symbol)
    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue
        symbol_state = state.symbols.setdefault(symbol, SupportResistanceSymbolState())
        decision = advance_support_resistance_symbol(
            symbol_state,
            snapshot,
            params["signal"],
            params["risk"],
            emit_signals=emit_signals,
        )
        if decision is not None:
            signals.append(_support_resistance_signal_event(runtime_strategy, symbol, snapshot, decision))
    return signals


def _support_resistance_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _support_resistance_replay_handler_with_state(
        runtime_strategy,
        market_data_by_symbol,
        SupportResistanceState(),
    )


def generate_support_resistance_replay_signals(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: SupportResistanceState,
) -> list[SignalEvent]:
    """Public paper-signal entrypoint that shares the causal replay state machine."""
    return _support_resistance_replay_handler_with_state(
        runtime_strategy,
        market_data_by_symbol,
        state,
    )


def _support_resistance_replay_handler_with_state(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: SupportResistanceState,
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    universe = _resolve_strategy_universe(params["universe"], market_data_by_symbol)
    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue
        symbol_state = state.symbols.setdefault(symbol, SupportResistanceSymbolState())
        history = list(snapshot.get("recent_bars") or [])
        snapshot_date = snapshot.get("dt_ny")
        if not history or history[-1].get("dt_ny") != snapshot_date:
            history.append(snapshot)
        decision = None
        for index, raw_bar in enumerate(history):
            replay_snapshot = dict(raw_bar)
            is_last = index == len(history) - 1
            if is_last:
                replay_snapshot.update(
                    {
                        "position": snapshot.get("position"),
                        "avg_entry_price": snapshot.get("avg_entry_price"),
                        "position_holding_days": snapshot.get("position_holding_days"),
                        "entry_signal_features": snapshot.get("entry_signal_features"),
                    }
                )
            decision = advance_support_resistance_symbol(
                symbol_state,
                replay_snapshot,
                params["signal"],
                params["risk"],
                emit_signals=is_last,
            )
        if decision is not None:
            signals.append(_support_resistance_signal_event(runtime_strategy, symbol, snapshot, decision))
    return signals


def _support_resistance_signal_event(
    runtime_strategy: RuntimeStrategy,
    symbol: str,
    snapshot: MarketSnapshot,
    decision: dict[str, Any],
) -> SignalEvent:
    return SignalEvent(
        strategy_id=runtime_strategy["strategy_id"],
        ts=snapshot.get("ts") or datetime.now(timezone.utc),
        symbol=symbol,
        action=decision["action"],
        reason=decision["reason"],
        score=decision.get("score"),
        metadata={
            "close": snapshot.get("close"),
            "open": snapshot.get("open"),
            "high": snapshot.get("high"),
            "low": snapshot.get("low"),
            "atr_14": snapshot.get("atr_14"),
            "position": float(snapshot.get("position", 0) or 0.0),
            "avg_entry_price": _safe_float_or_none(snapshot.get("avg_entry_price")),
            "support_resistance": decision["support_resistance"],
        },
    )


def _double_bottom_backtest_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: DoubleBottomBacktestState,
    *,
    emit_signals: bool = True,
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

        symbol_state = state.symbols.setdefault(symbol, DoubleBottomBacktestSymbolState())
        _append_double_bottom_backtest_history_bar(symbol_state, snapshot)
        pattern = _advance_double_bottom_backtest_symbol_state(symbol_state, signal_cfg)
        if not emit_signals:
            continue

        recent_bars = symbol_state.history_bars
        position = float(snapshot.get("position", 0) or 0.0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))
        action = reason = stage = None
        setup_payload: Dict[str, Any] | None = None
        if position > 0:
            exit_setup = _extract_double_bottom_position_setup(snapshot)
            if exit_setup is None and pattern is not None:
                exit_setup = _build_double_bottom_setup_payload(recent_bars, pattern)
            if exit_setup is not None:
                action, reason, stage = _resolve_double_bottom_exit_action(
                    recent_bars=recent_bars,
                    setup=exit_setup,
                    signal_cfg=signal_cfg,
                    risk_cfg=risk_cfg,
                    avg_entry_price=avg_entry_price,
                )
                if action is not None:
                    setup_payload = {**exit_setup, "exit_stage": stage}
        if action is None:
            candidate = symbol_state.right_candidates[-1] if symbol_state.right_candidates else None
            entry = _resolve_double_bottom_staged_entry(
                symbol=symbol,
                recent_bars=recent_bars,
                candidate=candidate,
                pattern=pattern,
                signal_cfg=signal_cfg,
                risk_cfg=risk_cfg,
            )
            if entry is not None:
                action, reason, stage, setup_payload = entry

        if action is None or reason is None or stage is None or setup_payload is None:
            continue

        score = _compute_double_bottom_signal_score(setup_payload)
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
                    "strength_inputs": _double_bottom_strength_inputs(snapshot, setup_payload),
                    "config": {
                        "downtrend_min_drop_pct": signal_cfg["downtrend_min_drop_pct"],
                        "downtrend_max_up_day_ratio": signal_cfg["downtrend_max_up_day_ratio"],
                        "downtrend_min_r_squared": signal_cfg["downtrend_min_r_squared"],
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


# Evaluate conservative double-bottom setups using recent OHLCV history and position-aware exits.
# Input: runtime strategy payload plus the symbol -> snapshot map with recent_bars populated.
# Output: BUY/SELL SignalEvent objects for retest-only entries or exit conditions.
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
        candidate, pattern = _replay_double_bottom_state(recent_bars, signal_cfg)
        position = float(snapshot.get("position", 0) or 0.0)
        avg_entry_price = _safe_float_or_none(snapshot.get("avg_entry_price"))
        action = reason = stage = None
        setup_payload: Dict[str, Any] | None = None
        if position > 0:
            exit_setup = _extract_double_bottom_position_setup(snapshot)
            if exit_setup is None and pattern is not None:
                exit_setup = _build_double_bottom_setup_payload(recent_bars, pattern)
            if exit_setup is not None:
                action, reason, stage = _resolve_double_bottom_exit_action(
                    recent_bars=recent_bars,
                    setup=exit_setup,
                    signal_cfg=signal_cfg,
                    risk_cfg=risk_cfg,
                    avg_entry_price=avg_entry_price,
                )
                if action is not None:
                    setup_payload = {**exit_setup, "exit_stage": stage}
        if action is None:
            entry = _resolve_double_bottom_staged_entry(
                symbol=symbol,
                recent_bars=recent_bars,
                candidate=candidate,
                pattern=pattern,
                signal_cfg=signal_cfg,
                risk_cfg=risk_cfg,
            )
            if entry is not None:
                action, reason, stage, setup_payload = entry

        if action is None or reason is None or stage is None or setup_payload is None:
            continue

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
                    "volume": snapshot.get("volume"),
                    "atr_14": snapshot.get("atr_14"),
                    "position": position,
                    "avg_entry_price": avg_entry_price,
                    "setup": setup_payload,
                    "strength_inputs": _double_bottom_strength_inputs(snapshot, setup_payload),
                    "config": {
                        "downtrend_min_drop_pct": signal_cfg["downtrend_min_drop_pct"],
                        "downtrend_max_up_day_ratio": signal_cfg["downtrend_max_up_day_ratio"],
                        "downtrend_min_r_squared": signal_cfg["downtrend_min_r_squared"],
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


def _bottom_reversal_handler(
    pattern_type: str,
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    """Route one of the new causal bottom-pattern detectors into normalized signals."""
    params = runtime_strategy["params"]
    universe = _resolve_strategy_universe(params["universe"], market_data_by_symbol)
    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue
        position = float(snapshot.get("position", 0) or 0.0)
        decision = evaluate_bottom_reversal(
            pattern_type=pattern_type,
            symbol=symbol,
            recent_bars=snapshot.get("recent_bars") or [],
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=position,
            avg_entry_price=_safe_float_or_none(snapshot.get("avg_entry_price")),
            entry_signal_features=(
                snapshot.get("entry_signal_features")
                if isinstance(snapshot.get("entry_signal_features"), dict)
                else None
            ),
        )
        if decision is None:
            continue
        signals.append(
            SignalEvent(
                strategy_id=runtime_strategy["strategy_id"],
                ts=snapshot.get("ts") or datetime.now(timezone.utc),
                symbol=symbol,
                action=decision.action,
                reason=decision.reason,
                metadata={
                    "close": snapshot.get("close"),
                    "open": snapshot.get("open"),
                    "high": snapshot.get("high"),
                    "low": snapshot.get("low"),
                    "volume": snapshot.get("volume"),
                    "atr_14": snapshot.get("atr_14"),
                    "position": position,
                    "avg_entry_price": snapshot.get("avg_entry_price"),
                    "setup": decision.setup,
                    "strength_inputs": decision.strength_inputs,
                    "price_semantics": "forward_adjusted_fallback_unadjusted",
                },
            )
        )
    return signals


def _head_shoulders_bottom_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _bottom_reversal_handler("head_shoulders_bottom", runtime_strategy, market_data_by_symbol)


def _rounded_bottom_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _bottom_reversal_handler("rounded_bottom", runtime_strategy, market_data_by_symbol)


def _v_reversal_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _bottom_reversal_handler("v_reversal", runtime_strategy, market_data_by_symbol)


# ============================================================================
# Double bottom helpers
# ============================================================================

def _append_double_bottom_backtest_history_bar(
    symbol_state: DoubleBottomBacktestSymbolState,
    snapshot: MarketSnapshot,
) -> None:
    history_bar = _build_history_bar_from_snapshot(snapshot)
    history_trade_date = _safe_date_or_none(history_bar.get("dt_ny"))
    if symbol_state.history_bars:
        last_trade_date = _safe_date_or_none(symbol_state.history_bars[-1].get("dt_ny"))
        if history_trade_date is not None and history_trade_date == last_trade_date:
            symbol_state.history_bars[-1] = history_bar
            return
    symbol_state.history_bars.append(history_bar)


def _build_history_bar_from_snapshot(snapshot: MarketSnapshot) -> HistoryBar:
    return {
        "dt_ny": _safe_date_or_none(snapshot.get("dt_ny")),
        "ts": snapshot.get("ts") or datetime.now(timezone.utc),
        "open": _safe_float_or_none(snapshot.get("open")),
        "high": _safe_float_or_none(snapshot.get("high")),
        "low": _safe_float_or_none(snapshot.get("low")),
        "close": _safe_float_or_none(snapshot.get("close")),
        "volume": _safe_float_or_none(snapshot.get("volume")),
        "atr_14": _safe_float_or_none(snapshot.get("atr_14")),
        "volume_sma_20": _safe_float_or_none(snapshot.get("volume_sma_20")),
        "ret_20d": _safe_float_or_none(snapshot.get("ret_20d")),
        "ret_60d": _safe_float_or_none(snapshot.get("ret_60d")),
        "sma_20": _safe_float_or_none(snapshot.get("sma_20")),
        "sma_50": _safe_float_or_none(snapshot.get("sma_50")),
    }


def _advance_double_bottom_backtest_symbol_state(
    symbol_state: DoubleBottomBacktestSymbolState,
    signal_cfg: Dict[str, Any],
) -> DoubleBottomPattern | None:
    recent_bars = symbol_state.history_bars
    if len(recent_bars) < 2:
        return symbol_state.best_pattern

    min_bottom_spacing = int(signal_cfg["min_bottom_spacing"])
    max_bottom_spacing = int(signal_cfg["max_bottom_spacing"])
    left_bottom_before_bars = int(signal_cfg.get("left_bottom_before_bars", 1))
    left_bottom_after_bars = int(signal_cfg.get("left_bottom_after_bars", 1))
    downtrend_lookback = int(signal_cfg["downtrend_lookback"])
    downtrend_min_drop_pct = float(signal_cfg["downtrend_min_drop_pct"])
    downtrend_max_up_day_ratio = float(signal_cfg["downtrend_max_up_day_ratio"])
    downtrend_min_r_squared = float(signal_cfg["downtrend_min_r_squared"])
    bottom_tolerance_pct = float(signal_cfg["bottom_tolerance_pct"])
    neckline_min_rebound_pct = float(signal_cfg["neckline_min_rebound_pct"])
    rebound_up_day_ratio_min = float(signal_cfg["rebound_up_day_ratio_min"])
    second_bottom_volume_ratio_max = float(signal_cfg["second_bottom_volume_ratio_max"])
    breakout_volume_ratio_min = float(signal_cfg["breakout_volume_ratio_min"])
    max_breakout_bars_after_right_bottom = int(signal_cfg.get("max_breakout_bars_after_right_bottom", 40))
    breakout_buffer_pct = float(signal_cfg["breakout_buffer_pct"])
    current_idx = len(recent_bars) - 1

    left_candidate = _build_double_bottom_left_candidate(
        recent_bars,
        current_idx=current_idx,
        downtrend_lookback=downtrend_lookback,
        downtrend_min_drop_pct=downtrend_min_drop_pct,
        downtrend_max_up_day_ratio=downtrend_max_up_day_ratio,
        downtrend_min_r_squared=downtrend_min_r_squared,
        left_bottom_before_bars=left_bottom_before_bars,
        left_bottom_after_bars=left_bottom_after_bars,
        bottom_volume_ratio_max=second_bottom_volume_ratio_max,
    )
    if (
        left_candidate is not None
        and all(existing.left_bottom_idx != left_candidate.left_bottom_idx for existing in symbol_state.left_candidates)
    ):
        symbol_state.left_candidates.append(left_candidate)

    symbol_state.left_candidates = [
        candidate
        for candidate in symbol_state.left_candidates
        if current_idx <= candidate.left_bottom_idx + max_bottom_spacing + 1
    ]

    right_bottom_idx = current_idx - left_bottom_after_bars
    if right_bottom_idx >= 0:
        new_right_candidates = _promote_double_bottom_right_candidates(
            recent_bars,
            left_candidates=symbol_state.left_candidates,
            right_bottom_idx=right_bottom_idx,
            min_bottom_spacing=min_bottom_spacing,
            max_bottom_spacing=max_bottom_spacing,
            bottom_tolerance_pct=bottom_tolerance_pct,
            neckline_min_rebound_pct=neckline_min_rebound_pct,
            rebound_up_day_ratio_min=rebound_up_day_ratio_min,
            bottom_volume_ratio_max=second_bottom_volume_ratio_max,
            pivot_before_bars=left_bottom_before_bars,
            pivot_after_bars=left_bottom_after_bars,
        )
        existing_pairs = {
            (candidate.left_bottom_idx, candidate.right_bottom_idx)
            for candidate in symbol_state.right_candidates
        }
        for candidate in new_right_candidates:
            pair = (candidate.left_bottom_idx, candidate.right_bottom_idx)
            if pair not in existing_pairs:
                symbol_state.right_candidates.append(candidate)
                existing_pairs.add(pair)

    active_right_candidates: list[DoubleBottomRightCandidate] = []
    for right_candidate in symbol_state.right_candidates:
        if current_idx > right_candidate.right_bottom_idx + max_breakout_bars_after_right_bottom:
            continue

        pattern = _build_double_bottom_pattern_from_right_candidate(
            recent_bars,
            right_candidate=right_candidate,
            breakout_idx=current_idx,
            breakout_buffer_pct=breakout_buffer_pct,
            breakout_volume_ratio_min=breakout_volume_ratio_min,
        )
        if pattern is None:
            active_right_candidates.append(right_candidate)
            continue

        if _is_preferred_double_bottom_pattern(pattern, symbol_state.best_pattern):
            symbol_state.best_pattern = pattern

    symbol_state.right_candidates = active_right_candidates
    return symbol_state.best_pattern


def _replay_double_bottom_state(
    recent_bars: list[HistoryBar],
    signal_cfg: Dict[str, Any],
) -> tuple[DoubleBottomRightCandidate | None, DoubleBottomPattern | None]:
    """Rebuild the causal state for daily/Paper evaluation without future bars."""
    state = DoubleBottomBacktestSymbolState()
    pattern = None
    for bar in recent_bars:
        state.history_bars.append(dict(bar))
        pattern = _advance_double_bottom_backtest_symbol_state(state, signal_cfg)
    candidate = state.right_candidates[-1] if state.right_candidates else None
    return candidate, pattern


def _resolve_double_bottom_staged_entry(
    *,
    symbol: str,
    recent_bars: list[HistoryBar],
    candidate: DoubleBottomRightCandidate | None,
    pattern: DoubleBottomPattern | None,
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
) -> tuple[Literal["BUY"], str, str, Dict[str, Any]] | None:
    if not recent_bars:
        return None
    current_idx = len(recent_bars) - 1
    if pattern is not None and current_idx == pattern.breakout_idx:
        setup = _build_staged_double_bottom_setup(
            symbol=symbol,
            recent_bars=recent_bars,
            candidate=DoubleBottomRightCandidate(
                left_bottom_idx=pattern.left_bottom_idx,
                neckline_idx=pattern.neckline_idx,
                right_bottom_idx=pattern.right_bottom_idx,
                left_bottom_low=pattern.left_bottom_low,
                right_bottom_low=pattern.right_bottom_low,
                neckline_price=pattern.neckline_price,
                bottom_distance_pct=pattern.bottom_distance_pct,
                rebound_up_day_ratio=pattern.rebound_up_day_ratio,
            ),
            risk_cfg=risk_cfg,
            signal_cfg=signal_cfg,
            stage_index=3,
            stage_key="neckline_breakout",
            pattern=pattern,
        )
        return "BUY", "broke above the double-bottom neckline on confirming volume", "neckline_breakout", setup
    if candidate is None:
        return None
    confirmation_bars = int(signal_cfg.get("left_bottom_after_bars", 1))
    if current_idx == candidate.right_bottom_idx + confirmation_bars:
        setup = _build_staged_double_bottom_setup(
            symbol=symbol,
            recent_bars=recent_bars,
            candidate=candidate,
            risk_cfg=risk_cfg,
            signal_cfg=signal_cfg,
            stage_index=1,
            stage_key="second_bottom",
        )
        return "BUY", "confirmed a low-volume second bottom", "second_bottom", setup
    if not _is_first_double_bottom_right_pullback(recent_bars, candidate, signal_cfg):
        return None
    setup = _build_staged_double_bottom_setup(
        symbol=symbol,
        recent_bars=recent_bars,
        candidate=candidate,
        risk_cfg=risk_cfg,
        signal_cfg=signal_cfg,
        stage_index=2,
        stage_key="right_side_pullback",
    )
    return "BUY", "confirmed the first low-volume right-side pullback above the second bottom", "right_side_pullback", setup


def _is_first_double_bottom_right_pullback(
    recent_bars: list[HistoryBar],
    candidate: DoubleBottomRightCandidate,
    signal_cfg: Dict[str, Any],
) -> bool:
    current_idx = len(recent_bars) - 1
    start = candidate.right_bottom_idx + int(signal_cfg.get("left_bottom_after_bars", 1)) + 1
    if current_idx < start + 1:
        return False
    halfway = candidate.right_bottom_low + (candidate.neckline_price - candidate.right_bottom_low) * 0.5
    prior_closes = [_safe_float_or_none(bar.get("close")) for bar in recent_bars[start:current_idx]]
    if not prior_closes or max((value for value in prior_closes if value is not None), default=0.0) < halfway:
        return False

    def qualifies(idx: int) -> bool:
        bar = recent_bars[idx]
        close = _safe_float_or_none(bar.get("close"))
        low = _safe_float_or_none(bar.get("low"))
        previous_close = _safe_float_or_none(recent_bars[idx - 1].get("close"))
        volume = _safe_float_or_none(bar.get("volume"))
        average_volume = _safe_float_or_none(bar.get("volume_sma_20"))
        return bool(
            close is not None
            and low is not None
            and previous_close is not None
            and volume is not None
            and average_volume is not None
            and average_volume > 0
            and close < previous_close
            and low > candidate.right_bottom_low
            and close > candidate.right_bottom_low
            and volume / average_volume <= float(signal_cfg["second_bottom_volume_ratio_max"])
        )

    if not qualifies(current_idx):
        return False
    return not any(qualifies(idx) for idx in range(start, current_idx))


def _build_staged_double_bottom_setup(
    *,
    symbol: str,
    recent_bars: list[HistoryBar],
    candidate: DoubleBottomRightCandidate,
    risk_cfg: Dict[str, Any],
    signal_cfg: Dict[str, Any],
    stage_index: int,
    stage_key: str,
    pattern: DoubleBottomPattern | None = None,
) -> Dict[str, Any]:
    extra: Dict[str, Any] = {
        "left_bottom_trade_date": str(recent_bars[candidate.left_bottom_idx]["dt_ny"]),
        "neckline_trade_date": str(recent_bars[candidate.neckline_idx]["dt_ny"]),
        "right_bottom_trade_date": str(recent_bars[candidate.right_bottom_idx]["dt_ny"]),
        "left_bottom_low": candidate.left_bottom_low,
        "right_bottom_low": candidate.right_bottom_low,
        "neckline_price": candidate.neckline_price,
        "bottom_distance_pct": candidate.bottom_distance_pct,
        "rebound_up_day_ratio": candidate.rebound_up_day_ratio,
    }
    if pattern is not None:
        extra.update(_build_double_bottom_setup_payload(recent_bars, pattern))
    return build_pattern_setup(
        pattern_type="double_bottom",
        symbol=symbol,
        stage_index=stage_index,
        stage_key=stage_key,
        risk_cfg=risk_cfg,
        anchors={
            "left_bottom_trade_date": extra["left_bottom_trade_date"],
            "right_bottom_trade_date": extra["right_bottom_trade_date"],
            "left_bottom_price": candidate.left_bottom_low,
            "right_bottom_price": candidate.right_bottom_low,
            "neckline_price": candidate.neckline_price,
        },
        invalidation_price=min(candidate.left_bottom_low, candidate.right_bottom_low) * (1.0 - float(signal_cfg["support_tolerance_pct"])),
        setup_id_anchors=(extra["left_bottom_trade_date"], extra["right_bottom_trade_date"]),
        extra=extra,
    )

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
        "breakout_volume": pattern.breakout_volume,
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
    if (
        left_bottom_low is None
        or right_bottom_low is None
        or neckline_price is None
    ):
        return None

    setup = dict(raw_setup)
    setup["left_bottom_low"] = left_bottom_low
    setup["right_bottom_low"] = right_bottom_low
    setup["neckline_price"] = neckline_price
    setup["breakout_close"] = _safe_float_or_none(raw_setup.get("breakout_close"))
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


def _double_bottom_strength_inputs(
    snapshot: MarketSnapshot,
    setup: Dict[str, Any],
) -> Dict[str, Any]:
    neckline_price = _safe_float_or_none(setup.get("neckline_price"))
    breakout_close = _safe_float_or_none(setup.get("breakout_close"))
    breakout_volume = _safe_float_or_none(setup.get("breakout_volume"))
    current_volume = _safe_float_or_none(snapshot.get("volume"))
    return {
        "stage": setup.get("stage_key") or setup.get("stage"),
        "bottom_distance_pct": _safe_float_or_none(setup.get("bottom_distance_pct")),
        "rebound_up_day_ratio": _safe_float_or_none(setup.get("rebound_up_day_ratio")),
        "current_volume_ratio": (
            current_volume / float(snapshot["volume_sma_20"])
            if current_volume is not None and _safe_float_or_none(snapshot.get("volume_sma_20")) not in {None, 0}
            else None
        ),
        "pullback_hold_pct": (
            (_safe_float_or_none(snapshot.get("close")) - _safe_float_or_none(setup.get("right_bottom_low")))
            / max(_safe_float_or_none(setup.get("neckline_price")) - _safe_float_or_none(setup.get("right_bottom_low")), 1e-12)
            if _safe_float_or_none(snapshot.get("close")) is not None
            and _safe_float_or_none(setup.get("right_bottom_low")) is not None
            and _safe_float_or_none(setup.get("neckline_price")) is not None
            else None
        ),
        "breakout_volume_ratio": _safe_float_or_none(setup.get("breakout_volume_ratio")),
        "breakout_extension_pct": (
            (breakout_close / neckline_price) - 1.0
            if breakout_close is not None and neckline_price is not None and neckline_price > 0
            else None
        ),
        "retest_volume_ratio": (
            current_volume / breakout_volume
            if current_volume is not None and breakout_volume is not None and breakout_volume > 0
            else None
        ),
    }


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
    if left_bottom_low is None or right_bottom_low is None:
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
        and breakout_close is not None
        and breakout_atr is not None
        and current_close >= breakout_close + (float(risk_cfg["take_profit_atr"]) * breakout_atr)
    ):
        return "SELL", "price reached the ATR take-profit target from the breakout confirmation", "take_profit"
    if (
        current_close is not None
        and current_atr is not None
        and (breakout_close is not None or avg_entry_price is not None)
        and current_close < (breakout_close if breakout_close is not None else float(avg_entry_price)) - (float(risk_cfg["stop_loss_atr"]) * current_atr)
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
    downtrend_max_up_day_ratio = float(signal_cfg["downtrend_max_up_day_ratio"])
    downtrend_min_r_squared = float(signal_cfg["downtrend_min_r_squared"])
    bottom_tolerance_pct = float(signal_cfg["bottom_tolerance_pct"])
    neckline_min_rebound_pct = float(signal_cfg["neckline_min_rebound_pct"])
    rebound_up_day_ratio_min = float(signal_cfg["rebound_up_day_ratio_min"])
    second_bottom_volume_ratio_max = float(signal_cfg["second_bottom_volume_ratio_max"])
    breakout_volume_ratio_min = float(signal_cfg["breakout_volume_ratio_min"])
    max_breakout_bars_after_right_bottom = int(signal_cfg.get("max_breakout_bars_after_right_bottom", 40))
    breakout_buffer_pct = float(signal_cfg["breakout_buffer_pct"])

    left_candidates: list[DoubleBottomLeftCandidate] = []
    right_candidates: list[DoubleBottomRightCandidate] = []
    best_pattern: DoubleBottomPattern | None = None

    for current_idx in range(len(recent_bars)):
        left_candidate = _build_double_bottom_left_candidate(
            recent_bars,
            current_idx=current_idx,
            downtrend_lookback=downtrend_lookback,
            downtrend_min_drop_pct=downtrend_min_drop_pct,
            downtrend_max_up_day_ratio=downtrend_max_up_day_ratio,
            downtrend_min_r_squared=downtrend_min_r_squared,
            left_bottom_before_bars=left_bottom_before_bars,
            left_bottom_after_bars=left_bottom_after_bars,
            bottom_volume_ratio_max=second_bottom_volume_ratio_max,
        )
        if left_candidate is not None:
            left_candidates.append(left_candidate)

        right_bottom_idx = current_idx - left_bottom_after_bars
        if right_bottom_idx >= 0:
            right_candidates.extend(
                _promote_double_bottom_right_candidates(
                    recent_bars,
                    left_candidates=left_candidates,
                    right_bottom_idx=right_bottom_idx,
                    min_bottom_spacing=min_bottom_spacing,
                    max_bottom_spacing=max_bottom_spacing,
                    bottom_tolerance_pct=bottom_tolerance_pct,
                    neckline_min_rebound_pct=neckline_min_rebound_pct,
                    rebound_up_day_ratio_min=rebound_up_day_ratio_min,
                    bottom_volume_ratio_max=second_bottom_volume_ratio_max,
                    pivot_before_bars=left_bottom_before_bars,
                    pivot_after_bars=left_bottom_after_bars,
                )
            )

        if not right_candidates:
            continue

        active_right_candidates: list[DoubleBottomRightCandidate] = []
        for right_candidate in right_candidates:
            if current_idx > right_candidate.right_bottom_idx + max_breakout_bars_after_right_bottom:
                continue
            pattern = _build_double_bottom_pattern_from_right_candidate(
                recent_bars,
                right_candidate=right_candidate,
                breakout_idx=current_idx,
                breakout_buffer_pct=breakout_buffer_pct,
                breakout_volume_ratio_min=breakout_volume_ratio_min,
            )
            if pattern is None:
                active_right_candidates.append(right_candidate)
                continue

            if _is_preferred_double_bottom_pattern(pattern, best_pattern):
                best_pattern = pattern
        right_candidates = active_right_candidates

    return best_pattern


def _build_double_bottom_left_candidate(
    recent_bars: list[HistoryBar],
    *,
    current_idx: int,
    downtrend_lookback: int,
    downtrend_min_drop_pct: float,
    downtrend_max_up_day_ratio: float,
    downtrend_min_r_squared: float,
    left_bottom_before_bars: int,
    left_bottom_after_bars: int,
    bottom_volume_ratio_max: float,
) -> DoubleBottomLeftCandidate | None:
    left_bottom_idx = current_idx - left_bottom_after_bars
    if left_bottom_idx < left_bottom_before_bars:
        return None
    if not _is_local_minimum(
        recent_bars,
        left_bottom_idx,
        before_span=left_bottom_before_bars,
        after_span=left_bottom_after_bars,
    ):
        return None

    left_bottom_bar = recent_bars[left_bottom_idx]
    left_bottom_low = _safe_float_or_none(left_bottom_bar.get("low"))
    left_bottom_volume = _safe_float_or_none(left_bottom_bar.get("volume"))
    left_bottom_avg_volume = _safe_float_or_none(left_bottom_bar.get("volume_sma_20"))
    if left_bottom_low is None or left_bottom_low <= 0:
        return None
    if (
        left_bottom_volume is None
        or left_bottom_avg_volume is None
        or left_bottom_avg_volume <= 0
    ):
        return None
    if left_bottom_volume / left_bottom_avg_volume > bottom_volume_ratio_max:
        return None
    if not _has_double_bottom_downtrend_context(
        recent_bars,
        left_bottom_idx=left_bottom_idx,
        downtrend_lookback=downtrend_lookback,
        min_drop_pct=downtrend_min_drop_pct,
    ):
        return None
    if not _has_smooth_double_bottom_downtrend(
        recent_bars,
        left_bottom_idx=left_bottom_idx,
        downtrend_lookback=downtrend_lookback,
        max_up_day_ratio=downtrend_max_up_day_ratio,
        min_r_squared=downtrend_min_r_squared,
    ):
        return None

    return DoubleBottomLeftCandidate(
        left_bottom_idx=left_bottom_idx,
        left_bottom_low=left_bottom_low,
    )


def _promote_double_bottom_right_candidates(
    recent_bars: list[HistoryBar],
    *,
    left_candidates: list[DoubleBottomLeftCandidate],
    right_bottom_idx: int,
    min_bottom_spacing: int,
    max_bottom_spacing: int,
    bottom_tolerance_pct: float,
    neckline_min_rebound_pct: float,
    rebound_up_day_ratio_min: float,
    bottom_volume_ratio_max: float,
    pivot_before_bars: int = 1,
    pivot_after_bars: int = 1,
) -> list[DoubleBottomRightCandidate]:
    if not left_candidates or not _is_local_minimum(
        recent_bars,
        right_bottom_idx,
        before_span=pivot_before_bars,
        after_span=pivot_after_bars,
    ):
        return []

    candidates: list[DoubleBottomRightCandidate] = []
    for left_candidate in left_candidates:
        spacing = right_bottom_idx - left_candidate.left_bottom_idx
        if spacing < min_bottom_spacing or spacing > max_bottom_spacing:
            continue

        right_candidate = _build_double_bottom_right_candidate(
            recent_bars,
            left_candidate=left_candidate,
            right_bottom_idx=right_bottom_idx,
            bottom_tolerance_pct=bottom_tolerance_pct,
            neckline_min_rebound_pct=neckline_min_rebound_pct,
            rebound_up_day_ratio_min=rebound_up_day_ratio_min,
            bottom_volume_ratio_max=bottom_volume_ratio_max,
        )
        if right_candidate is not None:
            candidates.append(right_candidate)

    return candidates


def _build_double_bottom_right_candidate(
    recent_bars: list[HistoryBar],
    *,
    left_candidate: DoubleBottomLeftCandidate,
    right_bottom_idx: int,
    bottom_tolerance_pct: float,
    neckline_min_rebound_pct: float,
    rebound_up_day_ratio_min: float,
    bottom_volume_ratio_max: float,
) -> DoubleBottomRightCandidate | None:
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
        return None
    if right_bottom_volume / right_bottom_avg_volume > bottom_volume_ratio_max:
        return None

    left_bottom_idx = left_candidate.left_bottom_idx
    left_bottom_low = left_candidate.left_bottom_low
    bottom_distance_pct = abs(right_bottom_low - left_bottom_low) / max(
        left_bottom_low,
        right_bottom_low,
    )
    if bottom_distance_pct > bottom_tolerance_pct:
        return None
    if right_bottom_low < left_bottom_low * (1.0 - bottom_tolerance_pct):
        return None
    if not _double_bottom_intermediate_lows_hold(
        recent_bars,
        left_bottom_idx=left_bottom_idx,
        right_bottom_idx=right_bottom_idx,
        floor_low=min(left_bottom_low, right_bottom_low),
    ):
        return None

    neckline_idx, neckline_price = _find_double_bottom_neckline(
        recent_bars,
        left_bottom_idx=left_bottom_idx,
        right_bottom_idx=right_bottom_idx,
    )
    if neckline_idx is None or neckline_price is None or neckline_price <= 0:
        return None
    if neckline_price < max(left_bottom_low, right_bottom_low) * (1.0 + neckline_min_rebound_pct):
        return None

    rebound_up_day_ratio = _compute_up_day_ratio(
        recent_bars,
        start_idx=left_bottom_idx,
        end_idx=right_bottom_idx,
    )
    if rebound_up_day_ratio is None or rebound_up_day_ratio < rebound_up_day_ratio_min:
        return None

    return DoubleBottomRightCandidate(
        left_bottom_idx=left_bottom_idx,
        neckline_idx=neckline_idx,
        right_bottom_idx=right_bottom_idx,
        left_bottom_low=left_bottom_low,
        right_bottom_low=right_bottom_low,
        neckline_price=neckline_price,
        bottom_distance_pct=bottom_distance_pct,
        rebound_up_day_ratio=rebound_up_day_ratio,
    )


def _build_double_bottom_pattern_from_right_candidate(
    recent_bars: list[HistoryBar],
    *,
    right_candidate: DoubleBottomRightCandidate,
    breakout_idx: int,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
) -> DoubleBottomPattern | None:
    if breakout_idx <= right_candidate.right_bottom_idx:
        return None

    breakout_match = _match_double_bottom_breakout_bar(
        recent_bars[breakout_idx],
        neckline_price=right_candidate.neckline_price,
        breakout_buffer_pct=breakout_buffer_pct,
        breakout_volume_ratio_min=breakout_volume_ratio_min,
    )
    if breakout_match is None:
        return None

    breakout_close, breakout_volume, breakout_avg_volume = breakout_match
    return DoubleBottomPattern(
        left_bottom_idx=right_candidate.left_bottom_idx,
        neckline_idx=right_candidate.neckline_idx,
        right_bottom_idx=right_candidate.right_bottom_idx,
        breakout_idx=breakout_idx,
        left_bottom_low=right_candidate.left_bottom_low,
        right_bottom_low=right_candidate.right_bottom_low,
        neckline_price=right_candidate.neckline_price,
        breakout_close=breakout_close,
        breakout_volume=breakout_volume,
        breakout_volume_ratio=breakout_volume / breakout_avg_volume,
        bottom_distance_pct=right_candidate.bottom_distance_pct,
        rebound_up_day_ratio=right_candidate.rebound_up_day_ratio,
    )


def _is_preferred_double_bottom_pattern(
    candidate: DoubleBottomPattern,
    incumbent: DoubleBottomPattern | None,
) -> bool:
    return (
        incumbent is None
        or candidate.breakout_idx > incumbent.breakout_idx
        or (
            candidate.breakout_idx == incumbent.breakout_idx
            and candidate.right_bottom_idx > incumbent.right_bottom_idx
        )
    )
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


def _compute_linear_trend_fit(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 2:
        return None

    count = len(values)
    x_mean = (count - 1) / 2.0
    y_mean = sum(values) / float(count)
    sum_squared_x = 0.0
    sum_xy = 0.0
    total_variance = 0.0
    for idx, value in enumerate(values):
        x_delta = idx - x_mean
        y_delta = value - y_mean
        sum_squared_x += x_delta * x_delta
        sum_xy += x_delta * y_delta
        total_variance += y_delta * y_delta

    if sum_squared_x <= 0:
        return None

    slope = sum_xy / sum_squared_x
    intercept = y_mean - (slope * x_mean)
    if total_variance <= 0:
        return slope, 1.0

    residual_variance = 0.0
    for idx, value in enumerate(values):
        fitted_value = intercept + (slope * idx)
        residual = value - fitted_value
        residual_variance += residual * residual

    r_squared = 1.0 - (residual_variance / total_variance)
    return slope, max(0.0, min(1.0, r_squared))


def _has_smooth_double_bottom_downtrend(
    recent_bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    downtrend_lookback: int,
    max_up_day_ratio: float,
    min_r_squared: float,
) -> bool:
    anchor_idx = left_bottom_idx - downtrend_lookback
    if anchor_idx < 0:
        return False

    up_day_ratio = _compute_up_day_ratio(
        recent_bars,
        start_idx=anchor_idx,
        end_idx=left_bottom_idx,
    )
    if up_day_ratio is None or up_day_ratio > max_up_day_ratio:
        return False

    closes: list[float] = []
    for idx in range(anchor_idx, left_bottom_idx + 1):
        close = _safe_float_or_none(recent_bars[idx].get("close"))
        if close is None or close <= 0:
            return False
        closes.append(close)

    trend_fit = _compute_linear_trend_fit(closes)
    if trend_fit is None:
        return False

    slope, r_squared = trend_fit
    return slope < 0 and r_squared >= min_r_squared


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

    return lookback_return is not None and lookback_return <= -min_drop_pct


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


def _match_double_bottom_breakout_bar(
    bar: HistoryBar,
    *,
    neckline_price: float,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
) -> tuple[float, float, float] | None:
    breakout_threshold = neckline_price * (1.0 + breakout_buffer_pct)
    high = _safe_float_or_none(bar.get("high"))
    close = _safe_float_or_none(bar.get("close"))
    volume = _safe_float_or_none(bar.get("volume"))
    avg_volume = _safe_float_or_none(bar.get("volume_sma_20"))
    if (
        high is None
        or close is None
        or volume is None
        or avg_volume is None
        or avg_volume <= 0
    ):
        return None
    if high <= breakout_threshold:
        return None
    if volume / avg_volume < breakout_volume_ratio_min:
        return None
    return close, volume, avg_volume


def _find_first_double_bottom_breakout_idx(
    recent_bars: list[HistoryBar],
    *,
    right_bottom_idx: int,
    neckline_price: float,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
    max_breakout_bars_after_right_bottom: int,
) -> int | None:
    breakout_search_end = min(
        len(recent_bars),
        right_bottom_idx + max_breakout_bars_after_right_bottom + 1,
    )
    for idx in range(right_bottom_idx + 1, breakout_search_end):
        if _match_double_bottom_breakout_bar(
            recent_bars[idx],
            neckline_price=neckline_price,
            breakout_buffer_pct=breakout_buffer_pct,
            breakout_volume_ratio_min=breakout_volume_ratio_min,
        ) is not None:
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
    "momentum_breakout": _momentum_breakout_handler,
    "island_reversal": _island_reversal_handler,
    "double_bottom": _double_bottom_handler,
    "head_shoulders_bottom": _head_shoulders_bottom_handler,
    "rounded_bottom": _rounded_bottom_handler,
    "v_reversal": _v_reversal_handler,
    "support_resistance": _support_resistance_handler,
}
