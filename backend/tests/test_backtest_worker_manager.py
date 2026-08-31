from __future__ import annotations

import sys
from unittest.mock import Mock
import unittest

from src.workers.backtest_worker_manager import BacktestWorkerManagerRunner, restart_delay_seconds


class BacktestWorkerManagerTests(unittest.TestCase):
    def test_restart_backoff_is_capped(self) -> None:
        self.assertEqual([restart_delay_seconds(value) for value in range(1, 7)], [1, 2, 5, 10, 30, 30])

    def test_worker_command_uses_configured_concurrency(self) -> None:
        runner = BacktestWorkerManagerRunner(concurrency=2, lease_seconds=180)
        self.assertEqual(
            runner._worker_command(),
            [
                sys.executable,
                "-m",
                "src.workers.backtest_worker",
                "--once",
                "--concurrency",
                "2",
                "--lease-seconds",
                "180",
            ],
        )

    def test_empty_queue_does_not_start_worker(self) -> None:
        runner = BacktestWorkerManagerRunner(poll_seconds=0.1)
        runner.leader_lock.try_acquire = Mock(return_value=True)  # type: ignore[method-assign]
        runner.leader_lock.release = Mock()  # type: ignore[method-assign]
        runner._queue_has_work = Mock(return_value=False)  # type: ignore[method-assign]
        runner._start_worker = Mock()  # type: ignore[method-assign]

        def stop_when_idle(status: str, **_kwargs: object) -> None:
            if status == "idle":
                runner.stop_event.set()

        runner._write_state = Mock(side_effect=stop_when_idle)  # type: ignore[method-assign]
        runner.run()
        runner._start_worker.assert_not_called()

    def test_existing_queue_starts_worker_immediately(self) -> None:
        runner = BacktestWorkerManagerRunner(poll_seconds=0.1)
        runner.leader_lock.try_acquire = Mock(return_value=True)  # type: ignore[method-assign]
        runner.leader_lock.release = Mock()  # type: ignore[method-assign]
        runner._queue_has_work = Mock(return_value=True)  # type: ignore[method-assign]

        def start_and_stop() -> None:
            runner.stop_event.set()

        runner._start_worker = Mock(side_effect=start_and_stop)  # type: ignore[method-assign]
        runner._write_state = Mock()  # type: ignore[method-assign]
        runner.run()
        runner._start_worker.assert_called_once_with()

    def test_standby_manager_never_starts_worker(self) -> None:
        runner = BacktestWorkerManagerRunner(poll_seconds=0.1)
        runner.leader_lock.try_acquire = Mock(return_value=False)  # type: ignore[method-assign]
        runner.leader_lock.release = Mock()  # type: ignore[method-assign]
        runner._start_worker = Mock()  # type: ignore[method-assign]

        def stop_when_standby(status: str, **_kwargs: object) -> None:
            if status == "standby":
                runner.stop_event.set()

        runner._write_state = Mock(side_effect=stop_when_standby)  # type: ignore[method-assign]
        runner.run()
        runner._start_worker.assert_not_called()

    def test_worker_crash_enters_backoff_when_queue_remains(self) -> None:
        runner = BacktestWorkerManagerRunner(poll_seconds=0.1)
        runner.leader_lock.try_acquire = Mock(return_value=True)  # type: ignore[method-assign]
        runner.leader_lock.release = Mock()  # type: ignore[method-assign]
        runner._queue_has_work = Mock(return_value=True)  # type: ignore[method-assign]
        worker = Mock()
        worker.poll.return_value = 9
        runner.worker = worker

        def stop_when_backoff(status: str, **_kwargs: object) -> None:
            if status == "backoff":
                runner.stop_event.set()

        runner._write_state = Mock(side_effect=stop_when_backoff)  # type: ignore[method-assign]
        runner.run()
        self.assertEqual(runner.last_worker_exit_code, 9)
        self.assertIsNotNone(runner.next_worker_start_at)


if __name__ == "__main__":
    unittest.main()
