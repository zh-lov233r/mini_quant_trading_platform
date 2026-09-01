from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import copy
import math
import unittest

import quant_kernel

from src.services.strategy_engine import STRATEGY_HANDLERS
from src.services.strategy_registry import normalize_strategy_params


class NativeKernelParityTests(unittest.TestCase):
    maxDiff = None

    def _assert_day_parity(
        self,
        strategy_type: str,
        snapshots: dict[str, dict[str, object]],
        *,
        params: dict[str, object] | None = None,
    ) -> None:
        raw_params = copy.deepcopy(params or {})
        raw_params.setdefault("universe", {})["symbols"] = list(snapshots)
        runtime = {
            "strategy_id": "native-parity",
            "strategy_type": strategy_type,
            "params": normalize_strategy_params(strategy_type, raw_params),
            "engine_ready": True,
        }
        python_signals = [
            asdict(signal)
            for signal in STRATEGY_HANDLERS[strategy_type](runtime, snapshots)
        ]
        native_signals = quant_kernel.evaluate_day(runtime, snapshots)
        self._assert_value_equal(native_signals, python_signals)

    def _assert_value_equal(self, actual: object, expected: object) -> None:
        if isinstance(expected, float):
            self.assertIsInstance(actual, (int, float))
            if math.isnan(expected):
                self.assertTrue(math.isnan(float(actual)))
            else:
                self.assertAlmostEqual(float(actual), expected, delta=1e-10)
            return
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict)
            self.assertEqual(set(actual), set(expected))
            for key in expected:
                with self.subTest(field=key):
                    self._assert_value_equal(actual[key], expected[key])
            return
        if isinstance(expected, list):
            self.assertIsInstance(actual, list)
            self.assertEqual(len(actual), len(expected))
            for actual_item, expected_item in zip(actual, expected, strict=True):
                self._assert_value_equal(actual_item, expected_item)
            return
        self.assertEqual(actual, expected)

    def test_native_abi_and_first_batch_catalog(self) -> None:
        self.assertEqual(quant_kernel.KERNEL_VERSION, "cpp-v1")
        self.assertEqual(quant_kernel.ABI_VERSION, 1)
        self.assertTrue(quant_kernel.BUILD_ID)
        self.assertEqual(
            [entry["strategy_type"] for entry in quant_kernel.catalog()],
            ["trend", "mean_reversion", "momentum_breakout"],
        )

    def test_trend_entry_exit_missing_and_stable_universe_order(self) -> None:
        ts = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
        self._assert_day_parity(
            "trend",
            {
                "ZZZ": {
                    "ts": ts,
                    "position": 2,
                    "avg_entry_price": 10,
                    "close": 8,
                    "atr_14": 1,
                },
                "AAA": {
                    "ts": ts,
                    "instrument_id": 42,
                    "position": 0,
                    "close": 11,
                    "atr_14": 1,
                    "volume": 150,
                    "volume_sma_20": 100,
                    "ema_15": 11,
                    "sma_200": 10,
                    "prev_ema_15": 10,
                    "prev_sma_200": 10,
                },
                "NAN": {
                    "ts": ts,
                    "position": 0,
                    "volume": math.nan,
                    "volume_sma_20": 100,
                    "ema_15": 11,
                    "sma_200": 10,
                    "prev_ema_15": 10,
                    "prev_sma_200": 10,
                },
            },
        )

    def test_mean_reversion_position_age_and_entry_exit_paths(self) -> None:
        ts = datetime(2025, 1, 8, 21, tzinfo=timezone.utc)
        recent_bars = [
            {"dt_ny": date(2025, 1, day)}
            for day in (2, 3, 6, 7, 8)
        ]
        self._assert_day_parity(
            "mean_reversion",
            {
                "ENTRY": {
                    "ts": ts,
                    "position": 0,
                    "close": 10,
                    "zscore_20": -2,
                },
                "EXIT": {
                    "ts": ts,
                    "position": 2,
                    "avg_entry_price": 10,
                    "close": 10,
                    "zscore_20": -1,
                    "entry_trade_date": date(2025, 1, 2),
                    "dt_ny": date(2025, 1, 8),
                    "recent_bars": recent_bars,
                },
                "NAN": {
                    "ts": ts,
                    "position": 0,
                    "close": 10,
                    "zscore_20": math.nan,
                },
            },
            params={"risk": {"max_holding_days": 4}},
        )

    def test_momentum_entry_exit_missing_and_timestamp_normalization(self) -> None:
        self._assert_day_parity(
            "momentum_breakout",
            {
                "ENTRY": {
                    "dt_ny": date(2025, 1, 2),
                    "position": 0,
                    "close": 10.2,
                    "sma_20": 10,
                    "ret_20d": 0.1,
                    "volume": 150,
                    "volume_sma_20": 100,
                },
                "EXIT": {
                    "ts": datetime(2025, 1, 2, 21),
                    "position": 2,
                    "avg_entry_price": 10,
                    "close": 12,
                    "sma_20": 10,
                    "ret_20d": 0.2,
                    "volume": 200,
                    "volume_sma_20": 100,
                },
                "MISSING": {
                    "dt_ny": date(2025, 1, 2),
                    "position": 0,
                    "close": math.nan,
                    "sma_20": 10,
                    "ret_20d": 0.2,
                    "volume": 200,
                    "volume_sma_20": 100,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
