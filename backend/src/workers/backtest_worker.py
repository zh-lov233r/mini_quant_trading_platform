from __future__ import annotations

import argparse
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import logging
import multiprocessing
import time
from uuid import UUID

from src.core.db import SessionLocal
from src.services.backtest_job_service import (
    claim_next_backtest_job,
    default_worker_id,
    execute_backtest_job,
    recover_expired_jobs,
)
from src.services.backtest_worker_config import resolve_backtest_worker_concurrency

log = logging.getLogger(__name__)


def create_backtest_executor(concurrency: int) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=resolve_backtest_worker_concurrency(concurrency),
        mp_context=multiprocessing.get_context("spawn"),
    )


def run_worker(*, concurrency: int, poll_seconds: float, lease_seconds: int, once: bool) -> None:
    configured_concurrency = resolve_backtest_worker_concurrency(concurrency)
    worker_id = default_worker_id()
    active: dict[Future[None], UUID] = {}
    with create_backtest_executor(configured_concurrency) as executor:
        while True:
            for future in list(active):
                if not future.done():
                    continue
                job_id = active.pop(future)
                try:
                    future.result()
                except BrokenProcessPool:
                    log.exception("Backtest process pool failed", extra={"job_id": str(job_id)})
                    raise
                except Exception:
                    log.exception("Backtest worker task failed", extra={"job_id": str(job_id)})

            while len(active) < configured_concurrency:
                db = SessionLocal()
                try:
                    recover_expired_jobs(db)
                    job = claim_next_backtest_job(
                        db,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                    )
                finally:
                    db.close()
                if job is None:
                    break
                future = executor.submit(
                    execute_backtest_job,
                    job.id,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
                active[future] = job.id

            if once and not active:
                return
            time.sleep(max(0.1, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable PostgreSQL-backed backtest jobs.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    concurrency = resolve_backtest_worker_concurrency(args.concurrency)
    run_worker(
        concurrency=concurrency,
        poll_seconds=args.poll_seconds,
        lease_seconds=args.lease_seconds,
        once=args.once,
    )


if __name__ == "__main__":
    main()
