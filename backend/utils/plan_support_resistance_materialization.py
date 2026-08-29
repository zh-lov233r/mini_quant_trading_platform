from __future__ import annotations

"""Read-only dry run for one support/resistance materialization request."""

import argparse
import json
import os
import sys
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from src.services.strategy_registry import normalize_strategy_params  # noqa: E402
from src.services.support_resistance_service import (  # noqa: E402
    SupportResistanceSymbolState,
    advance_symbol,
    normalized_detector_params,
)


SOURCE_COUNTS_SQL = """
SELECT
  (SELECT count(*)
     FROM eod_bars bars
     JOIN instruments instrument ON instrument.id = bars.instrument_id
    WHERE instrument.ticker_canonical = ANY(%s)
      AND bars.dt_ny BETWEEN %s AND %s) AS eod_rows,
  (SELECT count(*)
     FROM daily_features feature
     JOIN instruments instrument ON instrument.id = feature.instrument_id
    WHERE instrument.ticker_canonical = ANY(%s)
      AND feature.dt_ny BETWEEN %s AND %s) AS feature_rows,
  (SELECT max(bars.asof)
     FROM eod_bars bars
     JOIN instruments instrument ON instrument.id = bars.instrument_id
    WHERE instrument.ticker_canonical = ANY(%s)
      AND bars.dt_ny BETWEEN %s AND %s) AS latest_eod_asof,
  (SELECT max(feature.asof)
     FROM daily_features feature
     JOIN instruments instrument ON instrument.id = feature.instrument_id
    WHERE instrument.ticker_canonical = ANY(%s)
      AND feature.dt_ny BETWEEN %s AND %s) AS latest_feature_asof
"""

BAR_SQL = """
SELECT instrument.ticker_canonical AS symbol,
       bars.dt_ny,
       COALESCE(bars.open_fa, bars.open_u) AS open,
       COALESCE(bars.high_fa, bars.high_u) AS high,
       COALESCE(bars.low_fa, bars.low_u) AS low,
       COALESCE(bars.close_fa, bars.close_u) AS close,
       bars.volume,
       feature.atr_14,
       feature.adv_20 AS volume_sma_20
FROM eod_bars bars
JOIN instruments instrument ON instrument.id = bars.instrument_id
JOIN daily_features feature
  ON feature.instrument_id = bars.instrument_id
 AND feature.dt_ny = bars.dt_ny
WHERE instrument.ticker_canonical = ANY(%s)
  AND bars.dt_ny BETWEEN %s AND %s
ORDER BY instrument.ticker_canonical, bars.dt_ny
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--symbols", required=True, help="Comma-separated canonical symbols")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--params-json",
        default="{}",
        help="Optional support_resistance parameter overrides as one JSON object",
    )
    return parser.parse_args()


def normalize_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def estimate(rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    states: dict[str, SupportResistanceSymbolState] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        state = states.setdefault(symbol, SupportResistanceSymbolState())
        advance_symbol(
            state,
            row,
            params["signal"],
            params["risk"],
            emit_signals=False,
        )
    return {
        "symbols_loaded": sorted(states),
        "estimated_zone_version_count": sum(len(state.zone_versions) for state in states.values()),
        "estimated_run_event_count": sum(len(state.events) for state in states.values()),
    }


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or SQLALCHEMY_DATABASE_URL is required")
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    symbols = sorted({item.strip().upper() for item in args.symbols.split(",") if item.strip()})
    if not symbols or len(symbols) > 5_000:
        raise SystemExit("--symbols must contain between 1 and 5000 canonical symbols")
    try:
        raw_params = json.loads(args.params_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--params-json is invalid JSON: {exc}") from exc
    if not isinstance(raw_params, dict):
        raise SystemExit("--params-json must decode to an object")
    params = normalize_strategy_params("support_resistance", raw_params)

    repeated_parameters = (symbols, args.start_date, args.end_date) * 4
    with psycopg.connect(normalize_dsn(args.database_url), autocommit=False) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute(SOURCE_COUNTS_SQL, repeated_parameters)
            count_row = cursor.fetchone()
            count_columns = [item.name for item in cursor.description]
            source_counts = dict(zip(count_columns, count_row, strict=True))
            cursor.execute(BAR_SQL, (symbols, args.start_date, args.end_date))
            bar_columns = [item.name for item in cursor.description]
            rows = [dict(zip(bar_columns, row, strict=True)) for row in cursor.fetchall()]
        connection.rollback()

    scoped_revision = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in source_counts.items()
    }
    scoped_fingerprint = sha256(
        json.dumps(scoped_revision, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {
        "dry_run": True,
        "database_mutated": False,
        "algorithm_version": params["metadata"]["algorithm_version"],
        "price_semantics": params["metadata"]["price_semantics"],
        "detector_params": normalized_detector_params(params),
        "symbols_requested": symbols,
        "coverage_start": args.start_date.isoformat(),
        "coverage_end": args.end_date.isoformat(),
        "source_rows": {
            "eod_bars": int(source_counts["eod_rows"] or 0),
            "daily_features": int(source_counts["feature_rows"] or 0),
            "joined_bars": len(rows),
        },
        "scoped_source_fingerprint": scoped_fingerprint,
        **estimate(rows, params),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
