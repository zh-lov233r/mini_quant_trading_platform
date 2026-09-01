from __future__ import annotations

import math
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from src.services.backtest_engine import (
    BacktestCostConfig,
    _apply_buy_signals,
    _apply_sell_signals,
    _apply_split_adjustments,
)
from src.services.patterns.common import confirmed_pivot_lows
from src.services.patterns import head_shoulders_bottom, rounded_bottom, v_reversal
from src.services.patterns.models import PatternContext
from src.services.paper_trading_service import VirtualSubportfolioState, _apply_virtual_fill, _client_order_id
from src.services.staged_entry_service import (
    build_pattern_setup,
    can_apply_staged_entry,
    select_highest_stage_signals,
)
from src.services.strategy_engine import SignalEvent
from src.services.strategy_registry import build_strategy_catalog, normalize_strategy_params
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
)


class StagedBottomReversalTests(unittest.TestCase):
    def test_support_resistance_buy_requires_projected_execution_inside_channel(self) -> None:
        trade_day = date(2026, 1, 6)
        event = SignalEvent(
            "strategy",
            datetime(2026, 1, 5, 21, tzinfo=timezone.utc),
            "TEST",
            "BUY",
            "channel entry",
            metadata={
                "strength": {"score": 100, "passes_threshold": True},
                "support_resistance": {
                    "selected_setup": "support_bounce",
                    "entry_channel": {
                        "valid": True,
                        "lower": 101.0,
                        "upper": 109.0,
                        "lower_slope_per_session": 0.25,
                        "upper_slope_per_session": 0.5,
                        "support_zone_key": "support",
                        "resistance_zone_key": "resistance",
                    },
                },
            },
        )
        state = SupportResistanceState(
            symbols={"TEST": SupportResistanceSymbolState()}
        )
        holdings: dict[str, float] = {}
        common = dict(
            db=SimpleNamespace(add=lambda _value: None),
            strategy=SimpleNamespace(id="strategy"),
            run=SimpleNamespace(id="run"),
            signals=[event],
            holdings=holdings,
            avg_entry_prices={},
            entry_trade_dates={},
            entry_day_indices={},
            entry_signal_features={},
            execution_snapshots={"TEST": {"symbol": "TEST", "dt_ny": trade_day}},
            cash_ref={"cash": 1_000.0},
            equity_before=1_000.0,
            max_positions=1,
            position_size_pct=0.5,
            cost_config=BacktestCostConfig(0, 0, 0),
            trade_day=trade_day,
            trade_day_index=1,
            persist_transactions=False,
            support_resistance_state=state,
        )

        rejected = _apply_buy_signals(execution_prices={"TEST": 109.5001}, **common)
        self.assertEqual(rejected.trade_count, 0)
        self.assertEqual(holdings, {})
        self.assertEqual(state.symbols["TEST"].events[-1]["event_type"], "execution_rejection")

        accepted = _apply_buy_signals(execution_prices={"TEST": 109.5}, **common)
        self.assertEqual(accepted.trade_count, 1)
        self.assertGreater(holdings["TEST"], 0)

    def test_defaults_are_isolated_and_stage_targets_are_validated(self) -> None:
        trend = normalize_strategy_params("trend", {})
        trend["signal"]["min_strength_score"] = 99
        self.assertEqual(normalize_strategy_params("trend", {})["signal"]["min_strength_score"], 50.0)
        first = normalize_strategy_params("rounded_bottom", {})
        first["risk"]["stage_1_target_pct"] = 0.9
        second = normalize_strategy_params("rounded_bottom", {})
        self.assertEqual(second["risk"]["stage_1_target_pct"], 0.2)
        self.assertTrue({"head_shoulders_bottom", "rounded_bottom", "v_reversal"}.issubset(
            {item["strategy_type"] for item in build_strategy_catalog() if item["engine_ready"]}
        ))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            normalize_strategy_params("v_reversal", {"risk": {"stage_1_target_pct": 0.6, "stage_2_target_pct": 0.5}})
        with self.assertRaisesRegex(ValueError, "must equal 1"):
            normalize_strategy_params("v_reversal", {"risk": {"stage_3_target_pct": 0.9}})

    def test_confirmed_pivot_never_uses_missing_future_bars(self) -> None:
        bars = [{"low": value} for value in (5, 4, 3)]
        self.assertEqual(confirmed_pivot_lows(bars, 2, 2), [])
        bars.extend(({"low": 4}, {"low": 5}))
        self.assertEqual(confirmed_pivot_lows(bars, 2, 2), [2])

    def test_v_reversal_emits_standard_stage_one_setup(self) -> None:
        params = normalize_strategy_params("v_reversal", {})
        start = date(2025, 1, 1)
        bars = []
        for index in range(60):
            close = 130.0 - index * 0.5
            bars.append(self._bar(start + timedelta(days=index), close + 1, close + 2, close - 1, close, 100, 3))
        bars.append(self._bar(start + timedelta(days=60), 91, 96, 90, 95, 220, 3))
        decision = self._evaluate_pattern(
            pattern_type="v_reversal",
            symbol="TEST",
            recent_bars=bars,
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.setup["stage_index"], 1)
        self.assertEqual(decision.setup["stage_target_pct"], 0.2)
        self.assertEqual(decision.setup["pattern_type"], "v_reversal")

    def test_head_shoulders_head_candidate_waits_for_right_confirmation(self) -> None:
        params = normalize_strategy_params(
            "head_shoulders_bottom",
            {"signal": {"downtrend_lookback": 2, "min_segment_bars": 3, "max_segment_bars": 10}},
        )
        start = date(2025, 3, 1)
        lows = (14, 12, 10, 11, 12, 10, 8, 9, 10)
        closes = (15, 13, 10, 11, 12, 10, 8.5, 9.5, 10.5)
        bars = [
            self._bar(start + timedelta(days=index), close + 0.5, close + 1, low, close, 50 if index == 6 else 100, 2)
            for index, (low, close) in enumerate(zip(lows, closes, strict=True))
        ]
        before_confirmation = self._evaluate_pattern(
            pattern_type="head_shoulders_bottom",
            symbol="TEST",
            recent_bars=bars[:-1],
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        )
        self.assertIsNone(before_confirmation)
        confirmed = self._evaluate_pattern(
            pattern_type="head_shoulders_bottom",
            symbol="TEST",
            recent_bars=bars,
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        )
        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertEqual(confirmed.setup["stage_key"], "head_candidate")
        self.assertEqual(confirmed.setup["stage_index"], 1)

    def test_rounded_bottom_requires_two_higher_pullbacks_and_breakout_volume(self) -> None:
        params = normalize_strategy_params(
            "rounded_bottom",
            {"signal": {"min_lookback": 80, "max_lookback": 120, "min_r_squared": 0.70}},
        )
        start = date(2025, 1, 1)
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
            bars.append(self._bar(start + timedelta(days=index), close * 0.995, high, low, close, volume, 2))
        first_pullback = self._evaluate_pattern(
            pattern_type="rounded_bottom",
            symbol="TEST",
            recent_bars=bars[:88],
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        )
        second_pullback = self._evaluate_pattern(
            pattern_type="rounded_bottom",
            symbol="TEST",
            recent_bars=bars[:95],
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        )
        decision = self._evaluate_pattern(
            pattern_type="rounded_bottom",
            symbol="TEST",
            recent_bars=bars,
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        )
        self.assertIsNotNone(decision)
        assert first_pullback is not None and second_pullback is not None and decision is not None
        self.assertEqual(first_pullback.setup["stage_key"], "first_right_pullback")
        self.assertEqual(second_pullback.setup["stage_key"], "second_right_pullback")
        self.assertEqual(decision.setup["stage_key"], "rim_breakout")
        self.assertEqual(len(decision.setup["anchors"]["pullbacks"]), 2)
        self.assertEqual(
            len({first_pullback.setup["setup_id"], second_pullback.setup["setup_id"], decision.setup["setup_id"]}),
            1,
        )
        missing_volume = [dict(bar) for bar in bars]
        missing_volume[-1]["volume"] = 100.0
        self.assertIsNone(self._evaluate_pattern(
            pattern_type="rounded_bottom",
            symbol="TEST",
            recent_bars=missing_volume,
            signal_cfg=params["signal"],
            risk_cfg=params["risk"],
            position=0,
            avg_entry_price=None,
            entry_signal_features=None,
        ))

    def test_same_day_selects_highest_stage_for_one_setup(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        events = [self._event(stage, risk) for stage in (1, 3, 2)]
        selected = select_highest_stage_signals(events)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].metadata["setup"]["stage_index"], 3)

    def test_malformed_internal_setup_fails_instead_of_becoming_single_entry(self) -> None:
        with self.assertRaises(KeyError):
            can_apply_staged_entry(
                {"setup": {"pattern_type": "v_reversal"}},
                {"setup": {"setup_id": "v_reversal:TEST:existing", "stage_index": 1}},
            )

    def test_paper_stage_ids_are_distinct_and_virtual_fills_use_weighted_cost(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        first = self._event(1, risk)
        second = self._event(2, risk)
        strategy_id = UUID("00000000-0000-0000-0000-000000000001")
        first_id = _client_order_id(strategy_id, "Default", date(2026, 1, 5), first)
        second_id = _client_order_id(strategy_id, "Default", date(2026, 1, 5), second)
        self.assertNotEqual(first_id, second_id)
        self.assertIn("-s1", first_id)
        self.assertIn("-s2", second_id)
        self.assertLessEqual(len(first_id), 48)

        state = VirtualSubportfolioState(cash=1_000, equity=1_000, gross_exposure=0, net_exposure=0)
        _apply_virtual_fill(state, symbol="TEST", side="BUY", qty=10, price=10, fee=1, trade_date=date(2026, 1, 5), entry_signal_features=first.metadata)
        _apply_virtual_fill(state, symbol="TEST", side="BUY", qty=5, price=16, fee=0, trade_date=date(2026, 1, 6), entry_signal_features=second.metadata)
        self.assertAlmostEqual(state.positions_by_symbol["TEST"].avg_entry_price, 12.0)
        self.assertEqual(state.positions_by_symbol["TEST"].entry_signal_features["setup"]["stage_index"], 2)

    def test_backtest_entries_catch_up_to_cumulative_targets_and_reprice_cost(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        holdings: dict[str, float] = {}
        averages: dict[str, float] = {}
        features: dict[str, dict] = {}
        cash = {"cash": 1_000.0}
        trade_day = date(2026, 1, 5)
        prices = (10.0, 12.0, 8.0)
        for stage, price in enumerate(prices, start=1):
            event = self._event(stage, risk)
            _apply_buy_signals(
                db=SimpleNamespace(add=lambda _value: None),
                strategy=SimpleNamespace(id="strategy"),
                run=SimpleNamespace(id="run"),
                signals=[event],
                holdings=holdings,
                avg_entry_prices=averages,
                entry_trade_dates={},
                entry_day_indices={},
                entry_signal_features=features,
                execution_prices={"TEST": price},
                execution_snapshots={"TEST": {"symbol": "TEST", "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)}},
                cash_ref=cash,
                equity_before=1_000.0,
                max_positions=1,
                position_size_pct=0.5,
                cost_config=BacktestCostConfig(commission_bps=0, commission_min=0, slippage_bps=0),
                trade_day=trade_day,
                trade_day_index=stage,
                persist_transactions=False,
            )
        self.assertAlmostEqual(holdings["TEST"] * prices[-1], 500.0)
        self.assertEqual(features["TEST"]["setup"]["stage_index"], 3)
        self.assertEqual(len(features["TEST"]["entry_history"]), 3)
        self.assertGreater(averages["TEST"], 0)

    def test_backtest_can_enter_directly_at_stage_two_target(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        holdings: dict[str, float] = {}
        cash = {"cash": 1_000.0}
        trade_day = date(2026, 1, 5)
        _apply_buy_signals(
            db=SimpleNamespace(add=lambda _value: None), strategy=SimpleNamespace(id="strategy"), run=SimpleNamespace(id="run"),
            signals=[self._event(2, risk)], holdings=holdings, avg_entry_prices={}, entry_trade_dates={}, entry_day_indices={}, entry_signal_features={},
            execution_prices={"TEST": 10.0}, execution_snapshots={"TEST": {"symbol": "TEST", "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)}},
            cash_ref=cash, equity_before=1_000.0, max_positions=1, position_size_pct=0.5,
            cost_config=BacktestCostConfig(commission_bps=0, commission_min=0, slippage_bps=0), trade_day=trade_day, trade_day_index=1, persist_transactions=False,
        )
        self.assertAlmostEqual(holdings["TEST"], 25.0)

    def test_cash_limited_stage_can_retry_the_same_cumulative_target(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        holdings: dict[str, float] = {}
        features: dict[str, dict] = {}
        cash = {"cash": 50.0}
        trade_day = date(2026, 1, 5)
        common = dict(
            db=SimpleNamespace(add=lambda _value: None), strategy=SimpleNamespace(id="strategy"), run=SimpleNamespace(id="run"),
            holdings=holdings, avg_entry_prices={}, entry_trade_dates={}, entry_day_indices={}, entry_signal_features=features,
            execution_prices={"TEST": 10.0}, execution_snapshots={"TEST": {"symbol": "TEST", "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)}},
            cash_ref=cash, equity_before=1_000.0, max_positions=1, position_size_pct=0.5,
            cost_config=BacktestCostConfig(commission_bps=0, commission_min=0, slippage_bps=0), trade_day=trade_day, persist_transactions=False,
        )
        _apply_buy_signals(signals=[self._event(3, risk)], trade_day_index=1, **common)
        self.assertAlmostEqual(holdings["TEST"], 5.0)
        cash["cash"] = 450.0
        _apply_buy_signals(signals=[self._event(3, risk)], trade_day_index=2, **common)
        self.assertAlmostEqual(holdings["TEST"], 50.0)

    def test_existing_setup_can_add_at_max_positions_but_new_symbol_cannot(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        first = self._event(1, risk)
        holdings = {"TEST": 10.0}
        averages = {"TEST": 10.0}
        features = {"TEST": first.metadata}
        cash = {"cash": 1_000.0}
        trade_day = date(2026, 1, 5)
        common = dict(
            db=SimpleNamespace(add=lambda _value: None), strategy=SimpleNamespace(id="strategy"), run=SimpleNamespace(id="run"),
            holdings=holdings, avg_entry_prices=averages, entry_trade_dates={}, entry_day_indices={}, entry_signal_features=features,
            execution_prices={"TEST": 10.0, "NEW": 10.0},
            execution_snapshots={
                symbol: {"symbol": symbol, "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)}
                for symbol in ("TEST", "NEW")
            },
            cash_ref=cash, equity_before=1_000.0, max_positions=1, position_size_pct=0.5,
            cost_config=BacktestCostConfig(0, 0, 0), trade_day=trade_day, trade_day_index=2, persist_transactions=False,
        )
        _apply_buy_signals(signals=[self._event(2, risk)], **common)
        self.assertAlmostEqual(holdings["TEST"], 25.0)
        _apply_buy_signals(signals=[self._event(1, risk, symbol="NEW")], **common)
        self.assertNotIn("NEW", holdings)

    def test_stage_regression_and_setup_mismatch_do_not_add(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        current = self._event(2, risk)
        holdings = {"TEST": 25.0}
        features = {"TEST": current.metadata}
        cash = {"cash": 1_000.0}
        trade_day = date(2026, 1, 5)
        common = dict(
            db=SimpleNamespace(add=lambda _value: None), strategy=SimpleNamespace(id="strategy"), run=SimpleNamespace(id="run"),
            holdings=holdings, avg_entry_prices={"TEST": 10.0}, entry_trade_dates={}, entry_day_indices={}, entry_signal_features=features,
            execution_prices={"TEST": 10.0},
            execution_snapshots={"TEST": {"symbol": "TEST", "dt_ny": trade_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)}},
            cash_ref=cash, equity_before=1_000.0, max_positions=1, position_size_pct=0.5,
            cost_config=BacktestCostConfig(0, 0, 0), trade_day=trade_day, persist_transactions=False,
        )
        _apply_buy_signals(signals=[self._event(1, risk)], trade_day_index=2, **common)
        mismatch = self._event(3, risk)
        mismatch.metadata["setup"] = {**mismatch.metadata["setup"], "setup_id": "v_reversal:TEST:different"}
        _apply_buy_signals(signals=[mismatch], trade_day_index=3, **common)
        self.assertEqual(holdings["TEST"], 25.0)
        self.assertEqual(cash["cash"], 1_000.0)

    def test_stage_add_after_split_uses_adjusted_quantity_and_cost(self) -> None:
        risk = {"stage_1_target_pct": 0.2, "stage_2_target_pct": 0.5, "stage_3_target_pct": 1.0}
        first = self._event(1, risk)
        holdings = {"TEST": 10.0}
        averages = {"TEST": 20.0}
        features = {"TEST": first.metadata}
        split_day = date(2026, 1, 5)
        _apply_split_adjustments(split_day, {split_day: {"TEST": 2.0}}, holdings, averages)
        self.assertEqual(holdings["TEST"], 20.0)
        self.assertEqual(averages["TEST"], 10.0)
        cash = {"cash": 1_000.0}
        stats = _apply_buy_signals(
            db=SimpleNamespace(add=lambda _value: None), strategy=SimpleNamespace(id="strategy"), run=SimpleNamespace(id="run"),
            signals=[self._event(2, risk)], holdings=holdings, avg_entry_prices=averages,
            entry_trade_dates={}, entry_day_indices={}, entry_signal_features=features,
            execution_prices={"TEST": 10.0},
            execution_snapshots={"TEST": {"symbol": "TEST", "dt_ny": split_day, "ts": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)}},
            cash_ref=cash, equity_before=1_000.0, max_positions=1, position_size_pct=0.5,
            cost_config=BacktestCostConfig(commission_bps=0, commission_min=1, slippage_bps=100),
            trade_day=split_day, trade_day_index=2, persist_transactions=False,
        )
        expected_qty = 20.0 + (49.0 / 10.1)
        self.assertAlmostEqual(holdings["TEST"], expected_qty, places=6)
        self.assertGreater(averages["TEST"], 10.0)
        self.assertAlmostEqual(stats.total_fees, 1.0)
        self.assertGreater(stats.total_slippage, 0.0)

    def test_sell_cash_is_available_to_buy_on_the_same_execution_day(self) -> None:
        trade_day = date(2026, 1, 5)
        ts = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        sell = SignalEvent("strategy", ts, "OLD", "SELL", "rotate")
        buy = SignalEvent("strategy", ts, "NEW", "BUY", "rotate", metadata={"strength": {"score": 100, "passes_threshold": True}})
        holdings = {"OLD": 10.0}
        averages = {"OLD": 10.0}
        cash = {"cash": 0.0}
        snapshots = {
            "OLD": {"symbol": "OLD", "dt_ny": trade_day, "ts": ts},
            "NEW": {"symbol": "NEW", "dt_ny": trade_day, "ts": ts},
        }
        common = dict(
            db=SimpleNamespace(add=lambda _value: None), strategy=SimpleNamespace(id="strategy"), run=SimpleNamespace(id="run"),
            holdings=holdings, avg_entry_prices=averages, entry_trade_dates={}, entry_day_indices={}, entry_signal_features={},
            execution_prices={"OLD": 10.0, "NEW": 10.0}, execution_snapshots=snapshots,
            cash_ref=cash, cost_config=BacktestCostConfig(0, 0, 0), persist_transactions=False,
        )
        _apply_sell_signals(signals=[sell, buy], **common)
        self.assertEqual(cash["cash"], 100.0)
        _apply_buy_signals(
            signals=[sell, buy], equity_before=100.0, max_positions=1, position_size_pct=1.0,
            trade_day=trade_day, trade_day_index=1, **common,
        )
        self.assertNotIn("OLD", holdings)
        self.assertAlmostEqual(holdings["NEW"], 10.0)
        self.assertAlmostEqual(cash["cash"], 0.0)

    @staticmethod
    def _evaluate_pattern(
        *,
        pattern_type: str,
        symbol: str,
        recent_bars: list[dict],
        signal_cfg: dict,
        risk_cfg: dict,
        position: float,
        avg_entry_price: float | None,
        entry_signal_features: dict | None,
    ):
        evaluators = {
            "head_shoulders_bottom": head_shoulders_bottom.evaluate,
            "rounded_bottom": rounded_bottom.evaluate,
            "v_reversal": v_reversal.evaluate,
        }
        return evaluators[pattern_type](
            PatternContext(
                symbol=symbol,
                bars=recent_bars,
                signal_cfg=signal_cfg,
                risk_cfg=risk_cfg,
                position=position,
                avg_entry_price=avg_entry_price,
                entry_signal_features=entry_signal_features,
            )
        )

    @staticmethod
    def _bar(day: date, open_price: float, high: float, low: float, close: float, volume: float, atr: float) -> dict:
        return {"dt_ny": day, "open": open_price, "high": high, "low": low, "close": close, "volume": volume, "volume_sma_20": 100.0, "atr_14": atr}

    @staticmethod
    def _event(stage: int, risk: dict, *, symbol: str = "TEST") -> SignalEvent:
        setup = build_pattern_setup(
            pattern_type="v_reversal",
            symbol=symbol,
            stage_index=stage,
            stage_key=f"stage_{stage}",
            risk_cfg=risk,
            anchors={"pivot": "2026-01-02"},
            invalidation_price=9.0,
            setup_id_anchors=("2026-01-02",),
        )
        return SignalEvent(
            strategy_id="strategy",
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            symbol=symbol,
            action="BUY",
            reason="stage",
            metadata={"position": 0, "setup": setup, "strength": {"score": 100, "passes_threshold": True}},
        )


if __name__ == "__main__":
    unittest.main()
