from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import copy
import json
import math
from datetime import timedelta
import unittest

import quant_kernel

from src.services.strategy_engine import _prepared_day_input, evaluate_native_signals
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
        fallback_timestamp = datetime(2025, 1, 2, 21, tzinfo=timezone.utc)
        for snapshot in snapshots.values():
            snapshot.setdefault("ts", fallback_timestamp)
        raw_params = copy.deepcopy(params or {})
        raw_params.setdefault("universe", {})["symbols"] = list(snapshots)
        runtime = {
            "strategy_id": "native-parity",
            "strategy_type": strategy_type,
            "params": normalize_strategy_params(strategy_type, raw_params),
            "engine_ready": True,
        }
        python_signals = [
            asdict(signal) for signal in evaluate_native_signals(runtime, snapshots)
        ]
        dataset, portfolio_state = _prepared_day_input(snapshots)
        day_result = quant_kernel.evaluate_day(dataset, runtime, portfolio_state)
        columns = day_result.signals
        symbols = list(day_result.symbols)
        native_signals = [
            {
                "strategy_id": str(columns["strategy_id"][index]),
                "ts": datetime.fromtimestamp(
                    int(columns["timestamp_us"][index]) / 1_000_000,
                    tz=timezone.utc,
                ),
                "symbol": symbols[int(columns["symbol_id"][index])],
                "action": "BUY" if int(columns["action"][index]) == 1 else "SELL",
                "reason": str(columns["reason"][index]),
                "score": (
                    None
                    if math.isnan(float(columns["score"][index]))
                    else float(columns["score"][index])
                ),
                "metadata": json.loads(columns["metadata_json"][index]),
                "instrument_id": (
                    None
                    if int(columns["instrument_id"][index]) < 0
                    else int(columns["instrument_id"][index])
                ),
            }
            for index in range(len(day_result))
        ]
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

    def test_native_abi_and_all_engine_ready_strategy_catalog(self) -> None:
        self.assertEqual(quant_kernel.KERNEL_VERSION, "cpp-v1")
        self.assertEqual(quant_kernel.ABI_VERSION, 2)
        self.assertTrue(quant_kernel.BUILD_ID)
        self.assertEqual(
            [entry["strategy_type"] for entry in quant_kernel.catalog()],
            [
                "trend",
                "mean_reversion",
                "momentum_breakout",
                "island_reversal",
                "double_bottom",
                "head_shoulders_bottom",
                "rounded_bottom",
                "v_reversal",
                "support_resistance",
            ],
        )

    def test_native_descriptor_exposes_constraints_and_rejects_wrong_types(self) -> None:
        catalog = {item["strategy_type"]: item for item in quant_kernel.catalog()}
        trend_schema = catalog["trend"]["parameter_schema"]
        self.assertEqual(
            trend_schema["properties"]["execution"]["properties"]["timeframe"]["enum"],
            ("1d",),
        )
        strength_schema = trend_schema["properties"]["signal"]["properties"]["min_strength_score"]
        self.assertEqual((strength_schema["minimum"], strength_schema["maximum"]), (0.0, 100.0))
        self.assertFalse(trend_schema["properties"]["risk"]["additionalProperties"])
        mean_schema = catalog["mean_reversion"]["parameter_schema"]
        self.assertEqual(
            mean_schema["properties"]["signal"]["properties"]["lookback_window"]["enum"],
            (5, 10, 20),
        )

        with self.assertRaisesRegex(ValueError, "signal.min_strength_score must be a number"):
            quant_kernel.normalize_strategy("trend", {"signal": {"min_strength_score": True}})
        with self.assertRaisesRegex(ValueError, "risk.max_positions must be an integer"):
            quant_kernel.normalize_strategy("trend", {"risk": {"max_positions": "10"}})

    def test_support_resistance_replay_exit_matches_python(self) -> None:
        bar = self._bar(0, 91, 92, 89, 90, 100, atr=2)
        self._assert_day_parity(
            "support_resistance",
            {
                "TEST": {
                    **bar,
                    "ts": datetime(2025, 1, 1, 21, tzinfo=timezone.utc),
                    "position": 1.0,
                    "avg_entry_price": 100.0,
                    "position_holding_days": 1,
                    "entry_signal_features": {
                        "support_resistance": {
                            "entry_close": 100.0,
                            "entry_atr": 2.0,
                            "target_price": 106.0,
                            "zone": {"lower": 95.0},
                        }
                    },
                    "recent_bars": [bar],
                }
            },
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

    def test_island_reversal_three_stages_match_python(self) -> None:
        params = {"signal": {"downtrend_lookback": 3}}
        bars = [
            self._bar(0, 121, 122, 119, 120, 100),
            self._bar(1, 116, 117, 113, 114, 100),
            self._bar(2, 109, 110, 99, 100, 100),
            self._bar(3, 95, 96, 92, 93, 70),
            self._bar(4, 99, 104, 98, 102, 160),
            self._bar(5, 101, 102, 98, 99, 100),
        ]
        for end in (4, 5, 6):
            with self.subTest(stage=end):
                snapshot = self._pattern_snapshot(bars[:end])
                self._assert_day_parity("island_reversal", {"TEST": snapshot}, params=params)

    def test_head_shoulders_three_stages_match_python(self) -> None:
        params = {
            "signal": {
                "platform_bars": 3,
                "pivot_left_bars": 1,
                "pivot_right_bars": 1,
                "downtrend_lookback": 2,
                "min_segment_bars": 2,
                "max_segment_bars": 10,
            }
        }
        bars = [
            self._bar(0, 15, 16, 14, 15, 100),
            self._bar(1, 13, 14, 12, 13, 100),
            self._bar(2, 10.5, 11, 10, 10, 80),
            self._bar(3, 11, 13, 11, 12, 100),
            self._bar(4, 9, 10, 8, 8.5, 50),
            self._bar(5, 11.5, 13.5, 11, 12, 100),
            self._bar(6, 10.5, 11, 10.2, 10.4, 70),
            self._bar(7, 11, 12, 10.5, 11.5, 100),
            self._bar(8, 14.5, 16, 14, 15.5, 160),
        ]
        for end in (6, 8, 9):
            with self.subTest(stage=end):
                self._assert_day_parity(
                    "head_shoulders_bottom",
                    {"TEST": self._pattern_snapshot(bars[:end])},
                    params=params,
                )

    def test_double_bottom_three_stages_match_python(self) -> None:
        signal = {
            "downtrend_lookback": 3,
            "downtrend_min_drop_pct": 0.15,
            "downtrend_max_up_day_ratio": 0.35,
            "downtrend_min_r_squared": 0.65,
            "min_bottom_spacing": 2,
            "max_bottom_spacing": 6,
            "left_bottom_before_bars": 1,
            "left_bottom_after_bars": 1,
            "bottom_tolerance_pct": 0.03,
            "neckline_min_rebound_pct": 0.05,
            "rebound_up_day_ratio_min": 0.5,
            "second_bottom_volume_ratio_max": 1.0,
            "breakout_volume_ratio_min": 1.2,
            "max_breakout_bars_after_right_bottom": 4,
            "breakout_buffer_pct": 0.005,
            "retest_window": 5,
            "retest_volume_ratio_max": 0.8,
            "support_tolerance_pct": 0.02,
        }
        base = [
            self._bar(0, 120, 121, 119, 120, 100),
            self._bar(1, 116, 117, 114, 115, 100),
            self._bar(2, 111, 112, 108, 110, 100),
            self._bar(3, 101, 102, 98, 100, 80),
            self._bar(4, 101, 108, 100, 106, 110),
            self._bar(5, 107, 112, 105, 109, 120),
            self._bar(6, 108, 109, 104, 106, 90),
            self._bar(7, 101, 102, 99, 100, 70),
        ]
        cases = [
            base + [self._bar(8, 102, 105, 101, 104, 90)],
            base
            + [
                self._bar(8, 102, 105, 101, 104, 90),
                self._bar(9, 105, 109, 104, 108, 110),
                self._bar(10, 108, 110, 107, 109, 110),
                self._bar(11, 109, 110, 105, 107, 70),
            ],
            base + [self._bar(8, 103, 115, 103, 114, 160)],
        ]
        for index, bars in enumerate(cases, start=1):
            with self.subTest(stage=index):
                self._assert_day_parity(
                    "double_bottom",
                    {"TEST": self._pattern_snapshot(bars)},
                    params={"signal": signal},
                )

    def test_v_reversal_three_stages_match_python(self) -> None:
        bars = []
        for index in range(60):
            close = 130.0 - index * 0.5
            bars.append(self._bar(index, close + 1, close + 2, close - 1, close, 100, atr=3))
        bars.extend(
            [
                self._bar(60, 91, 96, 90, 95, 220, atr=3),
                self._bar(61, 95, 97, 94, 96, 120, atr=3),
                self._bar(62, 96, 98, 95, 97, 120, atr=3),
                self._bar(63, 97.5, 98, 96, 97, 100, atr=3),
                self._bar(64, 97, 98.5, 96.5, 97.5, 100, atr=3),
                self._bar(65, 97.5, 98, 96, 97, 100, atr=3),
                self._bar(66, 99, 101, 98.5, 100, 160, atr=3),
                self._bar(67, 100, 101, 99, 99.5, 100, atr=3),
            ]
        )
        for end in (61, 63, 68):
            with self.subTest(stage=end):
                self._assert_day_parity(
                    "v_reversal",
                    {"TEST": self._pattern_snapshot(bars[:end])},
                )

    def test_rounded_bottom_three_stages_match_python(self) -> None:
        params = {
            "signal": {"min_lookback": 80, "max_lookback": 120, "min_r_squared": 0.70}
        }
        log_bottom = math.log(80)
        curvature = (math.log(110) - log_bottom) / 0.25
        bars = []
        for index in range(101):
            x = index / 100
            close = math.exp(log_bottom + curvature * (x - 0.5) ** 2)
            low = close * 0.99
            high = close * 1.01
            volume = 100.0
            if index in (85, 92):
                low = close * 0.94
                volume = 70.0
            if index in (83, 90):
                volume = 140.0
            if index == 100:
                close, high, low, volume = 113.0, 114.0, 111.0, 170.0
            bars.append(self._bar(index, close * 0.995, high, low, close, volume))
        for end in (88, 95, 101):
            with self.subTest(stage=end):
                self._assert_day_parity(
                    "rounded_bottom",
                    {"TEST": self._pattern_snapshot(bars[:end])},
                    params=params,
                )

    @staticmethod
    def _bar(
        offset: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        *,
        atr: float = 2.0,
    ) -> dict[str, object]:
        return {
            "dt_ny": date(2025, 1, 1) + timedelta(days=offset),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "volume_sma_20": 100.0,
            "atr_14": atr,
        }

    @staticmethod
    def _pattern_snapshot(bars: list[dict[str, object]]) -> dict[str, object]:
        return {
            **bars[-1],
            "ts": datetime(2025, 8, 1, 20, tzinfo=timezone.utc),
            "position": 0.0,
            "avg_entry_price": None,
            "entry_signal_features": None,
            "recent_bars": bars,
        }


if __name__ == "__main__":
    unittest.main()
