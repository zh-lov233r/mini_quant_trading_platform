#!/usr/bin/env python3
"""Read-only schema/ORM and active-work preflight for effectiveness-study DDL."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_COLUMNS = {"parent_experiment_id", "study_kind"}


def normalize_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def inspect(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, inet_server_addr()::text")
        database, user, server = cursor.fetchone()
        cursor.execute(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'research_experiments'
            ORDER BY ordinal_position
            """
        )
        columns = {row[0]: {"nullable": row[1], "type": row[2]} for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT status, count(*)
            FROM research_experiments
            WHERE status IN ('queued', 'running', 'waiting_agent', 'cancel_requested')
            GROUP BY status ORDER BY status
            """
        )
        active = {row[0]: int(row[1]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT algorithm_version, count(*)
            FROM support_resistance_materializations
            GROUP BY algorithm_version
            ORDER BY algorithm_version
            """
        )
        materializations = {row[0]: int(row[1]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT id::text, strategy_key, name, version, status,
                   params #>> '{metadata,algorithm_version}' AS algorithm_version
            FROM strategies
            WHERE name = 'SR_test1'
            ORDER BY version
            """
        )
        strategies = [
            {
                "id": row[0],
                "strategyKey": row[1],
                "name": row[2],
                "version": int(row[3]),
                "status": row[4],
                "algorithmVersion": row[5],
            }
            for row in cursor.fetchall()
        ]
        cursor.execute("SELECT to_regclass('public.support_resistance_regime_versions')")
        regime_table_exists = cursor.fetchone()[0] is not None
        cursor.execute("SELECT to_regclass('public.backtest_jobs')")
        if cursor.fetchone()[0] is not None:
            cursor.execute(
                "SELECT status, count(*) FROM backtest_jobs "
                "WHERE status IN ('queued', 'running') GROUP BY status ORDER BY status"
            )
            active_backtests = {row[0]: int(row[1]) for row in cursor.fetchall()}
        else:
            active_backtests = {}
    return {
        "target": {"database": database, "user": user, "server": server},
        "columns": columns,
        "missingAdditiveColumns": sorted(EXPECTED_COLUMNS - set(columns)),
        "activeExperiments": active,
        "activeBacktests": active_backtests,
        "supportResistanceMaterializations": materializations,
        "targetStrategies": strategies,
        "regimeTableExists": regime_table_exists,
        "migrationImpact": {
            "createsTables": [] if regime_table_exists else ["support_resistance_regime_versions"],
            "createsIndexes": [] if regime_table_exists else ["idx_support_resistance_regime_versions_timeline"],
            "rewritesExistingRows": False,
            "deletesRows": False,
        },
        "backupRequiredBeforeApply": True,
        "ddlApplied": EXPECTED_COLUMNS.issubset(columns),
    }


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    database_url = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL or SQLALCHEMY_DATABASE_URL is required")
    with psycopg.connect(normalize_dsn(database_url), autocommit=False) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        payload = inspect(connection)
        connection.rollback()
    print(json.dumps(payload, default=str, sort_keys=True))
    return 1 if payload["activeExperiments"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
