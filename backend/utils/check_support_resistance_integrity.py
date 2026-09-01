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
           OR end_lower_price > end_center_price OR end_center_price > end_upper_price
           OR atr_width <= 0 OR pivot_count <= 0 OR touch_count < 0
    """,
    "invalid_current_v3_zone_domain": """
        SELECT count(*)
        FROM support_resistance_zone_versions zone
        JOIN support_resistance_materializations materialization
          ON materialization.id = zone.materialization_id
        WHERE materialization.algorithm_version = 'pivot-slope-regime-v3'
          AND materialization.status = 'completed'
          AND (materialization.detector_params->>'implementation_revision')::integer >= 10
          AND (zone.lower_price <= 0 OR zone.end_lower_price <= 0)
    """,
    "duplicate_regime_starts": """
        SELECT count(*) FROM (
          SELECT materialization_id, symbol, effective_from
          FROM support_resistance_regime_versions
          GROUP BY materialization_id, symbol, effective_from
          HAVING count(*) > 1
        ) duplicates
    """,
    "v3_symbols_missing_regime_timeline": """
        SELECT count(*) FROM (
          SELECT materialization.id
        FROM support_resistance_materializations materialization
        LEFT JOIN support_resistance_regime_versions regime
          ON regime.materialization_id = materialization.id
        WHERE materialization.algorithm_version = 'pivot-slope-regime-v3'
          AND materialization.status = 'completed'
        GROUP BY materialization.id, materialization.statistics
        HAVING count(regime.id) = 0
            OR (
              materialization.statistics ? 'regime_timeline_count'
              AND count(DISTINCT (regime.symbol, regime.instrument_id))
                  <> (materialization.statistics->>'regime_timeline_count')::bigint
            )
        ) invalid_materializations
    """,
    "invalid_regime_values": """
        SELECT count(*) FROM support_resistance_regime_versions
        WHERE regime NOT IN ('uptrend', 'downtrend', 'range', 'transition')
           OR version <= 0
    """,
    "adjacent_equal_regimes": """
        SELECT count(*) FROM (
          SELECT regime,
                 lag(regime) OVER (
                   PARTITION BY materialization_id, symbol
                   ORDER BY effective_from, version
                 ) AS previous_regime
          FROM support_resistance_regime_versions
        ) timeline
        WHERE previous_regime = regime
    """,
    "regime_transitions_without_market_session": """
        SELECT count(*)
        FROM support_resistance_regime_versions regime
        LEFT JOIN eod_bars bar
          ON bar.instrument_id = regime.instrument_id
         AND bar.dt_ny = regime.effective_from
        LEFT JOIN daily_features features
          ON features.instrument_id = regime.instrument_id
         AND features.dt_ny = regime.effective_from
        WHERE regime.instrument_id IS NOT NULL
          AND (bar.instrument_id IS NULL OR features.instrument_id IS NULL)
    """,
    "regime_timelines_missing_first_session": """
        WITH first_regimes AS (
          SELECT regime.materialization_id,
                 regime.symbol,
                 regime.instrument_id,
                 min(regime.effective_from) AS first_regime
          FROM support_resistance_regime_versions regime
          GROUP BY regime.materialization_id, regime.symbol, regime.instrument_id
        ), first_sessions AS (
          SELECT first_regime.materialization_id,
                 first_regime.symbol,
                 first_regime.instrument_id,
                 min(bar.dt_ny) AS first_session,
                 first_regime.first_regime
          FROM first_regimes first_regime
          JOIN support_resistance_materializations materialization
            ON materialization.id = first_regime.materialization_id
          JOIN eod_bars bar
            ON bar.instrument_id = first_regime.instrument_id
           AND bar.dt_ny BETWEEN materialization.coverage_start AND materialization.coverage_end
           AND bar.dt_ny >= first_regime.first_regime
          JOIN daily_features features
            ON features.instrument_id = bar.instrument_id
           AND features.dt_ny = bar.dt_ny
          GROUP BY first_regime.materialization_id, first_regime.symbol,
                   first_regime.instrument_id, first_regime.first_regime
        )
        SELECT count(*) FROM first_sessions WHERE first_session <> first_regime
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
        cursor.execute("SELECT to_regclass('public.support_resistance_regime_versions')")
        regime_table_exists = cursor.fetchone()[0] is not None
        results["missing_regime_versions_table"] = 0 if regime_table_exists else 1
        for name, query in CHECKS.items():
            if "support_resistance_regime_versions" in query and not regime_table_exists:
                results[name] = 0
                continue
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
