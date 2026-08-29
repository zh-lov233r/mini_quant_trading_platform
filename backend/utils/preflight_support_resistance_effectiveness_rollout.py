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
    return {
        "target": {"database": database, "user": user, "server": server},
        "columns": columns,
        "missingAdditiveColumns": sorted(EXPECTED_COLUMNS - set(columns)),
        "activeExperiments": active,
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
