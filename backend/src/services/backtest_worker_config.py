from __future__ import annotations

import os

BACKTEST_EXECUTION_MODEL = "process"
BACKTEST_INTRA_RUN_EXECUTION_MODEL = "thread"
DEFAULT_BACKTEST_WORKER_CONCURRENCY = 2
MIN_BACKTEST_WORKER_CONCURRENCY = 1
MAX_BACKTEST_WORKER_CONCURRENCY = 2
DEFAULT_BACKTEST_INTRA_RUN_THREADS = 4
MIN_BACKTEST_INTRA_RUN_THREADS = 1
MAX_BACKTEST_INTRA_RUN_THREADS = 16


def resolve_backtest_worker_concurrency(value: object | None = None) -> int:
    raw_value = (
        os.getenv("BACKTEST_WORKER_CONCURRENCY", str(DEFAULT_BACKTEST_WORKER_CONCURRENCY))
        if value is None
        else value
    )
    try:
        concurrency = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "BACKTEST_WORKER_CONCURRENCY must be an integer between 1 and 2"
        ) from exc
    if not MIN_BACKTEST_WORKER_CONCURRENCY <= concurrency <= MAX_BACKTEST_WORKER_CONCURRENCY:
        raise ValueError("BACKTEST_WORKER_CONCURRENCY must be between 1 and 2")
    return concurrency


def resolve_backtest_intra_run_threads(value: object | None = None) -> int:
    raw_value = (
        os.getenv("BACKTEST_INTRA_RUN_THREADS", str(DEFAULT_BACKTEST_INTRA_RUN_THREADS))
        if value is None
        else value
    )
    try:
        threads = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "BACKTEST_INTRA_RUN_THREADS must be an integer between 1 and 16"
        ) from exc
    if not MIN_BACKTEST_INTRA_RUN_THREADS <= threads <= MAX_BACKTEST_INTRA_RUN_THREADS:
        raise ValueError("BACKTEST_INTRA_RUN_THREADS must be between 1 and 16")
    return threads


def available_cpu_count() -> int:
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            return max(len(affinity(0)), 1)
        except OSError:
            pass
    return max(int(os.cpu_count() or 1), 1)


def resolve_effective_backtest_intra_run_threads(
    value: object | None = None,
    *,
    worker_concurrency: object | None = None,
    available_cpus: int | None = None,
) -> int:
    configured = resolve_backtest_intra_run_threads(value)
    processes = resolve_backtest_worker_concurrency(worker_concurrency)
    cpu_count = max(
        int(available_cpus if available_cpus is not None else available_cpu_count()),
        1,
    )
    return min(configured, max(cpu_count // processes, 1))
