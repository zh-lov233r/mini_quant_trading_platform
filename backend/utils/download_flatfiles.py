



from __future__ import annotations

import argparse
import gzip
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


DEFAULT_ENDPOINT = "https://files.massive.com"
DEFAULT_BUCKET = "flatfiles"
DEFAULT_PREFIX = "us_stocks_sip/day_aggs_v1"
DEFAULT_OUTPUT_ROOT = Path("data/massive")


@dataclass(frozen=True)
class DownloadResult:
    trade_date: date
    object_key: str
    output_path: Path
    downloaded: bool
    size_bytes: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a small sample of Massive flat files for local testing."
    )
    parser.add_argument(
        "--dates",
        help="Comma-separated dates in YYYY-MM-DD format, for example 2025-01-02,2025-01-03.",
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
        "--limit",
        type=int,
        default=2,
        help="Maximum number of trade dates to download when using a date range. Defaults to 2.",
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
        "--preview-lines",
        type=int,
        default=3,
        help="Print the first N lines from each downloaded gzip CSV. Defaults to 3.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not re-download files that already exist locally.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any requested date cannot be downloaded.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def resolve_trade_dates(args: argparse.Namespace) -> list[date]:
    if args.dates:
        return [_parse_date(part.strip()) for part in args.dates.split(",") if part.strip()]

    if not args.start_date or not args.end_date:
        raise SystemExit("Provide either --dates or both --start-date and --end-date")

    start = _parse_date(args.start_date)
    end = _parse_date(args.end_date)
    if end < start:
        raise SystemExit("--end-date must be on or after --start-date")

    dates: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)

    if args.limit:
        dates = dates[: args.limit]
    return dates


def build_object_key(dataset_prefix: str, trade_date: date) -> str:
    return f"{dataset_prefix}/{trade_date:%Y}/{trade_date:%m}/{trade_date.isoformat()}.csv.gz"


def build_output_path(output_root: Path, dataset_prefix: str, trade_date: date) -> Path:
    return output_root / dataset_prefix / f"{trade_date:%Y}" / f"{trade_date:%m}" / f"{trade_date.isoformat()}.csv.gz"


def massive_s3_client():
    try:
        import boto3
        from botocore.client import Config
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency `boto3`. Install it first, for example: "
            "`pip install boto3 python-dotenv`"
        ) from exc

    load_dotenv()
    access_key = os.getenv("MASSIVE_S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("MASSIVE_S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    endpoint = os.getenv("MASSIVE_S3_ENDPOINT", DEFAULT_ENDPOINT)

    if not access_key or not secret_key:
        raise SystemExit(
            "Missing Massive S3 credentials. Set MASSIVE_S3_ACCESS_KEY and MASSIVE_S3_SECRET_KEY."
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )


def download_one(
    s3_client,
    *,
    bucket: str,
    dataset_prefix: str,
    output_root: Path,
    trade_date: date,
    skip_existing: bool,
) -> DownloadResult:
    object_key = build_object_key(dataset_prefix, trade_date)
    output_path = build_output_path(output_root, dataset_prefix, trade_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if skip_existing and output_path.exists():
        return DownloadResult(
            trade_date=trade_date,
            object_key=object_key,
            output_path=output_path,
            downloaded=False,
            size_bytes=output_path.stat().st_size,
        )

    s3_client.download_file(bucket, object_key, str(output_path))
    return DownloadResult(
        trade_date=trade_date,
        object_key=object_key,
        output_path=output_path,
        downloaded=True,
        size_bytes=output_path.stat().st_size,
    )


def preview_gzip_csv(file_path: Path, preview_lines: int) -> list[str]:
    lines: list[str] = []
    with gzip.open(file_path, mode="rt", encoding="utf-8") as handle:
        for _ in range(preview_lines):
            line = handle.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
    return lines


def main() -> None:
    args = parse_args()

    try:
        from botocore.exceptions import ClientError
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency `boto3`. Install it first, for example: "
            "`pip install boto3 python-dotenv`"
        ) from exc

    trade_dates = resolve_trade_dates(args)
    if not trade_dates:
        raise SystemExit("No eligible trade dates selected.")

    output_root = Path(args.output_root)
    bucket = os.getenv("MASSIVE_S3_BUCKET", DEFAULT_BUCKET)
    s3_client = massive_s3_client()

    print(
        f"Preparing to download {len(trade_dates)} file(s) "
        f"from s3://{bucket}/{args.dataset_prefix}"
    )

    failed_dates: list[date] = []
    results: list[DownloadResult] = []
    for trade_date in trade_dates:
        try:
            result = download_one(
                s3_client,
                bucket=bucket,
                dataset_prefix=args.dataset_prefix,
                output_root=output_root,
                trade_date=trade_date,
                skip_existing=args.skip_existing,
            )
            results.append(result)
            action = "downloaded" if result.downloaded else "reused"
            print(
                f"[{trade_date}] {action} "
                f"{result.output_path} "
                f"({result.size_bytes or 0} bytes)"
            )
            preview = preview_gzip_csv(result.output_path, args.preview_lines)
            for line in preview:
                print(f"    {line}")
        except ClientError as exc:
            failed_dates.append(trade_date)
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            print(f"[{trade_date}] failed to download ({error_code})")

    if failed_dates and args.strict:
        raise SystemExit(f"Failed dates: {', '.join(d.isoformat() for d in failed_dates)}")

    if not results:
        raise SystemExit("No files were downloaded.")

    print("\nDownload complete.")
    print(
        "You can now test the local import with:\n"
        f"  .venv/bin/python backend/utils/backfill_eod_from_flatfiles.py "
        f"--root {output_root / args.dataset_prefix} --limit {len(results)}"
    )


if __name__ == "__main__":
    main()
