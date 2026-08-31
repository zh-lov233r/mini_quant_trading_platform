from __future__ import annotations

from datetime import datetime, timezone
from datetime import date
from types import SimpleNamespace
import unittest

from src.services.backtest_engine import BacktestCostConfig, _apply_buy_signals
from src.api.backtests import _serialize_signal
from src.services.signal_strength_service import (
    SignalStrengthError,
    annotate_and_rank_signals,
    evaluate_signal_strength,
    evaluate_support_resistance_strength,
    fall_score,
    passes_strength_threshold,
    rise_score,
    strength_level,
)
from src.services.strategy_engine import SignalEvent
from src.services.strategy_registry import normalize_strategy_params


class SignalStrengthServiceTests(unittest.TestCase):
    def test_all_engine_ready_strategies_default_and_validate_threshold(self) -> None:
        strategy_types = (
            "trend",
            "mean_reversion",
            "momentum_breakout",
            "island_reversal",
            "double_bottom",
            "support_resistance",
        )
        for strategy_type in strategy_types:
            with self.subTest(strategy_type=strategy_type):
                self.assertEqual(
                    50.0,
                    normalize_strategy_params(strategy_type, {})["signal"]["min_strength_score"],
                )
                self.assertEqual(
                    0.0,
                    normalize_strategy_params(
                        strategy_type,
                        {"signal": {"min_strength_score": 0}},
                    )["signal"]["min_strength_score"],
                )
                with self.assertRaises(ValueError):
                    normalize_strategy_params(
                        strategy_type,
                        {"signal": {"min_strength_score": 100.01}},
                    )

    def test_normalizers_and_levels_have_exact_boundaries(self) -> None:
        self.assertEqual(0.0, rise_score(1, 1, 2))
        self.assertEqual(50.0, rise_score(1.5, 1, 2))
        self.assertEqual(100.0, rise_score(3, 1, 2))
        self.assertEqual(0.0, fall_score(0.8, 0.8, 0))
        self.assertEqual(50.0, fall_score(0.4, 0.8, 0))
        self.assertEqual(100.0, fall_score(-1, 0.8, 0))
        self.assertEqual("weak", strength_level(49.99))
        self.assertEqual("medium", strength_level(50))
        self.assertEqual("strong", strength_level(70))
        self.assertEqual("very_strong", strength_level(85))

    def test_all_strategy_formulas_return_expected_midpoint(self) -> None:
        cases = [
            (
                "trend",
                {"strength_inputs": {"separation_atr": 0.25, "crossover_impulse_atr": 0.25, "volume_ratio": 2.25}},
            ),
            ("mean_reversion", {"strength_inputs": {"absolute_zscore": 3.0}}),
            (
                "momentum_breakout",
                {"strength_inputs": {"return_20d": 0.15, "price_extension": 0.03, "volume_ratio": 2.25}},
            ),
            (
                "island_reversal",
                {"strength_inputs": {"stage": "breakout", "left_gap_pct": 0.03, "right_gap_pct": 0.03, "breakout_volume_ratio": 2.25}},
            ),
            (
                "island_reversal",
                {"strength_inputs": {"stage": "retest", "left_gap_pct": 0.03, "right_gap_pct": 0.03, "breakout_volume_ratio": 2.25, "retest_volume_ratio": 0.35, "hold_margin_atr": 0.5}},
            ),
            (
                "double_bottom",
                {"strength_inputs": {"bottom_distance_pct": 0.015, "rebound_up_day_ratio": 0.8, "breakout_volume_ratio": 2.25, "breakout_extension_pct": 0.0075, "retest_volume_ratio": 0.4}},
            ),
        ]
        for strategy_type, metadata in cases:
            with self.subTest(strategy_type=strategy_type, stage=metadata["strength_inputs"].get("stage")):
                params = normalize_strategy_params(strategy_type, {})
                strength = evaluate_signal_strength(strategy_type, params["signal"], params["risk"], metadata)
                self.assertEqual(50.0, strength["score"])
                self.assertEqual("medium", strength["level"])
                self.assertTrue(strength["passes_threshold"])

        support_params = normalize_strategy_params("support_resistance", {})
        support_cases = [
            (
                "support_bounce",
                {"confirmation_atr": 0.375, "reward_risk": 2.25},
                [0.70, 0.30],
            ),
            (
                "resistance_breakout",
                {"confirmation_atr": 0.75, "volume_ratio": 2.25, "reward_risk": 2.25},
                [0.45, 0.35, 0.20],
            ),
            (
                "breakout_retest",
                {"hold_margin_atr": 0.125, "retest_volume_ratio": 0.4, "reward_risk": 2.25},
                [0.35, 0.35, 0.30],
            ),
        ]
        for setup, strength_inputs, expected_weights in support_cases:
            with self.subTest(setup=setup):
                strength = evaluate_support_resistance_strength(
                    support_params["signal"],
                    support_params["risk"],
                    {
                        "setup": setup,
                        "reward_risk": 2.25,
                        "strength_inputs": strength_inputs,
                    },
                )
                self.assertEqual(50.0, strength["score"])
                self.assertEqual(expected_weights, [item["weight"] for item in strength["components"]])

    def test_ranking_is_strength_first_then_instrument_and_symbol(self) -> None:
        params = normalize_strategy_params("mean_reversion", {})
        runtime = {"strategy_type": "mean_reversion", "params": params}
        events = [
            self._event("ZZZ", 3.0, 3),
            self._event("BBB", 3.6, 2),
            self._event("AAA", 3.6, 1),
        ]

        annotate_and_rank_signals(runtime, events)

        ranks = {event.symbol: event.metadata["strength"]["rank"] for event in events}
        self.assertEqual({"AAA": 1, "BBB": 2, "ZZZ": 3}, ranks)
        self.assertEqual(50.0, events[0].metadata["strength"]["score"])
        self.assertEqual(80.0, events[1].metadata["strength"]["score"])

    def test_ranking_resets_each_new_york_trade_date(self) -> None:
        params = normalize_strategy_params("mean_reversion", {})
        runtime = {"strategy_type": "mean_reversion", "params": params}
        first_day = self._event("AAA", 3.6, 1)
        next_day = self._event("BBB", 3.0, 2)
        next_day.ts = datetime(2026, 1, 3, 2, 0, tzinfo=timezone.utc)

        annotate_and_rank_signals(runtime, [first_day, next_day])

        self.assertEqual(1, first_day.metadata["strength"]["rank"])
        self.assertEqual(1, next_day.metadata["strength"]["rank"])

    def test_exit_signals_do_not_require_or_apply_entry_threshold(self) -> None:
        sell = SignalEvent(
            strategy_id="strategy",
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol="AAPL",
            action="SELL",
            reason="exit",
        )
        cover = SignalEvent(
            strategy_id="strategy",
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol="MSFT",
            action="BUY",
            reason="future short cover",
            metadata={"position": -1},
        )
        self.assertTrue(passes_strength_threshold(sell))
        self.assertTrue(passes_strength_threshold(cover))

    def test_missing_required_measurement_fails_closed(self) -> None:
        params = normalize_strategy_params("trend", {})
        event = SignalEvent(
            strategy_id="strategy",
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol="AAPL",
            action="BUY",
            reason="missing ATR measurements",
            metadata={"position": 0, "strength_inputs": {}},
        )
        with self.assertRaises(SignalStrengthError):
            annotate_and_rank_signals({"strategy_type": "trend", "params": params}, [event])

    def test_backtest_skips_missing_top_rank_and_below_threshold_then_fills_next(self) -> None:
        params = normalize_strategy_params("mean_reversion", {})
        runtime = {"strategy_type": "mean_reversion", "params": params}
        events = [
            self._event("NO_OPEN", 3.6, 1),
            self._event("MID", 3.0, 2),
            self._event("WEAK", 2.5, 3),
        ]
        annotate_and_rank_signals(runtime, events)
        holdings: dict[str, float] = {}
        cash_ref = {"cash": 1_000.0}
        trade_day = date(2026, 1, 5)
        execution_snapshots = {
            "MID": {"symbol": "MID", "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)},
            "WEAK": {"symbol": "WEAK", "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)},
        }

        stats = _apply_buy_signals(
            db=SimpleNamespace(add=lambda _value: None),
            strategy=SimpleNamespace(id="strategy"),
            run=SimpleNamespace(id="run"),
            signals=events,
            holdings=holdings,
            avg_entry_prices={},
            entry_trade_dates={},
            entry_day_indices={},
            entry_signal_features={},
            execution_prices={"MID": 10.0, "WEAK": 10.0},
            execution_snapshots=execution_snapshots,
            cash_ref=cash_ref,
            equity_before=1_000.0,
            max_positions=1,
            position_size_pct=0.5,
            cost_config=BacktestCostConfig(commission_bps=0.0, commission_min=0.0, slippage_bps=0.0),
            trade_day=trade_day,
            trade_day_index=1,
            persist_transactions=False,
        )

        self.assertEqual(1, stats.trade_count)
        self.assertEqual(["MID"], list(holdings))
        self.assertEqual(2, events[1].metadata["strength"]["rank"])
        self.assertFalse(events[2].metadata["strength"]["passes_threshold"])

    def test_backtest_api_exposes_strength_as_first_class_field(self) -> None:
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
        self.assertEqual(strength, payload["features"]["strength"])

    @staticmethod
    def _event(symbol: str, zscore: float, instrument_id: int) -> SignalEvent:
        return SignalEvent(
            strategy_id="strategy",
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol=symbol,
            action="BUY",
            reason="entry",
            instrument_id=instrument_id,
            metadata={"position": 0, "strength_inputs": {"absolute_zscore": zscore}},
        )


if __name__ == "__main__":
    unittest.main()
