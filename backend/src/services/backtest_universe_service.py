from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class ResolvedInstrument:
    instrument_id: int
    canonical_symbol: str
    requested_symbols: tuple[str, ...]
    is_active_now: bool
    listed_at: date | None
    delisted_at: date | None


@dataclass(frozen=True, slots=True)
class ResolvedUniverse:
    instruments: tuple[ResolvedInstrument, ...]
    membership_semantics: str = "current_active_snapshot"
    policy: dict[str, Any] | None = None

    @property
    def instrument_ids(self) -> list[int]:
        return [item.instrument_id for item in self.instruments]

    def manifest(self) -> dict[str, Any]:
        result = {
            "membership_semantics": self.membership_semantics,
            "survivorship_bias_warning": self.membership_semantics == "current_active_snapshot",
            "instrument_count": len(self.instruments),
            "instrument_set_hash": hashlib.sha256(
                json.dumps(self.instrument_ids, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        if self.policy is not None:
            result["policy"] = self.policy
        else:
            result["instruments"] = [
                {
                    "instrument_id": item.instrument_id,
                    "canonical_symbol": item.canonical_symbol,
                    "requested_symbols": list(item.requested_symbols),
                    "is_active_now": item.is_active_now,
                    "listed_at": item.listed_at.isoformat() if item.listed_at else None,
                    "delisted_at": item.delisted_at.isoformat() if item.delisted_at else None,
                }
                for item in self.instruments
            ]
        return result


def normalize_point_in_time_policy(value: dict[str, Any]) -> dict[str, Any]:
    policy = {
        "type": str(value.get("type") or "point_in_time_liquid"),
        "assetTypes": sorted(
            {str(item).strip().upper() for item in value.get("assetTypes", ["CS"]) if str(item).strip()}
        ),
        "exchanges": sorted(
            {
                str(item).strip().upper()
                for item in value.get("exchanges", ["XNAS", "XNYS", "XASE"])
                if str(item).strip()
            }
        ),
        "minUnadjustedClose": float(value.get("minUnadjustedClose", 5.0)),
        "minDollarVolume20": float(value.get("minDollarVolume20", 10_000_000.0)),
        "minHistorySessions": int(value.get("minHistorySessions", 200)),
        "membershipAsOf": str(value.get("membershipAsOf") or "signal_close"),
        "existingPositionPolicy": str(value.get("existingPositionPolicy") or "exit_only"),
        "delistingValuePolicy": str(
            value.get("delistingValuePolicy") or "zero_with_last_close_sensitivity"
        ),
    }
    if policy["type"] != "point_in_time_liquid":
        raise ValueError("unsupported universePolicy type")
    if not policy["assetTypes"] or not policy["exchanges"]:
        raise ValueError("universePolicy assetTypes and exchanges must be non-empty")
    if policy["minUnadjustedClose"] <= 0 or policy["minDollarVolume20"] <= 0:
        raise ValueError("universePolicy price and liquidity thresholds must be positive")
    if policy["minHistorySessions"] < 20 or policy["minHistorySessions"] > 252:
        raise ValueError("universePolicy minHistorySessions must be between 20 and 252")
    if policy["membershipAsOf"] != "signal_close":
        raise ValueError("universePolicy membershipAsOf must be signal_close")
    if policy["existingPositionPolicy"] != "exit_only":
        raise ValueError("universePolicy existingPositionPolicy must be exit_only")
    if policy["delistingValuePolicy"] != "zero_with_last_close_sensitivity":
        raise ValueError(
            "universePolicy delistingValuePolicy must be zero_with_last_close_sensitivity"
        )
    return policy


def point_in_time_entry_eligible(
    snapshot: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[bool, str | None]:
    """Return causal T-close membership and the first deterministic exclusion reason."""

    normalized = normalize_point_in_time_policy(policy)
    trade_date = snapshot.get("dt_ny")
    if not isinstance(trade_date, date):
        return False, "missing_trade_date"
    if str(snapshot.get("asset_type") or "").upper() not in normalized["assetTypes"]:
        return False, "asset_type"
    if str(snapshot.get("exchange") or "").upper() not in normalized["exchanges"]:
        return False, "exchange"
    listed_at = snapshot.get("listed_at")
    if isinstance(listed_at, date) and trade_date < listed_at:
        return False, "before_listing"
    delisted_at = snapshot.get("delisted_at")
    if isinstance(delisted_at, date) and trade_date > delisted_at:
        return False, "after_delisting"
    close_u = snapshot.get("close_unadjusted")
    if not isinstance(close_u, (int, float)) or float(close_u) < normalized["minUnadjustedClose"]:
        return False, "price"
    dollar_volume = snapshot.get("dollar_volume_20")
    if not isinstance(dollar_volume, (int, float)) or float(dollar_volume) < normalized["minDollarVolume20"]:
        return False, "liquidity"
    history_sessions = snapshot.get("history_sessions")
    if not isinstance(history_sessions, int) or history_sessions < normalized["minHistorySessions"]:
        return False, "history"
    return True, None


RESOLVE_UNIVERSE_SQL = """
SELECT
    i.id AS instrument_id,
    i.ticker_canonical,
    i.is_active,
    i.listed_at,
    i.delisted_at,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT matched.requested_symbol), NULL) AS requested_symbols
FROM instruments i
LEFT JOIN LATERAL (
    SELECT requested.symbol AS requested_symbol
    FROM UNNEST(CAST(:symbols AS TEXT[])) AS requested(symbol)
    WHERE UPPER(i.ticker_canonical) = requested.symbol
       OR EXISTS (
            SELECT 1
            FROM symbol_history sh
            WHERE sh.instrument_id = i.id
              AND sh.symbol = requested.symbol
              AND sh.valid_from <= :end_date
              AND (sh.valid_to IS NULL OR sh.valid_to >= :start_date)
       )
) matched ON TRUE
WHERE matched.requested_symbol IS NOT NULL
GROUP BY i.id, i.ticker_canonical, i.is_active, i.listed_at, i.delisted_at
ORDER BY i.id
"""


RESOLVE_POINT_IN_TIME_UNIVERSE_SQL = """
SELECT
    i.id AS instrument_id,
    i.ticker_canonical,
    i.is_active,
    i.listed_at,
    i.delisted_at
FROM instruments i
WHERE i.asset_type IN :asset_types
  AND i.exchange IN :exchanges
  AND EXISTS (
      SELECT 1
      FROM daily_features df
      JOIN eod_bars bars
        ON bars.instrument_id = df.instrument_id
       AND bars.dt_ny = df.dt_ny
      WHERE df.instrument_id = i.id
        AND df.dt_ny BETWEEN :start_date AND :end_date
  )
ORDER BY i.id
"""


def resolve_backtest_universe(
    db: Session,
    symbols: list[str],
    *,
    start_date: date,
    end_date: date,
) -> ResolvedUniverse:
    normalized = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not normalized:
        raise ValueError("backtest universe is empty")
    rows = db.execute(
        text(RESOLVE_UNIVERSE_SQL),
        {"symbols": normalized, "start_date": start_date, "end_date": end_date},
    ).mappings().all()
    instruments = tuple(
        ResolvedInstrument(
            instrument_id=int(row["instrument_id"]),
            canonical_symbol=str(row["ticker_canonical"] or "").upper(),
            requested_symbols=tuple(sorted(str(value).upper() for value in (row["requested_symbols"] or []))),
            is_active_now=bool(row["is_active"]),
            listed_at=row["listed_at"],
            delisted_at=row["delisted_at"],
        )
        for row in rows
    )
    if not instruments:
        raise ValueError("no instruments resolved for the requested universe and date range")
    return ResolvedUniverse(instruments=instruments)


def resolve_point_in_time_universe(
    db: Session,
    policy: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> ResolvedUniverse:
    normalized = normalize_point_in_time_policy(policy)
    statement = text(RESOLVE_POINT_IN_TIME_UNIVERSE_SQL).bindparams(
        bindparam("asset_types", expanding=True),
        bindparam("exchanges", expanding=True),
    )
    rows = db.execute(
        statement,
        {
            "asset_types": normalized["assetTypes"],
            "exchanges": normalized["exchanges"],
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().all()
    instruments = tuple(
        ResolvedInstrument(
            instrument_id=int(row["instrument_id"]),
            canonical_symbol=str(row["ticker_canonical"] or "").upper(),
            requested_symbols=(),
            is_active_now=bool(row["is_active"]),
            listed_at=row["listed_at"],
            delisted_at=row["delisted_at"],
        )
        for row in rows
    )
    if not instruments:
        raise ValueError("point-in-time universe has no instruments in the requested window")
    return ResolvedUniverse(
        instruments=instruments,
        membership_semantics="point_in_time_liquid",
        policy=normalized,
    )
