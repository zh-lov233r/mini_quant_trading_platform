from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.strategy_engine import (  # noqa: E402
    build_stateful_backtest_signal_state,
    _has_double_bottom_downtrend_context,
    _has_smooth_double_bottom_downtrend,
    _find_latest_double_bottom_pattern,
    generate_stateful_backtest_signals,
    _resolve_double_bottom_action,
)


def _build_bar(
    offset: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> dict[str, float | date]:
    return {
        "dt_ny": date(2025, 1, 1) + timedelta(days=offset),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "volume_sma_20": 100.0,
    }


class DoubleBottomStateMachineTests(unittest.TestCase):
    signal_cfg = {
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
        "retest_window": 3,
        "retest_volume_ratio_max": 0.8,
        "support_tolerance_pct": 0.02,
    }
    risk_cfg = {
        "max_loss_pct": 0.08,
        "take_profit_atr": 3.0,
        "stop_loss_atr": 1.5,
    }

    runtime_strategy = {
        "strategy_id": "test-double-bottom",
        "strategy_type": "double_bottom",
        "params": {
            "signal": signal_cfg,
            "risk": risk_cfg,
            "universe": {
                "symbols": ["TEST"],
                "selection_mode": "manual",
            },
        },
    }

    def test_breakout_bar_waits_for_retest_buy(self) -> None:
        bars = self._primary_pattern_bars()

        pattern = _find_latest_double_bottom_pattern(bars, self.signal_cfg)

        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.left_bottom_idx, 3)
        self.assertEqual(pattern.right_bottom_idx, 7)
        self.assertEqual(pattern.breakout_idx, 8)
        self.assertEqual(
            _resolve_double_bottom_action(
                recent_bars=bars,
                pattern=pattern,
                signal_cfg=self.signal_cfg,
                risk_cfg=self.risk_cfg,
                position=0.0,
            ),
            (None, None, None),
        )

    def test_breakout_pattern_still_supports_low_volume_retest_buy(self) -> None:
        bars = self._primary_pattern_bars()
        bars.append(_build_bar(9, 113, 114, 111, 113, 100))

        pattern = _find_latest_double_bottom_pattern(bars, self.signal_cfg)

        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.breakout_idx, 8)
        self.assertEqual(
            _resolve_double_bottom_action(
                recent_bars=bars,
                pattern=pattern,
                signal_cfg=self.signal_cfg,
                risk_cfg=self.risk_cfg,
                position=0.0,
            ),
            (
                "BUY",
                "low-volume retest held the neckline after the double-bottom breakout",
                "retest",
            ),
        )

    def test_intraday_wick_above_neckline_still_confirms_breakout(self) -> None:
        bars = self._primary_pattern_bars()
        bars[-1] = _build_bar(8, 113, 115, 111, 112, 160)
        bars.append(_build_bar(9, 113, 114, 111, 113, 100))

        pattern = _find_latest_double_bottom_pattern(bars, self.signal_cfg)

        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.breakout_idx, 8)
        self.assertEqual(
            _resolve_double_bottom_action(
                recent_bars=bars,
                pattern=pattern,
                signal_cfg=self.signal_cfg,
                risk_cfg=self.risk_cfg,
                position=0.0,
            ),
            (
                "BUY",
                "low-volume retest held the neckline after the double-bottom breakout",
                "retest",
            ),
        )

    def test_state_machine_prefers_latest_confirmed_breakout(self) -> None:
        bars = self._primary_pattern_bars() + [
            _build_bar(9, 122, 124, 121, 123, 110),
            _build_bar(10, 127, 129, 126, 128, 110),
            _build_bar(11, 119, 120, 117, 118, 105),
            _build_bar(12, 107, 108, 104, 106, 95),
            _build_bar(13, 104, 105, 102, 104, 75),
            _build_bar(14, 106, 110, 105, 108, 90),
            _build_bar(15, 111, 114, 109, 112, 95),
            _build_bar(16, 108, 109, 105, 107, 90),
            _build_bar(17, 104, 105, 103, 104, 70),
            _build_bar(18, 109, 117, 108, 116, 160),
        ]

        pattern = _find_latest_double_bottom_pattern(bars, self.signal_cfg)

        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.left_bottom_idx, 13)
        self.assertEqual(pattern.neckline_idx, 15)
        self.assertEqual(pattern.right_bottom_idx, 17)
        self.assertEqual(pattern.breakout_idx, 18)

    def test_stateful_backtest_runner_waits_for_retest_after_trading_breakout(self) -> None:
        state = build_stateful_backtest_signal_state(self.runtime_strategy)
        self.assertIsNotNone(state)

        for offset, bar in enumerate(self._primary_pattern_bars()[:-1]):
            signals = generate_stateful_backtest_signals(
                self.runtime_strategy,
                {"TEST": self._snapshot_from_bar(bar)},
                state,
                emit_signals=False,
            )
            self.assertEqual(signals, [], msg=f"unexpected warmup signal at offset {offset}")

        breakout_signals = generate_stateful_backtest_signals(
            self.runtime_strategy,
            {"TEST": self._snapshot_from_bar(self._primary_pattern_bars()[-1])},
            state,
            emit_signals=True,
        )

        self.assertEqual(breakout_signals, [])

        retest_signals = generate_stateful_backtest_signals(
            self.runtime_strategy,
            {"TEST": self._snapshot_from_bar(_build_bar(9, 113, 114, 111, 113, 100))},
            state,
            emit_signals=True,
        )

        self.assertEqual(len(retest_signals), 1)
        self.assertEqual(retest_signals[0].action, "BUY")
        self.assertEqual(retest_signals[0].metadata.get("setup", {}).get("stage"), "retest")

    def test_stateful_backtest_runner_supports_retest_after_warmup_breakout(self) -> None:
        state = build_stateful_backtest_signal_state(self.runtime_strategy)
        self.assertIsNotNone(state)

        for bar in self._primary_pattern_bars():
            signals = generate_stateful_backtest_signals(
                self.runtime_strategy,
                {"TEST": self._snapshot_from_bar(bar)},
                state,
                emit_signals=False,
            )
            self.assertEqual(signals, [])

        retest_signals = generate_stateful_backtest_signals(
            self.runtime_strategy,
            {"TEST": self._snapshot_from_bar(_build_bar(9, 113, 114, 111, 113, 100))},
            state,
            emit_signals=True,
        )

        self.assertEqual(len(retest_signals), 1)
        self.assertEqual(retest_signals[0].action, "BUY")
        self.assertEqual(retest_signals[0].metadata.get("setup", {}).get("stage"), "retest")

    def test_double_bottom_downtrend_only_uses_lookback_drop(self) -> None:
        bars = [
            {
                **_build_bar(0, 120, 121, 119, 120),
            },
            {
                **_build_bar(1, 115, 116, 113, 114),
            },
            {
                **_build_bar(2, 109, 110, 106, 108),
            },
            {
                **_build_bar(3, 101, 102, 98, 100),
            },
        ]

        self.assertTrue(
            _has_double_bottom_downtrend_context(
                bars,
                left_bottom_idx=3,
                downtrend_lookback=3,
                min_drop_pct=0.15,
            )
        )

    def test_smooth_downtrend_filter_rejects_choppy_left_side(self) -> None:
        bars = [
            _build_bar(0, 120, 121, 119, 120),
            _build_bar(1, 109, 110, 106, 108),
            _build_bar(2, 119, 120, 117, 118),
            _build_bar(3, 101, 102, 98, 100, 80),
            _build_bar(4, 101, 108, 100, 106, 90),
            _build_bar(5, 107, 112, 105, 109, 95),
            _build_bar(6, 108, 109, 104, 106, 90),
            _build_bar(7, 101, 102, 99, 100, 70),
            _build_bar(8, 103, 115, 103, 114, 160),
        ]

        self.assertTrue(
            _has_double_bottom_downtrend_context(
                bars,
                left_bottom_idx=3,
                downtrend_lookback=3,
                min_drop_pct=0.15,
            )
        )
        self.assertFalse(
            _has_smooth_double_bottom_downtrend(
                bars,
                left_bottom_idx=3,
                downtrend_lookback=3,
                max_up_day_ratio=0.35,
                min_r_squared=0.65,
            )
        )
        self.assertIsNone(_find_latest_double_bottom_pattern(bars, self.signal_cfg))

    @staticmethod
    def _primary_pattern_bars() -> list[dict[str, float | date]]:
        return [
            _build_bar(0, 120, 121, 119, 120),
            _build_bar(1, 116, 117, 114, 115),
            _build_bar(2, 111, 112, 108, 110),
            _build_bar(3, 101, 102, 98, 100, 80),
            _build_bar(4, 101, 108, 100, 106, 90),
            _build_bar(5, 107, 112, 105, 109, 95),
            _build_bar(6, 108, 109, 104, 106, 90),
            _build_bar(7, 101, 102, 99, 100, 70),
            _build_bar(8, 103, 115, 103, 114, 160),
        ]

    @staticmethod
    def _snapshot_from_bar(bar: dict[str, float | date]) -> dict[str, float | date | None]:
        return {
            **bar,
            "position": 0.0,
            "avg_entry_price": None,
            "entry_trade_date": None,
            "entry_signal_features": None,
            "recent_bars": [],
        }


if __name__ == "__main__":
    unittest.main()
