from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import aiohttp
import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]

SPLITS_URL = "https://api.massive.com/stocks/v1/splits"
DIVIDENDS_URL = "https://api.massive.com/v3/reference/dividends"

LOOKUP_INSTRUMENT_SQL = """
SELECT instrument_id
FROM symbol_history
WHERE symbol = %(symbol)s
  AND valid_from <= %(event_date)s::date
  AND (valid_to IS NULL OR valid_to >= %(event_date)s::date)
ORDER BY is_primary DESC, instrument_id
"""

UPSERT_CORPORATE_ACTION_SQL = """
INSERT INTO corporate_actions (
  instrument_id,
  action_type,
  ex_date,
  announcement_date,
  record_date,
  payable_date,
  split_from,
  split_to,
  cash_amount,
  currency,
  vendor_event_id,
  vendor_source,
  vendor_payload
) VALUES (
  %(instrument_id)s,
  %(action_type)s,
  %(ex_date)s,
  %(announcement_date)s,
  %(record_date)s,
  %(payable_date)s,
  %(split_from)s,
  %(split_to)s,
  %(cash_amount)s,
  %(currency)s,
  %(vendor_event_id)s,
  'massive',
  %(vendor_payload)s::jsonb
)
ON CONFLICT (vendor_source, vendor_event_id)
WHERE vendor_event_id IS NOT NULL
DO UPDATE SET
  instrument_id = EXCLUDED.instrument_id,
  action_type = EXCLUDED.action_type,
  ex_date = EXCLUDED.ex_date,
  announcement_date = EXCLUDED.announcement_date,
  record_date = EXCLUDED.record_date,
  payable_date = EXCLUDED.payable_date,
  split_from = EXCLUDED.split_from,
  split_to = EXCLUDED.split_to,
  cash_amount = EXCLUDED.cash_amount,
  currency = EXCLUDED.currency,
  vendor_payload = EXCLUDED.vendor_payload,
  updated_at = now();
"""


@dataclass
class Stats:
    fetched: int = 0
    inserted_or_updated: int = 0
    skipped_non_common: int = 0
    unresolved_missing: int = 0
    unresolved_ambiguous: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Massive corporate actions and upsert them into corporate_actions."
    )
    parser.add_argument(
        "--start-date",
        default="2016-03-18",
        help="Inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Inclusive end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _to_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _split_action_type(raw_type: str | None, split_from: Decimal | None, split_to: Decimal | None) -> str:
    raw = (raw_type or "").lower()
    if raw == "reverse_split":
        return "reverse_split"
    if raw == "stock_dividend":
        return "stock_dividend"
    if split_from and split_to and split_to < split_from:
        return "reverse_split"
    return "split"


def _lookup_instrument_id(conn: psycopg.Connection, symbol: str, event_date: date) -> tuple[int | None, str]:
    with conn.cursor() as cur:
        cur.execute(
            LOOKUP_INSTRUMENT_SQL,
            {"symbol": symbol, "event_date": event_date.isoformat()},
        )
        rows = [r[0] for r in cur.fetchall()]

    unique = sorted(set(rows))
    if not unique:
        return None, "missing"
    if len(unique) > 1:
        return None, "ambiguous"
    return unique[0], "resolved"


def _load_common_stock_symbols(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT sh.symbol
            FROM symbol_history sh
            JOIN instruments instr
              ON instr.id = sh.instrument_id
            WHERE instr.asset_type = 'CS'
            """
        )
        return {row[0] for row in cur.fetchall()}


async def _fetch_all(session: aiohttp.ClientSession, url: str, params: dict) -> list[dict]:
    next_url: str | None = url
    next_params: dict | None = params
    page = 0

    while next_url:
        page += 1
        async with session.get(next_url, params=next_params, timeout=180) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"{response.status} {response.reason} | {next_url} | {body}")
            payload = await response.json()

        results = payload.get("results") or []
        print(
            f"Fetched page {page} from {url} results={len(results)}",
            flush=True,
        )
        yield results
        next_url = payload.get("next_url")
        next_params = None


def _split_vendor_event_id(item: dict) -> str:
    return (
        item.get("id")
        or f"split:{item.get('ticker')}:{item.get('execution_date')}:{item.get('adjustment_type')}:"
           f"{item.get('split_from')}:{item.get('split_to')}"
    )


def _dividend_vendor_event_id(item: dict) -> str:
    return (
        item.get("id")
        or f"dividend:{item.get('ticker')}:{item.get('ex_dividend_date')}:{item.get('cash_amount')}:"
           f"{item.get('dividend_type')}"
    )


def _upsert_split(
    conn: psycopg.Connection,
    item: dict,
    stats: Stats,
    common_stock_symbols: set[str],
) -> None:
    stats.fetched += 1
    symbol = (item.get("ticker") or "").upper().strip()
    event_date = _parse_date(item.get("execution_date"))
    if not symbol or not event_date:
        stats.unresolved_missing += 1
        return
    if symbol not in common_stock_symbols:
        stats.skipped_non_common += 1
        return

    instrument_id, resolution = _lookup_instrument_id(conn, symbol, event_date)
    if resolution == "missing":
        stats.unresolved_missing += 1
        return
    if resolution == "ambiguous":
        stats.unresolved_ambiguous += 1
        return

    split_from = _to_decimal(item.get("split_from"))
    split_to = _to_decimal(item.get("split_to"))
    params = {
        "instrument_id": instrument_id,
        "action_type": _split_action_type(item.get("adjustment_type"), split_from, split_to),
        "ex_date": event_date,
        "announcement_date": None,
        "record_date": None,
        "payable_date": None,
        "split_from": split_from,
        "split_to": split_to,
        "cash_amount": None,
        "currency": "USD",
        "vendor_event_id": _split_vendor_event_id(item),
        "vendor_payload": json.dumps(item),
    }
    with conn.cursor() as cur:
        cur.execute(UPSERT_CORPORATE_ACTION_SQL, params)
    stats.inserted_or_updated += 1


def _upsert_dividend(
    conn: psycopg.Connection,
    item: dict,
    stats: Stats,
    common_stock_symbols: set[str],
) -> None:
    stats.fetched += 1
    symbol = (item.get("ticker") or "").upper().strip()
    event_date = _parse_date(item.get("ex_dividend_date"))
    if not symbol or not event_date:
        stats.unresolved_missing += 1
        return
    if symbol not in common_stock_symbols:
        stats.skipped_non_common += 1
        return

    instrument_id, resolution = _lookup_instrument_id(conn, symbol, event_date)
    if resolution == "missing":
        stats.unresolved_missing += 1
        return
    if resolution == "ambiguous":
        stats.unresolved_ambiguous += 1
        return

    params = {
        "instrument_id": instrument_id,
        "action_type": "cash_dividend",
        "ex_date": event_date,
        "announcement_date": _parse_date(item.get("declaration_date")),
        "record_date": _parse_date(item.get("record_date")),
        "payable_date": _parse_date(item.get("pay_date")),
        "split_from": None,
        "split_to": None,
        "cash_amount": _to_decimal(item.get("cash_amount")),
        "currency": (item.get("currency") or "USD").upper(),
        "vendor_event_id": _dividend_vendor_event_id(item),
        "vendor_payload": json.dumps(item),
    }
    with conn.cursor() as cur:
        cur.execute(UPSERT_CORPORATE_ACTION_SQL, params)
    stats.inserted_or_updated += 1


async def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY")

    headers = {"Authorization": f"Bearer {api_key}"}
    split_params = {
        "execution_date.gte": args.start_date,
        "execution_date.lte": args.end_date,
        "limit": 1000,
    }
    dividend_params = {
        "ex_dividend_date.gte": args.start_date,
        "ex_dividend_date.lte": args.end_date,
        "limit": 1000,
        "order": "asc",
        "sort": "ex_dividend_date",
    }

    stats = Stats()
    split_count = 0
    dividend_count = 0
    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        common_stock_symbols = _load_common_stock_symbols(conn)
        print(f"Loaded common_stock_symbols={len(common_stock_symbols)}", flush=True)
        async with aiohttp.ClientSession(headers=headers) as session:
            async for page_results in _fetch_all(session, SPLITS_URL, split_params):
                for item in page_results:
                    _upsert_split(conn, item, stats, common_stock_symbols)
                    split_count += 1
                conn.commit()
                print(
                    f"Processed splits={split_count} "
                    f"upserted={stats.inserted_or_updated} skipped_non_common={stats.skipped_non_common} "
                    f"missing={stats.unresolved_missing} "
                    f"ambiguous={stats.unresolved_ambiguous}",
                    flush=True,
                )

            async for page_results in _fetch_all(session, DIVIDENDS_URL, dividend_params):
                for item in page_results:
                    _upsert_dividend(conn, item, stats, common_stock_symbols)
                    dividend_count += 1
                conn.commit()
                print(
                    f"Processed dividends={dividend_count} "
                    f"upserted={stats.inserted_or_updated} skipped_non_common={stats.skipped_non_common} "
                    f"missing={stats.unresolved_missing} "
                    f"ambiguous={stats.unresolved_ambiguous}",
                    flush=True,
                )

    print(
        f"Fetched={stats.fetched} upserted={stats.inserted_or_updated} "
        f"skipped_non_common={stats.skipped_non_common} "
        f"missing={stats.unresolved_missing} ambiguous={stats.unresolved_ambiguous} "
        f"(splits={split_count} dividends={dividend_count})",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
