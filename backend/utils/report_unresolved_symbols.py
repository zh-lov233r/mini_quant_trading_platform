from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.services.data_service import (  # noqa: E402
    find_massive_day_agg_files,
    infer_trade_date_from_path,
    iter_massive_day_agg_rows,
)


DETAIL_HEADERS = [
    "trade_date",
    "symbol",
    "row_count",
    "classification",
    "cs_matches",
    "non_cs_matches",
    "asset_types",
    "exchanges",
]

SUMMARY_HEADERS = ["classification", "rows", "symbols"]

LOOKUP_SQL = """
SELECT
    COALESCE(SUM(CASE WHEN instr.asset_type = 'CS' THEN 1 ELSE 0 END), 0) AS cs_matches,
    COALESCE(SUM(CASE WHEN instr.asset_type <> 'CS' THEN 1 ELSE 0 END), 0) AS non_cs_matches,
    COALESCE(
        STRING_AGG(DISTINCT COALESCE(instr.asset_type, 'UNKNOWN'), ',' ORDER BY COALESCE(instr.asset_type, 'UNKNOWN')),
        ''
    ) AS asset_types,
    COALESCE(
        STRING_AGG(DISTINCT sh.exchange, ',' ORDER BY sh.exchange),
        ''
    ) AS exchanges
FROM symbol_history sh
JOIN instruments instr
  ON instr.id = sh.instrument_id
WHERE sh.symbol = %s
  AND sh.valid_from <= %s::date
  AND (sh.valid_to IS NULL OR sh.valid_to >= %s::date)
"""


@dataclass(frozen=True)
class UnresolvedRow:
    trade_date: date
    symbol: str
    row_count: int
    classification: str
    cs_matches: int
    non_cs_matches: int
    asset_types: str
    exchanges: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify unresolved Massive day-agg symbols for the common-stock pipeline."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Directory containing Massive .csv.gz day aggregate files.",
    )
    parser.add_argument("--start-date", help="Inclusive start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", help="Inclusive end date in YYYY-MM-DD format.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Only inspect the first N matching files.",
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


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _is_obvious_non_common_symbol(symbol: str) -> bool:
    return symbol.endswith((".WS", ".W", ".U", ".R", ".RT", ".WT"))


def _classify_symbol(cs_matches: int, non_cs_matches: int, symbol: str) -> str:
    if cs_matches == 1:
        return "resolved_common"
    if cs_matches > 1:
        return "ambiguous_common"
    if non_cs_matches > 0:
        return "non_common_only"
    if _is_obvious_non_common_symbol(symbol):
        return "obvious_non_common_pattern"
    return "suspected_common_gap"


def _symbol_counts_for_file(file_path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in iter_massive_day_agg_rows(file_path):
        counts[row[0]] += 1
    return counts


def build_report(conn: psycopg.Connection, files: list[Path]) -> list[UnresolvedRow]:
    rows: list[UnresolvedRow] = []
    with conn.cursor() as cur:
        for file_path in files:
            trade_date = infer_trade_date_from_path(file_path)
            symbol_counts = _symbol_counts_for_file(file_path)
            for symbol, row_count in sorted(symbol_counts.items()):
                cur.execute(LOOKUP_SQL, (symbol, trade_date, trade_date))
                cs_matches, non_cs_matches, asset_types, exchanges = cur.fetchone()
                classification = _classify_symbol(cs_matches, non_cs_matches, symbol)
                if classification == "resolved_common":
                    continue
                rows.append(
                    UnresolvedRow(
                        trade_date=trade_date,
                        symbol=symbol,
                        row_count=row_count,
                        classification=classification,
                        cs_matches=int(cs_matches),
                        non_cs_matches=int(non_cs_matches),
                        asset_types=asset_types,
                        exchanges=exchanges,
                    )
                )
    return rows


def write_reports(rows: list[UnresolvedRow], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "unresolved_symbol_details.csv"
    summary_path = output_dir / "unresolved_symbol_summary.csv"

    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETAIL_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "trade_date": row.trade_date.isoformat(),
                    "symbol": row.symbol,
                    "row_count": row.row_count,
                    "classification": row.classification,
                    "cs_matches": row.cs_matches,
                    "non_cs_matches": row.non_cs_matches,
                    "asset_types": row.asset_types,
                    "exchanges": row.exchanges,
                }
            )

    summary = Counter()
    symbol_sets: dict[str, set[str]] = {}
    for row in rows:
        summary[row.classification] += row.row_count
        symbol_sets.setdefault(row.classification, set()).add(row.symbol)

    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_HEADERS)
        writer.writeheader()
        for classification in sorted(summary):
            writer.writerow(
                {
                    "classification": classification,
                    "rows": summary[classification],
                    "symbols": len(symbol_sets[classification]),
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

    files = find_massive_day_agg_files(
        root_dir=args.root,
        start_date=_parse_date(args.start_date),
        end_date=_parse_date(args.end_date),
    )
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit("No matching flat files found.")

    with psycopg.connect(args.database_url) as conn:
        rows = build_report(conn, files)

    detail_path, summary_path = write_reports(rows, Path(args.output_dir))
    print(f"Wrote {len(rows)} unresolved rows to {detail_path}")
    print(f"Wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
