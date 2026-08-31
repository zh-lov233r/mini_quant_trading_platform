from __future__ import annotations

import multiprocessing
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest
import uuid

from src.workers.backtest_worker import create_backtest_executor, run_worker


def _wait_for_peer_and_return_pid(barrier: object) -> int:
    barrier.wait(timeout=10)  # type: ignore[attr-defined]
    return os.getpid()


def _raise_regular_task_error() -> None:
    raise RuntimeError("isolated task failure")


def _return_pid() -> int:
    return os.getpid()


class _EventuallyCompletedFuture:
    def __init__(self) -> None:
        self.done_calls = 0
        self.result_calls = 0

    def done(self) -> bool:
        self.done_calls += 1
        return self.done_calls >= 2

    def result(self) -> None:
        self.result_calls += 1


class BacktestWorkerProcessPoolTests(unittest.TestCase):
    def test_spawn_pool_runs_two_tasks_at_the_same_time_in_distinct_processes(self) -> None:
        context = multiprocessing.get_context("spawn")
        with context.Manager() as manager:
            barrier = manager.Barrier(2)
            with create_backtest_executor(2) as executor:
                first = executor.submit(_wait_for_peer_and_return_pid, barrier)
                second = executor.submit(_wait_for_peer_and_return_pid, barrier)
                child_pids = {first.result(timeout=15), second.result(timeout=15)}

        self.assertEqual(len(child_pids), 2)
        self.assertNotIn(os.getpid(), child_pids)

    def test_regular_task_failure_does_not_break_other_process_work(self) -> None:
        with create_backtest_executor(2) as executor:
            failed = executor.submit(_raise_regular_task_error)
            completed = executor.submit(_return_pid)
            with self.assertRaisesRegex(RuntimeError, "isolated task failure"):
                failed.result(timeout=15)
            child_pid = completed.result(timeout=15)
            follow_up_pid = executor.submit(_return_pid).result(timeout=15)

        self.assertNotEqual(child_pid, os.getpid())
        self.assertNotEqual(follow_up_pid, os.getpid())

    def test_once_mode_waits_for_every_active_task_before_exit(self) -> None:
        futures = [_EventuallyCompletedFuture(), _EventuallyCompletedFuture()]
        executor = MagicMock()
        executor.__enter__.return_value = executor
        executor.submit.side_effect = futures
        database = MagicMock()
        jobs = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]

        with (
            patch("src.workers.backtest_worker.create_backtest_executor", return_value=executor),
            patch("src.workers.backtest_worker.SessionLocal", return_value=database),
            patch("src.workers.backtest_worker.recover_expired_jobs"),
            patch("src.workers.backtest_worker.claim_next_backtest_job", side_effect=[*jobs, None]),
            patch("src.workers.backtest_worker.time.sleep") as sleep,
        ):
            run_worker(concurrency=2, poll_seconds=0.1, lease_seconds=60, once=True)

        self.assertEqual(executor.submit.call_count, 2)
        self.assertTrue(all(future.result_calls == 1 for future in futures))
        sleep.assert_called()


if __name__ == "__main__":
    unittest.main()
