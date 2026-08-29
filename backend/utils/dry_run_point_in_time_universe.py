#!/usr/bin/env python3
"""Read-only yearly coverage and exclusion report for point_in_time_liquid."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
QUERY = """
WITH observations AS (
  SELECT
    bars.instrument_id,
    bars.dt_ny,
    EXTRACT(YEAR FROM bars.dt_ny)::int AS year,
    instruments.asset_type,
    instruments.exchange,
    instruments.listed_at,
    instruments.delisted_at,
    bars.close_u,
    features.dollar_volume_20,
    count(*) OVER (
      PARTITION BY bars.instrument_id
      ORDER BY bars.dt_ny
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS history_sessions
  FROM eod_bars bars
  JOIN instruments ON instruments.id = bars.instrument_id
  LEFT JOIN daily_features features
    ON features.instrument_id = bars.instrument_id
   AND features.dt_ny = bars.dt_ny
  WHERE bars.dt_ny BETWEEN %(warmup_start)s AND %(end_date)s
), classified AS (
  SELECT *, CASE
    WHEN asset_type <> 'CS' THEN 'asset_type'
    WHEN exchange NOT IN ('XNAS', 'XNYS', 'XASE') THEN 'exchange'
    WHEN listed_at IS NOT NULL AND dt_ny < listed_at THEN 'before_listing'
    WHEN delisted_at IS NOT NULL AND dt_ny > delisted_at THEN 'after_delisting'
    WHEN close_u IS NULL OR close_u < 5 THEN 'price'
    WHEN dollar_volume_20 IS NULL OR dollar_volume_20 < 10000000 THEN 'liquidity'
    WHEN history_sessions < 200 THEN 'history'
    ELSE 'eligible'
  END AS reason
  FROM observations
  WHERE dt_ny >= %(study_start)s
)
SELECT
  year,
  reason,
  count(*) AS observations,
  count(DISTINCT instrument_id) AS instruments,
  count(DISTINCT dt_ny) AS sessions
FROM classified
GROUP BY year, reason
ORDER BY year, reason
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--warmup-start", default="2016-03-18")
    parser.add_argument("--study-start", default="2017-03-20")
    parser.add_argument("--end-date", default="2026-08-27")
    return parser.parse_args()


def normalize_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL or SQLALCHEMY_DATABASE_URL is required")
    parameters = {
        "warmup_start": args.warmup_start,
        "study_start": args.study_start,
        "end_date": args.end_date,
    }
    with psycopg.connect(normalize_dsn(args.database_url), autocommit=False) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute(QUERY, parameters)
            columns = [item.name for item in cursor.description]
            rows: list[dict[str, Any]] = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        connection.rollback()
    yearly: dict[str, dict[str, Any]] = {}
    for row in rows:
        year = str(row["year"])
        item = yearly.setdefault(year, {"eligibleInstruments": 0, "eligibleAverage": 0.0, "exclusionObservations": {}})
        reason = str(row["reason"])
        observations = int(row["observations"])
        if reason == "eligible":
            item["eligibleInstruments"] = int(row["instruments"])
            item["eligibleAverage"] = observations / max(1, int(row["sessions"]))
        else:
            item["exclusionObservations"][reason] = observations
    print(json.dumps({"policy": "point_in_time_liquid", "parameters": parameters, "years": yearly}, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
