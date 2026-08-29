from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
MIN_COMPLETE_RATIO = 0.90
MAX_DAILY_COUNT_CHANGE = 0.05


CRITICAL_COUNT_QUERIES = {
    "invalid_vwap_rows": """
        SELECT count(*) FROM eod_bars
        WHERE vwap IS NOT NULL
          AND (vwap <= 0 OR vwap::text IN ('NaN', 'Infinity', '-Infinity'))
    """,
    "missing_adjusted_prices": """
        SELECT count(*) FROM eod_bars
        WHERE open_fa IS NULL OR high_fa IS NULL OR low_fa IS NULL OR close_fa IS NULL
           OR open_ba IS NULL OR high_ba IS NULL OR low_ba IS NULL OR close_ba IS NULL
    """,
    "invalid_ohlcv_rows": """
        SELECT count(*) FROM eod_bars
        WHERE open_u <= 0 OR high_u <= 0 OR low_u <= 0 OR close_u <= 0
           OR high_u < GREATEST(open_u, close_u, low_u)
           OR low_u > LEAST(open_u, close_u, high_u)
           OR volume < 0
    """,
    "missing_daily_features": """
        SELECT count(*) FROM eod_bars e
        LEFT JOIN daily_features f USING (instrument_id, dt_ny)
        WHERE f.instrument_id IS NULL
    """,
    "orphan_daily_features": """
        SELECT count(*) FROM daily_features f
        LEFT JOIN eod_bars e USING (instrument_id, dt_ny)
        WHERE e.instrument_id IS NULL
    """,
    "negative_risk_features": """
        SELECT count(*) FROM daily_features
        WHERE atr_14 < 0 OR volatility_20d < 0 OR volatility_60d < 0
    """,
    "duplicate_canonical_tickers": """
        SELECT count(*) FROM (
          SELECT ticker_canonical FROM instruments
          WHERE ticker_canonical IS NOT NULL
          GROUP BY ticker_canonical HAVING count(*) > 1
        ) duplicates
    """,
    "duplicate_canonical_ticker_dates": """
        SELECT count(*) FROM (
          SELECT i.ticker_canonical, e.dt_ny
          FROM instruments i JOIN eod_bars e ON e.instrument_id = i.id
          WHERE i.ticker_canonical IS NOT NULL
          GROUP BY i.ticker_canonical, e.dt_ny HAVING count(*) > 1
        ) duplicates
    """,
    "symbol_history_overlap_pairs": """
        SELECT count(*) FROM symbol_history a JOIN symbol_history b
          ON a.id < b.id
         AND a.exchange = b.exchange
         AND a.symbol = b.symbol
         AND daterange(a.valid_from, COALESCE(a.valid_to, 'infinity'::date), '[]')
             && daterange(b.valid_from, COALESCE(b.valid_to, 'infinity'::date), '[]')
    """,
    "inactive_open_symbol_intervals": """
        SELECT count(*) FROM instruments i
        JOIN symbol_history sh ON sh.instrument_id = i.id
        WHERE NOT i.is_active AND sh.valid_to IS NULL
    """,
    "active_instruments_without_open_primary_symbol": """
        SELECT count(*) FROM instruments i
        WHERE i.is_active AND i.asset_type IN ('CS', 'ETF')
          AND NOT EXISTS (
            SELECT 1 FROM symbol_history sh
            WHERE sh.instrument_id = i.id
              AND sh.valid_to IS NULL
              AND sh.is_primary
          )
    """,
    "invalid_short_interest_rows": """
        SELECT count(*) FROM stock_short_interest
        WHERE short_interest < 0 OR avg_daily_volume < 0 OR days_to_cover < 0
           OR days_to_cover::text IN ('NaN', 'Infinity', '-Infinity')
    """,
    "orphan_short_interest_rows": """
        SELECT count(*) FROM stock_short_interest si
        LEFT JOIN instruments i ON i.id = si.instrument_id
        WHERE i.id IS NULL
    """,
    "applied_ticker_events_without_exact_interval": """
        SELECT count(*) FROM security_ticker_events event
        WHERE event.resolution_status = 'applied'
          AND NOT EXISTS (
            SELECT 1 FROM symbol_history sh
            WHERE sh.instrument_id = event.instrument_id
              AND sh.symbol = event.ticker
              AND sh.exchange = event.exchange
              AND sh.valid_from = event.event_date
              AND sh.valid_from_precision = 'exact'
              AND sh.is_primary
          )
    """,
}


@dataclass(frozen=True)
class DailyCount:
    trade_date: date
    rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run read-only integrity checks against market-data tables."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
    )
    parser.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        help="Only assess data through this YYYY-MM-DD date.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero status for warnings as well as failures.",
    )
    return parser.parse_args()


def normalize_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def choose_latest_complete_day(
    counts: list[DailyCount],
    *,
    minimum_ratio: float = MIN_COMPLETE_RATIO,
) -> tuple[DailyCount, float]:
    if not counts:
        raise ValueError("no EOD trading days found")
    if len(counts) == 1:
        return counts[0], float(counts[0].rows)

    baseline_values = [item.rows for item in counts[-21:-1]]
    baseline = float(median(baseline_values))
    threshold = baseline * minimum_ratio
    eligible = [item for item in counts if item.rows >= threshold]
    if not eligible:
        raise ValueError("no trading day reaches the completeness threshold")
    return eligible[-1], baseline


def find_daily_count_shocks(
    counts: Iterable[DailyCount],
    *,
    through: date,
    maximum_change: float = MAX_DAILY_COUNT_CHANGE,
) -> list[dict[str, Any]]:
    complete = [item for item in counts if item.trade_date <= through]
    shocks: list[dict[str, Any]] = []
    for previous, current in zip(complete, complete[1:]):
        if previous.rows == 0:
            continue
        change = (current.rows - previous.rows) / previous.rows
        if abs(change) > maximum_change:
            shocks.append(
                {
                    "previous_date": previous.trade_date,
                    "current_date": current.trade_date,
                    "previous_rows": previous.rows,
                    "current_rows": current.rows,
                    "change_pct": round(change * 100, 3),
                }
            )
    return shocks


def scalar(conn: psycopg.Connection, query: str, params: dict[str, Any] | None = None) -> int:
    row = conn.execute(query, params or {}).fetchone()
    return int(row[0])


def run_checks(conn: psycopg.Connection, *, as_of_date: date | None) -> dict[str, Any]:
    totals = {
        "eod_bars": scalar(conn, "SELECT count(*) FROM eod_bars"),
        "daily_features": scalar(conn, "SELECT count(*) FROM daily_features"),
        "instruments": scalar(conn, "SELECT count(*) FROM instruments"),
        "stock_short_interest": scalar(conn, "SELECT count(*) FROM stock_short_interest"),
        "security_ticker_events": scalar(conn, "SELECT count(*) FROM security_ticker_events"),
    }
    critical_counts = {
        name: scalar(conn, query) for name, query in CRITICAL_COUNT_QUERIES.items()
    }

    recent_rows = conn.execute(
        """
        SELECT dt_ny, row_count FROM (
          SELECT dt_ny, count(*) AS row_count
          FROM eod_bars
          WHERE (%(as_of)s::date IS NULL OR dt_ny <= %(as_of)s::date)
          GROUP BY dt_ny ORDER BY dt_ny DESC LIMIT 31
        ) recent ORDER BY dt_ny
        """,
        {"as_of": as_of_date},
    ).fetchall()
    counts = [DailyCount(row[0], int(row[1])) for row in recent_rows]
    latest_complete, baseline = choose_latest_complete_day(counts)
    latest_observed = counts[-1]
    shocks = find_daily_count_shocks(counts, through=latest_complete.trade_date)
    critical_counts["recent_daily_count_shocks"] = len(shocks)

    stale_rows = conn.execute(
        """
        SELECT i.id, i.ticker_canonical, MAX(e.dt_ny) AS last_bar
        FROM instruments i
        LEFT JOIN eod_bars e ON e.instrument_id = i.id
        WHERE i.is_active AND i.asset_type = 'CS'
        GROUP BY i.id
        HAVING MAX(e.dt_ny) IS NULL
            OR MAX(e.dt_ny) < %(latest_complete)s::date - 10
        ORDER BY last_bar NULLS FIRST, i.ticker_canonical
        """,
        {"latest_complete": latest_complete.trade_date},
    ).fetchall()
    missing_latest = scalar(
        conn,
        """
        SELECT count(*) FROM instruments i
        WHERE i.is_active AND i.asset_type = 'CS'
          AND NOT EXISTS (
            SELECT 1 FROM eod_bars e
            WHERE e.instrument_id = i.id AND e.dt_ny = %(latest_complete)s
          )
        """,
        {"latest_complete": latest_complete.trade_date},
    )

    warnings: list[dict[str, Any]] = []
    if latest_observed.trade_date != latest_complete.trade_date:
        warnings.append(
            {
                "name": "latest_observed_day_is_partial",
                "date": latest_observed.trade_date,
                "rows": latest_observed.rows,
                "baseline_rows": baseline,
            }
        )
    if stale_rows:
        warnings.append(
            {
                "name": "stale_active_common_stocks",
                "count": len(stale_rows),
                "sample": [
                    {"instrument_id": row[0], "ticker": row[1], "last_bar": row[2]}
                    for row in stale_rows[:20]
                ],
            }
        )
    if missing_latest:
        warnings.append(
            {
                "name": "active_common_stocks_without_latest_bar",
                "count": missing_latest,
                "date": latest_complete.trade_date,
            }
        )

    vwap_entitlement_start = date(2016, 8, 29)
    pre_entitlement_vwap = scalar(
        conn,
        "SELECT count(*) FROM eod_bars WHERE dt_ny < %(start)s AND vwap IS NULL",
        {"start": vwap_entitlement_start},
    )
    eligible_missing_vwap = scalar(
        conn,
        """
        SELECT count(*) FROM eod_bars
        WHERE dt_ny >= %(start)s
          AND (%(as_of)s::date IS NULL OR dt_ny <= %(as_of)s::date)
          AND vwap IS NULL
        """,
        {"start": vwap_entitlement_start, "as_of": as_of_date},
    )
    missing_sic = scalar(
        conn,
        """
        SELECT count(*) FROM instruments
        WHERE is_active AND asset_type = 'CS' AND sic_code IS NULL
        """,
    )
    unresolved_events = scalar(
        conn,
        "SELECT count(*) FROM security_ticker_events WHERE resolution_status = 'unresolved'",
    )
    latest_short_interest = conn.execute(
        "SELECT max(settlement_date) FROM stock_short_interest"
    ).fetchone()[0]

    if pre_entitlement_vwap:
        warnings.append(
            {
                "name": "vwap_before_plan_entitlement_missing",
                "count": pre_entitlement_vwap,
                "before": vwap_entitlement_start,
            }
        )
    if eligible_missing_vwap:
        warnings.append(
            {
                "name": "provider_or_identity_vwap_missing",
                "count": eligible_missing_vwap,
                "from": vwap_entitlement_start,
            }
        )
    if missing_sic:
        warnings.append({"name": "active_common_stocks_without_sic", "count": missing_sic})
    if unresolved_events:
        warnings.append({"name": "unresolved_ticker_events", "count": unresolved_events})
    stale_short_interest_cutoff = (as_of_date or latest_complete.trade_date) - timedelta(days=45)
    if latest_short_interest is None or latest_short_interest < stale_short_interest_cutoff:
        warnings.append(
            {
                "name": "short_interest_is_stale",
                "latest_settlement_date": latest_short_interest,
                "expected_on_or_after": stale_short_interest_cutoff,
            }
        )

    failures = [name for name, count in critical_counts.items() if count]
    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    return {
        "status": status,
        "totals": totals,
        "latest_observed": asdict(latest_observed),
        "latest_complete": asdict(latest_complete),
        "recent_median_rows": baseline,
        "critical_counts": critical_counts,
        "daily_count_shocks": shocks,
        "warnings": warnings,
        "failures": failures,
    }


def print_text(result: dict[str, Any]) -> None:
    print(f"Market data quality: {result['status']}")
    print(
        "Totals: "
        f"eod_bars={result['totals']['eod_bars']} "
        f"daily_features={result['totals']['daily_features']} "
        f"instruments={result['totals']['instruments']} "
        f"short_interest={result['totals']['stock_short_interest']} "
        f"ticker_events={result['totals']['security_ticker_events']}"
    )
    observed = result["latest_observed"]
    complete = result["latest_complete"]
    print(
        f"Latest observed: {observed['trade_date']} ({observed['rows']} rows); "
        f"latest complete: {complete['trade_date']} ({complete['rows']} rows)"
    )
    print("Critical checks:")
    for name, count in result["critical_counts"].items():
        print(f"  {'PASS' if count == 0 else 'FAIL'} {name}={count}")
    for warning in result["warnings"]:
        details = ", ".join(
            f"{key}={value}" for key, value in warning.items() if key != "name"
        )
        print(f"  WARN {warning['name']}: {details}")


def should_fail(result: dict[str, Any], *, strict: bool) -> bool:
    return result["status"] == "FAIL" or (strict and result["status"] == "WARN")


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv(
        "SQLALCHEMY_DATABASE_URL"
    )
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    with psycopg.connect(normalize_dsn(database_url)) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        result = run_checks(conn, as_of_date=args.as_of_date)

    if args.json:
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    else:
        print_text(result)

    if should_fail(result, strict=args.strict):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
