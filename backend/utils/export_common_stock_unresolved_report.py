from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.services.data_service import (  # noqa: E402
    EXCLUDED_TEST_SYMBOLS,
    find_massive_day_agg_files,
    infer_trade_date_from_path,
    iter_massive_day_agg_rows,
)


COMMON_STOCK_SYMBOL_MAP_SQL = """
SELECT
    sh.symbol,
    COUNT(*) AS match_count
FROM symbol_history sh
JOIN instruments instr
  ON instr.id = sh.instrument_id
WHERE sh.valid_from <= %(trade_date)s::date
  AND (sh.valid_to IS NULL OR sh.valid_to >= %(trade_date)s::date)
  AND instr.asset_type = 'CS'
GROUP BY sh.symbol;
"""

SYMBOL_REFERENCE_SQL = """
SELECT
    asset_type,
    exchange,
    is_common_stock
FROM symbol_reference
WHERE symbol = %(symbol)s
ORDER BY exchange, asset_type;
"""


@dataclass(frozen=True)
class UnresolvedRow:
    trade_date: str
    symbol: str
    row_count: int
    reason: str
    cs_match_count: int
    has_common_reference: bool
    has_non_common_reference: bool
    asset_types: str
    exchanges: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export unresolved common-stock gaps from Massive flat files."
    )
    parser.add_argument(
        "--root",
        default="data/massive/us_stocks_sip/day_aggs_v1",
        help="Directory containing Massive .csv.gz day aggregate files.",
    )
    parser.add_argument("--start-date", help="Inclusive start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", help="Inclusive end date in YYYY-MM-DD format.")
    parser.add_argument(
        "--output-dir",
        default="data/reports",
        help="Directory where CSV reports will be written.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _load_symbol_counts(file_path: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in iter_massive_day_agg_rows(file_path):
        counts[row[0]] += 1
    return dict(counts)


def _current_common_match_counts(conn: psycopg.Connection, trade_date: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(COMMON_STOCK_SYMBOL_MAP_SQL, {"trade_date": trade_date})
        return {symbol: int(match_count) for symbol, match_count in cur.fetchall()}


def _symbol_reference_snapshot(
    conn: psycopg.Connection, symbol: str
) -> tuple[bool, bool, str, str]:
    asset_types: list[str] = []
    exchanges: list[str] = []
    has_common_reference = False
    has_non_common_reference = False

    with conn.cursor() as cur:
        cur.execute(SYMBOL_REFERENCE_SQL, {"symbol": symbol})
        for asset_type, exchange, is_common_stock in cur.fetchall():
            asset_types.append(asset_type)
            exchanges.append(exchange)
            if is_common_stock:
                has_common_reference = True
            else:
                has_non_common_reference = True

    return (
        has_common_reference,
        has_non_common_reference,
        ",".join(sorted(set(asset_types))),
        ",".join(sorted(set(exchanges))),
    )


def _is_filtered_non_common(
    symbol: str,
    has_common_reference: bool,
    has_non_common_reference: bool,
) -> bool:
    if symbol in EXCLUDED_TEST_SYMBOLS:
        return True
    if has_common_reference:
        return False
    if has_non_common_reference:
        return True
    return symbol.endswith((".WS", ".W", ".U", ".R", ".RT", ".WT"))


def export_reports(
    conn: psycopg.Connection,
    file_paths: list[Path],
    output_dir: Path,
) -> tuple[Path, Path, list[UnresolvedRow]]:
    unresolved_rows: list[UnresolvedRow] = []
    summary_counter: Counter[str] = Counter()

    for file_path in file_paths:
        trade_date = infer_trade_date_from_path(file_path).isoformat()
        symbol_counts = _load_symbol_counts(file_path)
        common_match_counts = _current_common_match_counts(conn, trade_date)

        for symbol, row_count in sorted(symbol_counts.items()):
            match_count = common_match_counts.get(symbol, 0)
            if match_count == 1:
                continue

            has_common_reference, has_non_common_reference, asset_types, exchanges = (
                _symbol_reference_snapshot(conn, symbol)
            )
            if _is_filtered_non_common(symbol, has_common_reference, has_non_common_reference):
                continue

            reason = "ambiguous_common_symbol" if match_count > 1 else "missing_common_symbol"
            summary_counter[reason] += row_count
            unresolved_rows.append(
                UnresolvedRow(
                    trade_date=trade_date,
                    symbol=symbol,
                    row_count=row_count,
                    reason=reason,
                    cs_match_count=match_count,
                    has_common_reference=has_common_reference,
                    has_non_common_reference=has_non_common_reference,
                    asset_types=asset_types,
                    exchanges=exchanges,
                )
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    details_path = output_dir / "common_stock_unresolved_details.csv"
    summary_path = output_dir / "common_stock_unresolved_summary.csv"

    with details_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "trade_date",
                "symbol",
                "row_count",
                "reason",
                "cs_match_count",
                "has_common_reference",
                "has_non_common_reference",
                "asset_types",
                "exchanges",
            ]
        )
        for row in unresolved_rows:
            writer.writerow(
                [
                    row.trade_date,
                    row.symbol,
                    row.row_count,
                    row.reason,
                    row.cs_match_count,
                    row.has_common_reference,
                    row.has_non_common_reference,
                    row.asset_types,
                    row.exchanges,
                ]
            )

    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["reason", "rows", "symbols"])
        grouped_symbols: defaultdict[str, set[str]] = defaultdict(set)
        for row in unresolved_rows:
            grouped_symbols[row.reason].add(row.symbol)
        for reason in sorted(summary_counter):
            writer.writerow([reason, summary_counter[reason], len(grouped_symbols[reason])])

    return summary_path, details_path, unresolved_rows


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    file_paths = find_massive_day_agg_files(
        root_dir=args.root,
        start_date=args.start_date and date.fromisoformat(args.start_date),
        end_date=args.end_date and date.fromisoformat(args.end_date),
    )
    if not file_paths:
        raise SystemExit("No matching flat files found.")

    with psycopg.connect(_psycopg_dsn(database_url)) as conn:
        summary_path, details_path, unresolved_rows = export_reports(
            conn=conn,
            file_paths=file_paths,
            output_dir=Path(args.output_dir),
        )

    print(f"Wrote {len(unresolved_rows)} unresolved rows to {details_path}")
    print(f"Wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
