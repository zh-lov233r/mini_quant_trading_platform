from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
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

try:
    from backend.utils.market_data_maintenance_guard import require_market_data_maintenance_owner
except ModuleNotFoundError:
    from market_data_maintenance_guard import require_market_data_maintenance_owner


REPO_ROOT = Path(__file__).resolve().parents[2]
EARLIEST_PLAN_DATE = date(2016, 8, 29)
GROUPED_DAILY_URL = MASSIVE_API_BASE + "/v2/aggs/grouped/locale/us/market/stocks/{trade_date}"

MISSING_VWAP_SYMBOLS_SQL = """
SELECT
  e.dt_ny,
  e.instrument_id,
  CASE WHEN count(DISTINCT sh.symbol) = 1 THEN min(sh.symbol) END AS symbol
FROM eod_bars e
LEFT JOIN symbol_history sh
  ON sh.instrument_id = e.instrument_id
 AND sh.is_primary
 AND sh.valid_from <= e.dt_ny
 AND (sh.valid_to IS NULL OR sh.valid_to >= e.dt_ny)
WHERE e.dt_ny = ANY(%(trade_dates)s)
  AND e.vwap IS NULL
GROUP BY e.dt_ny, e.instrument_id
ORDER BY e.dt_ny, e.instrument_id
"""

STAGE_SQL = """
CREATE TEMP TABLE vwap_enrichment_stage (
  instrument_id BIGINT NOT NULL,
  trade_date DATE NOT NULL,
  vwap DOUBLE PRECISION NOT NULL
) ON COMMIT DROP
"""

UPDATE_SQL = """
WITH updated AS (
  UPDATE eod_bars e
  SET vwap = stage.vwap,
      asof = now()
  FROM vwap_enrichment_stage stage
  WHERE e.instrument_id = stage.instrument_id
    AND e.dt_ny = stage.trade_date
    AND e.vwap IS NULL
  RETURNING 1
)
SELECT count(*) FROM updated
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill null eod_bars.vwap values from Massive.")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_range(conn: psycopg.Connection, start: date | None, end: date | None) -> tuple[date, date]:
    row = conn.execute("SELECT min(dt_ny), max(dt_ny) FROM eod_bars").fetchone()
    if not row or row[0] is None or row[1] is None:
        raise SystemExit("eod_bars is empty")
    resolved_start = max(start or row[0], EARLIEST_PLAN_DATE)
    resolved_end = end or row[1]
    if resolved_start > resolved_end:
        raise SystemExit("No VWAP-eligible dates in the requested range")
    return resolved_start, resolved_end


def _load_missing(conn: psycopg.Connection, trade_date: date) -> list[tuple[int, str | None]]:
    return _load_missing_batch(conn, [trade_date])[trade_date]


def _load_missing_batch(
    conn: psycopg.Connection,
    trade_dates: list[date],
) -> dict[date, list[tuple[int, str | None]]]:
    result = {trade_date: [] for trade_date in trade_dates}
    rows = conn.execute(MISSING_VWAP_SYMBOLS_SQL, {"trade_dates": trade_dates}).fetchall()
    for trade_date, instrument_id, symbol in rows:
        result[trade_date].append(
            (int(instrument_id), str(symbol).upper() if symbol else None)
        )
    return result


def _fetch_vwaps(api_key: str, trade_date: date) -> dict[str, float]:
    payload = fetch_json(
        GROUPED_DAILY_URL.format(trade_date=trade_date.isoformat()),
        api_key=api_key,
        params={"adjusted": "false"},
    )
    values: dict[str, float] = {}
    for item in payload.get("results") or []:
        symbol = str(item.get("T") or item.get("ticker") or "").strip()
        raw_vwap = item.get("vw")
        if not symbol or symbol != symbol.upper() or raw_vwap is None:
            continue
        vwap = float(raw_vwap)
        if vwap > 0:
            values[symbol] = vwap
    return values


def build_stage_rows(
    missing: list[tuple[int, str | None]],
    provider_vwaps: dict[str, float],
    trade_date: date,
) -> list[tuple[int, date, float]]:
    rows: list[tuple[int, date, float]] = []
    seen: set[int] = set()
    for instrument_id, symbol in missing:
        if instrument_id in seen or symbol not in provider_vwaps:
            continue
        seen.add(instrument_id)
        rows.append((instrument_id, trade_date, provider_vwaps[symbol]))
    return rows


def _apply_rows(conn: psycopg.Connection, rows: list[tuple[int, date, float]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(STAGE_SQL)
        with cur.copy("COPY vwap_enrichment_stage (instrument_id, trade_date, vwap) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute(UPDATE_SQL)
        updated = int(cur.fetchone()[0])
    conn.commit()
    return updated


def iter_vwap_batches(
    conn: psycopg.Connection,
    api_key: str,
    trade_dates: list[date],
    *,
    workers: int,
):
    batch_size = workers * 2
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(trade_dates), batch_size):
            batch_dates = trade_dates[offset : offset + batch_size]
            missing_by_date = _load_missing_batch(conn, batch_dates)
            futures = {
                trade_date: executor.submit(_fetch_vwaps, api_key, trade_date)
                for trade_date in batch_dates
            }
            for trade_date in batch_dates:
                yield trade_date, missing_by_date[trade_date], futures[trade_date].result()


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.progress_every < 1:
        raise SystemExit("--progress-every must be >= 1")
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    api_key = os.getenv("MASSIVE_API_KEY")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    if not args.dry_run:
        require_market_data_maintenance_owner(database_url)
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY")

    requested_start = parse_optional_date(args.start_date)
    requested_end = parse_optional_date(args.end_date)
    if requested_start and requested_end and requested_start > requested_end:
        raise SystemExit("start date must be on or before end date")

    with psycopg.connect(normalize_dsn(database_url)) as conn:
        start_date, end_date = _load_range(conn, requested_start, requested_end)
        pre_entitlement = int(
            conn.execute(
                "SELECT count(*) FROM eod_bars WHERE vwap IS NULL AND dt_ny < %s",
                (EARLIEST_PLAN_DATE,),
            ).fetchone()[0]
        )
        print(
            f"VWAP range={start_date}..{end_date} dry_run={args.dry_run} "
            f"pre_entitlement_nulls={pre_entitlement}",
            flush=True,
        )
        total_missing = total_mapped = total_provider_missing = total_updated = 0
        unmatched_samples: list[dict[str, object]] = []
        pending_rows: list[tuple[int, date, float]] = []
        write_batch_size = args.workers * 2
        trade_dates = [row[0] for row in conn.execute(
            """
            SELECT DISTINCT dt_ny FROM eod_bars
            WHERE dt_ny BETWEEN %s AND %s AND vwap IS NULL
            ORDER BY dt_ny
            """,
            (start_date, end_date),
        )]
        batches = iter_vwap_batches(conn, api_key, trade_dates, workers=args.workers)
        for index, (trade_date, missing, provider_vwaps) in enumerate(batches, start=1):
            rows = build_stage_rows(missing, provider_vwaps, trade_date)
            if not args.dry_run:
                pending_rows.extend(rows)
            provider_missing = len(missing) - len(rows)
            total_missing += len(missing)
            total_mapped += len(rows)
            total_provider_missing += provider_missing
            if len(unmatched_samples) < 20:
                mapped_ids = {row[0] for row in rows}
                for instrument_id, symbol in missing:
                    if instrument_id not in mapped_ids:
                        unmatched_samples.append(
                            {
                                "trade_date": trade_date.isoformat(),
                                "instrument_id": instrument_id,
                                "symbol": symbol,
                            }
                        )
                        if len(unmatched_samples) == 20:
                            break
            should_report = index % args.progress_every == 0 or index == len(trade_dates)
            should_flush = (
                not args.dry_run
                and pending_rows
                and (index % write_batch_size == 0 or should_report)
            )
            if should_flush:
                total_updated += _apply_rows(conn, pending_rows)
                pending_rows.clear()
            if should_report:
                print(
                    f"VWAP progress={index}/{len(trade_dates)} through={trade_date} "
                    f"missing={total_missing} mapped={total_mapped} "
                    f"provider_or_identity_missing={total_provider_missing} updated={total_updated}",
                    flush=True,
                )

    print(
        "VWAP sync complete: "
        f"missing={total_missing} mapped={total_mapped} "
        f"provider_or_identity_missing={total_provider_missing} updated={total_updated}",
        flush=True,
    )
    if unmatched_samples:
        print(f"VWAP unmatched sample={unmatched_samples}", flush=True)


if __name__ == "__main__":
    main()
