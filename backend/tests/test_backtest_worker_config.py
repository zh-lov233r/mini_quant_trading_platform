from __future__ import annotations

import os
from unittest.mock import patch
import unittest

from src.services.backtest_worker_config import resolve_backtest_worker_concurrency


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


if __name__ == "__main__":
    unittest.main()
