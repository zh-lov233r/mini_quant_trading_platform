from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import numpy as np

from src.services.backtest_engine import _available_details, _downsample_snapshots
from src.services.backtest_repository import BacktestRepository
from src.services.prepared_dataset_service import PreparedDatasetCache
from src.services.strategy_registry import strategy_data_requirements
from src.services.stateless_signal_kernel import vectorized_stateless_prefilter


class BacktestPerformanceComponentTests(unittest.TestCase):
    def test_downsample_is_deterministic_and_preserves_endpoints(self) -> None:
        rows = [
            {"ts": f"2025-01-{index + 1:02d}", "equity": float(value)}
            for index, value in enumerate([100, 103, 96, 105, 94, 107, 98, 110, 101, 112])
        ]
        first = _downsample_snapshots(rows, max_points=6)
        second = _downsample_snapshots(rows, max_points=6)
        self.assertEqual(first, second)
        self.assertEqual(first[0], rows[0])
        self.assertEqual(first[-1], rows[-1])
        self.assertLessEqual(len(first), 6)

    def test_available_details_distinguish_unpersisted_from_empty(self) -> None:
        self.assertEqual(_available_details("summary"), ["summary", "equity"])
        self.assertIn("transactions", _available_details("trades"))
        self.assertIn("signals", _available_details("full"))

    def test_stateless_strategy_requirements_have_no_history_window(self) -> None:
        for strategy_type in ("trend", "mean_reversion", "momentum_breakout"):
            self.assertEqual(strategy_data_requirements(strategy_type).history_length, 0)
        self.assertGreater(strategy_data_requirements("double_bottom").history_length, 0)

    def test_vectorized_prefilter_keeps_positions_and_complete_rows_in_stable_order(self) -> None:
        runtime = {
            "strategy_type": "momentum_breakout",
            "params": {"signal": {}},
        }
        snapshots = {
            "AAA": {"position": 0, "close": 10, "volume": 100, "volume_sma_20": 80, "ret_20d": 0.2},
            "BBB": {"position": 0, "close": 10, "volume": None, "volume_sma_20": 80, "ret_20d": 0.2},
            "CCC": {"position": 5, "close": 10, "volume": None, "volume_sma_20": None, "ret_20d": None},
        }
        filtered = vectorized_stateless_prefilter(runtime, snapshots)
        self.assertEqual(list(filtered), ["AAA", "CCC"])

    def test_repository_flushes_core_rows_in_bounded_batches(self) -> None:
        db = MagicMock()
        repository = BacktestRepository(db, batch_size=2)
        repository.add_signal({"symbol": "AAA"})
        self.assertEqual(db.execute.call_count, 0)
        repository.add_signal({"symbol": "BBB"})
        self.assertEqual(db.execute.call_count, 1)
        repository.add_snapshot({"equity": 100.0})
        repository.flush()
        self.assertEqual(db.execute.call_count, 2)
        self.assertEqual(repository.rows_inserted, 3)

    def test_prepared_dataset_is_read_only_atomic_and_fingerprint_addressed(self) -> None:
        manifest = {
            "instrument_intervals": [[1, "2025-01-01", None]],
            "date_range": ["2025-01-01", "2025-01-03"],
            "feature_set": ["close", "ret_20d"],
            "price_semantics": "forward_adjusted_when_available",
            "data_fingerprint": "a" * 64,
        }
        dtype = np.dtype([("instrument_id", "<i8"), ("close", "<f8")])
        with tempfile.TemporaryDirectory() as directory:
            cache = PreparedDatasetCache(Path(directory))

            def writer(array: np.memmap) -> None:
                array[:] = [(1, 10.0), (1, 11.0), (1, 12.0)]

            built = cache.build(manifest, shape=(3,), dtype=dtype, writer=writer)
            self.assertFalse(built.flags.writeable)
            self.assertEqual(built[2]["close"], 12.0)
            reopened = cache.open({**manifest, "loader_schema_version": "v1"})
            self.assertIsNotNone(reopened)
            self.assertFalse(reopened.flags.writeable)
            self.assertFalse(cache.cleanup(manifest, active_lease_count=1))
            self.assertTrue(cache.cleanup(manifest, active_lease_count=0))


if __name__ == "__main__":
    unittest.main()
