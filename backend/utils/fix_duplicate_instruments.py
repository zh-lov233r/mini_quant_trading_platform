from __future__ import annotations

import argparse
import csv
import os
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

DETAIL_HEADERS = [
    "duplicate_ticker",
    "duplicate_count",
    "instrument_id",
    "name",
    "is_active",
    "old_ticker_canonical",
    "proposed_ticker_canonical",
    "open_symbols",
    "open_history_count",
    "close_open_history",
    "close_history_date",
    "action",
    "reason",
    "applied",
]

SUMMARY_HEADERS = [
    "action",
    "rows",
    "tickers",
]

DUPLICATE_ROWS_SQL = """
WITH duplicate_tickers AS (
    SELECT ticker_canonical, COUNT(*) AS duplicate_count
    FROM instruments
    WHERE ticker_canonical IS NOT NULL
    GROUP BY ticker_canonical
    HAVING COUNT(*) > 1
), open_symbol_map AS (
    SELECT
        sh.instrument_id,
        ARRAY_AGG(DISTINCT sh.symbol ORDER BY sh.symbol)
            FILTER (WHERE sh.valid_to IS NULL) AS open_symbols,
        COUNT(*) FILTER (WHERE sh.valid_to IS NULL) AS open_history_count
    FROM symbol_history sh
    GROUP BY sh.instrument_id
)
SELECT
    dup.ticker_canonical AS duplicate_ticker,
    dup.duplicate_count,
    instr.id AS instrument_id,
    instr.name,
    instr.exchange,
    instr.asset_type,
    instr.market,
    instr.locale,
    instr.is_active,
    instr.ticker_canonical,
    instr.share_class_figi,
    instr.composite_figi,
    instr.cik,
    instr.listed_at,
    instr.delisted_at,
    COALESCE(open_map.open_symbols, ARRAY[]::TEXT[]) AS open_symbols,
    COALESCE(open_map.open_history_count, 0) AS open_history_count
FROM duplicate_tickers dup
JOIN instruments instr
  ON instr.ticker_canonical = dup.ticker_canonical
LEFT JOIN open_symbol_map open_map
  ON open_map.instrument_id = instr.id
ORDER BY dup.ticker_canonical, instr.is_active DESC, instr.id;
"""

TICKER_OWNER_SQL = """
SELECT ticker_canonical, ARRAY_AGG(id ORDER BY id) AS instrument_ids
FROM instruments
WHERE ticker_canonical IS NOT NULL
GROUP BY ticker_canonical;
"""

UPDATE_INSTRUMENT_TICKER_SQL = """
UPDATE instruments
SET ticker_canonical = %(ticker_canonical)s
WHERE id = %(instrument_id)s;
"""

CLOSE_OPEN_SYMBOL_HISTORY_SQL = """
UPDATE symbol_history
SET valid_to = GREATEST(valid_from, %(close_date)s::date)
WHERE instrument_id = %(instrument_id)s
  AND valid_to IS NULL
  AND valid_from <= %(close_date)s::date;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Conservatively repair duplicate ticker_canonical values. "
            "Only duplicate groups with exactly one active instrument are auto-fixed."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Override DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "data" / "reports"),
        help="Directory to write the repair reports into.",
    )
    parser.add_argument(
        "--as-of-date",
        default=date.today().isoformat(),
        help="Date used to close stale open symbol_history rows for inactive instruments.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the proposed repairs. Without this flag the script runs in dry-run mode.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def fetch_duplicate_rows(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(DUPLICATE_ROWS_SQL)
        return list(cur.fetchall())


def fetch_ticker_owner_map(conn: psycopg.Connection) -> dict[str, list[int]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(TICKER_OWNER_SQL)
        rows = cur.fetchall()
    return {
        str(row["ticker_canonical"]).strip().upper(): [int(item) for item in (row["instrument_ids"] or [])]
        for row in rows
        if row["ticker_canonical"]
    }


def _normalize_open_symbols(raw_symbols: list[str] | None) -> list[str]:
    normalized = {
        str(symbol).strip().upper()
        for symbol in (raw_symbols or [])
        if str(symbol).strip()
    }
    return sorted(normalized)


def _owner_conflict_exists(
    owner_map: dict[str, list[int]],
    *,
    ticker: str,
    instrument_id: int,
) -> bool:
    owners = owner_map.get(ticker, [])
    return any(owner_id != instrument_id for owner_id in owners)


def _remove_ticker_owner(owner_map: dict[str, list[int]], ticker: str | None, instrument_id: int) -> None:
    if not ticker:
        return
    owners = [owner for owner in owner_map.get(ticker, []) if owner != instrument_id]
    if owners:
        owner_map[ticker] = owners
    else:
        owner_map.pop(ticker, None)


def _add_ticker_owner(owner_map: dict[str, list[int]], ticker: str | None, instrument_id: int) -> None:
    if not ticker:
        return
    owners = owner_map.setdefault(ticker, [])
    if instrument_id not in owners:
        owners.append(instrument_id)
        owners.sort()


def build_fix_plan(
    duplicate_rows: list[dict[str, Any]],
    *,
    owner_map: dict[str, list[int]],
    as_of_date: date,
) -> list[dict[str, Any]]:
    plan_rows: list[dict[str, Any]] = []
    rows_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in duplicate_rows:
        rows_by_ticker[str(row["duplicate_ticker"]).strip().upper()].append(row)

    for duplicate_ticker in sorted(rows_by_ticker):
        rows = rows_by_ticker[duplicate_ticker]
        active_rows = [row for row in rows if row["is_active"]]

        if len(active_rows) != 1:
            reason = (
                "multiple active instruments share this ticker"
                if len(active_rows) > 1
                else "no active instrument remains for this duplicate ticker"
            )
            for row in rows:
                plan_rows.append(
                    {
                        "duplicate_ticker": duplicate_ticker,
                        "duplicate_count": int(row["duplicate_count"]),
                        "instrument_id": int(row["instrument_id"]),
                        "name": row["name"] or "",
                        "is_active": bool(row["is_active"]),
                        "old_ticker_canonical": row["ticker_canonical"],
                        "proposed_ticker_canonical": row["ticker_canonical"],
                        "open_symbols": ",".join(_normalize_open_symbols(row["open_symbols"])),
                        "open_history_count": int(row["open_history_count"] or 0),
                        "close_open_history": False,
                        "close_history_date": "",
                        "action": "manual_review",
                        "reason": reason,
                        "applied": False,
                    }
                )
            continue

        keeper = active_rows[0]
        plan_rows.append(
            {
                "duplicate_ticker": duplicate_ticker,
                "duplicate_count": int(keeper["duplicate_count"]),
                "instrument_id": int(keeper["instrument_id"]),
                "name": keeper["name"] or "",
                "is_active": True,
                "old_ticker_canonical": keeper["ticker_canonical"],
                "proposed_ticker_canonical": keeper["ticker_canonical"],
                "open_symbols": ",".join(_normalize_open_symbols(keeper["open_symbols"])),
                "open_history_count": int(keeper["open_history_count"] or 0),
                "close_open_history": False,
                "close_history_date": "",
                "action": "keep_active",
                "reason": "active instrument remains the canonical owner of this ticker",
                "applied": False,
            }
        )

        for row in rows:
            if row["is_active"]:
                continue

            instrument_id = int(row["instrument_id"])
            old_ticker = str(row["ticker_canonical"]).strip().upper() if row["ticker_canonical"] else None
            open_symbols = _normalize_open_symbols(row["open_symbols"])
            open_history_count = int(row["open_history_count"] or 0)
            close_open_history = open_history_count > 0
            close_history_date = row["delisted_at"] or as_of_date

            proposed_ticker: str | None = None
            action = "nullify_ticker"
            reason = "inactive duplicate ticker was cleared to avoid colliding with the active instrument"

            if len(open_symbols) == 1 and open_symbols[0] != duplicate_ticker:
                candidate_ticker = open_symbols[0]
                if not _owner_conflict_exists(
                    owner_map,
                    ticker=candidate_ticker,
                    instrument_id=instrument_id,
                ):
                    proposed_ticker = candidate_ticker
                    action = "retarget_ticker"
                    reason = "inactive duplicate ticker was retargeted to its sole open symbol_history alias"
                else:
                    reason = (
                        "inactive duplicate ticker could not be retargeted because the open symbol alias "
                        "is already owned by another instrument; cleared instead"
                    )
            elif not open_symbols:
                reason = (
                    "inactive duplicate ticker had no open symbol_history alias; "
                    "ticker_canonical was cleared instead"
                )
            else:
                reason = (
                    "inactive duplicate ticker had no unique open symbol_history alias; "
                    "ticker_canonical was cleared instead"
                )

            if close_open_history:
                action = f"{action}+close_open_history"

            _remove_ticker_owner(owner_map, old_ticker, instrument_id)
            _add_ticker_owner(owner_map, proposed_ticker, instrument_id)

            plan_rows.append(
                {
                    "duplicate_ticker": duplicate_ticker,
                    "duplicate_count": int(row["duplicate_count"]),
                    "instrument_id": instrument_id,
                    "name": row["name"] or "",
                    "is_active": False,
                    "old_ticker_canonical": old_ticker,
                    "proposed_ticker_canonical": proposed_ticker or "",
                    "open_symbols": ",".join(open_symbols),
                    "open_history_count": open_history_count,
                    "close_open_history": close_open_history,
                    "close_history_date": close_history_date.isoformat() if close_open_history else "",
                    "action": action,
                    "reason": reason,
                    "applied": False,
                }
            )

    return plan_rows


def apply_fix_plan(
    conn: psycopg.Connection,
    plan_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    applied_rows: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        for row in plan_rows:
            action = str(row["action"])
            if action in {"manual_review", "keep_active"}:
                applied_rows.append({**row, "applied": False})
                continue

            instrument_id = int(row["instrument_id"])
            proposed_ticker = row["proposed_ticker_canonical"] or None
            cur.execute(
                UPDATE_INSTRUMENT_TICKER_SQL,
                {
                    "instrument_id": instrument_id,
                    "ticker_canonical": proposed_ticker,
                },
            )

            if row["close_open_history"]:
                cur.execute(
                    CLOSE_OPEN_SYMBOL_HISTORY_SQL,
                    {
                        "instrument_id": instrument_id,
                        "close_date": row["close_history_date"],
                    },
                )

            applied_rows.append({**row, "applied": True})

    conn.commit()
    return applied_rows


def write_reports(
    rows: list[dict[str, Any]],
    *,
    output_dir: Path,
    apply_mode: bool,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = "applied" if apply_mode else "dry_run"
    detail_path = output_dir / f"duplicate_instrument_fix_plan_{mode}.csv"
    summary_path = output_dir / f"duplicate_instrument_fix_summary_{mode}.csv"

    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETAIL_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "duplicate_ticker": row["duplicate_ticker"],
                    "duplicate_count": int(row["duplicate_count"]),
                    "instrument_id": int(row["instrument_id"]),
                    "name": row["name"],
                    "is_active": bool(row["is_active"]),
                    "old_ticker_canonical": row["old_ticker_canonical"] or "",
                    "proposed_ticker_canonical": row["proposed_ticker_canonical"] or "",
                    "open_symbols": row["open_symbols"],
                    "open_history_count": int(row["open_history_count"]),
                    "close_open_history": bool(row["close_open_history"]),
                    "close_history_date": row["close_history_date"],
                    "action": row["action"],
                    "reason": row["reason"],
                    "applied": bool(row["applied"]),
                }
            )

    action_counter: Counter[str] = Counter()
    ticker_sets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        action_counter[str(row["action"])] += 1
        ticker_sets[str(row["action"])].add(str(row["duplicate_ticker"]))

    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_HEADERS)
        writer.writeheader()
        for action in sorted(action_counter):
            writer.writerow(
                {
                    "action": action,
                    "rows": action_counter[action],
                    "tickers": len(ticker_sets[action]),
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

    as_of_date = _parse_date(args.as_of_date)

    with psycopg.connect(args.database_url) as conn:
        duplicate_rows = fetch_duplicate_rows(conn)
        owner_map = fetch_ticker_owner_map(conn)
        plan_rows = build_fix_plan(
            duplicate_rows,
            owner_map=owner_map,
            as_of_date=as_of_date,
        )
        result_rows = apply_fix_plan(conn, plan_rows) if args.apply else plan_rows

    detail_path, summary_path = write_reports(
        result_rows,
        output_dir=Path(args.output_dir),
        apply_mode=bool(args.apply),
    )
    print(f"Prepared {len(result_rows)} repair-plan rows.")
    print(f"Wrote detail report to {detail_path}")
    print(f"Wrote summary report to {summary_path}")
    if not args.apply:
        print("Dry-run only. Re-run with --apply to persist the proposed repairs.")


if __name__ == "__main__":
    main()
