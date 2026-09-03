from __future__ import annotations

import os
from unittest.mock import patch
import unittest

from src.services.backtest_worker_config import (
    available_cpu_count,
    resolve_backtest_intra_run_threads,
    resolve_backtest_worker_concurrency,
    resolve_effective_backtest_intra_run_threads,
)


class BacktestWorkerConfigTests(unittest.TestCase):
    def test_default_is_two(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_backtest_worker_concurrency(), 2)

    def test_one_and_two_are_supported(self) -> None:
        for value in ("1", "2", 1, 2):
            with self.subTest(value=value):
                self.assertEqual(resolve_backtest_worker_concurrency(value), int(value))

    def test_invalid_values_fail(self) -> None:
        for value in ("0", "-1", "1.5", "three", "3"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "between 1 and 2"):
                    resolve_backtest_worker_concurrency(value)

    def test_intra_run_threads_default_to_four(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_backtest_intra_run_threads(), 4)

    def test_intra_run_threads_accept_one_through_sixteen(self) -> None:
        for value in ("1", "4", "16", 1, 16):
            with self.subTest(value=value):
                self.assertEqual(resolve_backtest_intra_run_threads(value), int(value))

    def test_invalid_intra_run_threads_fail(self) -> None:
        for value in ("0", "-1", "1.5", "four", "17"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "between 1 and 16"):
                    resolve_backtest_intra_run_threads(value)

    def test_effective_threads_respect_cpu_and_process_budget(self) -> None:
        cases = (
            (4, 1, 8, 4),
            (4, 2, 8, 4),
            (4, 2, 4, 2),
            (16, 2, 1, 1),
        )
        for configured, processes, cpus, expected in cases:
            with self.subTest(
                configured=configured,
                processes=processes,
                cpus=cpus,
            ):
                self.assertEqual(
                    resolve_effective_backtest_intra_run_threads(
                        configured,
                        worker_concurrency=processes,
                        available_cpus=cpus,
                    ),
                    expected,
                )

    def test_linux_affinity_is_preferred_over_host_cpu_count(self) -> None:
        with (
            patch(
                "src.services.backtest_worker_config.os.sched_getaffinity",
                return_value={0, 1, 2},
                create=True,
            ),
            patch("src.services.backtest_worker_config.os.cpu_count", return_value=64),
        ):
            self.assertEqual(available_cpu_count(), 3)

    def test_cpu_count_is_the_affinity_fallback(self) -> None:
        with (
            patch(
                "src.services.backtest_worker_config.os.sched_getaffinity",
                side_effect=OSError,
                create=True,
            ),
            patch("src.services.backtest_worker_config.os.cpu_count", return_value=6),
        ):
            self.assertEqual(available_cpu_count(), 6)


if __name__ == "__main__":
    unittest.main()
