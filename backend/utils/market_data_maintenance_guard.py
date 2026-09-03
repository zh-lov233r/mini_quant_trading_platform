from __future__ import annotations

import os

import psycopg


def normalize_dsn(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return normalized.replace("postgresql+psycopg2://", "postgresql://", 1)


def require_market_data_maintenance_owner(database_url: str) -> str:
    owner_token = os.getenv("MARKET_DATA_MAINTENANCE_OWNER")
    if not owner_token:
        raise SystemExit(
            "Market-data writes must run through backend/utils/run_daily_market_backfill.py"
        )
    with psycopg.connect(normalize_dsn(database_url)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, owner_token::text
                FROM market_data_maintenance_state
                WHERE id = 1
                """
            )
            state = cur.fetchone()
    if state != ("updating", owner_token):
        raise SystemExit("Market-data maintenance owner token is not active")
    return owner_token
