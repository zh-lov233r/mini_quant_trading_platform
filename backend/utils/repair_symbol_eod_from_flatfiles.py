from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.services.data_service import (  # noqa: E402
    find_massive_day_agg_files,
    infer_trade_date_from_path,
    iter_massive_day_agg_rows,
)
from backend.utils.download_flatfiles import DEFAULT_OUTPUT_ROOT, DEFAULT_PREFIX  # noqa: E402


RESOLVE_INSTRUMENT_SQL = """
SELECT id
FROM instruments
WHERE ticker_canonical = %(symbol)s
ORDER BY id;
"""

RESOLVE_INSTRUMENT_BY_ID_SQL = """
SELECT id, ticker_canonical
FROM instruments
WHERE id = %(instrument_id)s
LIMIT 1;
"""

CREATE_STAGE_SQL = """
CREATE TEMP TABLE symbol_eod_repair_stage (
  instrument_id BIGINT NOT NULL,
  ts_utc TIMESTAMPTZ NOT NULL,
  open_u DOUBLE PRECISION,
  high_u DOUBLE PRECISION,
  low_u DOUBLE PRECISION,
  close_u DOUBLE PRECISION,
  volume BIGINT,
  vwap DOUBLE PRECISION,
  trades INT
) ON COMMIT DROP;
"""

COPY_STAGE_SQL = """
COPY symbol_eod_repair_stage (
  instrument_id, ts_utc, open_u, high_u, low_u, close_u, volume, vwap, trades
) FROM STDIN
"""

UPSERT_SQL = """
WITH upserted AS (
  INSERT INTO eod_bars (
    instrument_id,
    ts_utc,
    open_u,
    high_u,
    low_u,
    close_u,
    volume,
    vwap,
    trades,
    vendor
  )
  SELECT
    instrument_id,
    ts_utc,
    open_u,
    high_u,
    low_u,
    close_u,
    volume,
    vwap,
    trades,
    'massive_flatfile_repair'
  FROM symbol_eod_repair_stage
  ON CONFLICT (instrument_id, dt_ny) DO UPDATE SET
    ts_utc = EXCLUDED.ts_utc,
    open_u = EXCLUDED.open_u,
    high_u = EXCLUDED.high_u,
    low_u = EXCLUDED.low_u,
    close_u = EXCLUDED.close_u,
    volume = EXCLUDED.volume,
    vwap = EXCLUDED.vwap,
    trades = EXCLUDED.trades,
    vendor = EXCLUDED.vendor,
    asof = now()
  RETURNING 1
)
SELECT COUNT(*) FROM upserted;
"""


@dataclass(frozen=True)
class RepairStats:
    files_scanned: int
    matched_files: int
    staged_rows: int
    upserted_rows: int
    duplicate_days: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair one symbol in eod_bars by replaying exact uppercase rows from "
            "local Massive flat files."
        )
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="Ticker symbol to repair, for example BCPC.",
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_OUTPUT_ROOT / DEFAULT_PREFIX),
        help="Root directory containing Massive day aggregate flat files.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Optional inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Optional inclusive end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--instrument-id",
        type=int,
        default=None,
        help="Optional instrument_id override when a canonical ticker maps to multiple instruments.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan local files and report matches without writing to eod_bars.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return normalized.replace("postgresql+psycopg2://", "postgresql://", 1)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _resolve_instrument_id(
    conn: psycopg.Connection,
    symbol: str,
    instrument_id_override: int | None,
) -> int:
    with conn.cursor() as cur:
        if instrument_id_override is not None:
            cur.execute(
                RESOLVE_INSTRUMENT_BY_ID_SQL,
                {"instrument_id": instrument_id_override},
            )
            row = cur.fetchone()
            if row is None:
                raise SystemExit(
                    f"Could not find instrument_id {instrument_id_override} for ticker {symbol}"
                )
            resolved_id = int(row[0])
            resolved_symbol = str(row[1]).upper()
            if resolved_symbol != symbol:
                raise SystemExit(
                    f"instrument_id {resolved_id} belongs to {resolved_symbol}, not {symbol}"
                )
            return resolved_id

        cur.execute(RESOLVE_INSTRUMENT_SQL, {"symbol": symbol})
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"Could not find instrument for ticker {symbol}")
    if len(rows) > 1:
        instrument_ids = ", ".join(str(int(row[0])) for row in rows)
        raise SystemExit(
            f"Ticker {symbol} maps to multiple instruments ({instrument_ids}); "
            "pass --instrument-id to choose one."
        )
    return int(rows[0][0])


def _collect_stage_rows(files: list[Path], symbol: str, instrument_id: int) -> tuple[list[tuple], int, int]:
    stage_rows: list[tuple] = []
    matched_files = 0
    duplicate_days = 0

    for file_path in files:
        matching_rows = [row for row in iter_massive_day_agg_rows(file_path) if row[0] == symbol]
        if not matching_rows:
            continue

        matched_files += 1
        if len(matching_rows) > 1:
            duplicate_days += 1
            matching_rows.sort(key=lambda row: ((row[6] or 0), row[1]), reverse=True)

        _, ts_utc, open_u, high_u, low_u, close_u, volume, vwap, trades = matching_rows[0]
        stage_rows.append(
            (
                instrument_id,
                ts_utc,
                open_u,
                high_u,
                low_u,
                close_u,
                volume,
                vwap,
                trades,
            )
        )

    return stage_rows, matched_files, duplicate_days


def _upsert_rows(conn: psycopg.Connection, stage_rows: list[tuple]) -> int:
    with conn.cursor() as cur:
        cur.execute(CREATE_STAGE_SQL)
        with cur.copy(COPY_STAGE_SQL) as copy:
            for row in stage_rows:
                copy.write_row(row)
        cur.execute(UPSERT_SQL)
        upserted_rows = int(cur.fetchone()[0])
    conn.commit()
    return upserted_rows


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    symbol = args.symbol.strip().upper()
    files = find_massive_day_agg_files(
        root_dir=args.root,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
    )
    if not files:
        raise SystemExit(f"No Massive flat files found under {args.root}")

    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        instrument_id = _resolve_instrument_id(conn, symbol, args.instrument_id)
        stage_rows, matched_files, duplicate_days = _collect_stage_rows(files, symbol, instrument_id)
        if not stage_rows:
            raise SystemExit(f"No exact uppercase rows found for {symbol} in local flat files")

        if args.dry_run:
            upserted_rows = 0
        else:
            upserted_rows = _upsert_rows(conn, stage_rows)

    stats = RepairStats(
        files_scanned=len(files),
        matched_files=matched_files,
        staged_rows=len(stage_rows),
        upserted_rows=upserted_rows,
        duplicate_days=duplicate_days,
    )
    print(
        f"Repair scan complete for {symbol}. "
        f"files_scanned={stats.files_scanned} matched_files={stats.matched_files} "
        f"staged_rows={stats.staged_rows} upserted_rows={stats.upserted_rows} "
        f"duplicate_days={stats.duplicate_days}",
        flush=True,
    )


if __name__ == "__main__":
    main()
