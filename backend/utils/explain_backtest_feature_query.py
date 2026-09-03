#!/usr/bin/env python3
"""Read-only EXPLAIN and binary COPY observations for one real cache shard.

EXPLAIN and COPY timings are separate observations, not a subtractive estimate
of network cost. No cache is evicted, no source table or StrategyRun is written.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from src.services.columnar_market_data_loader import (
    FEATURE_RANGE_SQL, shard_manifests, spool_copy, wire_values,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basket", default="All A Shares (Tushare)")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--work-mem-mb", type=int, default=128)
    parser.add_argument("--measure-copy", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "tmp/explain_feature_query.txt")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10 or not 4 <= args.work_mem_mb <= 512:
        parser.error("repetitions must be 1..10 and work-mem-mb must be 4..512")
    load_dotenv(ROOT / ".env")
    url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
    observations = []
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=False) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET LOCAL work_mem = {}").format(sql.Literal(f"{args.work_mem_mb}MB")))
            cursor.execute("SELECT symbols FROM stock_baskets WHERE name=%s", (args.basket,))
            row = cursor.fetchone()
            if row is None:
                raise ValueError("basket not found")
            cursor.execute("SELECT id FROM instruments WHERE ticker_canonical=ANY(%s) ORDER BY id", (row[0],))
            ids = [int(row[0]) for row in cursor.fetchall()]
            if not ids:
                raise ValueError("basket contains no resolved instruments")
            parts = shard_manifests(ids, date.fromisoformat(args.start), date.fromisoformat(args.end))
            # Report one representative full-sized bucket/year, not the whole run.
            part = max(parts, key=lambda part: (len(part["instrument_ids"]), part["date_range"][0]))
            params = {"instrument_ids": part["instrument_ids"],
                      "start_date": date.fromisoformat(part["date_range"][0]),
                      "end_date": date.fromisoformat(part["date_range"][1])}
            for repetition in range(args.repetitions):
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) " + FEATURE_RANGE_SQL, params)
                observation = {"repetition": repetition + 1, "explain": cursor.fetchone()[0][0]}
                if args.measure_copy:
                    with tempfile.TemporaryDirectory(prefix="quant-copy-probe-") as root:
                        path = Path(root) / "wire.bin"
                        started = perf_counter()
                        with cursor.copy("COPY (" + FEATURE_RANGE_SQL + ") TO STDOUT (FORMAT BINARY)", params) as stream:
                            rows = spool_copy(stream, path)
                        observation["copy_and_spool_seconds"] = perf_counter() - started
                        started = perf_counter()
                        wire_values(path, rows)
                        observation["wire_validation_seconds"] = perf_counter() - started
                        observation["rows"] = rows
                observations.append(observation)
    report = {"target": {"host": url.host, "port": url.port, "database": url.database},
              "scope": "one full instrument bucket and calendar year; not end-to-end",
              "shard": part, "total_shards": len(parts), "work_mem_mb": args.work_mem_mb,
              "cache": "not evicted; observations are sequential",
              "observations": observations,
              "median_server_ms": statistics.median(item["explain"]["Execution Time"] for item in observations)}
    if args.measure_copy:
        report["median_copy_and_spool_seconds"] = statistics.median(item["copy_and_spool_seconds"] for item in observations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("observations", "shard")}, indent=2))


if __name__ == "__main__":
    main()
