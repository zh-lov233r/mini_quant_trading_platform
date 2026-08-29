from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
NEW_YORK = ZoneInfo("America/New_York")

LATEST_COVERAGE_SQL = """
SELECT
  (SELECT MAX(dt_ny) FROM eod_bars) AS latest_eod_date,
  (SELECT MAX(dt_ny) FROM daily_features) AS latest_feature_date
"""


@dataclass(frozen=True)
class CoverageWindow:
    latest_eod_date: date | None
    latest_feature_date: date | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the daily market-data flow: security master, SIC/ticker events, EOD gaps, "
            "VWAP, corporate actions, adjusted prices, short interest, daily features, "
            "then read-only integrity checks."
        )
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
        "--lookback-days",
        type=int,
        default=14,
        help=(
            "Rolling calendar-day window to rescan when the database is already current. "
            "Defaults to 14."
        ),
    )
    parser.add_argument(
        "--cutoff-hour-ny",
        type=int,
        default=20,
        help=(
            "Before this New York hour, default end_date becomes the previous calendar day. "
            "Defaults to 20."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--skip-security-master",
        action="store_true",
        help="Skip the instrument and symbol-history refresh.",
    )
    parser.add_argument(
        "--skip-sic",
        action="store_true",
        help="Skip SIC/reference-detail enrichment.",
    )
    parser.add_argument(
        "--skip-ticker-events",
        action="store_true",
        help="Skip raw ticker-event sync and safe identity repair.",
    )
    parser.add_argument(
        "--skip-vwap",
        action="store_true",
        help="Skip filling null eod_bars.vwap values.",
    )
    parser.add_argument(
        "--skip-short-interest",
        action="store_true",
        help="Skip the short-interest fact refresh.",
    )
    parser.add_argument(
        "--full-reference-refresh",
        action="store_true",
        help="Refresh all SIC and ticker-event candidates instead of only stale rows.",
    )
    parser.add_argument(
        "--skip-features",
        action="store_true",
        help="Only fill eod_bars and skip daily_features refresh.",
    )
    parser.add_argument(
        "--skip-adjustments",
        action="store_true",
        help="Skip recomputing adjusted OHLC prices after the EOD sync step.",
    )
    parser.add_argument(
        "--skip-corporate-actions",
        action="store_true",
        help="Skip syncing corporate actions after the EOD sync step.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect the eod gap-fill step without writing rows.",
    )
    parser.add_argument(
        "--skip-quality-check",
        action="store_true",
        help="Skip the final read-only market-data integrity gate.",
    )
    parser.add_argument(
        "--strict-quality-check",
        action="store_true",
        help="Treat quality-check warnings as failures.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return normalized.replace("postgresql+psycopg2://", "postgresql://", 1)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _default_end_date(cutoff_hour_ny: int) -> date:
    now_ny = datetime.now(NEW_YORK)
    if now_ny.hour >= cutoff_hour_ny:
        return now_ny.date()
    return now_ny.date() - timedelta(days=1)


def _load_latest_coverage(database_url: str) -> CoverageWindow:
    with psycopg.connect(_psycopg_dsn(database_url)) as conn:
        with conn.cursor() as cur:
            cur.execute(LATEST_COVERAGE_SQL)
            latest_eod_date, latest_feature_date = cur.fetchone()
    return CoverageWindow(
        latest_eod_date=latest_eod_date,
        latest_feature_date=latest_feature_date,
    )


def _resolve_date_range(
    args: argparse.Namespace,
    coverage: CoverageWindow,
) -> tuple[date, date, str]:
    if args.lookback_days < 1:
        raise SystemExit("--lookback-days must be >= 1")

    end_date = _parse_date(args.end_date) or _default_end_date(args.cutoff_hour_ny)
    start_date = _parse_date(args.start_date)

    if start_date is not None:
        reason = "explicit date range"
    else:
        catchup_candidates: list[date] = []
        if coverage.latest_eod_date is not None and coverage.latest_eod_date < end_date:
            catchup_candidates.append(coverage.latest_eod_date + timedelta(days=1))
        if coverage.latest_feature_date is not None and coverage.latest_feature_date < end_date:
            catchup_candidates.append(coverage.latest_feature_date + timedelta(days=1))

        if catchup_candidates:
            start_date = min(catchup_candidates)
            reason = "catching up from the earliest missing table coverage"
        else:
            start_date = end_date - timedelta(days=args.lookback_days - 1)
            reason = "database looks current; rescanning the rolling lookback window"

    if start_date > end_date:
        raise SystemExit("start date must be on or before end date")
    return start_date, end_date, reason


def _run_step(
    step_name: str,
    script_path: Path,
    script_args: list[str],
    *,
    database_url: str,
) -> None:
    command = [sys.executable, str(script_path), *script_args]
    printable = " ".join(command)
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    # This maintenance pipeline never needs the paper-trading runtime. Keep
    # both controls explicitly disabled for every child even if a developer's
    # shell or .env enables them for the backend application.
    env["PAPER_TRADING_SCHEDULER_ENABLED"] = "false"
    env["PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS"] = "false"
    print(f"\n[{step_name}] {printable}", flush=True)
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def _run_adjusted_price_refresh(shared_args: list[str], *, database_url: str) -> None:
    adjustment_script = REPO_ROOT / "backend" / "utils" / "backfill_adjusted_prices.py"
    _run_step(
        "refresh-adjusted-prices",
        adjustment_script,
        shared_args,
        database_url=database_url,
    )


def _run_corporate_action_sync(shared_args: list[str], *, database_url: str) -> None:
    action_script = REPO_ROOT / "backend" / "utils" / "backfill_corporate_actions.py"
    _run_step(
        "sync-corporate-actions",
        action_script,
        shared_args,
        database_url=database_url,
    )


def _run_quality_check(database_url: str, as_of_date: date, *, strict: bool) -> None:
    quality_script = REPO_ROOT / "backend" / "utils" / "check_market_data_quality.py"
    quality_args = [
        "--as-of-date",
        as_of_date.isoformat(),
    ]
    if strict:
        quality_args.append("--strict")
    _run_step(
        "check-market-data-quality",
        quality_script,
        quality_args,
        database_url=database_url,
    )


def _run_script(
    step_name: str,
    filename: str,
    script_args: list[str],
    *,
    database_url: str,
) -> None:
    _run_step(
        step_name,
        REPO_ROOT / "backend" / "utils" / filename,
        script_args,
        database_url=database_url,
    )


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()

    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    initial_coverage = _load_latest_coverage(args.database_url)
    start_date, end_date, reason = _resolve_date_range(args, initial_coverage)

    print(
        "Resolved backfill window:",
        flush=True,
    )
    print(
        f"  start_date={start_date} end_date={end_date} reason={reason}",
        flush=True,
    )
    print(
        "Current coverage before run:",
        flush=True,
    )
    print(
        f"  latest_eod_date={initial_coverage.latest_eod_date} "
        f"latest_feature_date={initial_coverage.latest_feature_date}",
        flush=True,
    )

    shared_args = [
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
    ]

    if args.dry_run:
        print("\nDry run: security-master sync is skipped because it has no dry-run mode.", flush=True)
    elif args.skip_security_master:
        print("\nSkipping security-master sync by request.", flush=True)
    else:
        _run_script(
            "sync-security-master",
            "backfill_instruments_and_symbol.py",
            [],
            database_url=args.database_url,
        )

    reference_args: list[str] = []
    if args.full_reference_refresh:
        reference_args.append("--full-refresh")
    if args.dry_run:
        reference_args.append("--dry-run")

    if args.skip_sic:
        print("\nSkipping SIC enrichment by request.", flush=True)
    else:
        _run_script(
            "sync-sic-reference",
            "backfill_sic_from_massive.py",
            reference_args,
            database_url=args.database_url,
        )

    if args.skip_ticker_events:
        print("\nSkipping ticker-event sync by request.", flush=True)
    else:
        _run_script(
            "sync-ticker-events",
            "backfill_ticker_events_from_massive.py",
            reference_args,
            database_url=args.database_url,
        )

    eod_args = [
        *shared_args,
        "--skip-security-master",
        "--skip-features",
        "--skip-adjustments",
        "--skip-corporate-actions",
    ]
    if args.dry_run:
        eod_args.append("--dry-run")
    _run_script(
        "fill-eod-gaps",
        "backfill_missing_eod_from_massive.py",
        eod_args,
        database_url=args.database_url,
    )

    if args.skip_vwap:
        print("\nSkipping VWAP enrichment by request.", flush=True)
    else:
        vwap_args = [*shared_args]
        if args.dry_run:
            vwap_args.append("--dry-run")
        _run_script(
            "fill-vwap-gaps",
            "backfill_vwap_from_massive.py",
            vwap_args,
            database_url=args.database_url,
        )

    if args.dry_run:
        print(
            "\nDry run enabled; skipping corporate-action, adjusted-price, and daily_features refresh because no EOD rows were written.",
            flush=True,
        )
    elif args.skip_corporate_actions:
        print("\nSkipping corporate-action sync by request.", flush=True)
    else:
        _run_corporate_action_sync(shared_args, database_url=args.database_url)

    if args.dry_run:
        pass
    elif args.skip_adjustments:
        print("\nSkipping adjusted-price refresh by request.", flush=True)
    else:
        _run_adjusted_price_refresh(shared_args, database_url=args.database_url)

    if args.skip_short_interest:
        print("\nSkipping short-interest sync by request.", flush=True)
    else:
        short_args = ["--end-date", end_date.isoformat()]
        if args.start_date:
            short_args[0:0] = ["--start-date", start_date.isoformat()]
        else:
            short_args.extend(["--lookback-days", "60"])
        if args.dry_run:
            short_args.append("--dry-run")
        _run_script(
            "sync-short-interest",
            "backfill_short_interest_from_massive.py",
            short_args,
            database_url=args.database_url,
        )

    if args.dry_run:
        pass
    elif args.skip_features:
        print("\nSkipping daily_features refresh by request.", flush=True)
    else:
        feature_script = REPO_ROOT / "backend" / "utils" / "backfill_daily_features.py"
        _run_step(
            "refresh-daily-features",
            feature_script,
            shared_args,
            database_url=args.database_url,
        )

    final_coverage = _load_latest_coverage(args.database_url)
    print("\nCoverage after run:", flush=True)
    print(
        f"  latest_eod_date={final_coverage.latest_eod_date} "
        f"latest_feature_date={final_coverage.latest_feature_date}",
        flush=True,
    )
    if args.skip_quality_check:
        print("Skipping market-data quality check by request.", flush=True)
    else:
        _run_quality_check(
            args.database_url,
            end_date,
            strict=args.strict_quality_check,
        )


if __name__ == "__main__":
    main()
