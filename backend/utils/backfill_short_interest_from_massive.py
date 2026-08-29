from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

if __package__:
    from .massive_enrichment_common import (
        MASSIVE_API_BASE,
        fetch_json,
        normalize_dsn,
        parse_optional_date,
    )
else:
    from massive_enrichment_common import (
        MASSIVE_API_BASE,
        fetch_json,
        normalize_dsn,
        parse_optional_date,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
EARLIEST_AVAILABLE = date(2017, 12, 29)
SHORT_INTEREST_URL = MASSIVE_API_BASE + "/stocks/v1/short-interest"

STAGE_SQL = """
CREATE TEMP TABLE short_interest_stage (
  ticker TEXT NOT NULL,
  settlement_date DATE NOT NULL,
  short_interest BIGINT,
  avg_daily_volume BIGINT,
  days_to_cover DOUBLE PRECISION,
  vendor_payload JSONB NOT NULL
) ON COMMIT DROP
"""

RESOLVE_SQL = """
CREATE TEMP TABLE short_interest_resolved ON COMMIT DROP AS
SELECT
  stage.*,
  min(sh.instrument_id) AS instrument_id,
  count(DISTINCT sh.instrument_id) AS match_count
FROM short_interest_stage stage
LEFT JOIN symbol_history sh
  ON sh.symbol = stage.ticker
 AND sh.is_primary
 AND sh.valid_from <= stage.settlement_date
 AND (sh.valid_to IS NULL OR sh.valid_to >= stage.settlement_date)
GROUP BY
  stage.ticker,
  stage.settlement_date,
  stage.short_interest,
  stage.avg_daily_volume,
  stage.days_to_cover,
  stage.vendor_payload
;

CREATE TEMP TABLE short_interest_dedup ON COMMIT DROP AS
SELECT
  min(ticker) AS ticker,
  settlement_date,
  min(short_interest) AS short_interest,
  min(avg_daily_volume) AS avg_daily_volume,
  min(days_to_cover) AS days_to_cover,
  min(vendor_payload::text)::jsonb AS vendor_payload,
  instrument_id
FROM short_interest_resolved
WHERE match_count = 1
GROUP BY instrument_id, settlement_date
HAVING count(DISTINCT ROW(short_interest, avg_daily_volume, days_to_cover)) = 1
"""

UPSERT_SQL = """
WITH upserted AS (
  INSERT INTO stock_short_interest (
    instrument_id,
    settlement_date,
    short_interest,
    avg_daily_volume,
    days_to_cover,
    vendor_source,
    vendor_payload,
    asof
  )
  SELECT
    instrument_id,
    settlement_date,
    short_interest,
    avg_daily_volume,
    days_to_cover,
    'massive',
    vendor_payload,
    now()
  FROM short_interest_dedup
  ON CONFLICT (instrument_id, settlement_date, vendor_source) DO UPDATE SET
    short_interest = EXCLUDED.short_interest,
    avg_daily_volume = EXCLUDED.avg_daily_volume,
    days_to_cover = EXCLUDED.days_to_cover,
    vendor_payload = EXCLUDED.vendor_payload,
    asof = now()
  RETURNING 1
)
SELECT count(*) FROM upserted
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill point-in-time-mapped Massive short interest.")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_item(item: dict) -> tuple | None:
    ticker = str(item.get("ticker") or "").strip().upper()
    raw_date = item.get("settlement_date")
    if not ticker or not raw_date:
        return None
    settlement_date = date.fromisoformat(str(raw_date))
    short_interest = item.get("short_interest")
    avg_daily_volume = item.get("avg_daily_volume")
    days_to_cover = item.get("days_to_cover")
    normalized = (
        ticker,
        settlement_date,
        None if short_interest is None else int(short_interest),
        None if avg_daily_volume is None else int(avg_daily_volume),
        None if days_to_cover is None else float(days_to_cover),
        json.dumps(item),
    )
    numeric = normalized[2:5]
    if any(value is not None and value < 0 for value in numeric):
        return None
    return normalized


def _resolve_range(
    conn: psycopg.Connection,
    *,
    requested_start: date | None,
    requested_end: date | None,
    lookback_days: int,
) -> tuple[date, date]:
    end_date = requested_end or date.today()
    if requested_start:
        start_date = requested_start
    else:
        latest = conn.execute("SELECT max(settlement_date) FROM stock_short_interest").fetchone()[0]
        start_date = max(EARLIEST_AVAILABLE, (latest or EARLIEST_AVAILABLE) - timedelta(days=lookback_days))
    start_date = max(start_date, EARLIEST_AVAILABLE)
    if start_date > end_date:
        raise SystemExit("start date must be on or before end date")
    return start_date, end_date


def _stage_page(conn: psycopg.Connection, rows: list[tuple], *, dry_run: bool) -> tuple[int, int, int]:
    if not rows:
        return 0, 0, 0
    with conn.cursor() as cur:
        cur.execute(STAGE_SQL)
        with cur.copy(
            """
            COPY short_interest_stage (
              ticker, settlement_date, short_interest, avg_daily_volume,
              days_to_cover, vendor_payload
            ) FROM STDIN
            """
        ) as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute(RESOLVE_SQL)
        resolved, unresolved = cur.execute(
            """
            SELECT
              (SELECT count(*) FROM short_interest_dedup),
              (SELECT count(*) FROM short_interest_stage)
                - (SELECT count(*) FROM short_interest_dedup)
            """
        ).fetchone()
        upserted = 0
        if not dry_run:
            cur.execute(UPSERT_SQL)
            upserted = int(cur.fetchone()[0])
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return int(resolved), int(unresolved), upserted


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if args.lookback_days < 1:
        raise SystemExit("--lookback-days must be >= 1")
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    api_key = os.getenv("MASSIVE_API_KEY")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY")

    requested_start = parse_optional_date(args.start_date)
    requested_end = parse_optional_date(args.end_date)
    total_fetched = total_valid = total_resolved = total_unresolved = total_upserted = 0
    with psycopg.connect(normalize_dsn(database_url)) as conn:
        start_date, end_date = _resolve_range(
            conn,
            requested_start=requested_start,
            requested_end=requested_end,
            lookback_days=args.lookback_days,
        )
        print(f"Short-interest range={start_date}..{end_date} dry_run={args.dry_run}", flush=True)
        next_url: str | None = SHORT_INTEREST_URL
        params: dict[str, object] | None = {
            "settlement_date.gte": start_date.isoformat(),
            "settlement_date.lte": end_date.isoformat(),
            "limit": 50000,
            "sort": "settlement_date.asc",
        }
        page = 0
        while next_url:
            page += 1
            payload = fetch_json(next_url, api_key=api_key, params=params)
            items = payload.get("results") or []
            rows = [row for item in items if (row := normalize_item(item)) is not None]
            resolved, unresolved, upserted = _stage_page(conn, rows, dry_run=args.dry_run)
            total_fetched += len(items)
            total_valid += len(rows)
            total_resolved += resolved
            total_unresolved += unresolved
            total_upserted += upserted
            print(
                f"Short-interest page={page} fetched={len(items)} valid={len(rows)} "
                f"resolved={resolved} unresolved={unresolved} upserted={upserted}",
                flush=True,
            )
            next_url = payload.get("next_url")
            params = None

    print(
        "Short-interest sync complete: "
        f"fetched={total_fetched} valid={total_valid} resolved={total_resolved} "
        f"unresolved={total_unresolved} upserted={total_upserted}",
        flush=True,
    )


if __name__ == "__main__":
    main()
