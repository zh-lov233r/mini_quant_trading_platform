"""Read-only funnel, regime, rejection-return and stop-gap report for one run."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row


def audit(connection: psycopg.Connection, run_id: UUID) -> dict:
    def rows(sql: str) -> list[dict]:
        return connection.execute(sql, {"run": run_id}).fetchall()

    runs = rows("SELECT id,window_start,window_end,status FROM strategy_runs WHERE id=%(run)s")
    if not runs:
        raise ValueError("run not found")
    report = {"database_mutated": False, "run": runs[0], "target": connection.info.dbname}
    report["funnel"] = rows("""
        SELECT setup, count(*) AS candidates,
          count(*) FILTER (WHERE (payload->>'regime_eligible')::bool) AS regime_eligible,
          count(*) FILTER (WHERE (payload->>'channel_eligible')::bool) AS channel_eligible,
          count(*) FILTER (WHERE (payload->>'entry_eligible')::bool) AS entry_eligible,
          count(*) FILTER (WHERE (payload->>'entry_eligible')::bool AND
            (payload->'strength'->>'passes_threshold')::bool) AS strength_eligible,
          avg((payload->>'reward_risk')::float) AS reward_risk_mean,
          stddev_pop((payload->>'reward_risk')::float) AS reward_risk_stddev,
          avg((payload->'strength'->>'score')::float) AS strength_mean,
          stddev_pop((payload->'strength'->>'score')::float) AS strength_stddev,
          count(*) FILTER (WHERE payload ? 'overhead_count') AS target_source_observed,
          count(*) FILTER (WHERE payload->>'target_source'='atr_fallback') AS atr_fallback
        FROM support_resistance_run_events WHERE run_id=%(run)s AND event_type='candidate'
        GROUP BY setup ORDER BY setup
    """)
    report["fills"] = rows("""
        SELECT meta->'entry_signal_features'->'support_resistance'->>'selected_setup' AS setup,
          side,count(*) AS fills FROM transactions WHERE run_id=%(run)s GROUP BY 1,2 ORDER BY 1,2
    """)
    report["rejections"] = rows("""
        SELECT event_type,coalesce(payload->>'rejection_reason',payload->>'reason_code') AS reason,count(*) AS count
        FROM support_resistance_run_events WHERE run_id=%(run)s AND
          (event_type IN ('execution_rejection','entry_channel_rejection') OR
           (event_type='candidate' AND NOT (payload->>'entry_eligible')::bool))
        GROUP BY 1,2 ORDER BY 1,2
    """)
    report["regime_sessions"] = rows("""
        WITH intervals AS (
          SELECT g.instrument_id,g.regime,g.effective_from,
            lead(g.effective_from,1,m.coverage_end+1) OVER (PARTITION BY g.instrument_id ORDER BY g.effective_from) AS until,
            r.window_start,r.window_end
          FROM support_resistance_regime_versions g JOIN support_resistance_materializations m ON m.id=g.materialization_id
          JOIN support_resistance_run_materializations l ON l.materialization_id=m.id
          JOIN strategy_runs r ON r.id=l.run_id WHERE l.run_id=%(run)s
        )
        SELECT regime,count(*) AS sessions FROM intervals i
          JOIN daily_features f ON f.instrument_id=i.instrument_id AND f.dt_ny >= i.effective_from AND f.dt_ny < i.until
            AND f.dt_ny BETWEEN i.window_start AND i.window_end
          JOIN eod_bars b ON b.instrument_id=f.instrument_id AND b.dt_ny=f.dt_ny
        GROUP BY regime ORDER BY regime
    """)
    report["rejected_forward_returns"] = rows("""
        WITH rejected AS (
          SELECT DISTINCT instrument_id,event_date,
            CASE WHEN (payload->>'simulated_execution_price')::float > (payload->'entry_channel'->>'upper')::float
              THEN 'above_channel' WHEN (payload->>'simulated_execution_price')::float < (payload->'entry_channel'->>'lower')::float
              THEN 'below_channel' ELSE coalesce(payload->>'reason_code','unknown') END AS reason,
            (payload->>'reference_open')::float AS reference
          FROM support_resistance_run_events WHERE run_id=%(run)s AND event_type='execution_rejection'
        ), forward AS (
          SELECT r.*, b.close / nullif(r.reference,0) - 1 AS return_pct,b.session
          FROM rejected r CROSS JOIN LATERAL (
            SELECT COALESCE(close_fa,close_u)::float AS close,row_number() OVER (ORDER BY dt_ny) AS session
            FROM eod_bars WHERE instrument_id=r.instrument_id AND dt_ny>=r.event_date ORDER BY dt_ny LIMIT 20
          ) b
        ) SELECT reason,session,count(*) AS samples,avg(return_pct) AS mean_return,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY return_pct) AS median_return
          FROM forward WHERE session IN (1,5,10,20) GROUP BY 1,2 ORDER BY 1,2
    """)
    report["stop_exit_gaps"] = rows("""
        SELECT count(*) AS samples,avg(b.open / nullif((s.features->>'close')::float,0)-1) AS mean_gap,
          min(b.open / nullif((s.features->>'close')::float,0)-1) AS worst_gap,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY b.open / nullif((s.features->>'close')::float,0)-1) AS median_gap
        FROM signals s CROSS JOIN LATERAL (
          SELECT COALESCE(open_fa,open_u)::float AS open FROM eod_bars
          WHERE instrument_id=s.instrument_id AND dt_ny>(s.ts AT TIME ZONE 'America/New_York')::date
          ORDER BY dt_ny LIMIT 1
        ) b WHERE s.run_id=%(run)s AND s.signal='SELL' AND
          (s.features->'support_resistance'->>'exit_reason_code'='stop' OR s.reason LIKE 'closed below the %%zone-aware%%')
    """)
    report["notes"] = [
        "Regime coverage and forward returns use currently stored market data; historical corrections may differ from the original run.",
        "Old runs without overhead_count cannot establish the ATR fallback rate.",
        "Rejection returns are overlapping descriptive observations, not independent samples or filled portfolio returns.",
        "Stop gaps measure next available open / exit-signal close, without slippage.",
    ]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout='60s'")
        report = audit(connection, args.run_id)
        connection.rollback()
    print(json.dumps(report, default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
