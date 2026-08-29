from __future__ import annotations

"""Read-only dry-run report for unreferenced support/resistance caches.

This command intentionally has no apply mode. Deletion requires a separately
reviewed, explicitly authorized database operation with an exact ID list.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=100)
    return parser.parse_args()


def normalize_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or SQLALCHEMY_DATABASE_URL is required")
    if args.limit < 1 or args.limit > 10_000:
        raise SystemExit("--limit must be between 1 and 10000")
    query = """
        SELECT materialization.id, materialization.cache_key, materialization.status,
               materialization.coverage_start, materialization.coverage_end,
               materialization.created_at,
               count(zone.id) AS zone_version_count
        FROM support_resistance_materializations materialization
        LEFT JOIN support_resistance_run_materializations link
          ON link.materialization_id = materialization.id
        LEFT JOIN support_resistance_zone_versions zone
          ON zone.materialization_id = materialization.id
        WHERE link.id IS NULL
        GROUP BY materialization.id
        ORDER BY materialization.created_at, materialization.id
        LIMIT %s
    """
    with psycopg.connect(normalize_dsn(args.database_url), autocommit=False) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute(query, (args.limit,))
            columns = [item.name for item in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        connection.rollback()
    print(json.dumps({"dry_run": True, "candidate_count": len(rows), "candidates": rows}, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
