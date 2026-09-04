"""Manual latest-cap enrichment. `plan` is read-only and makes no vendor calls."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.models.tables import Instrument, InstrumentMarketCap

try:
    from .import_tushare_a_share import TushareClient, TushareError
    from .massive_enrichment_common import MASSIVE_API_BASE, fetch_json
except ImportError:
    from import_tushare_a_share import TushareClient, TushareError
    from massive_enrichment_common import MASSIVE_API_BASE, fetch_json


def normalize_cap(instrument, payload: dict, *, data_date: date | None, retrieved_at: datetime) -> dict:
    if instrument.vendor_source == "tushare":
        if payload.get("ts_code") != instrument.ticker_canonical:
            raise ValueError("Tushare ticker identity mismatch")
        if payload.get("trade_date") != data_date.strftime("%Y%m%d"):
            raise ValueError("Tushare data date mismatch")
        value, multiplier, currency = payload.get("total_mv"), 10000, "CNY"
    else:
        if (payload.get("share_class_figi") != instrument.share_class_figi
                or payload.get("ticker") != instrument.ticker_canonical):
            raise ValueError("Massive ticker/FIGI identity mismatch")
        if str(payload.get("currency_name", "")).upper() != "USD":
            raise ValueError("Massive currency mismatch")
        value, multiplier, currency = payload.get("market_cap"), 1, "USD"
    if currency != instrument.currency:
        raise ValueError("Instrument currency mismatch")
    try:
        amount = Decimal(str(value)) * multiplier
    except InvalidOperation as exc:
        raise ValueError("Missing or invalid market cap") from exc
    if not amount.is_finite() or not 0 < amount < Decimal("1e20"):
        raise ValueError("Missing or invalid market cap")
    return dict(amount=amount, currency=currency, source=instrument.vendor_source,
                data_date=data_date, retrieved_at=retrieved_at, vendor_payload=payload)


def store_snapshot(db: Session, instrument_id: int, values: dict) -> bool:
    current = db.get(InstrumentMarketCap, instrument_id)
    if current:
        # A cached payload or a historical rerun must never roll the latest snapshot back.
        existing_time = (current.retrieved_at.astimezone(timezone.utc) if current.retrieved_at.tzinfo
                         else current.retrieved_at.replace(tzinfo=timezone.utc))
        if values["retrieved_at"] < existing_time:
            return False
        if current.data_date and (values["data_date"] is None or values["data_date"] < current.data_date):
            return False
        if current.data_date == values["data_date"] and current.vendor_payload == values["vendor_payload"]:
            return False
        for key, value in values.items():
            setattr(current, key, value)
    else:
        db.add(InstrumentMarketCap(instrument_id=instrument_id, **values))
    return True


def main():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "apply"])
    parser.add_argument("--market", choices=["US", "CN"], required=True)
    parser.add_argument("--date", type=date.fromisoformat, help="Explicit market date; required for vendor refresh")
    parser.add_argument("--cached", action="store_true", help="US only: import saved ticker_overview without vendor calls")
    parser.add_argument("--symbols", nargs="+", help="Optional exact tickers, including exchange suffix for A shares")
    parser.add_argument("--request-interval", type=float, default=12,
                        help="Minimum seconds between live US requests (default 12, suitable for 5 requests/minute)")
    args = parser.parse_args()
    if args.cached and (args.market != "US" or args.date):
        parser.error("--cached is US-only and cannot be combined with --date")
    if not args.cached and not args.date:
        parser.error("--date is required unless --cached is selected")
    if args.date and args.date > date.today():
        parser.error("Future dates are not supported")
    if not 0 <= args.request_interval <= 3600:
        parser.error("--request-interval must be between 0 and 3600 seconds")
    engine = create_engine(os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
                           or "postgresql+psycopg://localhost:5432/hzy")
    statement = select(Instrument).where(
        Instrument.is_active.is_(True), Instrument.asset_type == "CS",
        Instrument.vendor_source == ("tushare" if args.market == "CN" else "massive"),
        Instrument.currency == ("CNY" if args.market == "CN" else "USD"),
        Instrument.ticker_canonical.is_not(None),
    ).order_by(Instrument.id)
    if args.market == "US":
        statement = statement.where(Instrument.locale == "us")
    if args.symbols:
        statement = statement.where(Instrument.ticker_canonical.in_([s.strip().upper() for s in args.symbols]))
    with engine.connect() as connection, Session(connection, expire_on_commit=False) as db:
        if args.mode == "plan":
            db.execute(text("SET TRANSACTION READ ONLY"))
        candidates = db.scalars(statement).all()
        installed = inspect(db.connection()).has_table("instrument_market_caps")
        print(json.dumps(dict(database=engine.url.database, host=engine.url.host, market=args.market,
                              candidates=len(candidates), schema_installed=installed,
                              cached=args.cached, date=str(args.date) if args.date else None)))
        if args.mode == "plan":
            if args.cached:
                reusable = 0
                for item in candidates:
                    try:
                        if item.sic_asof is not None:
                            normalize_cap(item, item.vendor_payload.get("ticker_overview") or {},
                                          data_date=None, retrieved_at=item.sic_asof)
                            reusable += 1
                    except ValueError:
                        continue
                print(json.dumps(dict(reusable_cached_snapshots=reusable, unavailable=len(candidates) - reusable)))
            print("Read-only plan; no vendor requests or database changes. Back up and obtain approval before apply.")
            return
        if not installed:
            raise SystemExit("Apply create_instrument_market_caps.sql after approval first")
        # Serialize only this independent snapshot writer; no research/cache/scheduler mutations.
        if not db.scalar(text("SELECT pg_try_advisory_lock(hashtext('stock_market_caps_refresh'))")):
            raise SystemExit("Another market-cap refresh is running")
        try:
            cn_rows = {}
            if args.market == "CN":
                client = TushareClient(os.getenv("TUSHARE_TOKEN", ""))
                rows = client.query("daily_basic", params={"trade_date": args.date.strftime("%Y%m%d")},
                                    fields="ts_code,trade_date,total_mv")
                cn_rows = {row["ts_code"]: row for row in rows}
                if not rows:
                    raise SystemExit("No data returned for the date; existing snapshots unchanged")
            api_key = os.getenv("MASSIVE_API_KEY", "")
            if args.market == "US" and not args.cached and not api_key:
                raise SystemExit("MASSIVE_API_KEY is required")
            updated = skipped = failed = 0
            last_request = None
            for index, item in enumerate(candidates, 1):
                try:
                    if args.cached:
                        payload = item.vendor_payload.get("ticker_overview") or {}
                        if not item.sic_asof:
                            raise ValueError("Cached payload has no known retrieval time")
                        retrieved_at = item.sic_asof
                    elif args.market == "CN":
                        payload = cn_rows.get(item.ticker_canonical, {})
                        retrieved_at = datetime.now(timezone.utc)
                    else:
                        if last_request is not None:
                            time.sleep(max(0, args.request_interval - (time.monotonic() - last_request)))
                        last_request = time.monotonic()
                        payload = fetch_json(f"{MASSIVE_API_BASE}/v3/reference/tickers/{item.ticker_canonical}",
                                             api_key=api_key, params={"date": args.date.isoformat()}).get("results") or {}
                        retrieved_at = datetime.now(timezone.utc)
                    values = normalize_cap(item, payload, data_date=args.date, retrieved_at=retrieved_at)
                    # Recheck the current master after the network read, and lock its identity until commit.
                    fresh = db.scalar(select(Instrument).where(Instrument.id == item.id)
                                      .with_for_update().execution_options(populate_existing=True))
                    if fresh is None or not fresh.is_active or fresh.asset_type != "CS":
                        raise ValueError("Instrument no longer eligible")
                    normalize_cap(fresh, payload, data_date=args.date, retrieved_at=retrieved_at)
                    changed = store_snapshot(db, item.id, values)
                    db.commit()
                    updated += int(changed)
                    skipped += int(not changed)
                except (ValueError, RuntimeError, OSError) as exc:
                    db.rollback()
                    failed += 1
                    # Vendor error bodies may contain credentials; report class and ticker only.
                    print(f"FAILED {item.ticker_canonical}: {type(exc).__name__}", flush=True)
                if index % 100 == 0:
                    print(json.dumps(dict(processed=index, candidates=len(candidates), updated=updated,
                                          skipped=skipped, failed=failed)), flush=True)
            print(json.dumps(dict(updated=updated, skipped=skipped, failed=failed)))
            if failed:
                raise SystemExit("Some symbols failed; rerun the same command or use --symbols to retry. Successful snapshots retained.")
        finally:
            db.rollback()
            db.execute(text("SELECT pg_advisory_unlock(hashtext('stock_market_caps_refresh'))"))
            db.commit()


if __name__ == "__main__":
    try:
        main()
    except TushareError:
        raise SystemExit("Tushare request failed; verify daily_basic access. Existing snapshots unchanged.") from None
