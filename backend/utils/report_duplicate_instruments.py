from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

DETAIL_HEADERS = [
    "ticker_canonical",
    "duplicate_count",
    "instrument_id",
    "name",
    "exchange",
    "asset_type",
    "market",
    "locale",
    "is_active",
    "share_class_figi",
    "composite_figi",
    "cik",
    "open_symbols",
]

SUMMARY_HEADERS = [
    "ticker_canonical",
    "duplicate_count",
    "active_count",
    "inactive_count",
]

DUPLICATE_INSTRUMENTS_SQL = """
WITH duplicate_tickers AS (
    SELECT ticker_canonical, COUNT(*) AS duplicate_count
    FROM instruments
    WHERE ticker_canonical IS NOT NULL
    GROUP BY ticker_canonical
    HAVING COUNT(*) > 1
), open_symbol_map AS (
    SELECT
        sh.instrument_id,
        STRING_AGG(
            DISTINCT (sh.exchange || ':' || sh.symbol),
            ',' ORDER BY (sh.exchange || ':' || sh.symbol)
        ) AS open_symbols
    FROM symbol_history sh
    WHERE sh.valid_to IS NULL
    GROUP BY sh.instrument_id
)
SELECT
    dup.ticker_canonical,
    dup.duplicate_count,
    instr.id AS instrument_id,
    instr.name,
    instr.exchange,
    instr.asset_type,
    instr.market,
    instr.locale,
    instr.is_active,
    instr.share_class_figi,
    instr.composite_figi,
    instr.cik,
    COALESCE(open_map.open_symbols, '') AS open_symbols
FROM duplicate_tickers dup
JOIN instruments instr
  ON instr.ticker_canonical = dup.ticker_canonical
LEFT JOIN open_symbol_map open_map
  ON open_map.instrument_id = instr.id
ORDER BY dup.ticker_canonical, instr.is_active DESC, instr.id;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report duplicate instruments that share the same ticker_canonical."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Override DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "data" / "reports"),
        help="Directory to write the CSV reports into.",
    )
    return parser.parse_args()


def fetch_duplicate_rows(conn: psycopg.Connection) -> list[dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(DUPLICATE_INSTRUMENTS_SQL)
        return list(cur.fetchall())


def write_reports(rows: list[dict[str, object]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "duplicate_instruments.csv"
    summary_path = output_dir / "duplicate_instrument_summary.csv"

    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETAIL_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "ticker_canonical": row["ticker_canonical"],
                    "duplicate_count": int(row["duplicate_count"]),
                    "instrument_id": int(row["instrument_id"]),
                    "name": row["name"] or "",
                    "exchange": row["exchange"] or "",
                    "asset_type": row["asset_type"] or "",
                    "market": row["market"] or "",
                    "locale": row["locale"] or "",
                    "is_active": bool(row["is_active"]),
                    "share_class_figi": row["share_class_figi"] or "",
                    "composite_figi": row["composite_figi"] or "",
                    "cik": row["cik"] or "",
                    "open_symbols": row["open_symbols"] or "",
                }
            )

    summary_by_ticker: dict[str, Counter[str]] = {}
    duplicate_count_by_ticker: dict[str, int] = {}
    for row in rows:
        ticker = str(row["ticker_canonical"])
        duplicate_count_by_ticker[ticker] = int(row["duplicate_count"])
        counter = summary_by_ticker.setdefault(ticker, Counter())
        if row["is_active"]:
            counter["active"] += 1
        else:
            counter["inactive"] += 1

    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_HEADERS)
        writer.writeheader()
        for ticker in sorted(summary_by_ticker):
            counter = summary_by_ticker[ticker]
            writer.writerow(
                {
                    "ticker_canonical": ticker,
                    "duplicate_count": duplicate_count_by_ticker[ticker],
                    "active_count": counter["active"],
                    "inactive_count": counter["inactive"],
                }
            )

    return detail_path, summary_path


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        args.database_url = os.getenv("DATABASE_URL")
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or --database-url")

    with psycopg.connect(args.database_url) as conn:
        rows = fetch_duplicate_rows(conn)

    detail_path, summary_path = write_reports(rows, Path(args.output_dir))
    print(f"Found {len(rows)} duplicate instrument rows.")
    print(f"Wrote detail report to {detail_path}")
    print(f"Wrote summary report to {summary_path}")


if __name__ == "__main__":
    main()
