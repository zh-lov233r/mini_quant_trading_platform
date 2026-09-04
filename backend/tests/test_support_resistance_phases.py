from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import math
import unittest

from src.services.support_resistance_service import (
    Pivot, Zone, SupportResistanceSymbolState, advance_symbol, _rebuild_zones, _record_zone_version,
)
from src.services.strategy_registry import normalize_strategy_params


START = date(2025, 1, 1)


def bar(index: int, close: float = 102, low: float = 101.5, **extra) -> dict:
    return dict(dt_ny=START + timedelta(days=index), open=close,
                high=max(close + 0.5, low), low=low, close=close,
                volume=150, volume_sma_20=100, atr_14=1, position=0, **extra)


def state_with_zones() -> SupportResistanceSymbolState:
    state = SupportResistanceSymbolState(phase_start=START)
    state.history = [bar(i) for i in range(25)]
    for kind, center, role in (("low", 100, "support"), ("high", 120, "resistance")):
        members = [Pivot(f"{kind}:{(START + timedelta(days=i)).isoformat()}", kind,
                         i, START + timedelta(days=i), START + timedelta(days=i + 3),
                         center, 1) for i in (0, 10, 20)]
        state.pivots.extend(members)
        state.zones[role] = Zone(role, kind, role, "active", center, center - 1,
                                center + 1, 1, tuple(p.pivot_key for p in members),
                                3, 3, members[0].trade_date, members[-1].trade_date,
                                START + timedelta(days=23), phase_start=START)
    return state


class FrozenPhaseTests(unittest.TestCase):
    def setUp(self):
        params = normalize_strategy_params("support_resistance", {})
        self.signal, self.risk = params["signal"], params["risk"]
        self.signal["min_strength_score"] = 0

    def advance(self, state, value):
        return advance_symbol(state, value, self.signal, self.risk)

    def test_repeated_trades_in_same_frozen_region(self):
        state = state_with_zones()
        first = self.advance(state, bar(25, low=100))
        self.assertEqual(first["action"], "BUY")
        held = bar(26, close=110, low=109)
        held.update(position=1, avg_entry_price=102, position_holding_days=1,
                    entry_signal_features={"support_resistance": first["support_resistance"]})
        self.assertIsNone(self.advance(state, held))
        held.update(bar(27, close=119, low=118))
        held.update(position=1, position_holding_days=2)
        self.assertEqual(self.advance(state, held)["action"], "SELL")
        second = self.advance(state, bar(28, low=100))
        self.assertEqual(second["action"], "BUY")
        self.assertEqual(first["support_resistance"]["zone_key"], second["support_resistance"]["zone_key"])
        self.assertEqual(state.phase_start, START)

    def test_wick_and_boundary_equality_do_not_break_or_flip(self):
        for close in (99, 100, 102):
            with self.subTest(close=close):
                state = state_with_zones()
                self.advance(state, bar(25, close=close, low=98))
                self.assertEqual(state.phase_start, START)
                self.assertEqual(state.zones["support"].role, "support")
                self.assertTrue(any(e["event_type"] == "touch" for e in state.events))

    def test_strict_close_break_ends_every_zone_before_signals(self):
        state = state_with_zones()
        self.assertIsNone(self.advance(state, bar(25, close=98.9, low=98)))
        self.assertFalse(state.zones)
        self.assertFalse(state.pivots)
        self.assertEqual(state.phase_start, START + timedelta(days=25))
        terminal = [v for v in state.zone_versions if v["status"] == "expired"]
        self.assertEqual(len(terminal), 2)
        self.assertTrue(all(v["end_reason"] == "close_break" for v in terminal))
        event = next(e for e in state.events if e["event_type"] == "phase_ended")
        self.assertEqual(event["effective_to"], (START + timedelta(days=24)).isoformat())

    def test_confirmation_does_not_allow_same_day_entry(self):
        state = state_with_zones()
        state.zones["support"].valid_from = START + timedelta(days=25)
        self.assertIsNone(self.advance(state, bar(25, low=100)))

    def test_rebuild_preserves_members_geometry_and_role(self):
        state = state_with_zones()
        before = deepcopy(state.zones)
        state.history.append(bar(25))
        state.pivots.append(Pivot("low:extra", "low", 24, START + timedelta(days=24),
                                  START + timedelta(days=25), 100.5, 10))
        _rebuild_zones(state, {**bar(25), "atr_14": 10}, self.signal)
        for key, zone in before.items():
            self.assertEqual(state.zones[key].snapshot(), zone.snapshot())

    def test_projection_conflict_and_invalid_geometry_end_phase(self):
        for reason in ("zone_conflict", "invalid_geometry"):
            state = state_with_zones()
            zone = state.zones["support"]
            zone.anchor_session_index = 24
            zone.slope_per_session = 18 if reason == "zone_conflict" else -100
            self.advance(state, bar(25))
            self.assertFalse(state.zones)
            event = next(e for e in state.events if e["event_type"] == "phase_ended")
            self.assertEqual(event["reason"], reason)

    def test_phase_end_does_not_force_liquidation(self):
        state = state_with_zones()
        value = bar(25, close=98, low=97)
        value.update(position=1, avg_entry_price=95, position_holding_days=1,
                     entry_signal_features={"support_resistance": {
                         "zone_key": "entry", "zone": {"lower": 90, "slope_per_session": 0},
                         "stop_price": 90, "target_price": 130, "entry_atr": 5,
                         "signal_date": "2025-01-25"}})
        self.assertIsNone(self.advance(state, value))
        self.assertFalse(state.zones)

    def test_cache_replays_boundaries_and_empty_waiting_phase(self):
        cold = state_with_zones()
        for zone in cold.zones.values():
            _record_zone_version(cold, zone, START + timedelta(days=23), status="active")
        sequence = [bar(25, low=100), bar(26, close=98, low=97), bar(27), bar(28)]
        expected = [self.advance(cold, value) for value in sequence]
        cached = SupportResistanceSymbolState(history=[bar(i) for i in range(25)],
            cached_zone_timeline=deepcopy(cold.zone_versions),
            cached_regime_timeline=deepcopy(cold.regime_versions))
        actual = [self.advance(cached, value) for value in sequence]
        self.assertEqual(actual, expected)
        self.assertEqual(cached.phase_start, cold.phase_start)
        self.assertEqual(cached.events, cold.events)
        self.assertFalse(cached.zones)

    def test_adjacent_transition_phases_are_persistable(self):
        from src.services.support_resistance_persistence_service import _validate_regime_versions
        days = [START, START + timedelta(days=1)]
        versions = [dict(version=i + 1, effective_from=day.isoformat(), regime="transition",
                         evidence={"phase_start": day.isoformat()}) for i, day in enumerate(days)]
        _validate_regime_versions("TEST", days, versions)
        versions[1]["evidence"]["phase_start"] = START.isoformat()
        with self.assertRaisesRegex(ValueError, "within a phase"):
            _validate_regime_versions("TEST", days, versions)

    def test_cached_zones_compare_geometry_on_the_same_session_not_anchor_dates(self):
        state = state_with_zones()
        state.history = [bar(i, close=116, low=115) for i in range(50)]
        support = state.zones["support"]
        support.center, support.lower, support.upper = 140, 139, 141
        support.anchor_center, support.anchor_lower, support.anchor_upper = 140, 139, 141
        support.anchor_session_index, support.slope_per_session = 23, -1
        resistance = state.zones["resistance"]
        resistance.center, resistance.lower, resistance.upper = 130, 129, 131
        resistance.anchor_center, resistance.anchor_lower, resistance.anchor_upper = 130, 129, 131
        resistance.anchor_session_index = 49
        resistance.valid_from = START + timedelta(days=49)
        resistance.first_pivot_date = START + timedelta(days=36)
        for zone in state.zones.values():
            _record_zone_version(state, zone, zone.valid_from, status="active")
        cached = SupportResistanceSymbolState(history=deepcopy(state.history), phase_start=START,
            cached_zone_timeline=deepcopy(state.zone_versions))
        self.advance(cached, bar(50, close=116, low=113))
        self.assertEqual(cached.phase_start, START)
        self.assertEqual(set(cached.zones), {"support", "resistance"})
        self.assertFalse(any(e["event_type"] == "phase_ended" for e in cached.events))

    def test_generated_phases_replay_identical_decisions_and_events(self):
        sequence = []
        for i in range(260):
            close = 100 + math.sin(i * 0.4) * 5 + i * 0.02 + (15 if i >= 140 else 0)
            sequence.append(bar(i, close=close, low=close - 1))
        cold = SupportResistanceSymbolState()
        expected = [self.advance(cold, value) for value in sequence]
        self.assertGreater(len(cold.zone_versions), 3)
        self.assertTrue(any(e["event_type"] == "phase_ended" for e in cold.events))
        cached = SupportResistanceSymbolState(cached_zone_timeline=deepcopy(cold.zone_versions),
            cached_regime_timeline=deepcopy(cold.regime_versions))
        actual = [self.advance(cached, value) for value in sequence]
        self.assertEqual(actual, expected)
        self.assertEqual(cached.events, cold.events)
        self.assertEqual(cached.phase_start, cold.phase_start)


if __name__ == "__main__":
    unittest.main()
