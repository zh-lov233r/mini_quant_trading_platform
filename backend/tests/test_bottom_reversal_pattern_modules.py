from __future__ import annotations

import inspect
import unittest
from datetime import date, timedelta

from src.services.patterns import (
    double_bottom,
    head_shoulders_bottom,
    island_reversal,
    rounded_bottom,
    v_reversal,
)
from src.services.patterns.models import PatternContext
from src.services.strategy_registry import normalize_strategy_params


class BottomReversalPatternModuleTests(unittest.TestCase):
    def test_all_five_patterns_have_independent_pure_evaluators(self) -> None:
        modules = (
            island_reversal,
            double_bottom,
            head_shoulders_bottom,
            rounded_bottom,
            v_reversal,
        )
        for module in modules:
            self.assertTrue(callable(module.evaluate))
            source = inspect.getsource(module)
            self.assertNotIn("sqlalchemy", source)
            self.assertNotIn("datetime.now", source)

    def test_island_reversal_full_lifecycle_is_causal_and_stable(self) -> None:
        params = normalize_strategy_params(
            "island_reversal",
            {"signal": {"downtrend_lookback": 3}},
        )
        bars = self._island_bars()
        decisions = [
            island_reversal.evaluate(self._context("island_reversal", bars[:end], params))
            for end in (4, 5, 6)
        ]
        self.assertEqual(
            [decision.setup["stage_key"] if decision else None for decision in decisions],
            ["exhaustion_gap", "upside_gap", "gap_retest"],
        )
        self.assertEqual(
            [decision.setup["stage_target_pct"] for decision in decisions if decision],
            [0.2, 0.5, 1.0],
        )
        self.assertEqual(len({decision.setup["setup_id"] for decision in decisions if decision}), 1)

        # A missing breakout volume input cannot be treated as confirming volume.
        missing_volume = [dict(bar) for bar in bars[:5]]
        missing_volume[-1]["volume_sma_20"] = None
        self.assertIsNone(
            island_reversal.evaluate(self._context("island_reversal", missing_volume, params))
        )

        # Trading-session indexes, rather than calendar-day distance, govern the stages.
        missing_session_dates = [dict(bar) for bar in bars]
        missing_session_dates[4]["dt_ny"] += timedelta(days=4)
        missing_session_dates[5]["dt_ny"] += timedelta(days=4)
        decision = island_reversal.evaluate(
            self._context("island_reversal", missing_session_dates, params)
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.setup["stage_key"], "gap_retest")

    def test_island_invalidation_exits_the_whole_pattern_position(self) -> None:
        params = normalize_strategy_params(
            "island_reversal",
            {"signal": {"downtrend_lookback": 3}},
        )
        bars = self._island_bars()
        entry = island_reversal.evaluate(self._context("island_reversal", bars, params))
        self.assertIsNotNone(entry)
        assert entry is not None
        failed = bars + [self._bar(10, 92, 93, 89, 90, 100)]
        exit_decision = island_reversal.evaluate(
            self._context(
                "island_reversal",
                failed,
                params,
                position=25,
                avg_entry_price=99,
                entry_signal_features={"setup": entry.setup},
            )
        )
        self.assertIsNotNone(exit_decision)
        assert exit_decision is not None
        self.assertEqual(exit_decision.action, "SELL")
        self.assertEqual(exit_decision.setup["exit_stage"], "pattern_invalidation")

    def test_head_shoulders_three_stage_golden_path_and_false_breakout(self) -> None:
        params = normalize_strategy_params(
            "head_shoulders_bottom",
            {
                "signal": {
                    "pivot_left_bars": 1,
                    "pivot_right_bars": 1,
                    "downtrend_lookback": 2,
                    "min_segment_bars": 2,
                    "max_segment_bars": 10,
                }
            },
        )
        bars = self._head_shoulders_bars()
        decisions = [
            head_shoulders_bottom.evaluate(
                self._context("head_shoulders_bottom", bars[:end], params)
            )
            for end in (6, 8, 9)
        ]
        self.assertEqual(
            [decision.setup["stage_key"] if decision else None for decision in decisions],
            ["head_candidate", "right_shoulder", "neckline_breakout"],
        )
        self.assertEqual(len({decision.setup["setup_id"] for decision in decisions if decision}), 1)
        false_breakout = [dict(bar) for bar in bars]
        false_breakout[-1]["volume"] = 100
        self.assertIsNone(
            head_shoulders_bottom.evaluate(
                self._context("head_shoulders_bottom", false_breakout, params)
            )
        )

    def test_v_reversal_three_stage_golden_path_and_bearish_failure(self) -> None:
        params = normalize_strategy_params("v_reversal", {})
        bars = self._v_bars()
        stage_one = v_reversal.evaluate(self._context("v_reversal", bars[:61], params))
        stage_two = v_reversal.evaluate(self._context("v_reversal", bars[:63], params))
        stage_three = v_reversal.evaluate(self._context("v_reversal", bars, params))
        self.assertEqual(stage_one.setup["stage_key"], "volume_pivot")
        self.assertEqual(stage_two.setup["stage_key"], "continuation")
        self.assertEqual(stage_three.setup["stage_key"], "top_breakout_retest")
        self.assertEqual(
            len({stage_one.setup["setup_id"], stage_two.setup["setup_id"], stage_three.setup["setup_id"]}),
            1,
        )

        bearish = bars[:63] + [self._bar(80, 96, 97, 91, 92, 220, atr=3)]
        exit_decision = v_reversal.evaluate(
            self._context(
                "v_reversal",
                bearish,
                params,
                position=10,
                avg_entry_price=95,
                entry_signal_features={"setup": stage_two.setup},
            )
        )
        self.assertIsNotNone(exit_decision)
        self.assertEqual(exit_decision.action, "SELL")
        self.assertEqual(exit_decision.setup["exit_stage"], "bearish_volume_failure")

    @staticmethod
    def _context(
        pattern_type: str,
        bars: list[dict],
        params: dict,
        *,
        position: float = 0,
        avg_entry_price: float | None = None,
        entry_signal_features: dict | None = None,
    ) -> PatternContext:
        return PatternContext(
            symbol="TEST",
            bars=bars,
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=position,
            avg_entry_price=avg_entry_price,
            entry_signal_features=entry_signal_features,
        )

    @classmethod
    def _island_bars(cls) -> list[dict]:
        return [
            cls._bar(0, 121, 122, 119, 120, 100),
            cls._bar(1, 116, 117, 113, 114, 100),
            cls._bar(2, 109, 110, 99, 100, 100),
            cls._bar(3, 95, 96, 92, 93, 70),
            cls._bar(4, 99, 104, 98, 102, 160),
            cls._bar(5, 101, 102, 98, 99, 100),
        ]

    @classmethod
    def _head_shoulders_bars(cls) -> list[dict]:
        return [
            cls._bar(0, 15, 16, 14, 15, 100),
            cls._bar(1, 13, 14, 12, 13, 100),
            cls._bar(2, 10.5, 11, 10, 10, 80),
            cls._bar(3, 11, 13, 11, 12, 100),
            cls._bar(4, 9, 10, 8, 8.5, 50),
            cls._bar(5, 11.5, 13.5, 11, 12, 100),
            cls._bar(6, 10.5, 11, 10.2, 10.4, 70),
            cls._bar(7, 11, 12, 10.5, 11.5, 100),
            cls._bar(8, 14.5, 16, 14, 15.5, 160),
        ]

    @classmethod
    def _v_bars(cls) -> list[dict]:
        bars = []
        for idx in range(60):
            close = 130.0 - idx * 0.5
            bars.append(cls._bar(idx, close + 1, close + 2, close - 1, close, 100, atr=3))
        bars.extend(
            [
                cls._bar(60, 91, 96, 90, 95, 220, atr=3),
                cls._bar(61, 95, 97, 94, 96, 120, atr=3),
                cls._bar(62, 96, 98, 95, 97, 120, atr=3),
                cls._bar(63, 97.5, 98, 96, 97, 100, atr=3),
                cls._bar(64, 97, 98.5, 96.5, 97.5, 100, atr=3),
                cls._bar(65, 97.5, 98, 96, 97, 100, atr=3),
                cls._bar(66, 99, 101, 98.5, 100, 160, atr=3),
                cls._bar(67, 100, 101, 99, 99.5, 100, atr=3),
            ]
        )
        return bars

    @staticmethod
    def _bar(
        offset: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        *,
        atr: float = 2,
    ) -> dict:
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


if __name__ == "__main__":
    unittest.main()
