from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


_DOWNSAMPLED_SNAPSHOT_CTE = """
WITH ordered AS (
    SELECT
        id,
        ts,
        equity,
        ROW_NUMBER() OVER (ORDER BY ts ASC) AS row_num,
        COUNT(*) OVER () AS total_count
    FROM portfolio_snapshots
    WHERE run_id = :run_id
),
bucketed AS (
    SELECT
        id,
        ts,
        equity,
        row_num,
        total_count,
        CASE
            WHEN total_count <= :max_points OR row_num IN (1, total_count) THEN NULL
            ELSE FLOOR(
                (row_num - 2) / GREATEST(
                    1,
                    CEIL(
                        (total_count - 2)::numeric
                        / GREATEST(1, FLOOR((:max_points - 2)::numeric / 2))
                    )
                )
            )::integer
        END AS bucket_num
    FROM ordered
),
ranked AS (
    SELECT
        id,
        ts,
        ROW_NUMBER() OVER (
            PARTITION BY bucket_num
            ORDER BY equity ASC, ts ASC
        ) AS low_rank,
        ROW_NUMBER() OVER (
            PARTITION BY bucket_num
            ORDER BY equity DESC, ts DESC
        ) AS high_rank
    FROM bucketed
    WHERE total_count > :max_points
      AND row_num NOT IN (1, total_count)
),
candidate_ids AS (
    SELECT id, ts
    FROM ranked
    WHERE low_rank = 1 OR high_rank = 1
),
limited_candidates AS (
    SELECT id
    FROM candidate_ids
    ORDER BY ts ASC
    LIMIT GREATEST(:max_points - 2, 0)
),
selected_ids AS (
    SELECT id
    FROM ordered
    WHERE total_count <= :max_points

    UNION

    SELECT id
    FROM ordered
    WHERE total_count > :max_points
      AND row_num IN (1, total_count)

    UNION

    SELECT id
    FROM limited_candidates
)
"""


def build_downsampled_snapshot_ids_query():
    """Return the PostgreSQL query used by both compact and full equity reads."""

    return text(
        _DOWNSAMPLED_SNAPSHOT_CTE
        + """
SELECT ps.id
FROM selected_ids selected
JOIN portfolio_snapshots ps ON ps.id = selected.id
ORDER BY ps.ts ASC
"""
    )


def build_downsampled_chart_query():
    """Return a compact chart query that never materializes positions or full metrics."""

    return text(
        _DOWNSAMPLED_SNAPSHOT_CTE
        + """
SELECT
    ps.ts,
    ps.equity,
    ps.drawdown,
    ps.metrics ->> 'benchmark_symbol' AS benchmark_symbol,
    ps.metrics ->> 'benchmark_close' AS benchmark_close,
    ps.metrics ->> 'benchmark_equity' AS benchmark_equity,
    ps.metrics ->> 'benchmark_return' AS benchmark_return,
    ps.metrics ->> 'benchmark_excess_return' AS benchmark_excess_return
FROM selected_ids selected
JOIN portfolio_snapshots ps ON ps.id = selected.id
ORDER BY ps.ts ASC
"""
    )


def load_downsampled_chart_points(
    db: Session,
    run_id: UUID,
    *,
    max_points: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        build_downsampled_chart_query(),
        {"run_id": run_id, "max_points": max_points},
    ).mappings()
    return [_serialize_chart_point(row) for row in rows]


def _serialize_chart_point(row: Any) -> dict[str, Any]:
    return {
        "ts": row["ts"].isoformat() if row["ts"] is not None else None,
        "equity": float(row["equity"]),
        "drawdown": float(row["drawdown"]) if row["drawdown"] is not None else None,
        "benchmark_symbol": row["benchmark_symbol"],
        "benchmark_close": _optional_float(row["benchmark_close"]),
        "benchmark_equity": _optional_float(row["benchmark_equity"]),
        "benchmark_return": _optional_float(row["benchmark_return"]),
        "benchmark_excess_return": _optional_float(row["benchmark_excess_return"]),
    }


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
