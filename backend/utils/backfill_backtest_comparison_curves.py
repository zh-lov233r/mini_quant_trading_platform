from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable
from uuid import UUID

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.backtests import _resolve_comparison_curves_and_summary
from src.core.db import SessionLocal
from src.models.tables import PortfolioSnapshot, StrategyRun


def _iter_target_runs(
    run_id: str | None,
    limit: int | None,
) -> Iterable[StrategyRun]:
    with SessionLocal() as db:
        stmt = (
            select(StrategyRun)
            .where(StrategyRun.mode == "backtest")
            .where(StrategyRun.status == "completed")
            .order_by(StrategyRun.finished_at.desc().nullslast(), StrategyRun.created_at.desc())
        )
        if run_id:
            stmt = stmt.where(StrategyRun.id == UUID(run_id))
        if limit is not None:
            stmt = stmt.limit(limit)
        for run in db.execute(stmt).scalars():
            yield run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill cached SPY/QQQ comparison curves into strategy_runs.summary_metrics."
    )
    parser.add_argument("--run-id", help="Only backfill one run id")
    parser.add_argument("--limit", type=int, help="Only process the newest N completed backtests")
    parser.add_argument("--force", action="store_true", help="Recompute even if comparison_curves already exist")
    args = parser.parse_args()

    processed = 0
    updated = 0
    skipped = 0

    target_run_ids = [run.id for run in _iter_target_runs(args.run_id, args.limit)]
    with SessionLocal() as db:
        for run_id in target_run_ids:
            run = db.get(StrategyRun, run_id)
            if run is None:
                continue

            summary_metrics = dict(run.summary_metrics or {})
            has_cached_curves = isinstance(summary_metrics.get("comparison_curves"), dict)
            has_benchmark_summary = (
                run.benchmark_symbol is None
                or summary_metrics.get("benchmark_total_return") is not None
            )
            if not args.force and has_cached_curves and has_benchmark_summary:
                skipped += 1
                continue

            equity_curve = db.execute(
                select(PortfolioSnapshot)
                .where(PortfolioSnapshot.run_id == run.id)
                .order_by(PortfolioSnapshot.ts.asc())
            ).scalars().all()
            if not equity_curve:
                skipped += 1
                continue

            has_stored_benchmark = any(
                (snapshot.metrics or {}).get("benchmark_equity") is not None
                for snapshot in equity_curve
            )
            _, updated_summary_metrics, _, cache_updated = _resolve_comparison_curves_and_summary(
                db,
                run,
                equity_curve,
                summary_metrics,
                has_stored_benchmark,
            )

            processed += 1
            if not cache_updated and not args.force:
                skipped += 1
                continue

            run.summary_metrics = updated_summary_metrics
            db.add(run)
            db.commit()
            updated += 1
            print(f"updated {run.id} ({run.strategy_id})")

    print(
        f"done processed={processed} updated={updated} skipped={skipped}"
    )


if __name__ == "__main__":
    main()
