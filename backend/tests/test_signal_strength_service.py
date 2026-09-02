from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from src.api.backtests import _serialize_signal
from src.services.signal_strength_service import (
    SignalStrengthError,
    get_signal_strength,
    ordered_entry_buy_signals,
    passes_strength_threshold,
)
from src.services.strategy_engine import SignalEvent, evaluate_native_signals
from src.services.strategy_registry import normalize_strategy_params


class SignalStrengthServiceTests(unittest.TestCase):
    def test_all_engine_ready_strategies_default_and_validate_threshold(self) -> None:
        for strategy_type in (
            "trend",
            "mean_reversion",
            "momentum_breakout",
            "island_reversal",
            "double_bottom",
            "head_shoulders_bottom",
            "rounded_bottom",
            "v_reversal",
            "support_resistance",
        ):
            self.assertEqual(
                50.0,
                normalize_strategy_params(strategy_type, {})["signal"]["min_strength_score"],
            )
            with self.assertRaises(ValueError):
                normalize_strategy_params(
                    strategy_type,
                    {"signal": {"min_strength_score": 100.01}},
                )

    def test_native_day_scores_and_ranks_by_strength_then_identity(self) -> None:
        runtime = {
            "strategy_id": "strategy",
            "strategy_type": "mean_reversion",
            "params": normalize_strategy_params(
                "mean_reversion",
                {"universe": {"symbols": ["ZZZ", "BBB", "AAA"]}},
            ),
        }
        timestamp = datetime(2026, 1, 2, 21, tzinfo=timezone.utc)
        snapshots = {
            "ZZZ": {"instrument_id": 3, "ts": timestamp, "position": 0, "zscore_20": -3.0},
            "BBB": {"instrument_id": 2, "ts": timestamp, "position": 0, "zscore_20": -3.6},
            "AAA": {"instrument_id": 1, "ts": timestamp, "position": 0, "zscore_20": -3.6},
        }
        events = evaluate_native_signals(runtime, snapshots)
        ranks = {event.symbol: event.metadata["strength"]["rank"] for event in events}
        self.assertEqual({"AAA": 1, "BBB": 2, "ZZZ": 3}, ranks)
        self.assertEqual(["AAA", "BBB", "ZZZ"], [
            event.symbol for event in ordered_entry_buy_signals(events)
        ])
        self.assertTrue(all(passes_strength_threshold(event) for event in events))

    def test_exit_does_not_require_strength_but_entry_does(self) -> None:
        sell = SignalEvent(
            strategy_id="strategy",
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol="AAPL",
            action="SELL",
            reason="exit",
        )
        self.assertTrue(passes_strength_threshold(sell))
        entry = SignalEvent(
            strategy_id="strategy",
            ts=sell.ts,
            symbol="MSFT",
            action="BUY",
            reason="entry",
            metadata={"position": 0},
        )
        with self.assertRaises(SignalStrengthError):
            passes_strength_threshold(entry)

    def test_api_exposes_native_strength_as_first_class_field(self) -> None:
        strength = {"score": 75.0, "level": "strong", "threshold": 50.0}
        signal = SimpleNamespace(
            id="signal",
            run_id="run",
            strategy_id="strategy",
            instrument_id=1,
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol="AAPL",
            signal="BUY",
            score=3.0,
            reason="entry",
            features={"strength": strength},
        )
        payload = _serialize_signal(signal)
        self.assertEqual(strength, payload["strength"])
        self.assertEqual(strength, get_signal_strength(SimpleNamespace(metadata=signal.features)))


if __name__ == "__main__":
    unittest.main()
