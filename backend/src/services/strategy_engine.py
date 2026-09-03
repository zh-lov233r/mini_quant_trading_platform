from __future__ import annotations

"""Native signal orchestration shared by daily runs and paper trading."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import math
from typing import Any, Dict, Literal

import quant_kernel
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from src.models.tables import Signal, Strategy, StrategyRun
from src.services.prepared_dataset_service import build_in_memory_prepared_dataset
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
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

RECENT_BAR_COUNT = 40
RECENT_BAR_LOOKBACK_DAYS = 90
NATIVE_STRATEGY_TYPES = frozenset(
    str(item["strategy_type"]) for item in quant_kernel.catalog()
)

FEATURE_SNAPSHOT_SQL = """
SELECT
    i.id AS instrument_id,
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
            for symbol, payload in support_resistance_hydration_payload(
                replay_state,
                snapshots,
            ).items():
                if symbol in snapshots:
                    snapshots[symbol]["support_resistance_hydration"] = payload
        strategy_signals, native_audit = evaluate_native_day(runtime, snapshots)
        if runtime["strategy_type"] == "support_resistance":
            replay_state = support_resistance_state_from_native_day(native_audit, snapshots)
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

        strategy_signals = evaluate_native_signals(runtime, market_data_by_symbol)
        signals.extend(strategy_signals)

    return signals


def evaluate_native_signals(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> list[SignalEvent]:
    """Adapt native day results at the Python persistence and broker boundary."""
    return evaluate_native_day(runtime_strategy, market_data_by_symbol)[0]


def evaluate_native_day(
    runtime_strategy: RuntimeStrategy,
    market_data_by_symbol: MarketDataBySymbol,
) -> tuple[list[SignalEvent], dict[str, Any]]:
    dataset, portfolio_state = _prepared_day_input(market_data_by_symbol)
    result = quant_kernel.evaluate_day(dataset, runtime_strategy, portfolio_state)
    columns = result.signals
    symbols = list(result.symbols)
    signals = [
        SignalEvent(
            strategy_id=str(columns["strategy_id"][index]),
            ts=datetime.fromtimestamp(
                int(columns["timestamp_us"][index]) / 1_000_000,
                tz=timezone.utc,
            ),
            symbol=symbols[int(columns["symbol_id"][index])],
            action="BUY" if int(columns["action"][index]) == 1 else "SELL",
            reason=str(columns["reason"][index]),
            score=(
                None
                if math.isnan(float(columns["score"][index]))
                else float(columns["score"][index])
            ),
            metadata=json.loads(columns["metadata_json"][index]),
            instrument_id=(
                None
                if int(columns["instrument_id"][index]) < 0
                else int(columns["instrument_id"][index])
            ),
        )
        for index in range(len(result))
    ]
    support_columns = result.support_resistance
    support_symbols = list(support_columns["symbols"])
    audit = {
        symbol: {"events": [], "zone_versions": [], "regime_versions": []}
        for symbol in support_symbols
    }
    for collection_name in ("events", "zone_versions", "regime_versions"):
        collection = support_columns[collection_name]
        for symbol_id, payload_json in zip(
            collection["symbol_id"],
            collection["payload_json"],
            strict=True,
        ):
            audit[support_symbols[int(symbol_id)]][collection_name].append(
                json.loads(payload_json)
            )
    return signals, audit


def _prepared_day_input(
    market_data_by_symbol: MarketDataBySymbol,
) -> tuple[Any, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    positions: dict[str, dict[str, Any]] = {}
    hydration: dict[str, dict[str, Any]] = {}
    for synthetic_index, symbol in enumerate(sorted(market_data_by_symbol)):
        current = dict(market_data_by_symbol[symbol])
        instrument_id = int(current.get("instrument_id") or -(synthetic_index + 1))
        current_date = _prepared_bar_date(current)
        identity = {
            "instrument_id": instrument_id,
            "symbol": str(symbol).upper(),
            "asset_type": current.get("asset_type"),
            "exchange": current.get("exchange"),
            "listed_at": current.get("listed_at"),
            "delisted_at": current.get("delisted_at"),
        }
        by_date: dict[date, dict[str, Any]] = {}
        for raw_bar in list(current.get("recent_bars") or []):
            bar = dict(raw_bar)
            bar_date = _prepared_bar_date(bar, fallback=current_date)
            bar.setdefault(
                "ts",
                datetime(
                    bar_date.year,
                    bar_date.month,
                    bar_date.day,
                    21,
                    tzinfo=timezone.utc,
                ),
            )
            by_date[bar_date] = {**identity, **bar, "dt_ny": bar_date}
        current.setdefault("ts", datetime.now(timezone.utc))
        by_date[current_date] = {
            **by_date.get(current_date, {}),
            **identity,
            **current,
            "dt_ny": current_date,
        }
        rows.extend(by_date.values())
        positions[str(instrument_id)] = {
            key: current.get(key)
            for key in (
                "position",
                "avg_entry_price",
                "entry_trade_date",
                "position_holding_days",
                "entry_signal_features",
            )
        }
        raw_hydration = current.get("support_resistance_hydration")
        if isinstance(raw_hydration, dict):
            hydration[str(instrument_id)] = dict(raw_hydration)
    return (
        build_in_memory_prepared_dataset(rows),
        {
            "positions": positions,
            "support_resistance_hydration": hydration,
        },
    )


def _prepared_bar_date(
    snapshot: dict[str, Any],
    *,
    fallback: date | None = None,
) -> date:
    value = snapshot.get("dt_ny")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    timestamp = snapshot.get("ts")
    if isinstance(timestamp, datetime):
        return timestamp.date()
    if fallback is not None:
        return fallback
    raise ValueError("daily strategy snapshot requires dt_ny or ts")


def support_resistance_state_from_native_day(
    audit: dict[str, Any],
    market_data_by_symbol: MarketDataBySymbol,
) -> SupportResistanceState:
    state = SupportResistanceState()
    for symbol, raw_payload in audit.items():
        payload = dict(raw_payload or {})
        snapshot = market_data_by_symbol.get(symbol)
        raw_instrument_id = snapshot.get("instrument_id") if snapshot else None
        instrument_id = int(raw_instrument_id) if raw_instrument_id is not None else None
        symbol_state = state.symbols.setdefault(
            symbol,
            SupportResistanceSymbolState(instrument_id=instrument_id, symbol=symbol),
        )
        history = list((market_data_by_symbol.get(symbol) or {}).get("recent_bars") or [])
        if snapshot and (not history or history[-1].get("dt_ny") != snapshot.get("dt_ny")):
            history.append(snapshot)
        symbol_state.history.extend(history)
        symbol_state.events.extend(dict(item) for item in payload.get("events") or [])
        symbol_state.zone_versions.extend(
            dict(item) for item in payload.get("zone_versions") or []
        )
        symbol_state.regime_versions.extend(
            dict(item) for item in payload.get("regime_versions") or []
        )
    return state


def support_resistance_hydration_payload(
    state: SupportResistanceState,
    market_data_by_symbol: MarketDataBySymbol | None = None,
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for state_key, symbol_state in state.symbols.items():
        symbol = str(symbol_state.symbol or state_key).upper()
        snapshot = (market_data_by_symbol or {}).get(symbol)
        snapshot_instrument_id = snapshot.get("instrument_id") if snapshot else None
        if (
            snapshot_instrument_id is not None
            and symbol_state.instrument_id is not None
            and int(snapshot_instrument_id) != symbol_state.instrument_id
        ):
            continue
        payloads[symbol] = {
            "zone_timeline": list(symbol_state.cached_zone_timeline),
            "regime_timeline": list(symbol_state.cached_regime_timeline),
            "lifecycle_events": [
                list(item) for item in sorted(symbol_state.cached_lifecycle_events)
            ],
        }
    return payloads


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
    strategy_type = runtime_strategy["strategy_type"]
    descriptor = next(
        (item for item in quant_kernel.catalog() if item["strategy_type"] == strategy_type),
        None,
    )
    if descriptor is None:
        raise ValueError(f"unsupported engine-ready strategy_type: {strategy_type}")
    return int(descriptor["history_length"])


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
        if runtime["strategy_type"] not in NATIVE_STRATEGY_TYPES:
            raise ValueError(f"unsupported engine-ready strategy_type: {runtime['strategy_type']}")
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
        "instrument_id": int(row["instrument_id"]),
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
