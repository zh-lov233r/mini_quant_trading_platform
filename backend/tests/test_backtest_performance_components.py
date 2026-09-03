from __future__ import annotations

from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from src.services.backtest_engine import (
    _available_details,
    _downsample_snapshots,
    _finalize_engine_performance,
    _load_split_adjustments_by_date,
)
from src.services.backtest_equity_service import (
    build_downsampled_chart_query,
    build_downsampled_snapshot_ids_query,
)
from src.services.native_backtest_service import _load_prepared_dataset
from src.services.prepared_dataset_service import (
    PREPARED_DATASET_SCHEMA_VERSION,
    PreparedDatasetCache,
    PreparedDatasetDataChangedError,
    encode_prepared_snapshot,
)
from src.services.strategy_engine import evaluate_native_signals
from src.services.strategy_registry import normalize_strategy_params, strategy_data_requirements
from backend.utils.benchmark_backtests import (
    _cases,
    _performance_acceptance,
    _planned_write_estimate,
)


class BacktestPerformanceComponentTests(unittest.TestCase):
    def test_native_cold_dataset_build_supplies_loader_performance(self) -> None:
        performance: dict[str, object] = {}
        fingerprint = {
            "sha256": "a" * 64,
            "rowCount": 1,
            "instrumentIds": [1],
            "startDate": "2025-01-02",
            "endDate": "2025-01-02",
            "universePolicy": None,
        }
        cache = MagicMock()
        cache.open.return_value = None
        dataset = SimpleNamespace(sidecar={})

        def build(_manifest, *, row_count, writer):
            self.assertEqual(row_count, 1)
            writer([None])
            return dataset

        cache.build.side_effect = build
        resolved_universe = SimpleNamespace(manifest=lambda: {})

        with (
            patch(
                "src.services.research_experiment_service.calculate_data_fingerprint",
                return_value=fingerprint,
            ),
            patch(
                "src.services.native_backtest_service.PreparedDatasetCache",
                return_value=cache,
            ),
            patch(
                "src.services.native_backtest_service.MarketDataLoader",
                autospec=True,
            ) as loader_class,
            patch("src.services.native_backtest_service.encode_prepared_snapshot"),
            patch(
                "src.services.native_backtest_service._split_adjustments",
                return_value=[],
            ),
        ):
            loader_class.return_value.iter_days.return_value = [
                (
                    date(2025, 1, 2),
                    {"AAA": {"instrument_id": 1, "symbol": "AAA"}},
                )
            ]
            loaded, _manifest, status, materialization = _load_prepared_dataset(
                MagicMock(),
                runtime={"strategy_type": "trend"},
                symbols=["AAA"],
                resolved_universe=resolved_universe,
                start_date=date(2025, 1, 2),
                end_date=date(2025, 1, 2),
                universe_policy=None,
                supplied=None,
                performance=performance,
            )

        self.assertIs(loaded, dataset)
        self.assertEqual(status, "cold")
        self.assertIsNone(materialization)
        self.assertIs(loader_class.call_args.kwargs["performance"], performance)

    def test_benchmark_cases_use_exact_supplied_correctness_session_window(self) -> None:
        start = date(2024, 7, 1)
        end = date(2025, 1, 2)
        cases = _cases("correctness", end, [], correctness_start=start)
        self.assertEqual(len(cases), 9)
        self.assertTrue(all(case.start_date == start for case in cases))
        self.assertTrue(all(case.symbol_count == 20 for case in cases))

    def test_benchmark_plan_reports_complete_write_authorization_scope(self) -> None:
        cases = _cases(
            "plan",
            date(2026, 9, 1),
            [],
            correctness_start=date(2026, 3, 12),
        )

        self.assertEqual(
            _planned_write_estimate(cases),
            {
                "benchmarkDraftStrategies": 1,
                "pythonBaselineStrategyRuns": 105,
                "nativeStrategyRuns": 159,
                "totalStrategyRuns": 264,
            },
        )

    def test_benchmark_acceptance_enforces_speedup_and_peak_rss(self) -> None:
        results = [
            {
                "case": "screening-trend-warm-summary",
                "medianEngineTotalMs": 20.0,
                "maxPeakRssMb": 100.0,
            },
            {
                "case": "confirmation-support_resistance-3640-5y-cold-summary",
                "medianEngineTotalMs": 40.0,
                "maxPeakRssMb": 121.0,
            },
        ]
        baseline = {
            "screening-trend-warm-summary": {
                "medianEngineTotalMs": 100.0,
                "maxPeakRssMb": 100.0,
            },
            "confirmation-support_resistance-3640-5y-cold-summary": {
                "medianEngineTotalMs": 120.0,
                "maxPeakRssMb": 120.0,
            },
        }

        comparisons = _performance_acceptance(results, baseline)

        self.assertTrue(comparisons[0]["passed"])
        self.assertEqual(comparisons[0]["requiredSpeedup"], 5.0)
        self.assertFalse(comparisons[1]["passed"])
        self.assertEqual(comparisons[1]["requiredSpeedup"], 3.0)
        self.assertFalse(comparisons[1]["rssPassed"])

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

    def test_split_adjustments_load_by_stable_instrument_identity(self) -> None:
        db = MagicMock()
        db.execute.return_value.mappings.return_value.all.return_value = [
            {
                "instrument_id": 7,
                "symbol": "NEW",
                "ex_date": date(2025, 1, 3),
                "split_from": 1,
                "split_to": 2,
            }
        ]

        result = _load_split_adjustments_by_date(
            db,
            [],
            date(2025, 1, 1),
            date(2025, 1, 4),
            instrument_ids=[7, 7],
        )

        self.assertEqual(result, {date(2025, 1, 3): {7: 2.0}})
        params = db.execute.call_args.args[1]
        self.assertEqual(params["instrument_ids"], [7])

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

    def test_stateless_strategy_requirements_have_no_history_window(self) -> None:
        for strategy_type in ("trend", "mean_reversion", "momentum_breakout"):
            self.assertEqual(strategy_data_requirements(strategy_type).history_length, 0)
        self.assertGreater(strategy_data_requirements("double_bottom").history_length, 0)

    def test_native_day_kernel_is_the_stateless_signal_entrypoint(self) -> None:
        ts = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
        cases = {
            "trend": {
                "ENTRY": {"position": 0, "close": 11, "atr_14": 1, "volume": 150, "volume_sma_20": 100, "ema_15": 11, "sma_200": 10, "prev_ema_15": 10, "prev_sma_200": 10},
                "NONE": {"position": 0, "close": 10, "atr_14": 1, "volume": 200, "volume_sma_20": 100, "ema_15": 9, "sma_200": 10, "prev_ema_15": 9, "prev_sma_200": 10},
                "HOLDING": {"position": 2, "avg_entry_price": 10, "close": 8, "atr_14": 1},
            },
            "mean_reversion": {
                "ENTRY": {"position": 0, "close": 10, "zscore_20": -2},
                "NONE": {"position": 0, "close": 10, "zscore_20": 0},
                "HOLDING": {"position": 2, "avg_entry_price": 10, "close": 11, "zscore_20": 0},
            },
            "momentum_breakout": {
                "ENTRY": {"position": 0, "close": 10.2, "sma_20": 10, "ret_20d": 0.1, "volume": 150, "volume_sma_20": 100},
                "NONE": {"position": 0, "close": 10, "sma_20": 10, "ret_20d": 0, "volume": 100, "volume_sma_20": 100},
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
                    symbol: {**snapshot, "ts": ts, "asset_type": "CS"}
                    for symbol, snapshot in snapshots.items()
                }
                events = evaluate_native_signals(runtime, prepared)
                self.assertIn("ENTRY", {event.symbol for event in events})
                self.assertTrue(all("strength" in event.metadata for event in events if event.action == "BUY" and float(event.metadata.get("position") or 0) >= 0))

    def test_prepared_dataset_is_read_only_atomic_and_fingerprint_addressed(self) -> None:
        manifest = {
            "instrument_intervals": [[1, "2025-01-01", None]],
            "date_range": ["2025-01-01", "2025-01-03"],
            "feature_set": ["close", "ret_20d"],
            "price_semantics": "forward_adjusted_when_available",
            "data_fingerprint": "a" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = PreparedDatasetCache(Path(directory))

            def writer(array: object) -> None:
                for index in range(3):
                    encode_prepared_snapshot(
                        array,
                        index,
                        {
                            "instrument_id": 1,
                            "symbol": "AAA",
                            "dt_ny": date(2025, 1, index + 1),
                            "close": 10.0 + index,
                        },
                    )

            built = cache.build(manifest, row_count=3, writer=writer)
            self.assertFalse(built.writeable)
            self.assertEqual(built.floats[2, 3], 12.0)
            reopened = cache.open({**manifest, "loader_schema_version": PREPARED_DATASET_SCHEMA_VERSION})
            self.assertIsNotNone(reopened)
            self.assertFalse(reopened.writeable)
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
        with tempfile.TemporaryDirectory() as directory:
            cache = PreparedDatasetCache(Path(directory))
            writes = 0

            def writer(array: object) -> dict[str, object]:
                nonlocal writes
                writes += 1
                for index in range(2):
                    encode_prepared_snapshot(
                        array,
                        index,
                        {
                            "instrument_id": 1,
                            "symbol": "AAA",
                            "dt_ny": date(2025, 1, index + 1),
                            "close": 10.0 + index,
                        },
                    )
                return {"date_offsets": [["2025-01-01", 0, 1], ["2025-01-02", 1, 1]]}

            with ThreadPoolExecutor(max_workers=2) as executor:
                arrays = list(
                    executor.map(
                        lambda _: cache.build(manifest, row_count=2, writer=writer),
                        range(2),
                    )
                )
            self.assertEqual(writes, 1)
            self.assertTrue(all(not array.writeable for array in arrays))
            self.assertEqual(cache.metadata(manifest)["sidecar"]["date_offsets"][1][1], 1)

            data_path = next(Path(directory).glob("*.v3/integers.npy"))
            data_path.write_bytes(b"corrupt")
            rebuilt = cache.build(manifest, row_count=2, writer=writer)
            self.assertEqual(writes, 2)
            self.assertEqual(float(rebuilt.floats[1, 3]), 11.0)

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
                row_count=2,
                writer=writer,
            )
            self.assertTrue(np.isfortran(array.integers))
            self.assertTrue(np.isfortran(array.floats))
            self.assertEqual(array.sidecar["symbols"], ["AAA"])
            self.assertTrue(np.isnan(array.floats[0]).sum() > 0)
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

            def writer(_array: object) -> None:
                raise PreparedDatasetDataChangedError("fingerprint changed")

            with self.assertRaises(PreparedDatasetDataChangedError):
                cache.build(manifest, row_count=1, writer=writer)
            self.assertEqual(list(Path(directory).glob("*.v3")), [])


if __name__ == "__main__":
    unittest.main()
