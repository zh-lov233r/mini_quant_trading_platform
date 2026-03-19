from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.services.data_service import (  # noqa: E402
    backfill_massive_day_aggs,
    find_massive_day_agg_files,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Massive stock day aggregate flat files into eod_bars."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Directory containing Massive .csv.gz day aggregate files.",
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
        help="Only process the first N matching files, useful for smoke tests.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Override DATABASE_URL from the environment.",
    )
    return parser.parse_args()


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> None:
    load_dotenv()
    args = parse_args()
    if not args.database_url:
        args.database_url = os.getenv("DATABASE_URL")

    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or --database-url")

    files = find_massive_day_agg_files(
        root_dir=args.root,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
    )
    if args.limit:
        files = files[: args.limit]

    if not files:
        raise SystemExit("No matching flat files found.")

    print(f"Found {len(files)} flat files under {args.root}")

    with psycopg.connect(args.database_url) as conn:
        stats = backfill_massive_day_aggs(conn, files)

    staged_total = 0
    upserted_total = 0
    filtered_total = 0
    unresolved_total = 0
    for item in stats:
        staged_total += item.staged_rows
        upserted_total += item.upserted_rows
        filtered_total += item.filtered_rows
        unresolved_total += item.unresolved_symbols
        print(
            f"[{item.trade_date}] staged={item.staged_rows} "
            f"upserted={item.upserted_rows} filtered={item.filtered_rows} "
            f"unresolved={item.unresolved_symbols} "
            f"path={item.file_path}"
        )

    print(
        "Done. "
        f"files={len(stats)} staged_rows={staged_total} "
        f"upserted_rows={upserted_total} filtered_rows={filtered_total} "
        f"unresolved_symbols={unresolved_total}"
    )


if __name__ == "__main__":
    main()
