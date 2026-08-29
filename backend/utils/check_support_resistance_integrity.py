from __future__ import annotations

"""Read-only integrity checks for support/resistance derived tables."""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKS = {
    "linked_non_completed_materializations": """
        SELECT count(*)
        FROM support_resistance_run_materializations link
        JOIN support_resistance_materializations materialization
          ON materialization.id = link.materialization_id
        WHERE materialization.status <> 'completed'
    """,
    "overlapping_zone_versions": """
        SELECT count(*) FROM (
          SELECT effective_from,
                 lag(effective_from) OVER (
                   PARTITION BY materialization_id, instrument_id, zone_key
                   ORDER BY effective_from, version
                 ) AS previous_start,
                 lag(effective_to) OVER (
                   PARTITION BY materialization_id, instrument_id, zone_key
                   ORDER BY effective_from, version
                 ) AS previous_end
          FROM support_resistance_zone_versions
        ) timeline
        WHERE previous_start IS NOT NULL
          AND (previous_end IS NULL OR effective_from <= previous_end)
    """,
    "run_events_without_link": """
        SELECT count(*)
        FROM support_resistance_run_events event
        LEFT JOIN support_resistance_run_materializations link
          ON link.run_id = event.run_id
         AND link.materialization_id = event.materialization_id
        WHERE link.id IS NULL
    """,
    "duplicate_run_links": """
        SELECT count(*) FROM (
          SELECT run_id FROM support_resistance_run_materializations
          GROUP BY run_id HAVING count(*) > 1
        ) duplicates
    """,
    "invalid_zone_bounds": """
        SELECT count(*) FROM support_resistance_zone_versions
        WHERE lower_price > center_price OR center_price > upper_price
           OR atr_width <= 0 OR pivot_count <= 0 OR touch_count < 0
    """,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def normalize_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def run_checks(connection: psycopg.Connection[Any]) -> dict[str, int]:
    results: dict[str, int] = {}
    with connection.cursor() as cursor:
        for name, query in CHECKS.items():
            cursor.execute(query)
            results[name] = int(cursor.fetchone()[0])
    return results


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or SQLALCHEMY_DATABASE_URL is required")
    with psycopg.connect(normalize_dsn(args.database_url), autocommit=False) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        results = run_checks(connection)
        connection.rollback()
    payload = {"ok": not any(results.values()), "checks": results}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else payload)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
