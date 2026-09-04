from __future__ import annotations

"""Native signal orchestration shared by daily runs and paper trading."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import math
from typing import Any, Dict, Literal

import quant_kernel
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.services.feature_snapshot_sql import FEATURE_SNAPSHOT_PROJECTION_SQL
from src.services.prepared_dataset_service import build_in_memory_prepared_dataset
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
)
from src.services.strategy_types import (
    HistoryBar,
    MarketDataBySymbol,
    MarketSnapshot,
    RuntimeStrategy,
)

RECENT_BAR_COUNT = 40
RECENT_BAR_LOOKBACK_DAYS = 90
FEATURE_SNAPSHOT_SQL = f"""
SELECT
    i.id AS instrument_id,
    i.ticker_canonical AS symbol,
    i.asset_type,
    curr.dt_ny,
    bars.ts_utc AS ts,
{FEATURE_SNAPSHOT_PROJECTION_SQL}
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


# ============================================================================
# Public orchestration API
# ============================================================================

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
                "support_risk_context",
                "support_stopped_zones",
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
