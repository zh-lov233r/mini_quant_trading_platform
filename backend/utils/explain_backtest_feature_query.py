#!/usr/bin/env python3
"""Read-only timing breakdown for the cold prepared-dataset feature query.

Prices each part of the backtest read path separately so optimization work is
aimed at measured cost rather than guesses:

  V0  the real FEATURE_RANGE_V2_SQL, instrument set passed as = ANY(array)
  V0b the same query with a literal IN (...) list, to price the expanding-bindparam cost
  V1  the same query with the `prev` LEFT JOIN LATERAL removed (prev_* -> NULL)
  V2  V1 with the `identity_symbol` LEFT JOIN LATERAL also removed
  CNT the COUNT(*) preflight that runs before the real query

EXPLAIN ANALYZE executes the query server-side but returns no rows, so the
difference between its reported time and the observed sql_read_ms is transport
plus driver conversion. --measure-transport streams the real rows and discards
them to measure that half directly.

Executes SELECT / EXPLAIN only. Writes no tables and submits no orders.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path
from time import perf_counter

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from src.services.backtest_engine import FEATURE_RANGE_V2_SQL  # noqa: E402

PREV_LATERAL_RE = re.compile(
    r"LEFT JOIN LATERAL \(\s*SELECT \*\s*FROM daily_features prev_df.*?\) prev ON TRUE",
    re.S,
)
IDENTITY_LATERAL_RE = re.compile(
    r"LEFT JOIN LATERAL \(\s*SELECT sh\.symbol.*?\) identity_symbol ON TRUE",
    re.S,
)
PREV_COLUMN_RE = re.compile(r"prev\.(\w+) AS (prev_\w+)")


def to_psycopg_sql(sql: str) -> str:
    """Rewrite the SQLAlchemy text() placeholders into psycopg named parameters."""
    sql = sql.replace(
        "AND curr.instrument_id IN :instrument_ids",
        "AND curr.instrument_id = ANY(%(instrument_ids)s)",
    )
    sql = sql.replace(":start_date", "%(start_date)s").replace(":end_date", "%(end_date)s")
    return sql.rstrip().rstrip(";")


def without_prev_lateral(sql: str) -> str:
    sql = PREV_COLUMN_RE.sub(r"NULL::double precision AS \2", sql)
    return PREV_LATERAL_RE.sub("", sql)


def without_identity_lateral(sql: str) -> str:
    sql = sql.replace(
        "COALESCE(identity_symbol.symbol, i.ticker_canonical) AS symbol",
        "i.ticker_canonical AS symbol",
    )
    return IDENTITY_LATERAL_RE.sub("", sql)


def with_literal_in_list(sql: str, instrument_ids: list[int]) -> str:
    literal = ",".join(str(int(v)) for v in instrument_ids)
    return sql.replace(
        "AND curr.instrument_id = ANY(%(instrument_ids)s)",
        f"AND curr.instrument_id IN ({literal})",
    )


def resolve_instrument_ids(cur, *, basket: str | None, a_share: bool) -> list[int]:
    if basket:
        cur.execute("SELECT symbols FROM stock_baskets WHERE name = %s", (basket,))
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"stock basket not found: {basket}")
        symbols = list(row[0] or [])
        if not symbols:
            raise SystemExit(f"stock basket is empty: {basket}")
        cur.execute(
            "SELECT id FROM instruments WHERE ticker_canonical = ANY(%s) AND is_active = TRUE",
            (symbols,),
        )
    elif a_share:
        cur.execute(
            """
            SELECT id FROM instruments
            WHERE is_active = TRUE
              AND (ticker_canonical LIKE '%%.SH'
                OR ticker_canonical LIKE '%%.SZ'
                OR ticker_canonical LIKE '%%.BJ')
            """
        )
    else:
        raise SystemExit("pass --basket NAME or --a-share")
    return sorted(int(r[0]) for r in cur.fetchall())


def explain(cur, label: str, sql: str, params: dict, out: list[str]) -> float:
    started = perf_counter()
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING, VERBOSE OFF) " + sql, params)
    elapsed = (perf_counter() - started) * 1000.0
    plan = "\n".join(r[0] for r in cur.fetchall())
    out.append(f"\n{'=' * 78}\n{label}  (wall {elapsed / 1000:.2f} s)\n{'=' * 78}\n{plan}")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basket", help="stock basket name to use as the universe")
    parser.add_argument("--a-share", action="store_true", help="use every active .SH/.SZ/.BJ instrument")
    parser.add_argument("--start", required=True, help="start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="end date, YYYY-MM-DD")
    parser.add_argument("--measure-transport", action="store_true",
                        help="also stream the real rows and discard them, to time transport")
    parser.add_argument("--out", default=str(REPO_ROOT / "tmp" / "explain_feature_query.txt"))
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set in .env")
    url = url.replace("postgresql+psycopg://", "postgresql://")

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    base = to_psycopg_sql(FEATURE_RANGE_V2_SQL)
    out: list[str] = [
        "backtest feature-query timing breakdown",
        f"window   {start_date} .. {end_date}",
    ]

    with psycopg.connect(url) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            ids = resolve_instrument_ids(cur, basket=args.basket, a_share=args.a_share)
            params = {"instrument_ids": ids, "start_date": start_date, "end_date": end_date}
            out.append(f"universe {len(ids)} instruments")

            started = perf_counter()
            cur.execute(
                """
                SELECT COUNT(*)
                FROM daily_features df
                JOIN eod_bars bars
                  ON bars.instrument_id = df.instrument_id AND bars.dt_ny = df.dt_ny
                WHERE df.instrument_id = ANY(%(instrument_ids)s)
                  AND df.dt_ny BETWEEN %(start_date)s AND %(end_date)s
                """,
                params,
            )
            row_count = cur.fetchone()[0]
            count_ms = (perf_counter() - started) * 1000.0
            out.append(f"rows     {row_count:,}")
            out.append(f"\nCNT  COUNT(*) preflight: {count_ms / 1000:.2f} s  "
                       f"(this whole pass is thrown away except for the integer)")

            v0 = explain(cur, "V0  real query, = ANY(array)", base, params, out)
            v0b = explain(cur, "V0b real query, literal IN (...) list",
                          with_literal_in_list(base, ids), params, out)
            v1 = explain(cur, "V1  without the prev LATERAL",
                         without_prev_lateral(base), params, out)
            v2 = explain(cur, "V2  without prev AND identity_symbol LATERALs",
                         without_identity_lateral(without_prev_lateral(base)), params, out)

            transport_ms = None
            if args.measure_transport:
                started = perf_counter()
                seen = 0
                with conn.cursor(name="feature_transport_probe") as scur:
                    scur.itersize = 50_000
                    scur.execute(base, params)
                    for _ in scur:
                        seen += 1
                transport_ms = (perf_counter() - started) * 1000.0
                out.append(f"\nTRANSPORT streamed {seen:,} rows to the client in "
                           f"{transport_ms / 1000:.2f} s")

    out.append("\n" + "=" * 78)
    out.append("SUMMARY (server-side only unless noted)")
    out.append("=" * 78)
    out.append(f"  COUNT(*) preflight            {count_ms / 1000:8.2f} s")
    out.append(f"  V0  real query                {v0 / 1000:8.2f} s")
    out.append(f"  V0b literal IN list           {v0b / 1000:8.2f} s   "
               f"(delta vs V0: {(v0b - v0) / 1000:+.2f} s)")
    out.append(f"  V1  no prev LATERAL           {v1 / 1000:8.2f} s   "
               f"=> prev LATERAL costs {(v0 - v1) / 1000:.2f} s")
    out.append(f"  V2  no both LATERALs          {v2 / 1000:8.2f} s   "
               f"=> identity LATERAL costs {(v1 - v2) / 1000:.2f} s")
    if transport_ms is not None:
        out.append(f"  transport + driver            {(transport_ms - v0) / 1000:8.2f} s   "
                   f"(streamed {transport_ms / 1000:.2f} s minus server {v0 / 1000:.2f} s)")
    out.append("\nRead the `loops=` counter on each LATERAL node above: if it is in the")
    out.append("millions, that join is executed once per output row.")

    text = "\n".join(out)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten to {destination}")


if __name__ == "__main__":
    main()
