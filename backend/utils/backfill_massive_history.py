from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.services.data_service import backfill_massive_day_aggs
from backend.utils.download_flatfiles import (
    DEFAULT_BUCKET,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PREFIX,
    DownloadResult,
    download_one,
    massive_s3_client,
)


@dataclass(frozen=True)
class BatchSummary:
    start_date: date
    end_date: date
    requested_dates: int
    downloaded_files: int
    reused_files: int
    failed_dates: int
    staged_rows: int
    upserted_rows: int
    filtered_rows: int
    unresolved_rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Massive flat files in yearly batches and localize them into eod_bars."
    )
    parser.add_argument(
        "--start-date",
        default="2010-01-01",
        help="Inclusive start date in YYYY-MM-DD format. Defaults to 2010-01-01.",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Inclusive end date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--batch-years",
        type=int,
        default=1,
        help="How many calendar years to process per batch. Defaults to 1.",
    )
    parser.add_argument(
        "--dataset-prefix",
        default=DEFAULT_PREFIX,
        help=f"S3 dataset prefix under the flatfiles bucket. Defaults to {DEFAULT_PREFIX}.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"Local root directory for downloads. Defaults to {DEFAULT_OUTPUT_ROOT}.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Reuse already-downloaded files instead of fetching them again.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _iter_weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _year_batches(start: date, end: date, batch_years: int) -> list[tuple[date, date]]:
    batches: list[tuple[date, date]] = []
    current_start = start
    while current_start <= end:
        batch_end_year = current_start.year + batch_years - 1
        current_end = date(batch_end_year, 12, 31)
        if current_end > end:
            current_end = end
        batches.append((current_start, current_end))
        current_start = current_end + timedelta(days=1)
    return batches


def _download_batch(
    s3_client,
    bucket: str,
    dataset_prefix: str,
    output_root: Path,
    start: date,
    end: date,
    skip_existing: bool,
) -> tuple[list[Path], int, int, int]:
    from botocore.exceptions import ClientError

    files: list[Path] = []
    downloaded = 0
    reused = 0
    failed = 0
    attempted = 0

    for trade_date in _iter_weekdays(start, end):
        attempted += 1
        try:
            result: DownloadResult = download_one(
                s3_client,
                bucket=bucket,
                dataset_prefix=dataset_prefix,
                output_root=output_root,
                trade_date=trade_date,
                skip_existing=skip_existing,
            )
            files.append(result.output_path)
            if result.downloaded:
                downloaded += 1
            else:
                reused += 1
        except ClientError:
            failed += 1

        if attempted % 25 == 0:
            print(
                f"  download progress: attempted={attempted} "
                f"downloaded={downloaded} reused={reused} failed={failed}",
                flush=True,
            )

    return files, downloaded, reused, failed


def main() -> None:
    load_dotenv()
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.batch_years < 1:
        raise SystemExit("--batch-years must be >= 1")

    database_url = _psycopg_dsn(args.database_url)
    output_root = Path(args.output_root)
    bucket = os.getenv("MASSIVE_S3_BUCKET", DEFAULT_BUCKET)
    s3_client = massive_s3_client()

    grand_staged = 0
    grand_upserted = 0
    grand_filtered = 0
    grand_unresolved = 0
    grand_downloaded = 0
    grand_reused = 0
    grand_failed = 0
    grand_requested = 0

    batches = _year_batches(start, end, args.batch_years)
    print(
        f"Starting Massive history backfill for {start} to {end} "
        f"in {len(batches)} batch(es).",
        flush=True,
    )

    with psycopg.connect(database_url) as conn:
        for batch_start, batch_end in batches:
            requested_dates = len(_iter_weekdays(batch_start, batch_end))
            grand_requested += requested_dates
            print(
                f"\n[{batch_start} -> {batch_end}] downloading {requested_dates} weekday files",
                flush=True,
            )

            files, downloaded, reused, failed = _download_batch(
                s3_client=s3_client,
                bucket=bucket,
                dataset_prefix=args.dataset_prefix,
                output_root=output_root,
                start=batch_start,
                end=batch_end,
                skip_existing=args.skip_existing,
            )
            grand_downloaded += downloaded
            grand_reused += reused
            grand_failed += failed

            if not files:
                print(
                    f"[{batch_start} -> {batch_end}] no files available "
                    f"(failed={failed})",
                    flush=True,
                )
                continue

            stats = backfill_massive_day_aggs(conn, files)
            staged_rows = sum(item.staged_rows for item in stats)
            upserted_rows = sum(item.upserted_rows for item in stats)
            filtered_rows = sum(item.filtered_rows for item in stats)
            unresolved_rows = sum(item.unresolved_symbols for item in stats)

            grand_staged += staged_rows
            grand_upserted += upserted_rows
            grand_filtered += filtered_rows
            grand_unresolved += unresolved_rows

            summary = BatchSummary(
                start_date=batch_start,
                end_date=batch_end,
                requested_dates=requested_dates,
                downloaded_files=downloaded,
                reused_files=reused,
                failed_dates=failed,
                staged_rows=staged_rows,
                upserted_rows=upserted_rows,
                filtered_rows=filtered_rows,
                unresolved_rows=unresolved_rows,
            )
            print(
                f"[{summary.start_date} -> {summary.end_date}] "
                f"downloaded={summary.downloaded_files} reused={summary.reused_files} "
                f"failed={summary.failed_dates} staged={summary.staged_rows} "
                f"upserted={summary.upserted_rows} filtered={summary.filtered_rows} "
                f"unresolved={summary.unresolved_rows}",
                flush=True,
            )

    print("\nBackfill complete.", flush=True)
    print(
        f"requested_dates={grand_requested} downloaded_files={grand_downloaded} "
        f"reused_files={grand_reused} failed_dates={grand_failed} "
        f"staged_rows={grand_staged} upserted_rows={grand_upserted} "
        f"filtered_rows={grand_filtered} unresolved_rows={grand_unresolved}",
        flush=True,
    )


if __name__ == "__main__":
    main()
