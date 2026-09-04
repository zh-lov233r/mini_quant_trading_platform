from __future__ import annotations

from datetime import date, timedelta
import copy
import unittest
from unittest.mock import MagicMock

from quant_kernel import support_resistance as native
import quant_kernel
from src.services.strategy_registry import normalize_strategy_params
from src.services.support_resistance_service import Pivot, SupportResistanceSymbolState, SetupStats, advance_symbol, _rebuild_zones
from src.services.support_resistance_risk_service import load_support_risk_context
from src.services.prepared_dataset_service import PREPARED_FLOAT_INDEX, PREPARED_INTEGER_INDEX
from backend.tests.test_support_resistance_strategy import _bar, _zone, _cached_regime


def channel_history() -> list[dict]:
    bars = []
    for index in range(86):
        drift = index * 0.1
        peak = 102.0
        if index > 0 and index % 8 == 0:
            peak = 118.0 if index <= 24 else (110.0 if index <= 48 else 104.0)
        bars.append(_bar(index, high=peak + drift,
            low=(99.0 if index % 8 == 4 else 100.0) + drift, close=101.0 + drift))
    return bars


class SupportResistanceReviewTests(unittest.TestCase):
    def setUp(self):
        self.params = normalize_strategy_params("support_resistance", {})
        self.signal, self.risk = self.params["signal"], self.params["risk"]

    def test_real_detector_keeps_multiple_lines_without_old_zone_retest_entry(self):
        state = SupportResistanceSymbolState()
        signal = {**self.signal,
            "max_zones_per_kind": 5, "max_abs_slope_atr_per_session": 0.105}
        for bar in channel_history():
            advance_symbol(state, bar, signal, self.risk)
        self.assertEqual(sum(z.source_kind == "high" for z in state.zones.values()), 3)
        self.assertTrue(all(z.touch_count < 3 for z in state.zones.values() if z.source_kind == "high"))
        history = copy.deepcopy(state.zone_versions)
        advance_symbol(state, _bar(86, high=114.2, low=111.0, close=114.0, volume=160), signal, self.risk)
        decision = advance_symbol(state, _bar(87, high=114.0, low=113.1, close=113.4, volume=60), signal, self.risk)
        self.assertIsNone(decision)
        self.assertTrue(any(event["event_type"] == "phase_ended" for event in state.events))
        self.assertEqual(history, state.zone_versions[:len(history)])

    def test_channel_respects_frozen_roles(self):
        support = _zone("floor", "resistance", 100)
        ceiling = _zone("ceiling", "support", 110)
        channel = native.build_entry_channel([ceiling, support], 102, date(2025, 1, 2))
        self.assertFalse(channel["valid"])
        support.role = "support"
        ceiling.role = "resistance"
        channel = native.build_entry_channel([ceiling, support], 102, date(2025, 1, 2))
        self.assertTrue(channel["valid"])
        self.assertEqual((channel["lower"], channel["upper"]), (101, 109))
        state = SupportResistanceSymbolState(zones={z.zone_key: z for z in [support, ceiling]},
            history=[_bar(0, high=103, low=102, close=102)], cached_regime_timeline=_cached_regime())
        result = advance_symbol(state, _bar(1, high=103, low=100, close=102), self.signal, self.risk)
        self.assertEqual(result["support_resistance"]["target_price"], 109)

    def test_nearer_support_scores_higher_and_quality_dimensions_are_visible(self):
        def strength(close):
            state = SupportResistanceSymbolState(zones={"floor": _zone("floor", "support", 100),
                "ceiling": _zone("ceiling", "resistance", 120)}, history=[_bar(0, high=105, low=103, close=104)],
                cached_regime_timeline=_cached_regime())
            result = advance_symbol(state, _bar(1, high=105, low=100, close=close), self.signal, self.risk)
            return result["support_resistance"]["strength"]
        near, far = strength(101.3), strength(103.0)
        self.assertGreater(near["score"], far["score"])
        self.assertEqual({x["key"] for x in near["components"]},
            {"reward_risk", "pivot_count", "touch_count", "fit_residual_atr", "support_proximity_atr", "volume_ratio"})

    def test_risk_sizing_scales_with_distance_and_includes_minimum_commissions(self):
        risk = {**self.risk, "position_size_pct": 1.0}
        narrow = native.size_entry({"stop_price": 98, "target_price": 110}, 100, 100000, 100000, risk)
        wide = native.size_entry({"stop_price": 96, "target_price": 110}, 100, 100000, 100000, risk)
        self.assertAlmostEqual(narrow["quantity"], 250)
        self.assertAlmostEqual(wide["quantity"], 125)
        costs = native.size_entry({"stop_price": 98, "target_price": 110}, 100, 100000, 100000, risk, 1, 5, 10)
        self.assertAlmostEqual(costs["planned_loss"], 500, places=8)
        self.assertLess(costs["quantity"], narrow["quantity"])
        rejected = native.size_entry({"stop_price": 98, "target_price": 103}, 100, 100000, 100000, risk, 1, 5, 10)
        self.assertEqual(rejected["reason_code"], "net_reward_risk_below_minimum")
        self.assertEqual(rejected["quantity"], 0)

    def test_projected_stop_and_prior_close_break_even(self):
        zone = _zone("floor", "support", 100).snapshot()
        zone["slope_per_session"] = 0.5
        frozen = {"zone_key": "floor", "zone": zone, "signal_date": "2025-01-01",
            "entry_close": 103, "entry_atr": 2, "stop_price": 100, "target_price": 120}
        state = SupportResistanceSymbolState(history=[_bar(0, high=104, low=102, close=103),
            _bar(1, high=107, low=102, close=106)], cached_regime_timeline=_cached_regime())
        decision = advance_symbol(state, {**_bar(2, high=104, low=101, close=102), "position": 10,
            "avg_entry_price": 103, "position_holding_days": 1,
            "entry_signal_features": {"support_resistance": frozen}}, self.signal, self.risk)
        self.assertEqual(decision["support_resistance"]["exit_stop_price"], 103)
        self.assertEqual(decision["support_resistance"]["exit_reason_code"], "stop")
        self.assertIn("floor", state.stopped_zones)
        frozen.update({"stop_price": 100.5, "zone": {"lower": 99, "slope_per_session": 0}})
        gap_state = SupportResistanceSymbolState(history=[_bar(0, high=104, low=102, close=103)],
            cached_regime_timeline=_cached_regime())
        gap_exit = advance_symbol(gap_state, {**_bar(1, high=102, low=100, close=100.25),
            "position": 10, "avg_entry_price": 101.5, "position_holding_days": 0,
            "entry_signal_features": {"support_resistance": frozen}}, self.signal, self.risk)
        self.assertEqual(gap_exit["support_resistance"]["exit_stop_price"], 100.5)

    def test_market_filter_missing_and_bearish_data_block_buys(self):
        for values, reason in [({}, "missing_market_filter_data"),
            ({"market_close": 99, "market_sma_200": 100}, "market_below_sma_200")]:
            state = SupportResistanceSymbolState(zones={"floor": _zone("floor", "support", 100),
                "ceiling": _zone("ceiling", "resistance", 120)}, history=[_bar(0, high=105, low=103, close=104)],
                cached_regime_timeline=_cached_regime())
            result = advance_symbol(state, {**_bar(1, high=103, low=100, close=102), **values},
                self.signal, {**self.risk, "market_filter_enabled": True})
            self.assertIsNone(result)
            self.assertEqual(next(e for e in state.events if e["event_type"] == "candidate")["rejection_reason"], reason)

    def test_disabled_market_filter_requires_no_security_master_data(self):
        db = MagicMock()
        context = load_support_risk_context(db, self.risk, date(2025, 1, 1), date(2025, 2, 1))
        self.assertEqual(context, {"market": {}})
        db.execute.assert_not_called()
        self.assertNotIn("max_industry_positions", self.risk)

    def test_censored_outcomes_count_as_non_success_and_audit_only_mode_is_invalid(self):
        self.assertAlmostEqual(SetupStats(wins=2, losses=1, censored=5).posterior, 0.3)
        with self.assertRaisesRegex(ValueError, "support_bounce_enabled"):
            normalize_strategy_params("support_resistance", {"signal": {
                "support_bounce_enabled": False}})

    def test_detected_multi_zone_cache_replays_identical_decisions(self):
        bars = channel_history() + [
            _bar(86, high=114.2, low=111.0, close=114.0, volume=160),
            _bar(87, high=114.0, low=113.1, close=113.4, volume=60),
        ]
        signal = {**self.signal, "max_zones_per_kind": 5, "max_abs_slope_atr_per_session": 0.105}
        live = SupportResistanceSymbolState()
        expected = [advance_symbol(live, bar, signal, self.risk) for bar in bars]
        cached = SupportResistanceSymbolState(cached_zone_timeline=copy.deepcopy(live.zone_versions),
            cached_regime_timeline=copy.deepcopy(live.regime_versions))
        self.assertTrue(any(e["event_type"] == "phase_ended" for e in live.events))
        for index, bar in enumerate(bars):
            with self.subTest(index=index):
                self.assertEqual(advance_symbol(cached, bar, signal, self.risk), expected[index])

    def test_tied_pivot_waits_for_confirmation_and_keeps_earliest(self):
        state = SupportResistanceSymbolState()
        bars = [_bar(i, high=110 if i in (3, 4) else 105, low=99, close=100) for i in range(8)]
        for bar in bars[:6]:
            advance_symbol(state, bar, self.signal, self.risk)
        self.assertFalse(state.pivots)
        for bar in bars[6:]:
            advance_symbol(state, bar, self.signal, self.risk)
        self.assertEqual([(p.kind, p.trade_date, p.confirmed_on) for p in state.pivots],
            [("high", date(2025, 1, 4), date(2025, 1, 7))])

    def test_band_contains_its_members_and_never_reanchors_on_volatility(self):
        state = SupportResistanceSymbolState(history=[_bar(i, high=105, low=99, close=103) for i in range(21)],
            pivots=[Pivot(f"low:{i}", "low", i, date(2025, 1, 1) + timedelta(days=i),
                date(2025, 1, 21), price, 1.0) for i, price in [(0, 100), (10, 100.6), (20, 100)]])
        _rebuild_zones(state, state.history[-1], self.signal)
        zone = next(iter(state.zones.values()))
        self.assertAlmostEqual(zone.upper - zone.center, 0.6)
        before = copy.deepcopy(state.zone_versions)
        state.history.append(_bar(21, high=105, low=99, close=103))
        _rebuild_zones(state, {**state.history[-1], "atr_14": 3.0}, self.signal)
        rebased = next(iter(state.zones.values()))
        self.assertEqual(rebased.anchor_session_index, 20)
        self.assertAlmostEqual(rebased.upper - rebased.center, 0.6)
        self.assertEqual(state.zone_versions, before)
        self.assertEqual(state.zone_versions[:len(before)], before)

    def test_stop_cooldown_expires_and_future_stop_does_not_leak_into_history(self):
        for stopped, allowed in [(0, False), (-1, True), (7, True)]:
            state = SupportResistanceSymbolState(zones={"floor": _zone("floor", "support", 100),
                "ceiling": _zone("ceiling", "resistance", 120)},
                history=[_bar(i, high=105, low=103, close=104) for i in range(5)],
                cached_regime_timeline=_cached_regime(), stopped_zones={"floor": stopped})
            decision = advance_symbol(state, _bar(5, high=103, low=100, close=102), self.signal, self.risk)
            self.assertEqual(decision is not None, allowed)

    def test_market_average_is_causal_without_industry_lookup(self):
        db = MagicMock()
        first = date(2024, 1, 1)
        identity = MagicMock()
        identity.scalars.return_value.all.return_value = [9]
        db.execute.side_effect = [identity,
            [(first + timedelta(days=i), 100 if i < 200 else 200) for i in range(201)]]
        context = load_support_risk_context(db, {**self.risk, "market_filter_enabled": True},
            first, first + timedelta(days=200))
        self.assertEqual(context["market"], {
            str((first + timedelta(days=199)).toordinal()): [100, 100],
            str((first + timedelta(days=200)).toordinal()): [200, 100.5]})
        self.assertEqual(db.execute.call_count, 2)

    def test_native_execution_sizes_multiple_positions_without_industry_data(self):
        from backend.tests.test_native_nine_strategy_golden import NativeNineStrategyGoldenTests
        fixture = NativeNineStrategyGoldenTests()
        fixture.setUp()
        dataset, runtime = fixture._support_case()
        runtime["params"]["risk"].update({"risk_per_trade_pct": 0.00001})
        dataset.floats = dataset.floats.copy(order="F")
        sessions = dataset.integers[:, PREPARED_INTEGER_INDEX["session_index"]]
        dataset.floats[sessions % 6 == 2, PREPARED_FLOAT_INDEX["open"]] = 102.0
        result = quant_kernel.run_backtest(dataset, runtime, {"initial_cash": 100000,
            "commission_bps": 0, "commission_min": 0, "slippage_bps": 0})
        self.assertGreater(result.summary["trade_count"], 0)
        self.assertEqual(int(result.trades["instrument_id"][0]), 1)
        self.assertAlmostEqual(float(result.trades["quantity"][0]), 1 / 1.5)
        self.assertEqual(float(result.trades["price"][0]), 102)
        first_session = result.trades["session_index"][0]
        first_ids = [int(instrument) for session, instrument in zip(
            result.trades["session_index"], result.trades["instrument_id"])
            if session == first_session]
        self.assertEqual(first_ids, list(range(1, 21)))



if __name__ == "__main__":
    unittest.main()
