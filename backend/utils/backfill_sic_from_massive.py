from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

if __package__:
    from .massive_enrichment_common import MASSIVE_API_BASE, fetch_json, normalize_dsn
else:
    from massive_enrichment_common import MASSIVE_API_BASE, fetch_json, normalize_dsn

try:
    from backend.utils.market_data_maintenance_guard import require_market_data_maintenance_owner
except ModuleNotFoundError:
    from market_data_maintenance_guard import require_market_data_maintenance_owner


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = "sic"

CANDIDATES_SQL = """
SELECT
  i.id,
  i.ticker_canonical,
  i.share_class_figi,
  i.is_active,
  COALESCE(i.delisted_at, bars.last_bar) AS inactive_asof
FROM instruments i
LEFT JOIN LATERAL (
  SELECT max(e.dt_ny) AS last_bar
  FROM eod_bars e
  WHERE e.instrument_id = i.id
) bars ON TRUE
LEFT JOIN instrument_vendor_sync_state state
  ON state.instrument_id = i.id
 AND state.dataset = 'sic'
 AND state.vendor_source = 'massive'
WHERE i.ticker_canonical IS NOT NULL
  AND (
    %(full_refresh)s
    OR state.last_success_at IS NULL
    OR state.last_success_at < now() - make_interval(days => %(stale_days)s)
  )
ORDER BY i.is_active DESC, i.ticker_canonical
"""

UPDATE_INSTRUMENT_SQL = """
UPDATE instruments
SET sic_code = COALESCE(%(sic_code)s, sic_code),
    sic_description = COALESCE(%(sic_description)s, sic_description),
    sic_source = CASE WHEN %(sic_code)s IS NOT NULL THEN 'massive' ELSE sic_source END,
    sic_asof = CASE WHEN %(sic_code)s IS NOT NULL THEN now() ELSE sic_asof END,
    mic = COALESCE(mic, %(primary_exchange)s),
    country = COALESCE(country, %(country)s),
    listed_at = COALESCE(listed_at, %(list_date)s),
    vendor_payload = jsonb_set(
      vendor_payload,
      '{ticker_overview}',
      %(vendor_payload)s::jsonb,
      true
    )
WHERE id = %(instrument_id)s
"""

UPSERT_STATE_SQL = """
INSERT INTO instrument_vendor_sync_state (
  instrument_id, dataset, vendor_source, last_attempt_at, last_success_at, last_error
) VALUES (%s, 'sic', 'massive', now(), now(), NULL)
ON CONFLICT (instrument_id, dataset, vendor_source) DO UPDATE SET
  last_attempt_at = now(),
  last_success_at = now(),
  last_error = NULL
"""

UPSERT_ERROR_STATE_SQL = """
INSERT INTO instrument_vendor_sync_state (
  instrument_id, dataset, vendor_source, last_attempt_at, last_error
) VALUES (%s, 'sic', 'massive', now(), %s)
ON CONFLICT (instrument_id, dataset, vendor_source) DO UPDATE SET
  last_attempt_at = now(),
  last_error = EXCLUDED.last_error
"""


@dataclass(frozen=True)
class SicCandidate:
    instrument_id: int
    ticker: str
    share_class_figi: str
    is_active: bool
    inactive_asof: date | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich instruments with current Massive SIC metadata.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_sic_payload(item: dict) -> dict[str, object]:
    code = str(item.get("sic_code") or "").strip() or None
    description = str(item.get("sic_description") or "").strip() or None
    locale = str(item.get("locale") or "").lower()
    return {
        "sic_code": code,
        "sic_description": description,
        "primary_exchange": item.get("primary_exchange"),
        "country": "US" if locale == "us" else None,
        "list_date": item.get("list_date"),
    }


def details_match_instrument(item: dict, share_class_figi: str) -> bool:
    returned = item.get("share_class_figi")
    return returned in (None, "", share_class_figi)


def candidate_asof_date(candidate: SicCandidate) -> date | None:
    return None if candidate.is_active else candidate.inactive_asof


def _load_candidates(conn: psycopg.Connection, *, full_refresh: bool, stale_days: int) -> list[SicCandidate]:
    rows = conn.execute(
        CANDIDATES_SQL,
        {"full_refresh": full_refresh, "stale_days": stale_days},
    ).fetchall()
    return [
        SicCandidate(int(row[0]), str(row[1]), str(row[2]), bool(row[3]), row[4])
        for row in rows
    ]


def _fetch_details(api_key: str, candidate: SicCandidate) -> dict:
    params: dict[str, object] = {}
    asof_date = candidate_asof_date(candidate)
    if asof_date:
        params["date"] = asof_date.isoformat()
    payload = fetch_json(
        f"{MASSIVE_API_BASE}/v3/reference/tickers/{candidate.ticker}",
        api_key=api_key,
        params=params,
    )
    return payload.get("results") or {}


def fetch_candidate_details(
    api_key: str,
    candidates: list[SicCandidate],
    *,
    workers: int,
):
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_fetch_details, api_key, candidate) for candidate in candidates]
        for candidate, future in zip(candidates, futures):
            try:
                yield candidate, future.result(), None
            except Exception as exc:
                yield candidate, None, exc


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if args.stale_days < 1:
        raise SystemExit("--stale-days must be >= 1")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    api_key = os.getenv("MASSIVE_API_KEY")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    if not args.dry_run:
        require_market_data_maintenance_owner(database_url)
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY")

    fetched = enriched = missing = mismatched = failures = 0
    with psycopg.connect(normalize_dsn(database_url)) as conn:
        candidates = _load_candidates(
            conn,
            full_refresh=args.full_refresh,
            stale_days=args.stale_days,
        )
        print(
            f"SIC candidates={len(candidates)} full_refresh={args.full_refresh} dry_run={args.dry_run}",
            flush=True,
        )
        for index, (candidate, item, fetch_error) in enumerate(
            fetch_candidate_details(api_key, candidates, workers=args.workers),
            start=1,
        ):
            try:
                if fetch_error is not None:
                    raise fetch_error
                fetched += 1
                if not item:
                    missing += 1
                    if not args.dry_run:
                        conn.execute(UPSERT_STATE_SQL, (candidate.instrument_id,))
                        conn.commit()
                elif not details_match_instrument(item, candidate.share_class_figi):
                    mismatched += 1
                    if not args.dry_run:
                        conn.execute(
                            UPSERT_ERROR_STATE_SQL,
                            (candidate.instrument_id, "ticker overview FIGI mismatch"),
                        )
                        conn.commit()
                else:
                    normalized = normalize_sic_payload(item)
                    if normalized["sic_code"]:
                        enriched += 1
                    else:
                        missing += 1
                    if not args.dry_run:
                        conn.execute(
                            UPDATE_INSTRUMENT_SQL,
                            {
                                **normalized,
                                "instrument_id": candidate.instrument_id,
                                "vendor_payload": json.dumps(item),
                            },
                        )
                        conn.execute(UPSERT_STATE_SQL, (candidate.instrument_id,))
                        conn.commit()
            except Exception as exc:
                conn.rollback()
                failures += 1
                message = f"{type(exc).__name__}: {str(exc)[:300]}"
                if not args.dry_run:
                    conn.execute(UPSERT_ERROR_STATE_SQL, (candidate.instrument_id, message))
                    conn.commit()
                print(f"WARN SIC {candidate.ticker}: {message}", flush=True)
            if index % 100 == 0 or index == len(candidates):
                print(
                    f"SIC progress={index}/{len(candidates)} enriched={enriched} "
                    f"missing={missing} mismatched={mismatched} failures={failures}",
                    flush=True,
                )

    print(
        f"SIC sync complete: fetched={fetched} enriched={enriched} missing={missing} "
        f"mismatched={mismatched} failures={failures}",
        flush=True,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
