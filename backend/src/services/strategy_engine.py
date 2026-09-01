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
from src.services.patterns import double_bottom, head_shoulders_bottom, island_reversal, rounded_bottom, v_reversal
from src.services.patterns.models import PatternContext, PatternDecision, PatternEvaluator
from src.services.signal_strength_service import annotate_and_rank_signals
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
from src.services.strategy_types import (
    HistoryBar,
    MarketDataBySymbol,
    MarketSnapshot,
    RuntimeStrategy,
)

StrategyHandler = Callable[[RuntimeStrategy, MarketDataBySymbol], list["SignalEvent"]]

RECENT_BAR_COUNT = 40
RECENT_BAR_LOOKBACK_DAYS = 90
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

        handler = STRATEGY_HANDLERS[runtime["strategy_type"]]

        replay_state: SupportResistanceState | None = None
        replay_symbols: list[str] = []
        replay_dates: list[date] = []
        if runtime["strategy_type"] == "support_resistance":
            replay_symbols = _resolve_strategy_universe(runtime["params"]["universe"], snapshots)
            for symbol in replay_symbols:
                for bar in (snapshots.get(symbol) or {}).get("recent_bars") or []:
                    value = bar.get("dt_ny")
                    if value is not None:
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

        handler = STRATEGY_HANDLERS[runtime["strategy_type"]]

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


def required_recent_bar_count_for_runtime(runtime_strategy: RuntimeStrategy) -> int:
    recent_bar_count = RECENT_BAR_COUNT
    strategy_type = runtime_strategy["strategy_type"]
    signal_cfg = runtime_strategy["params"]["signal"]
    risk_cfg = runtime_strategy["params"]["risk"]

    if strategy_type == "island_reversal":
        downtrend_lookback = int(signal_cfg["downtrend_lookback"])
        max_island_bars = int(signal_cfg["max_island_bars"])
        retest_window = int(signal_cfg["retest_window"])
        return max(
            recent_bar_count,
            downtrend_lookback + max_island_bars + retest_window + 2,
        )

    if strategy_type == "double_bottom":
        downtrend_lookback = int(signal_cfg["downtrend_lookback"])
        max_bottom_spacing = int(signal_cfg["max_bottom_spacing"])
        left_bottom_before_bars = int(signal_cfg["left_bottom_before_bars"])
        max_breakout_wait = int(signal_cfg["max_breakout_bars_after_right_bottom"])
        retest_window = int(signal_cfg["retest_window"])
        return max(
            recent_bar_count,
            downtrend_lookback + max_bottom_spacing + left_bottom_before_bars + max_breakout_wait + retest_window + 10,
        )

    if strategy_type == "head_shoulders_bottom":
        downtrend_lookback = int(signal_cfg["downtrend_lookback"])
        max_segment_bars = int(signal_cfg["max_segment_bars"])
        pivot_right_bars = int(signal_cfg["pivot_right_bars"])
        return max(recent_bar_count, downtrend_lookback + max_segment_bars * 2 + pivot_right_bars + 10)

    if strategy_type == "rounded_bottom":
        max_lookback = int(signal_cfg["max_lookback"])
        pivot_right_bars = int(signal_cfg["pivot_right_bars"])
        return max(recent_bar_count, max_lookback + pivot_right_bars + 10)

    if strategy_type == "v_reversal":
        downtrend_lookback = int(signal_cfg["downtrend_lookback"])
        continuation_window = int(signal_cfg["continuation_window"])
        consolidation_max_bars = int(signal_cfg["consolidation_max_bars"])
        retest_window = int(signal_cfg["retest_window"])
        return max(recent_bar_count, downtrend_lookback + continuation_window + consolidation_max_bars + retest_window + 10)

    if strategy_type == "support_resistance":
        detection_window = int(signal_cfg["detection_window"])
        pivot_right = int(signal_cfg["pivot_right_bars"])
        score_window = int(signal_cfg["score_outcome_window"])
        retest_window = int(signal_cfg["retest_window"])
        max_holding_days = int(risk_cfg["max_holding_days"])
        return max(
            recent_bar_count,
            detection_window + pivot_right + score_window + retest_window + 10,
            max_holding_days + 5,
        )

    if strategy_type in {"trend", "mean_reversion", "momentum_breakout"}:
        return 0

    raise ValueError(f"unsupported engine-ready strategy_type: {strategy_type}")


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
        STRATEGY_HANDLERS[runtime["strategy_type"]]
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
        "open": _float_or_none(row["open"]),
        "high": _float_or_none(row["high"]),
        "low": _float_or_none(row["low"]),
        "close": _float_or_none(row["close"]),
        "volume": _float_or_none(row["volume"]),
        "atr_14": _float_or_none(row["atr_14"]),
        "volume_sma_20": _float_or_none(row["volume_sma_20"]),
        "ret_20d": _float_or_none(row["ret_20d"]),
        "ret_60d": _float_or_none(row["ret_60d"]),
        "sma_10": _float_or_none(row["sma_10"]),
        "sma_20": _float_or_none(row["sma_20"]),
        "sma_50": _float_or_none(row["sma_50"]),
        "sma_100": _float_or_none(row["sma_100"]),
        "sma_200": _float_or_none(row["sma_200"]),
        "ema_12": _float_or_none(row["ema_12"]),
        "ema_15": _float_or_none(row["ema_15"]),
        "ema_20": _float_or_none(row["ema_20"]),
        "ema_50": _float_or_none(row["ema_50"]),
        "rsi_2": _float_or_none(row["rsi_2"]),
        "rsi_5": _float_or_none(row["rsi_5"]),
        "rsi_14": _float_or_none(row["rsi_14"]),
        "zscore_5": _float_or_none(row["zscore_5"]),
        "zscore_10": _float_or_none(row["zscore_10"]),
        "zscore_20": _float_or_none(row["zscore_20"]),
        "prev_sma_10": _float_or_none(row["prev_sma_10"]),
        "prev_sma_20": _float_or_none(row["prev_sma_20"]),
        "prev_sma_50": _float_or_none(row["prev_sma_50"]),
        "prev_sma_100": _float_or_none(row["prev_sma_100"]),
        "prev_sma_200": _float_or_none(row["prev_sma_200"]),
        "prev_ema_12": _float_or_none(row["prev_ema_12"]),
        "prev_ema_15": _float_or_none(row["prev_ema_15"]),
        "prev_ema_20": _float_or_none(row["prev_ema_20"]),
        "prev_ema_50": _float_or_none(row["prev_ema_50"]),
        "position": 0,
        "avg_entry_price": None,
        "entry_trade_date": None,
        "position_holding_days": None,
        "entry_signal_features": None,
        "recent_bars": [],
    }


# Market snapshots preserve missing values but reject malformed internal values.
def _float_or_zero(value: Any) -> float:
    return 0.0 if value is None else float(value)


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _signal_timestamp_utc(snapshot: MarketSnapshot) -> datetime:
    timestamp = snapshot.get("ts")
    if timestamp is not None:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    trade_date = snapshot.get("dt_ny")
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

    entry_trade_date = snapshot.get("entry_trade_date")
    current_trade_date = snapshot.get("dt_ny")
    if entry_trade_date is None or current_trade_date is None or current_trade_date < entry_trade_date:
        return None

    recent_bars = snapshot.get("recent_bars")
    if recent_bars is not None:
        session_dates = sorted(
            {
                bar["dt_ny"]
                for bar in recent_bars
                if bar["dt_ny"] is not None
                and entry_trade_date <= bar["dt_ny"] <= current_trade_date
            }
        )
        if session_dates and session_dates[0] == entry_trade_date and session_dates[-1] == current_trade_date:
            return max(len(session_dates) - 1, 0)

    return max((current_trade_date - entry_trade_date).days, 0)


# Convert one RECENT_BAR_HISTORY_SQL row into the history-bar shape used by pattern scanners.
# Input: SQLAlchemy mapping row with historical OHLCV plus a few trend/volume indicators.
# Output: compact per-day dict stored under snapshot["recent_bars"].
def _build_history_bar(row: Dict[str, Any]) -> HistoryBar:
    return {
        "dt_ny": row["dt_ny"],
        "ts": row["ts"] or datetime.now(timezone.utc),
        "open": _float_or_none(row["open"]),
        "high": _float_or_none(row["high"]),
        "low": _float_or_none(row["low"]),
        "close": _float_or_none(row["close"]),
        "volume": _float_or_none(row["volume"]),
        "atr_14": _float_or_none(row["atr_14"]),
        "volume_sma_20": _float_or_none(row["volume_sma_20"]),
        "ret_20d": _float_or_none(row["ret_20d"]),
        "ret_60d": _float_or_none(row["ret_60d"]),
        "sma_20": _float_or_none(row["sma_20"]),
        "sma_50": _float_or_none(row["sma_50"]),
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
        avg_entry_price = _float_or_none(snapshot.get("avg_entry_price"))
        close_price = _float_or_none(snapshot.get("close"))
        current_atr = _float_or_none(snapshot.get("atr_14"))
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

        volume = _float_or_zero(snapshot.get("volume"))
        avg_volume = _float_or_zero(snapshot.get("volume_sma_20"))
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

        zscore = _float_or_none(snapshot.get(zscore_key))

        action: Literal["BUY", "SELL", "HOLD"] | None = None
        reason: str | None = None
        position = float(snapshot.get("position", 0) or 0)
        avg_entry_price = _float_or_none(snapshot.get("avg_entry_price"))
        close_price = _float_or_none(snapshot.get("close"))
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

        close_price = _float_or_none(snapshot.get("close"))
        sma_20 = _float_or_none(snapshot.get("sma_20"))
        return_20d = _float_or_none(snapshot.get("ret_20d"))
        volume = _float_or_none(snapshot.get("volume"))
        average_volume = _float_or_none(snapshot.get("volume_sma_20"))
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
        avg_entry_price = _float_or_none(snapshot.get("avg_entry_price"))
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
def _pattern_context(
    symbol: str,
    snapshot: MarketSnapshot,
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
) -> PatternContext:
    return PatternContext(
        symbol=symbol,
        bars=snapshot.get("recent_bars") or [],
        signal_cfg=signal_cfg,
        risk_cfg=risk_cfg,
        position=float(snapshot.get("position", 0) or 0.0),
        avg_entry_price=snapshot.get("avg_entry_price"),
        entry_signal_features=snapshot.get("entry_signal_features"),
    )


def _pattern_config_metadata(
    pattern_type: str,
    signal_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
) -> Dict[str, Any] | None:
    if pattern_type == "island_reversal":
        keys = (
            "downtrend_min_drop_pct",
            "left_gap_min_pct",
            "right_gap_min_pct",
            "retest_window",
            "support_tolerance_pct",
        )
    elif pattern_type == "double_bottom":
        keys = (
            "downtrend_min_drop_pct",
            "downtrend_max_up_day_ratio",
            "downtrend_min_r_squared",
            "bottom_tolerance_pct",
            "left_bottom_before_bars",
            "left_bottom_after_bars",
            "neckline_min_rebound_pct",
            "breakout_buffer_pct",
            "breakout_volume_ratio_min",
            "max_breakout_bars_after_right_bottom",
            "retest_window",
            "support_tolerance_pct",
        )
    else:
        return None
    config = {key: signal_cfg[key] for key in keys if key in signal_cfg}
    config.update(
        {
            "max_loss_pct": risk_cfg["max_loss_pct"],
            "take_profit_atr": risk_cfg["take_profit_atr"],
        }
    )
    return config


def _pattern_signal_event(
    *,
    pattern_type: str,
    runtime_strategy: RuntimeStrategy,
    snapshot: MarketSnapshot,
    symbol: str,
    decision: PatternDecision,
) -> SignalEvent:
    params = runtime_strategy["params"]
    metadata: Dict[str, Any] = {
        "close": snapshot.get("close"),
        "open": snapshot.get("open"),
        "high": snapshot.get("high"),
        "low": snapshot.get("low"),
        "volume": snapshot.get("volume"),
        "atr_14": snapshot.get("atr_14"),
        "position": float(snapshot.get("position", 0) or 0.0),
        "avg_entry_price": snapshot.get("avg_entry_price"),
        "setup": decision.setup,
        "strength_inputs": decision.strength_inputs,
    }
    config = _pattern_config_metadata(pattern_type, params["signal"], params["risk"])
    if config is not None:
        metadata["config"] = config
    else:
        metadata["price_semantics"] = "forward_adjusted_fallback_unadjusted"
    return SignalEvent(
        strategy_id=runtime_strategy["strategy_id"],
        ts=snapshot.get("ts") or datetime.now(timezone.utc),
        symbol=symbol,
        action=decision.action,
        reason=decision.reason,
        score=decision.score,
        metadata=metadata,
    )


def _run_pattern_evaluator(
    pattern_type: str,
    evaluator: PatternEvaluator,
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    params = runtime_strategy["params"]
    universe = _resolve_strategy_universe(params["universe"], market_data_by_symbol)
    signals: list[SignalEvent] = []
    for symbol in universe:
        snapshot = market_data_by_symbol.get(symbol)
        if not snapshot:
            continue
        decision = evaluator(
            _pattern_context(symbol, snapshot, params["signal"], params["risk"])
        )
        if decision is not None:
            signals.append(
                _pattern_signal_event(
                    pattern_type=pattern_type,
                    runtime_strategy=runtime_strategy,
                    snapshot=snapshot,
                    symbol=symbol,
                    decision=decision,
                )
            )
    return signals


def _island_reversal_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _run_pattern_evaluator(
        "island_reversal",
        island_reversal.evaluate,
        runtime_strategy,
        market_data_by_symbol,
    )


def build_stateful_backtest_signal_state(
    runtime_strategy: RuntimeStrategy,
) -> double_bottom.DoubleBottomState | SupportResistanceState | None:
    if runtime_strategy["strategy_type"] == "double_bottom":
        return double_bottom.create_state()
    if runtime_strategy["strategy_type"] == "support_resistance":
        return SupportResistanceState()
    return None


def generate_stateful_backtest_signals(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: double_bottom.DoubleBottomState | SupportResistanceState,
    *,
    emit_signals: bool = True,
) -> list[SignalEvent]:
    strategy_type = runtime_strategy["strategy_type"]
    if strategy_type == "double_bottom":
        if not isinstance(state, double_bottom.DoubleBottomState):
            raise TypeError("double_bottom requires DoubleBottomState")
        return _double_bottom_backtest_handler(
            runtime_strategy,
            market_data_by_symbol,
            state,
            emit_signals=emit_signals,
        )
    if strategy_type == "support_resistance":
        if not isinstance(state, SupportResistanceState):
            raise TypeError("support_resistance requires SupportResistanceState")
        return _support_resistance_backtest_handler(
            runtime_strategy,
            market_data_by_symbol,
            state,
            emit_signals=emit_signals,
        )
    raise ValueError(f"strategy_type {strategy_type} does not support stateful backtests")


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
            "avg_entry_price": _float_or_none(snapshot.get("avg_entry_price")),
            "support_resistance": decision["support_resistance"],
        },
    )


def _double_bottom_backtest_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
    state: double_bottom.DoubleBottomState,
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
        symbol_state = state.symbols.setdefault(symbol, double_bottom.DoubleBottomSymbolState())
        double_bottom.append_snapshot(symbol_state, snapshot)
        double_bottom.advance_symbol(symbol_state, params["signal"])
        if not emit_signals:
            continue
        context = PatternContext(
            symbol=symbol,
            bars=symbol_state.history_bars,
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=float(snapshot.get("position", 0) or 0.0),
            avg_entry_price=snapshot.get("avg_entry_price"),
            entry_signal_features=snapshot.get("entry_signal_features"),
        )
        decision = double_bottom.evaluate(context, symbol_state=symbol_state)
        if decision is not None:
            signals.append(
                _pattern_signal_event(
                    pattern_type="double_bottom",
                    runtime_strategy=runtime_strategy,
                    snapshot=snapshot,
                    symbol=symbol,
                    decision=decision,
                )
            )
    return signals


def _double_bottom_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _run_pattern_evaluator(
        "double_bottom",
        double_bottom.evaluate,
        runtime_strategy,
        market_data_by_symbol,
    )


def _head_shoulders_bottom_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _run_pattern_evaluator(
        "head_shoulders_bottom",
        head_shoulders_bottom.evaluate,
        runtime_strategy,
        market_data_by_symbol,
    )


def _rounded_bottom_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _run_pattern_evaluator(
        "rounded_bottom",
        rounded_bottom.evaluate,
        runtime_strategy,
        market_data_by_symbol,
    )


def _v_reversal_handler(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    return _run_pattern_evaluator(
        "v_reversal",
        v_reversal.evaluate,
        runtime_strategy,
        market_data_by_symbol,
    )


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
