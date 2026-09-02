#!/usr/bin/env python3
"""Controlled backtest benchmark funnel.

`plan` is always read-only. Other modes are also read-only unless `--apply` is
present; apply mode runs synchronously and therefore creates StrategyRun detail
rows according to each case's persist level.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from typing import Any

import numpy as np
import quant_kernel
from sqlalchemy import func, select, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.core.db import SessionLocal, engine  # noqa: E402
from src.models.tables import BacktestJob, Strategy  # noqa: E402
from src.services.backtest_engine import run_backtest  # noqa: E402
from src.services.stock_basket_service import load_default_common_stock_symbols  # noqa: E402
from src.services.strategy_registry import build_runtime_payload  # noqa: E402


STATELESS_TYPES = ("trend", "mean_reversion", "momentum_breakout")
ALL_ENGINE_TYPES = (
    *STATELESS_TYPES,
    "island_reversal",
    "double_bottom",
    "head_shoulders_bottom",
    "rounded_bottom",
    "v_reversal",
    "support_resistance",
)


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    strategy_type: str
    persist_level: str
    symbol_count: int
    start_date: date
    end_date: date
    cache_state: str = "warm"

    @property
    def repetitions(self) -> int:
        return 5 if self.cache_state == "cold" else 6


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _cases(
    mode: str,
    end_date: date,
    winners: list[str],
    *,
    correctness_start: date | None = None,
) -> list[BenchmarkCase]:
    if mode == "correctness":
        start = correctness_start or end_date - timedelta(days=180)
        return [
            BenchmarkCase(f"correctness-{kind}", kind, "full", 20, start, end_date)
            for kind in ALL_ENGINE_TYPES
        ]
    if mode == "screening":
        start = end_date - timedelta(days=365)
        return [
            BenchmarkCase(
                f"screening-{kind}-warm-summary",
                kind,
                "summary",
                500,
                start,
                end_date,
            )
            for kind in ALL_ENGINE_TYPES
        ]
    if mode == "confirmation":
        if not winners:
            winners = ["trend", "double_bottom", "support_resistance"]
        invalid = sorted(set(winners) - set(ALL_ENGINE_TYPES))
        if invalid:
            raise ValueError("unsupported confirmation winner(s): " + ", ".join(invalid))
        cases = [
            BenchmarkCase(
                f"confirmation-{kind}-3640-5y-{cache_state}-{level}",
                kind,
                level,
                3640,
                end_date - timedelta(days=365 * 5),
                end_date,
                cache_state,
            )
            for kind in winners
            for cache_state, level in (
                ("cold", "summary"),
                ("warm", "summary"),
                ("warm", "full"),
            )
        ]
        return cases
    return (
        _cases("correctness", end_date, [], correctness_start=correctness_start)
        + _cases("screening", end_date, [])
        + _cases("confirmation", end_date, [])
    )


def _planned_write_estimate(cases: list[BenchmarkCase]) -> dict[str, int]:
    native_runs = sum(case.repetitions for case in cases)
    python_baseline_runs = sum(
        case.repetitions
        for case in cases
        if case.name.startswith(("screening-", "confirmation-"))
    )
    return {
        "benchmarkDraftStrategies": 1,
        "pythonBaselineStrategyRuns": python_baseline_runs,
        "nativeStrategyRuns": native_runs,
        "totalStrategyRuns": python_baseline_runs + native_runs,
    }


def _strategy_ids(db: Any) -> dict[str, str]:
    rows = db.execute(
        select(Strategy).where(Strategy.status.in_(("active", "draft"))).order_by(
            Strategy.strategy_type, Strategy.status, Strategy.updated_at.desc()
        )
    ).scalars()
    result: dict[str, str] = {}
    for strategy in rows:
        if strategy.strategy_type in result:
            continue
        try:
            if build_runtime_payload(strategy)["engine_ready"]:
                result[strategy.strategy_type] = str(strategy.id)
        except ValueError:
            continue
    return result


def _target(db: Any) -> dict[str, str]:
    database, schema = db.execute(text("SELECT current_database(), current_schema()")) .one()
    return {
        "database": str(database),
        "schema": str(schema),
        "url": engine.url.render_as_string(hide_password=True),
    }


def _service_state(db: Any) -> dict[str, Any]:
    jobs = db.execute(
        text(
            "SELECT "
            "count(*) FILTER (WHERE status = 'queued') AS queued, "
            "count(*) FILTER (WHERE status = 'running') AS running "
            "FROM backtest_jobs"
        )
    ).mappings().one()
    research = db.execute(
        text(
            "SELECT count(*) FILTER (WHERE status IN "
            "('queued','running','waiting_agent','cancel_requested')) AS active "
            "FROM research_experiments"
        )
    ).mappings().one()
    managers = db.execute(
        text(
            "SELECT "
            "count(*) FILTER (WHERE heartbeat_at >= now() - interval '15 seconds') AS live, "
            "count(*) FILTER (WHERE is_leader AND heartbeat_at >= now() - interval '15 seconds') "
            "AS live_leaders "
            "FROM backtest_worker_managers"
        )
    ).mappings().one()
    return {
        "backtestJobs": {key: int(value or 0) for key, value in jobs.items()},
        "activeResearchExperiments": int(research["active"] or 0),
        "backtestManagers": {key: int(value or 0) for key, value in managers.items()},
        "environment": {
            key: os.getenv(key)
            for key in (
                "RESEARCH_WORKER_ENABLED",
                "PAPER_TRADING_SCHEDULER_ENABLED",
                "PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS",
                "BACKTEST_WORKER_CONCURRENCY",
            )
        },
    }


def _plan_data_fingerprint(
    db: Any,
    *,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Return a compact read-only source revision without materializing rows in Python."""
    row = db.execute(
        text(
            """
            WITH selected_instruments AS (
                SELECT id, ticker_canonical, updated_at
                FROM instruments
                WHERE ticker_canonical = ANY(:symbols)
            ), market_rows AS (
                SELECT
                    df.*,
                    bars.ts_utc AS bar_ts_utc,
                    bars.open_u,
                    bars.high_u,
                    bars.low_u,
                    bars.close_u,
                    bars.open_fa,
                    bars.high_fa,
                    bars.low_fa,
                    bars.close_fa,
                    bars.volume AS bar_volume,
                    bars.asof AS bar_asof
                FROM daily_features df
                JOIN selected_instruments selected ON selected.id = df.instrument_id
                JOIN eod_bars bars
                  ON bars.instrument_id = df.instrument_id
                 AND bars.dt_ny = df.dt_ny
                WHERE df.dt_ny BETWEEN :lookback_start AND :end_date
            ), market_revision AS (
                SELECT
                    count(*) AS row_count,
                    min(dt_ny) AS min_date,
                    max(dt_ny) AS max_date,
                    max(asof) AS feature_max_asof,
                    max(bar_asof) AS bar_max_asof,
                    sum(hashtextextended(row_to_json(market_rows)::text, 0)::numeric)
                        AS row_hash_sum
                FROM market_rows
            ), action_rows AS (
                SELECT ca.*
                FROM corporate_actions ca
                JOIN selected_instruments selected ON selected.id = ca.instrument_id
                WHERE ca.ex_date BETWEEN :start_date AND :end_date
            ), action_revision AS (
                SELECT
                    count(*) AS action_count,
                    max(updated_at) AS action_max_updated_at,
                    sum(hashtextextended(row_to_json(action_rows)::text, 0)::numeric)
                        AS action_hash_sum
                FROM action_rows
            ), identity_revision AS (
                SELECT
                    count(*) AS instrument_count,
                    max(updated_at) AS instrument_max_updated_at,
                    (
                        SELECT count(*)
                        FROM symbol_history history
                        WHERE history.instrument_id IN (SELECT id FROM selected_instruments)
                    ) AS symbol_history_count,
                    (
                        SELECT max(updated_at)
                        FROM symbol_history history
                        WHERE history.instrument_id IN (SELECT id FROM selected_instruments)
                    ) AS symbol_history_max_updated_at
                FROM selected_instruments
            )
            SELECT *
            FROM market_revision
            CROSS JOIN action_revision
            CROSS JOIN identity_revision
            """
        ),
        {
            "symbols": symbols,
            "lookback_start": start_date - timedelta(days=400),
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().one()
    payload = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in sorted(row.items())
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return {
        "kind": "postgresql-source-revision-v1",
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        **payload,
    }


def _require_apply_preconditions(db: Any) -> None:
    required = {
        "RESEARCH_WORKER_ENABLED": "false",
        "PAPER_TRADING_SCHEDULER_ENABLED": "false",
        "PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS": "false",
        "BACKTEST_WORKER_CONCURRENCY": "1",
    }
    mismatches = [f"{key}={os.getenv(key)!r}" for key, value in required.items() if os.getenv(key) != value]
    if mismatches:
        raise RuntimeError("unsafe benchmark environment: " + ", ".join(mismatches))
    active = int(
        db.execute(
            select(func.count()).select_from(BacktestJob).where(BacktestJob.status.in_(("queued", "running")))
        ).scalar_one()
    )
    if active:
        raise RuntimeError(f"{active} queued/running backtest job(s) must reach a terminal state first")
    if _git("status", "--porcelain"):
        raise RuntimeError("benchmark apply requires a clean worktree")


def _correctness_payload(db: Any, run_id: str) -> dict[str, Any]:
    summary = dict(
        db.execute(
            text("SELECT summary_metrics FROM strategy_runs WHERE id = :id"), {"id": run_id}
        ).scalar_one()
        or {}
    )
    for key in (
        "performance",
        "kernel",
        "support_resistance_materialization_id",
        "support_resistance_cache_key",
    ):
        summary.pop(key, None)
    return {
        "summary": summary,
        "signals": [
            list(row)
            for row in db.execute(
                text(
                    """
                    SELECT ts, symbol, signal, score, reason, features
                    FROM signals WHERE run_id = :id
                    ORDER BY ts, symbol, signal, reason
                    """
                ),
                {"id": run_id},
            ).all()
        ],
        "transactions": [
            list(row)
            for row in db.execute(
                text(
                    """
                    SELECT ts, symbol, side, qty, price, fee, meta
                    FROM transactions WHERE run_id = :id
                    ORDER BY ts, symbol, side, qty, price
                    """
                ),
                {"id": run_id},
            ).all()
        ],
        "snapshots": [
            list(row)
            for row in db.execute(
                text(
                    """
                    SELECT ts, cash, equity, gross_exposure, net_exposure, drawdown, positions, metrics
                    FROM portfolio_snapshots WHERE run_id = :id ORDER BY ts
                    """
                ),
                {"id": run_id},
            ).all()
        ],
    }


def _first_difference(left: Any, right: Any, path: str = "root") -> str | None:
    numeric = (int, float, Decimal)
    if isinstance(left, numeric) and not isinstance(left, bool) and isinstance(right, numeric) and not isinstance(right, bool):
        return None if math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-10) else path
    if type(left) is not type(right):
        return path
    if isinstance(left, dict):
        if set(left) != set(right):
            return path
        for key in sorted(left):
            difference = _first_difference(left[key], right[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(left, (list, tuple)):
        if len(left) != len(right):
            return path
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            difference = _first_difference(left_item, right_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    return None if left == right else path


def _load_baseline(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("baseline report must contain a results array")
    indexed: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("case"), str):
            raise ValueError("baseline result entries require a case name")
        indexed[item["case"]] = item
    return indexed


def _required_speedup(case_name: str) -> float | None:
    if case_name.startswith("screening-"):
        return 5.0
    if not case_name.startswith("confirmation-"):
        return None
    if case_name.endswith("-cold-summary"):
        return 3.0
    if case_name.endswith("-warm-summary"):
        return 5.0
    if case_name.endswith("-warm-full"):
        return 2.0
    raise ValueError(f"unrecognized confirmation case: {case_name}")


def _performance_acceptance(
    results: list[dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for item in results:
        threshold = _required_speedup(item["case"])
        if threshold is None:
            continue
        reference = baseline.get(item["case"])
        if reference is None:
            comparisons.append(
                {
                    "case": item["case"],
                    "requiredSpeedup": threshold,
                    "passed": False,
                    "reason": "missing baseline case",
                }
            )
            continue
        baseline_median = float(reference["medianEngineTotalMs"])
        baseline_rss = float(reference["maxPeakRssMb"])
        native_median = float(item["medianEngineTotalMs"])
        native_rss = float(item["maxPeakRssMb"])
        speedup = baseline_median / native_median if native_median > 0 else math.inf
        rss_passed = native_rss <= baseline_rss
        comparisons.append(
            {
                "case": item["case"],
                "baselineMedianEngineTotalMs": baseline_median,
                "nativeMedianEngineTotalMs": native_median,
                "speedup": speedup,
                "requiredSpeedup": threshold,
                "baselineMaxPeakRssMb": baseline_rss,
                "nativeMaxPeakRssMb": native_rss,
                "rssPassed": rss_passed,
                "passed": speedup >= threshold and rss_passed,
            }
        )
    return comparisons


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "correctness", "screening", "confirmation"))
    parser.add_argument("--apply", action="store_true", help="create benchmark StrategyRun rows")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="frozen pre-cutover JSON report used for speedup and RSS acceptance",
    )
    parser.add_argument("--winner", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        earliest, latest = db.execute(
            text("SELECT MIN(dt_ny), MAX(dt_ny) FROM daily_features")
        ).one()
        if not isinstance(earliest, date) or not isinstance(latest, date):
            raise RuntimeError("daily_features has no benchmark end date")
        correctness_start = db.execute(
            text(
                "SELECT MIN(dt_ny) FROM ("
                "SELECT DISTINCT dt_ny FROM daily_features "
                "WHERE dt_ny <= :latest ORDER BY dt_ny DESC LIMIT 120"
                ") AS recent_sessions"
            ),
            {"latest": latest},
        ).scalar_one()
        if not isinstance(correctness_start, date):
            raise RuntimeError("daily_features has no benchmark session window")
        selected_mode = "plan" if args.mode == "plan" else args.mode
        cases = _cases(
            selected_mode,
            latest,
            args.winner,
            correctness_start=correctness_start,
        )
        strategies = _strategy_ids(db)
        symbols = load_default_common_stock_symbols(db)
        available_cases = [
            case for case in cases if case.strategy_type in strategies and len(symbols) >= case.symbol_count
        ]
        planned_writes = _planned_write_estimate(cases)
        target = _target(db)
        report: dict[str, Any] = {
            "mode": args.mode,
            "readOnly": not args.apply,
            "target": target,
            "serviceState": _service_state(db),
            "codeCommit": _git("rev-parse", "HEAD"),
            "versions": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "postgresql": db.execute(text("SHOW server_version")).scalar_one(),
                "kernel": quant_kernel.KERNEL_VERSION,
                "kernelAbi": quant_kernel.ABI_VERSION,
                "kernelBuildId": quant_kernel.BUILD_ID,
            },
            "cases": [{**asdict(case), "start_date": case.start_date.isoformat(), "end_date": case.end_date.isoformat()} for case in available_cases],
            "estimatedRunCount": planned_writes["totalStrategyRuns"],
            "currentlyRunnableNativeRunCount": sum(
                case.repetitions for case in available_cases
            ),
            "plannedWrites": planned_writes,
            "missingStrategyTypes": sorted({case.strategy_type for case in cases} - set(strategies)),
            "omittedForUniverseSize": [case.name for case in cases if len(symbols) < case.symbol_count],
            "dataFingerprints": {},
            "performanceThresholds": {
                "screeningWarmSummarySpeedup": 5.0,
                "confirmationColdSummarySpeedup": 3.0,
                "confirmationWarmSummarySpeedup": 5.0,
                "confirmationWarmFullSpeedup": 2.0,
                "peakRssMayExceedBaseline": False,
            },
        }
        for case in available_cases:
            fingerprint_key = f"{case.symbol_count}:{case.start_date}:{case.end_date}"
            if fingerprint_key not in report["dataFingerprints"]:
                report["dataFingerprints"][fingerprint_key] = _plan_data_fingerprint(
                    db,
                    symbols=symbols[: case.symbol_count],
                    start_date=case.start_date,
                    end_date=case.end_date,
                )
        if args.mode == "plan" or not args.apply:
            report["authorizationRequired"] = args.mode != "plan"
        else:
            if args.mode in {"screening", "confirmation"} and args.baseline is None:
                raise RuntimeError(
                    f"{args.mode} apply requires --baseline with the frozen Python report"
                )
            _require_apply_preconditions(db)
            results: list[dict[str, Any]] = []
            original_cache_root = os.environ.get("BACKTEST_PREPARED_DATASET_DIR")
            try:
                for case in available_cases:
                    measured: list[float] = []
                    peak_rss: list[float] = []
                    run_ids: list[str] = []
                    with tempfile.TemporaryDirectory(prefix="quant-benchmark-cache-") as cache_root:
                        for repetition in range(case.repetitions):
                            cache_name = (
                                f"cold-{repetition}" if case.cache_state == "cold" else "warm"
                            )
                            os.environ["BACKTEST_PREPARED_DATASET_DIR"] = str(
                                Path(cache_root) / cache_name
                            )
                            result = run_backtest(
                                db,
                                strategies[case.strategy_type],
                                case.start_date,
                                case.end_date,
                                universe_symbols=symbols[: case.symbol_count],
                                persist_level=case.persist_level,
                            )
                            run_ids.append(result.run_id)
                            performance = db.execute(
                                text(
                                    "SELECT summary_metrics -> 'performance' "
                                    "FROM strategy_runs WHERE id = :id"
                                ),
                                {"id": result.run_id},
                            ).scalar_one() or {}
                            if case.cache_state == "cold" or repetition > 0:
                                measured.append(float(performance["engine_total_ms"]))
                                peak_rss.append(float(performance["peak_rss_mb"]))
                    results.append(
                        {
                            "case": case.name,
                            "runIds": run_ids,
                            "medianEngineTotalMs": statistics.median(measured),
                            "maxEngineTotalMs": max(measured),
                            "maxPeakRssMb": max(peak_rss),
                        }
                    )
            finally:
                if original_cache_root is None:
                    os.environ.pop("BACKTEST_PREPARED_DATASET_DIR", None)
                else:
                    os.environ["BACKTEST_PREPARED_DATASET_DIR"] = original_cache_root
            report["results"] = results
            if args.mode in {"screening", "confirmation"}:
                assert args.baseline is not None
                performance_acceptance = _performance_acceptance(
                    results,
                    _load_baseline(args.baseline),
                )
                report["performanceAcceptance"] = performance_acceptance
                report["acceptance"] = (
                    "passed"
                    if performance_acceptance
                    and all(item["passed"] for item in performance_acceptance)
                    else "blocked"
                )
            if args.mode == "correctness":
                comparisons: list[dict[str, Any]] = []
                for item in results:
                    payloads = [
                        _correctness_payload(db, run_id) for run_id in item["runIds"]
                    ]
                    difference = next(
                        (
                            found
                            for payload in payloads[1:]
                            if (found := _first_difference(payloads[0], payload)) is not None
                        ),
                        None,
                    )
                    comparisons.append(
                        {
                            "strategyType": item["case"].removeprefix("correctness-"),
                            "matches": difference is None,
                            "firstDifference": difference,
                        }
                    )
                report["nativeDeterminism"] = comparisons
                if any(not item["matches"] for item in comparisons):
                    report["acceptance"] = "blocked"
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
