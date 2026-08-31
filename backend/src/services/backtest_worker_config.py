from __future__ import annotations

import os

BACKTEST_EXECUTION_MODEL = "process"
DEFAULT_BACKTEST_WORKER_CONCURRENCY = 2
MIN_BACKTEST_WORKER_CONCURRENCY = 1
MAX_BACKTEST_WORKER_CONCURRENCY = 2


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
