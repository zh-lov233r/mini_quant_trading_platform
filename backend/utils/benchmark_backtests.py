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
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from typing import Any

import numpy as np
from sqlalchemy import func, select, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.core.db import SessionLocal, engine  # noqa: E402
from src.models.tables import BacktestJob, Strategy  # noqa: E402
from src.services.backtest_engine import run_backtest  # noqa: E402
from src.services.research_experiment_service import calculate_data_fingerprint  # noqa: E402
from src.services.stock_basket_service import load_default_common_stock_symbols  # noqa: E402
from src.services.strategy_registry import build_runtime_payload  # noqa: E402


STATELESS_TYPES = ("trend", "mean_reversion", "momentum_breakout")
SCREENING_TYPES = (*STATELESS_TYPES, "double_bottom", "support_resistance")
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
    engine_version: str
    persist_level: str
    symbol_count: int
    start_date: date
    end_date: date
    candidate_kernel: bool = False

    @property
    def repetitions(self) -> int:
        return 6  # one warm-up plus five measured runs


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _cases(
    mode: str,
    end_date: date,
    winners: list[str],
    *,
    earliest_date: date | None = None,
) -> list[BenchmarkCase]:
    if mode == "correctness":
        start = end_date - timedelta(days=120)
        return [
            BenchmarkCase(f"correctness-{kind}-{version}", kind, version, "full", 20, start, end_date)
            for kind in ALL_ENGINE_TYPES
            for version in ("v1", "v2")
        ]
    if mode == "screening":
        start = end_date - timedelta(days=365)
        cases = [
            BenchmarkCase(
                f"screening-{kind}-{level}{'-kernel' if kernel else '-baseline'}",
                kind,
                "v2",
                level,
                100,
                start,
                end_date,
                kernel,
            )
            for kind in SCREENING_TYPES
            for level in ("summary", "full")
            for kernel in ((False, True) if kind in STATELESS_TYPES else (False,))
        ]
    if mode == "confirmation":
        if not winners:
            raise ValueError("confirmation requires at least one --winner strategy type")
        invalid = sorted(set(winners) - set(ALL_ENGINE_TYPES))
        if invalid:
            raise ValueError("unsupported confirmation winner(s): " + ", ".join(invalid))
        return [
            BenchmarkCase(
                f"confirmation-{kind}-{count}-{years}y",
                kind,
                "v2",
                "summary",
                count,
                end_date - timedelta(days=365 * years),
                end_date,
                kind in STATELESS_TYPES,
            )
            for kind in winners
            for count in (500, 3640)
            for years in (1, 5)
        ]
        if earliest_date is not None:
            cases.extend(
                BenchmarkCase(
                    f"confirmation-{kind}-full-history",
                    kind,
                    "v2",
                    "summary",
                    3640,
                    earliest_date,
                    end_date,
                    kind in STATELESS_TYPES,
                )
                for kind in ("trend", "double_bottom", "support_resistance")
            )
        return cases
    return _cases("correctness", end_date, []) + _cases("screening", end_date, [])


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
        "engine_version",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("plan", "correctness", "screening", "confirmation"))
    parser.add_argument("--apply", action="store_true", help="create benchmark StrategyRun rows")
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
        selected_mode = "plan" if args.mode == "plan" else args.mode
        cases = _cases(selected_mode, latest, args.winner, earliest_date=earliest)
        strategies = _strategy_ids(db)
        symbols = load_default_common_stock_symbols(db)
        available_cases = [
            case for case in cases if case.strategy_type in strategies and len(symbols) >= case.symbol_count
        ]
        target = _target(db)
        report: dict[str, Any] = {
            "mode": args.mode,
            "readOnly": not args.apply,
            "target": target,
            "codeCommit": _git("rev-parse", "HEAD"),
            "versions": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "postgresql": db.execute(text("SHOW server_version")).scalar_one(),
            },
            "cases": [{**asdict(case), "start_date": case.start_date.isoformat(), "end_date": case.end_date.isoformat()} for case in available_cases],
            "estimatedRunCount": sum(case.repetitions for case in available_cases),
            "missingStrategyTypes": sorted({case.strategy_type for case in cases} - set(strategies)),
            "omittedForUniverseSize": [case.name for case in cases if len(symbols) < case.symbol_count],
            "dataFingerprints": {},
        }
        for case in available_cases:
            fingerprint_key = f"{case.symbol_count}:{case.start_date}:{case.end_date}"
            if fingerprint_key not in report["dataFingerprints"]:
                report["dataFingerprints"][fingerprint_key] = calculate_data_fingerprint(
                    db,
                    symbols=symbols[: case.symbol_count],
                    start_date=case.start_date,
                    end_date=case.end_date,
                )
        if args.mode == "plan" or not args.apply:
            report["authorizationRequired"] = args.mode != "plan"
        else:
            _require_apply_preconditions(db)
            results: list[dict[str, Any]] = []
            for case in available_cases:
                measured: list[float] = []
                peak_rss: list[float] = []
                run_ids: list[str] = []
                for repetition in range(case.repetitions):
                    result = run_backtest(
                        db,
                        strategies[case.strategy_type],
                        case.start_date,
                        case.end_date,
                        universe_symbols=symbols[: case.symbol_count],
                        persist_level=case.persist_level,
                        engine_version=case.engine_version,
                        stateless_candidate_kernel_types=(
                            frozenset({case.strategy_type}) if case.candidate_kernel else frozenset()
                        ),
                    )
                    run_ids.append(result.run_id)
                    performance = db.execute(
                        text("SELECT summary_metrics -> 'performance' FROM strategy_runs WHERE id = :id"),
                        {"id": result.run_id},
                    ).scalar_one() or {}
                    if repetition:
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
            report["results"] = results
            if args.mode == "correctness":
                by_name = {item["case"]: item for item in results}
                comparisons: list[dict[str, Any]] = []
                for strategy_type in ALL_ENGINE_TYPES:
                    v1 = by_name.get(f"correctness-{strategy_type}-v1")
                    v2 = by_name.get(f"correctness-{strategy_type}-v2")
                    if v1 is None or v2 is None:
                        continue
                    difference = _first_difference(
                        _correctness_payload(db, v1["runIds"][1]),
                        _correctness_payload(db, v2["runIds"][1]),
                    )
                    comparisons.append(
                        {
                            "strategyType": strategy_type,
                            "matches": difference is None,
                            "firstDifference": difference,
                        }
                    )
                report["correctnessDifferential"] = comparisons
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
