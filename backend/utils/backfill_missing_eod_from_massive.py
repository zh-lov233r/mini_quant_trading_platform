from __future__ import annotations
"""Daily market backfill entrypoint.

This script keeps the local market database current by syncing the security
master first, then refreshing corporate actions, filling missing eod_bars rows,
recomputing adjusted prices, and finally refreshing the daily_features window
used by backtests and paper trading.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import TYPE_CHECKING
from urllib import error, parse, request
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs) -> bool:
        return False

if TYPE_CHECKING:
    import psycopg


REPO_ROOT = Path(__file__).resolve().parents[2]
GROUPED_DAILY_URL = "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{trade_date}"
NEW_YORK = ZoneInfo("America/New_York")
BENCHMARK_PROXY_SYMBOLS = ("SPY", "QQQ")
BENCHMARK_PROXY_SYMBOLS_SQL = ", ".join(f"'{symbol}'" for symbol in BENCHMARK_PROXY_SYMBOLS)

MISSING_SYMBOLS_SQL = """
WITH symbol_map AS (
    SELECT
        sh.symbol,
        MIN(sh.instrument_id) AS instrument_id,
        COUNT(DISTINCT sh.instrument_id) AS match_count
    FROM symbol_history sh
    JOIN instruments instr
      ON instr.id = sh.instrument_id
    WHERE sh.valid_from <= %(trade_date)s::date
      AND (sh.valid_to IS NULL OR sh.valid_to >= %(trade_date)s::date)
      -- Gap fills should only target the current primary alias for securities
      -- that are still active in the security master. Without this guard the
      -- job treats stale aliases and inactive tickers as same-day market-data
      -- gaps, which inflates still-missing counts dramatically.
      AND sh.is_primary
      AND instr.is_active = TRUE
      AND (
        instr.asset_type = 'CS'
        OR (
          instr.asset_type = 'ETF'
          AND instr.ticker_canonical IN (""" + BENCHMARK_PROXY_SYMBOLS_SQL + """)
        )
      )
      AND (instr.listed_at IS NULL OR instr.listed_at <= %(trade_date)s::date)
      AND (instr.delisted_at IS NULL OR instr.delisted_at >= %(trade_date)s::date)
    GROUP BY sh.symbol
)
SELECT
    map.symbol,
    map.instrument_id
FROM symbol_map map
LEFT JOIN eod_bars e
  ON e.instrument_id = map.instrument_id
 AND e.dt_ny = %(trade_date)s::date
WHERE map.match_count = 1
  AND e.instrument_id IS NULL
ORDER BY map.symbol;
"""

STAGE_TABLE_SQL = """
CREATE TEMP TABLE massive_missing_day_stage (
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
COPY massive_missing_day_stage (
    instrument_id, ts_utc, open_u, high_u, low_u, close_u, volume, vwap, trades
) FROM STDIN
"""

UPSERT_EOD_SQL = """
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
        'massive'
    FROM massive_missing_day_stage
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
class GroupedDailyBar:
    symbol: str
    ts_utc: datetime
    open_u: float | None
    high_u: float | None
    low_u: float | None
    close_u: float | None
    volume: int | None
    vwap: float | None
    trades: int | None


@dataclass(frozen=True)
class DaySyncStats:
    trade_date: date
    missing_symbols: int
    unique_missing_instruments: int
    api_rows: int
    staged_rows: int
    upserted_rows: int
    api_missing_symbols: int
    deduped_symbol_aliases: int


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for daily EOD gap filling."""
    parser = argparse.ArgumentParser(
        description=(
            "Fill missing eod_bars rows from Massive grouped daily aggregates. "
            "Intended to be run daily with a short recent lookback."
        )
    )
    parser.add_argument(
        "--date",
        help="Sync a single trade date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--start-date",
        help="Inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        help="Inclusive end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help=(
            "When no explicit date range is provided, scan this many recent calendar days. "
            "Defaults to 7."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect and fetch gaps but do not write to eod_bars.",
    )
    parser.add_argument(
        "--skip-features",
        action="store_true",
        help="Only fill eod_bars and skip the daily_features refresh step.",
    )
    parser.add_argument(
        "--skip-adjustments",
        action="store_true",
        help="Skip recomputing adjusted OHLC prices after the EOD gap-fill step.",
    )
    parser.add_argument(
        "--skip-corporate-actions",
        action="store_true",
        help="Skip syncing corporate actions after the security-master refresh step.",
    )
    parser.add_argument(
        "--skip-security-master",
        action="store_true",
        help="Skip the instrument + symbol_history sync step before gap filling.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    """Normalize SQLAlchemy-style URLs into raw psycopg DSNs."""
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return normalized.replace("postgresql+psycopg2://", "postgresql://", 1)


def _parse_date(value: str | None) -> date | None:
    """Parse an optional ISO date string."""
    return date.fromisoformat(value) if value else None


def _default_end_date() -> date:
    """Choose the latest trade date that should have complete daily data."""
    now_ny = datetime.now(NEW_YORK)
    cutoff_hour = 20
    if now_ny.hour >= cutoff_hour:
        return now_ny.date()
    return now_ny.date() - timedelta(days=1)


def _resolve_date_range(args: argparse.Namespace) -> tuple[date, date]:
    """Resolve the effective sync window from explicit args or lookback days."""
    if args.lookback_days < 1:
        raise SystemExit("--lookback-days must be >= 1")

    if args.date:
        trade_date = date.fromisoformat(args.date)
        return trade_date, trade_date

    end_date = _parse_date(args.end_date) or _default_end_date()
    start_date = _parse_date(args.start_date)
    if start_date is None:
        start_date = end_date - timedelta(days=args.lookback_days - 1)

    if start_date > end_date:
        raise SystemExit("start date must be on or before end date")
    return start_date, end_date


def _iter_weekdays(start_date: date, end_date: date) -> list[date]:
    """Expand a date range into weekday trade dates."""
    days: list[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _to_float(value: object) -> float | None:
    """Convert API payload values into floats when present."""
    return None if value is None else float(value)


def _to_int(value: object) -> int | None:
    """Convert API payload values into rounded integers when present."""
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Could not parse integer-like value: {value}") from exc


def _ts_ms_to_utc(ts_ms: int | float | str) -> datetime:
    """Convert Massive millisecond timestamps into UTC datetimes."""
    return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)


def _load_missing_symbols(conn, trade_date: date) -> list[tuple[str, int]]:
    """Load symbols that are expected for trade_date but missing in eod_bars."""
    with conn.cursor() as cur:
        cur.execute(MISSING_SYMBOLS_SQL, {"trade_date": trade_date.isoformat()})
        return [(str(row[0]).upper(), int(row[1])) for row in cur.fetchall()]


def _build_stage_rows(
    missing_symbols: list[tuple[str, int]],
    bars_by_symbol: dict[str, GroupedDailyBar],
) -> tuple[list[tuple], int, int]:
    """Map fetched API bars into unique instrument-level stage rows."""
    symbols_by_instrument: dict[int, list[str]] = {}
    for symbol, instrument_id in missing_symbols:
        symbols_by_instrument.setdefault(instrument_id, []).append(symbol)

    stage_rows: list[tuple] = []
    api_missing_symbols = 0
    deduped_symbol_aliases = 0

    for instrument_id, candidate_symbols in symbols_by_instrument.items():
        available_bars = [
            (symbol, bars_by_symbol[symbol])
            for symbol in candidate_symbols
            if symbol in bars_by_symbol
        ]
        if not available_bars:
            api_missing_symbols += len(candidate_symbols)
            continue

        # Multiple live aliases can point at the same instrument_id.
        # Choose a single bar per instrument to match the eod_bars primary key.
        chosen_symbol, chosen_bar = max(
            available_bars,
            key=lambda item: (
                item[1].ts_utc,
                item[1].volume or 0,
                item[0],
            ),
        )
        deduped_symbol_aliases += max(0, len(available_bars) - 1)
        api_missing_symbols += len(candidate_symbols) - len(available_bars)
        stage_rows.append(
            (
                instrument_id,
                chosen_bar.ts_utc,
                chosen_bar.open_u,
                chosen_bar.high_u,
                chosen_bar.low_u,
                chosen_bar.close_u,
                chosen_bar.volume,
                chosen_bar.vwap,
                chosen_bar.trades,
            )
        )

    return stage_rows, api_missing_symbols, deduped_symbol_aliases


def _stage_and_upsert_rows(conn, rows: list[tuple]) -> int:
    """Bulk stage grouped daily bars and upsert them into eod_bars."""
    with conn.cursor() as cur:
        cur.execute(STAGE_TABLE_SQL)
        with cur.copy(COPY_STAGE_SQL) as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute(UPSERT_EOD_SQL)
        upserted_rows = int(cur.fetchone()[0])
    conn.commit()
    return upserted_rows


def _fetch_json(url: str, *, headers: dict[str, str], params: dict[str, str] | None) -> dict:
    """Issue a GET request and decode the JSON payload."""
    final_url = url
    if params:
        final_url = f"{url}?{parse.urlencode(params)}"

    req = request.Request(final_url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=180) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        if exc.code == 404:
            return {}
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{exc.code} {exc.reason} | {final_url} | {body}") from exc

    return json.loads(body)


def _fetch_grouped_daily(
    headers: dict[str, str],
    trade_date: date,
) -> dict[str, GroupedDailyBar]:
    """Fetch grouped daily bars from Massive for one trade date."""
    next_url: str | None = GROUPED_DAILY_URL.format(trade_date=trade_date.isoformat())
    next_params: dict | None = {"adjusted": "false"}
    bars: dict[str, GroupedDailyBar] = {}

    while next_url:
        payload = _fetch_json(next_url, headers=headers, params=next_params)
        if not payload:
            return {}

        for item in payload.get("results") or []:
            raw_symbol = str(item.get("T") or item.get("ticker") or "").strip()
            if not raw_symbol or raw_symbol != raw_symbol.upper():
                continue
            symbol = raw_symbol.upper()
            timestamp_ms = item.get("t")
            if not symbol or timestamp_ms in (None, ""):
                continue
            bars[symbol] = GroupedDailyBar(
                symbol=symbol,
                ts_utc=_ts_ms_to_utc(timestamp_ms),
                open_u=_to_float(item.get("o")),
                high_u=_to_float(item.get("h")),
                low_u=_to_float(item.get("l")),
                close_u=_to_float(item.get("c")),
                volume=_to_int(item.get("v")),
                vwap=_to_float(item.get("vw")),
                trades=_to_int(item.get("n")),
            )

        next_url = payload.get("next_url")
        next_params = None

    return bars


def _run_security_master_sync(*, database_url: str) -> None:
    """Refresh instruments and symbol_history before looking for market-data gaps."""
    sync_script = REPO_ROOT / "backend" / "utils" / "backfill_instruments_and_symbol.py"
    command = [sys.executable, str(sync_script)]
    printable = " ".join(command)
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    print(f"\n[sync-security-master] {printable}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def _run_feature_refresh(
    *,
    database_url: str,
    start_date: date,
    end_date: date,
) -> None:
    """Refresh daily_features for the same date window after EOD writes."""
    feature_script = REPO_ROOT / "backend" / "utils" / "backfill_daily_features.py"
    command = [
        sys.executable,
        str(feature_script),
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--database-url",
        database_url,
    ]
    printable = " ".join(command)
    print(f"\n[refresh-daily-features] {printable}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def _run_corporate_action_sync(
    *,
    database_url: str,
    start_date: date,
    end_date: date,
) -> None:
    """Refresh corporate actions for the same date window after security-master sync."""
    action_script = REPO_ROOT / "backend" / "utils" / "backfill_corporate_actions.py"
    command = [
        sys.executable,
        str(action_script),
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--database-url",
        database_url,
    ]
    printable = " ".join(command)
    print(f"\n[sync-corporate-actions] {printable}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def _run_adjusted_price_refresh(
    *,
    database_url: str,
    start_date: date,
    end_date: date,
) -> None:
    """Refresh adjusted OHLC columns for the same date window after EOD writes."""
    adjustment_script = REPO_ROOT / "backend" / "utils" / "backfill_adjusted_prices.py"
    command = [
        sys.executable,
        str(adjustment_script),
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--database-url",
        database_url,
    ]
    printable = " ".join(command)
    print(f"\n[refresh-adjusted-prices] {printable}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def main() -> None:
    """Run the full daily sync flow: security master, corporate actions, EOD gaps, adjustments, then features."""
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency `psycopg`. Install it first, for example: pip install psycopg[binary]"
        ) from exc

    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY")
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    start_date, end_date = _resolve_date_range(args)
    trade_dates = _iter_weekdays(start_date, end_date)
    if not trade_dates:
        raise SystemExit("No weekday trade dates in the requested range")

    print(
        f"Scanning {len(trade_dates)} weekday(s) for missing eod_bars "
        f"from {start_date} to {end_date}.",
        flush=True,
    )

    if args.dry_run:
        print(
            "Dry run enabled; skipping security master and corporate-action sync because they write to the database.",
            flush=True,
        )
    elif args.skip_security_master:
        print("Skipping security master sync by request.", flush=True)
    else:
        _run_security_master_sync(database_url=args.database_url)

    if args.dry_run:
        pass
    elif args.skip_corporate_actions:
        print("Skipping corporate-action sync by request.", flush=True)
    else:
        _run_corporate_action_sync(
            database_url=args.database_url,
            start_date=start_date,
            end_date=end_date,
        )

    total_missing = 0
    total_unique_missing_instruments = 0
    total_api_rows = 0
    total_staged = 0
    total_upserted = 0
    total_api_missing = 0
    total_deduped_aliases = 0

    headers = {"Authorization": f"Bearer {api_key}"}
    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        for trade_date in trade_dates:
            missing_symbols = _load_missing_symbols(conn, trade_date)
            missing_count = len(missing_symbols)
            total_missing += missing_count
            unique_missing_instruments = len({instrument_id for _, instrument_id in missing_symbols})
            total_unique_missing_instruments += unique_missing_instruments

            if not missing_symbols:
                print(f"[{trade_date}] no database gaps detected", flush=True)
                continue

            print(
                f"[{trade_date}] missing_symbols={missing_count} "
                f"unique_instruments={unique_missing_instruments} "
                f"fetching Massive grouped daily bars",
                flush=True,
            )
            bars_by_symbol = _fetch_grouped_daily(headers, trade_date)
            api_rows = len(bars_by_symbol)
            total_api_rows += api_rows

            stage_rows, api_missing_symbols, deduped_symbol_aliases = _build_stage_rows(
                missing_symbols,
                bars_by_symbol,
            )
            staged_rows = len(stage_rows)
            total_staged += staged_rows
            total_api_missing += api_missing_symbols
            total_deduped_aliases += deduped_symbol_aliases

            if args.dry_run:
                upserted_rows = 0
            else:
                upserted_rows = _stage_and_upsert_rows(conn, stage_rows) if stage_rows else 0
            total_upserted += upserted_rows

            stats = DaySyncStats(
                trade_date=trade_date,
                missing_symbols=missing_count,
                unique_missing_instruments=unique_missing_instruments,
                api_rows=api_rows,
                staged_rows=staged_rows,
                upserted_rows=upserted_rows,
                api_missing_symbols=api_missing_symbols,
                deduped_symbol_aliases=deduped_symbol_aliases,
            )
            print(
                f"[{stats.trade_date}] api_rows={stats.api_rows} "
                f"unique_instruments={stats.unique_missing_instruments} "
                f"staged={stats.staged_rows} upserted={stats.upserted_rows} "
                f"still_missing={stats.api_missing_symbols} "
                f"deduped_aliases={stats.deduped_symbol_aliases}",
                flush=True,
            )

    print("\nSync complete.", flush=True)
    print(
        f"total_missing_symbols={total_missing} "
        f"total_unique_missing_instruments={total_unique_missing_instruments} "
        f"total_api_rows={total_api_rows} "
        f"total_staged={total_staged} total_upserted={total_upserted} "
        f"total_still_missing={total_api_missing} "
        f"total_deduped_aliases={total_deduped_aliases}",
        flush=True,
    )

    if args.dry_run:
        print("\nDry run enabled; skipping adjusted-price and daily_features refresh.", flush=True)
        return

    if args.skip_adjustments:
        print("\nSkipping adjusted-price refresh by request.", flush=True)
    else:
        _run_adjusted_price_refresh(
            database_url=args.database_url,
            start_date=start_date,
            end_date=end_date,
        )

    if args.skip_features:
        print("\nSkipping daily_features refresh by request.", flush=True)
        return

    _run_feature_refresh(
        database_url=args.database_url,
        start_date=start_date,
        end_date=end_date,
    )


if __name__ == "__main__":
    main()
