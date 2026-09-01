from __future__ import annotations

from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

import numpy as np

from src.services.backtest_engine import (
    _available_details,
    _downsample_snapshots,
    _finalize_engine_performance,
    _inject_backtest_positions,
    _update_last_marks,
)
from src.services.backtest_equity_service import (
    build_downsampled_chart_query,
    build_downsampled_snapshot_ids_query,
)
from src.services.backtest_repository import BacktestRepository
from src.services.prepared_dataset_service import (
    PREPARED_DATASET_DTYPE,
    PREPARED_DATASET_SCHEMA_VERSION,
    PreparedDatasetCache,
    PreparedDatasetDataChangedError,
    PreparedDatasetDayLoader,
    encode_prepared_snapshot,
)
from src.services.strategy_engine import STRATEGY_HANDLERS
from src.services.strategy_registry import normalize_strategy_params, strategy_data_requirements
from src.services.stateless_signal_kernel import vectorized_stateless_candidates


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

    def test_downsample_handles_small_odd_even_limits_and_ties(self) -> None:
        rows = [
            {"ts": f"2025-01-{index + 1:02d}", "equity": float(value)}
            for index, value in enumerate([100, 90, 90, 110, 110, 95, 120])
        ]
        for max_points in (2, 3, 4, 5, 6):
            with self.subTest(max_points=max_points):
                sampled = _downsample_snapshots(rows, max_points=max_points)
                self.assertLessEqual(len(sampled), max_points)
                self.assertEqual(sampled[0], rows[0])
                self.assertEqual(sampled[-1], rows[-1])
                self.assertEqual(
                    [item["ts"] for item in sampled],
                    sorted(item["ts"] for item in sampled),
                )

    def test_downsample_returns_short_inputs_unchanged(self) -> None:
        self.assertEqual(_downsample_snapshots([], max_points=3), [])
        one = [{"ts": "2025-01-01", "equity": 100.0}]
        self.assertEqual(_downsample_snapshots(one, max_points=3), one)
        two = [*one, {"ts": "2025-01-02", "equity": 101.0}]
        self.assertEqual(_downsample_snapshots(two, max_points=3), two)

    def test_database_downsample_queries_share_the_deterministic_selector(self) -> None:
        ids_sql = str(build_downsampled_snapshot_ids_query()).upper()
        chart_sql = str(build_downsampled_chart_query()).upper()
        for sql in (ids_sql, chart_sql):
            self.assertIn("ROW_NUMBER() OVER", sql)
            self.assertIn("COUNT(*) OVER", sql)
            self.assertIn("ORDER BY EQUITY ASC, TS ASC", sql)
            self.assertIn("ORDER BY EQUITY DESC, TS DESC", sql)
            self.assertIn("LIMIT GREATEST(:MAX_POINTS - 2, 0)", sql)
        self.assertNotIn("POSITIONS", chart_sql)

    def test_available_details_distinguish_unpersisted_from_empty(self) -> None:
        self.assertEqual(_available_details("summary"), ["summary", "equity"])
        self.assertIn("transactions", _available_details("trades"))
        self.assertIn("signals", _available_details("full"))

    def test_engine_performance_has_non_overlapping_phases_and_throughput(self) -> None:
        performance = {
            "sql_execute_ms": 10.0,
            "sql_fetch_ms": 20.0,
            "row_decode_ms": 10.0,
            "day_grouping_ms": 10.0,
            "history_state_ms": 5.0,
            "signal_generation_ms": 10.0,
            "execution_simulation_ms": 5.0,
            "detail_build_ms": 5.0,
            "persist_details_ms": 10.0,
            "persist_summary_ms": 5.0,
            "response_serialization_ms": 5.0,
            "rows_loaded": 100,
            "trading_days": 10,
            "signals_generated": 4,
            "trades_generated": 2,
        }
        _finalize_engine_performance(
            performance,
            engine_total_ms=200.0,
            setup_wall_ms=60.0,
            loop_wall_ms=100.0,
            finalization_wall_ms=40.0,
            streaming_data=True,
        )

        self.assertAlmostEqual(performance["unaccounted_ms"], 0.0, places=3)
        self.assertEqual(performance["rows_per_second"], 500.0)
        self.assertEqual(performance["microseconds_per_input_row"], 2_000.0)
        self.assertEqual(performance["data_prepare_ms"], 50.0)

    def test_position_state_and_marks_only_touch_open_holdings(self) -> None:
        snapshots = {
            1: {"position": 0.0, "avg_entry_price": None, "close": 12.0},
            2: {"position": 0.0, "avg_entry_price": None, "close": 20.0},
        }
        holdings = {1: 3.0, 3: 2.0}
        _inject_backtest_positions(
            snapshots,
            holdings,
            {1: 10.0, 3: 30.0},
            {},
            {},
            {},
            date(2025, 1, 2),
            1,
        )
        self.assertEqual(snapshots[1]["position"], 3.0)
        self.assertEqual(snapshots[2]["position"], 0.0)

        marks = {1: 11.0, 2: 19.0, 3: 30.0}
        _update_last_marks(holdings, marks, snapshots, {})
        self.assertEqual(marks, {1: 12.0, 3: 30.0})

    def test_stateless_strategy_requirements_have_no_history_window(self) -> None:
        for strategy_type in ("trend", "mean_reversion", "momentum_breakout"):
            self.assertEqual(strategy_data_requirements(strategy_type).history_length, 0)
        self.assertGreater(strategy_data_requirements("double_bottom").history_length, 0)

    def test_vectorized_candidates_keep_positions_and_true_entries_in_stable_order(self) -> None:
        runtime = {
            "strategy_type": "momentum_breakout",
            "params": {
                "signal": {
                    "breakout_buffer_pct": 0.05,
                    "minimum_return_20d": 0.1,
                    "volume_multiplier": 1.2,
                }
            },
        }
        snapshots = {
            "AAA": {"position": 0, "close": 11, "sma_20": 10, "volume": 100, "volume_sma_20": 80, "ret_20d": 0.2},
            "BBB": {"position": 0, "close": 11, "sma_20": 10, "volume": None, "volume_sma_20": 80, "ret_20d": 0.2},
            "CCC": {"position": 5, "close": 10, "volume": None, "volume_sma_20": None, "ret_20d": None},
            "DDD": {"position": 0, "close": 10.2, "sma_20": 10, "volume": 100, "volume_sma_20": 80, "ret_20d": 0.2},
        }
        filtered = vectorized_stateless_candidates(runtime, snapshots)
        self.assertEqual(list(filtered), ["AAA", "CCC"])

    def test_stateless_candidate_kernels_match_shared_handlers(self) -> None:
        ts = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
        cases = {
            "trend": {
                "ENTRY": {"position": 0, "close": 11, "atr_14": 1, "volume": 150, "volume_sma_20": 100, "ema_15": 11, "sma_200": 10, "prev_ema_15": 10, "prev_sma_200": 10},
                "NONE": {"position": 0, "close": 10, "atr_14": 1, "volume": 200, "volume_sma_20": 100, "ema_15": 9, "sma_200": 10, "prev_ema_15": 9, "prev_sma_200": 10},
                "NAN": {"position": 0, "close": 10, "atr_14": 1, "volume": float("nan"), "volume_sma_20": 100, "ema_15": 11, "sma_200": 10, "prev_ema_15": 10, "prev_sma_200": 10},
                "HOLDING": {"position": 2, "avg_entry_price": 10, "close": 8, "atr_14": 1},
            },
            "mean_reversion": {
                "ENTRY": {"position": 0, "close": 10, "zscore_20": -2},
                "NONE": {"position": 0, "close": 10, "zscore_20": 0},
                "NAN": {"position": 0, "close": 10, "zscore_20": float("nan")},
                "HOLDING": {"position": 2, "avg_entry_price": 10, "close": 11, "zscore_20": 0},
            },
            "momentum_breakout": {
                "ENTRY": {"position": 0, "close": 10.2, "sma_20": 10, "ret_20d": 0.1, "volume": 150, "volume_sma_20": 100},
                "NONE": {"position": 0, "close": 10, "sma_20": 10, "ret_20d": 0, "volume": 100, "volume_sma_20": 100},
                "NAN": {"position": 0, "close": float("nan"), "sma_20": 10, "ret_20d": 0.2, "volume": 200, "volume_sma_20": 100},
                "HOLDING": {"position": 2, "avg_entry_price": 10, "close": 12, "sma_20": 10, "ret_20d": 0.2, "volume": 200, "volume_sma_20": 100},
            },
        }
        for strategy_type, snapshots in cases.items():
            with self.subTest(strategy_type=strategy_type):
                runtime = {
                    "strategy_id": "strategy-id",
                    "strategy_type": strategy_type,
                    "params": normalize_strategy_params(strategy_type, {}),
                    "engine_ready": True,
                }
                prepared = {
                    symbol: {**snapshot, "ts": ts}
                    for symbol, snapshot in snapshots.items()
                }
                handler = STRATEGY_HANDLERS[strategy_type]
                self.assertEqual(
                    handler(runtime, prepared),
                    handler(runtime, vectorized_stateless_candidates(runtime, prepared)),
                )

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
            reopened = cache.open(
                {**manifest, "loader_schema_version": PREPARED_DATASET_SCHEMA_VERSION}
            )
            self.assertIsNotNone(reopened)
            self.assertFalse(reopened.flags.writeable)
            self.assertFalse(cache.cleanup(manifest, active_lease_count=1))
            self.assertTrue(cache.cleanup(manifest, active_lease_count=0))

    def test_prepared_dataset_concurrent_build_and_corruption_recovery(self) -> None:
        manifest = {
            "loader_schema_version": "v1",
            "instrument_ids": [1],
            "date_range": ["2025-01-01", "2025-01-02"],
            "feature_set": ["daily_features"],
            "data_fingerprint": "b" * 64,
        }
        dtype = np.dtype([("instrument_id", "<i8"), ("close", "<f8")])
        with tempfile.TemporaryDirectory() as directory:
            cache = PreparedDatasetCache(Path(directory))
            writes = 0

            def writer(array: np.memmap) -> dict[str, object]:
                nonlocal writes
                writes += 1
                array[:] = [(1, 10.0), (1, 11.0)]
                return {"date_offsets": [["2025-01-01", 0, 1], ["2025-01-02", 1, 1]]}

            with ThreadPoolExecutor(max_workers=2) as executor:
                arrays = list(
                    executor.map(
                        lambda _: cache.build(manifest, shape=(2,), dtype=dtype, writer=writer),
                        range(2),
                    )
                )
            self.assertEqual(writes, 1)
            self.assertTrue(all(not array.flags.writeable for array in arrays))
            self.assertEqual(cache.metadata(manifest)["sidecar"]["date_offsets"][1][1], 1)

            data_path = next(Path(directory).glob("*.npy"))
            data_path.write_bytes(b"corrupt")
            rebuilt = cache.build(manifest, shape=(2,), dtype=dtype, writer=writer)
            self.assertEqual(writes, 2)
            self.assertEqual(float(rebuilt[1]["close"]), 11.0)

    def test_prepared_dataset_round_trip_and_active_lease_cleanup(self) -> None:
        manifest = {
            "loader_schema_version": PREPARED_DATASET_SCHEMA_VERSION,
            "instrument_ids": [1],
            "date_range": ["2025-01-01", "2025-01-02"],
            "feature_set": ["daily_features"],
            "data_fingerprint": "c" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = PreparedDatasetCache(Path(directory))

            def writer(array: np.memmap) -> dict[str, object]:
                for index, trade_day in enumerate((date(2025, 1, 1), date(2025, 1, 2))):
                    encode_prepared_snapshot(
                        array,
                        index,
                        {
                            "instrument_id": 1,
                            "symbol": "AAA",
                            "dt_ny": trade_day,
                            "ts": datetime(2025, 1, index + 1, 21, tzinfo=timezone.utc),
                            "close": 10.0 + index,
                            "volume": 100.0,
                        },
                    )
                return {"date_offsets": [["2025-01-01", 0, 1], ["2025-01-02", 1, 1]]}

            array = cache.build(
                manifest,
                shape=(2,),
                dtype=PREPARED_DATASET_DTYPE,
                writer=writer,
            )
            performance = {"row_decode_ms": 0.0, "day_grouping_ms": 0.0}
            days = list(
                PreparedDatasetDayLoader(
                    array,
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 1, 2),
                    performance=performance,
                ).iter_days()
            )
            self.assertEqual([item[0] for item in days], [date(2025, 1, 1), date(2025, 1, 2)])
            self.assertEqual(days[1][1]["AAA"]["close"], 11.0)
            self.assertEqual(days[1][1]["AAA"]["history_sessions"], 2)

            db = MagicMock()
            db.execute.return_value.scalar_one.return_value = 1
            self.assertFalse(cache.cleanup_if_unused(db, manifest))
            db.execute.return_value.scalar_one.return_value = 0
            self.assertTrue(cache.cleanup_if_unused(db, manifest))

    def test_prepared_dataset_failed_fingerprint_build_is_not_published(self) -> None:
        manifest = {
            "loader_schema_version": PREPARED_DATASET_SCHEMA_VERSION,
            "instrument_ids": [1],
            "date_range": ["2025-01-01", "2025-01-01"],
            "feature_set": ["daily_features"],
            "data_fingerprint": "d" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = PreparedDatasetCache(Path(directory))

            def writer(_array: np.memmap) -> None:
                raise PreparedDatasetDataChangedError("fingerprint changed")

            with self.assertRaises(PreparedDatasetDataChangedError):
                cache.build(manifest, shape=(1,), dtype=PREPARED_DATASET_DTYPE, writer=writer)
            self.assertEqual(list(Path(directory).glob("*.npy")), [])
            self.assertEqual(list(Path(directory).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
