from __future__ import annotations

import sys
import unittest
import uuid
import copy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.strategy_registry import normalize_strategy_params  # noqa: E402
from src.models.tables import (  # noqa: E402
    Base,
    Instrument,
    PaperTradingAccount,
    Signal,
    Strategy,
    StrategyPortfolio,
    StrategyRun,
    SupportResistanceMaterialization,
    SupportResistanceMaterializationEvent,
    SupportResistanceRegimeVersion,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    SupportResistanceZoneVersion,
    Transaction,
)
from src.services.support_resistance_persistence_service import (  # noqa: E402
    SupportResistanceMaterializationBuildError,
    _insert_in_batches,
    _instrument_ids,
    _validate_regime_versions,
    _zone_version_rows,
    find_reusable_materialization,
    hydrate_state_from_materialization,
    persist_support_resistance_run,
    record_failed_materialization_after_rollback,
)
from src.services.support_resistance_service import (  # noqa: E402
    PendingOutcome,
    Pivot,
    SupportResistanceSymbolState,
    Zone,
    SupportResistanceState,
    _fit_pivot_line,
    _new_zone_key,
    _record_regime_version,
    _record_zone_version,
    _rebuild_zones,
    advance_symbol,
    build_entry_channel,
    classify_market_regime,
    entry_price_is_inside_channel,
    normalized_detector_params,
    project_entry_channel,
)
from src.services.strategy_engine import (  # noqa: E402
    evaluate_native_signals,
    support_resistance_hydration_payload,
)
from src.services.paper_trading_service import (  # noqa: E402
    VirtualPosition,
    _inject_virtual_positions,
    run_paper_trading,
)
from src.api.backtests import (  # noqa: E402
    BacktestCreate,
    _build_regime_intervals,
    create_backtest,
    get_backtest_support_resistance,
)
from src.services.adaptive_research_service import _catalog_item  # noqa: E402
from src.services.support_resistance_validation_report_service import (  # noqa: E402
    _trade_results_by_regime,
)


def _bar(offset: int, *, high: float, low: float, close: float, volume: float = 100.0) -> dict:
    return {
        "dt_ny": date(2025, 1, 1) + timedelta(days=offset),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "volume_sma_20": 100.0,
        "atr_14": 1.0,
        "position": 0.0,
    }


def _zone(key: str, role: str, center: float) -> Zone:
    return Zone(
        zone_key=key,
        source_kind="low" if role == "support" else "high",
        role=role,
        status="active",
        center=center,
        lower=center - 1.0,
        upper=center + 1.0,
        atr=2.0,
        pivot_keys=(f"{key}:1", f"{key}:2", f"{key}:3"),
        pivot_count=3,
        touch_count=3,
        first_pivot_date=date(2024, 12, 1),
        last_pivot_date=date(2024, 12, 20),
        valid_from=date(2024, 12, 23),
    )


def _cached_regime(regime: str = "uptrend") -> list[dict]:
    return [
        {
            "version": 1,
            "effective_from": date(2024, 1, 1),
            "regime": regime,
            "lower_zone_key": "support",
            "upper_zone_key": "resistance",
            "reason_code": "test_fixture",
            "evidence": {"reason_code": "test_fixture"},
        }
    ]


class SupportResistanceStrategyTests(unittest.TestCase):
    def test_future_regime_changes_append_without_rewriting_historical_evidence(self) -> None:
        state = SupportResistanceSymbolState()
        first_evidence = {
            "reason_code": "missing_boundary",
            "lower_zone_key": None,
            "upper_zone_key": None,
        }
        _record_regime_version(
            state,
            date(2025, 1, 2),
            "transition",
            first_evidence,
        )
        frozen = copy.deepcopy(state.regime_versions[0])
        _record_regime_version(
            state,
            date(2025, 1, 3),
            "uptrend",
            {
                "reason_code": "rising_channel_higher_highs_higher_lows",
                "lower_zone_key": "low",
                "upper_zone_key": "high",
            },
        )
        self.assertEqual(state.regime_versions[0], frozen)
        self.assertEqual([item["version"] for item in state.regime_versions], [1, 2])

    def test_zone_projection_alone_does_not_create_a_new_version(self) -> None:
        state = SupportResistanceSymbolState()
        zone = _zone("projected", "support", 100)
        zone.anchor_session_index = 0
        zone.anchor_center = 100
        zone.anchor_lower = 99
        zone.anchor_upper = 101
        zone.slope_per_session = 0.5
        _record_zone_version(state, zone, date(2025, 1, 2), status="active")
        _record_zone_version(
            state,
            zone.projected(1),
            date(2025, 1, 3),
            status="active",
        )
        self.assertEqual(len(state.zone_versions), 1)

    def test_new_zone_key_changes_when_pivot_membership_changes(self) -> None:
        first = Pivot("low:1", "low", 1, date(2025, 1, 2), date(2025, 1, 3), 10, 1)
        second = Pivot("low:2", "low", 2, date(2025, 1, 3), date(2025, 1, 4), 11, 1)
        third = Pivot("low:3", "low", 3, date(2025, 1, 4), date(2025, 1, 5), 12, 1)

        self.assertNotEqual(
            _new_zone_key("low", [first, second]),
            _new_zone_key("low", [first, second, third]),
        )

    def test_non_positive_projected_zone_expires_before_classification_or_entry(self) -> None:
        zone = _zone("falling", "support", 0.2)
        zone.lower = 0.1
        zone.upper = 0.3
        zone.anchor_center = 0.2
        zone.anchor_lower = 0.1
        zone.anchor_upper = 0.3
        zone.anchor_session_index = 0
        zone.slope_per_session = -0.2
        state = SupportResistanceSymbolState(
            history=[_bar(0, high=1.0, low=0.5, close=0.8)],
            zones={zone.zone_key: zone},
        )

        decision = advance_symbol(
            state,
            _bar(1, high=1.0, low=0.4, close=0.7),
            self.signal,
            self.risk,
        )

        self.assertIsNone(decision)
        self.assertNotIn(zone.zone_key, state.zones)
        expired = next(item for item in state.zone_versions if item["status"] == "expired")
        self.assertGreater(expired["lower"], 0)
        self.assertEqual(expired["slope_per_session"], -0.2)
        invalidation = next(
            item
            for item in state.events
            if item["event_type"] == "invalidation" and item["zone_key"] == zone.zone_key
        )
        self.assertEqual(invalidation["reason"], "invalid_geometry")
        self.assertFalse(any(item["event_type"] == "candidate" for item in state.events))

    def setUp(self) -> None:
        params = normalize_strategy_params("support_resistance", {})
        self.signal = params["signal"]
        self.risk = params["risk"]

    def test_rejects_all_entry_modes_disabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "support_bounce_enabled"):
            normalize_strategy_params(
                "support_resistance",
                {
                    "signal": {
                        "support_bounce_enabled": False,
                    }
                },
            )

    def test_minimum_two_pivots_is_accepted_and_one_is_rejected(self) -> None:
        self.assertEqual(
            normalize_strategy_params("support_resistance", {})["signal"]["min_line_pivots"],
            2,
        )
        self.assertEqual(
            normalize_strategy_params(
                "support_resistance", {"signal": {"min_line_pivots": 2}}
            )["signal"]["min_line_pivots"],
            2,
        )
        with self.assertRaisesRegex(ValueError, "at least 2"):
            normalize_strategy_params(
                "support_resistance", {"signal": {"min_line_pivots": 1}}
            )

    def test_rejects_non_daily_or_mislabelled_price_semantics(self) -> None:
        with self.assertRaisesRegex(ValueError, "execution.timeframe"):
            normalize_strategy_params(
                "support_resistance",
                {"execution": {"timeframe": "1h"}},
            )
        with self.assertRaisesRegex(ValueError, "metadata.price_semantics"):
            normalize_strategy_params(
                "support_resistance",
                {"metadata": {"price_semantics": "unadjusted"}},
            )

    def test_category_research_catalog_exposes_engine_ready_defaults(self) -> None:
        catalog = _catalog_item("support_resistance")

        self.assertTrue(catalog["engine_ready"])
        self.assertTrue(catalog["defaults"]["signal"]["support_bounce_enabled"])

    def _classify(
        self,
        lower_delta: float,
        upper_delta: float,
        lower_slope: float,
        upper_slope: float,
        *,
        close: float = 105.0,
    ) -> tuple[str, dict]:
        lower = _zone("lower", "support", 100)
        lower.source_kind = "low"
        lower.slope_per_session = lower_slope
        lower.pivot_keys = ("low:1", "low:2")
        upper = _zone("upper", "resistance", 110)
        upper.source_kind = "high"
        upper.slope_per_session = upper_slope
        upper.pivot_keys = ("high:1", "high:2")
        state = SupportResistanceSymbolState(
            pivots=[
                Pivot("low:1", "low", 0, date(2024, 12, 1), date(2024, 12, 4), 100, 1),
                Pivot("low:2", "low", 10, date(2024, 12, 11), date(2024, 12, 14), 100 + lower_delta, 1),
                Pivot("high:1", "high", 0, date(2024, 12, 1), date(2024, 12, 4), 110, 1),
                Pivot("high:2", "high", 10, date(2024, 12, 11), date(2024, 12, 14), 110 + upper_delta, 1),
            ]
        )
        return classify_market_regime(
            state,
            [lower, upper],
            _bar(1, high=close + 1, low=close - 1, close=close),
            self.signal,
        )

    def test_four_regime_classifier_matrix_and_conflict_fallback(self) -> None:
        self.assertEqual(self._classify(2, 2, 0.2, 0.2)[0], "uptrend")
        self.assertEqual(self._classify(-2, -2, -0.2, -0.2)[0], "downtrend")
        self.assertEqual(self._classify(0, 0, 0, 0)[0], "range")
        self.assertEqual(self._classify(2, -2, 0.2, -0.2)[1]["reason_code"], "contracting_range")
        self.assertEqual(self._classify(-2, 2, -0.2, 0.2)[1]["reason_code"], "expanding_range")
        regime, evidence = self._classify(2, -2, 0.2, 0.2)
        self.assertEqual(regime, "transition")
        self.assertEqual(evidence["reason_code"], "structure_conflict")

    def test_transition_rejects_candidate_but_keeps_audit_event(self) -> None:
        state = SupportResistanceSymbolState(cached_regime_timeline=_cached_regime("transition"))
        state.history.append(_bar(-1, high=103, low=102, close=102.5))
        state.zones["support"] = _zone("support", "support", 100)

        decision = advance_symbol(
            state,
            _bar(1, high=104, low=100, close=102),
            self.signal,
            self.risk,
        )

        self.assertIsNone(decision)
        candidate = next(event for event in state.events if event["event_type"] == "candidate")
        self.assertFalse(candidate["entry_eligible"])
        self.assertFalse(candidate["regime_eligible"])
        self.assertTrue(any(event["event_type"] == "regime_rejection" for event in state.events))

    def test_downtrend_rejects_buy_candidate_but_keeps_audit_event(self) -> None:
        state = SupportResistanceSymbolState(cached_regime_timeline=_cached_regime("downtrend"))
        state.history.append(_bar(-1, high=103, low=102, close=102.5))
        state.zones["support"] = _zone("support", "support", 100)

        decision = advance_symbol(
            state,
            _bar(1, high=104, low=100, close=102),
            self.signal,
            self.risk,
        )

        self.assertIsNone(decision)
        candidate = next(event for event in state.events if event["event_type"] == "candidate")
        self.assertFalse(candidate["entry_eligible"])
        self.assertFalse(candidate["regime_eligible"])
        self.assertEqual(candidate["regime"], "downtrend")

    def test_falling_support_zone_rejects_entry_but_keeps_audit_event(self) -> None:
        state = SupportResistanceSymbolState(cached_regime_timeline=_cached_regime("uptrend"))
        state.history.append(_bar(-1, high=103, low=102, close=102.5))
        support = _zone("support", "support", 100)
        support.slope_per_session = -0.01
        state.zones[support.zone_key] = support
        state.zones["resistance"] = _zone("resistance", "resistance", 110)

        decision = advance_symbol(
            state,
            _bar(1, high=104, low=100, close=102),
            self.signal,
            self.risk,
        )

        self.assertIsNone(decision)
        candidate = next(event for event in state.events if event["event_type"] == "candidate")
        self.assertFalse(candidate["entry_eligible"])
        self.assertFalse(candidate["risk_eligible"])
        self.assertTrue(candidate["regime_eligible"])
        self.assertEqual(candidate["rejection_reason"], "falling_support_zone")

    def test_transition_does_not_force_an_open_position_to_exit(self) -> None:
        snapshot = _bar(1, high=108, low=103, close=104)
        snapshot.update(
            {
                "position": 10,
                "avg_entry_price": 105,
                "position_holding_days": 3,
                "entry_signal_features": {
                    "support_resistance": {
                        "zone": {"lower": 99, "upper": 101},
                        "entry_atr": 2,
                        "entry_close": 105,
                        "target_price": 115,
                    }
                },
            }
        )

        decision = advance_symbol(
            SupportResistanceSymbolState(cached_regime_timeline=_cached_regime("transition")),
            snapshot,
            self.signal,
            self.risk,
        )

        self.assertIsNone(decision)

    def test_confirmed_downtrend_exits_after_stop_and_target_checks(self) -> None:
        def snapshot(close: float, holding_days: int = 3) -> dict:
            item = _bar(1, high=max(108, close), low=min(103, close), close=close)
            item.update(
                {
                    "position": 10,
                    "avg_entry_price": 105,
                    "position_holding_days": holding_days,
                    "entry_signal_features": {
                        "support_resistance": {
                            "zone": {"lower": 99, "upper": 101},
                            "entry_atr": 2,
                            "entry_close": 105,
                            "target_price": 115,
                        }
                    },
                }
            )
            return item

        cases = (
            ("downtrend", snapshot(100), "closed below the projected zone-aware stop"),
            ("downtrend", snapshot(116), "reached the frozen support/resistance target"),
            ("downtrend", snapshot(104), "confirmed downtrend regime"),
            (
                "uptrend",
                snapshot(104, int(self.risk["max_holding_days"])),
                "reached the maximum support/resistance holding period",
            ),
        )
        for regime, current, expected_reason in cases:
            with self.subTest(regime=regime, expected_reason=expected_reason):
                decision = advance_symbol(
                    SupportResistanceSymbolState(cached_regime_timeline=_cached_regime(regime)),
                    current,
                    self.signal,
                    self.risk,
                )
                self.assertEqual(decision["action"], "SELL")
                self.assertEqual(decision["reason"], expected_reason)

    def test_pivot_is_confirmed_only_after_right_hand_bars(self) -> None:
        state = SupportResistanceSymbolState()
        self.signal.update(
            {
                "pivot_left_bars": 1,
                "pivot_right_bars": 2,
                "min_line_pivots": 3,
                "detection_window": 20,
            }
        )
        bars = [
            _bar(0, high=12, low=10, close=11),
            _bar(1, high=11, low=8, close=9),
            _bar(2, high=12, low=9, close=11),
            _bar(3, high=13, low=10, close=12),
        ]
        for raw in bars[:3]:
            advance_symbol(state, raw, self.signal, self.risk, emit_signals=False)
        self.assertFalse(any(pivot.trade_date == bars[1]["dt_ny"] for pivot in state.pivots))

        advance_symbol(state, bars[3], self.signal, self.risk, emit_signals=False)

        pivot = next(pivot for pivot in state.pivots if pivot.trade_date == bars[1]["dt_ny"])
        self.assertEqual(pivot.confirmed_on, bars[3]["dt_ny"])

    def test_outcomes_use_close_exit_signals_and_next_open_fills(self) -> None:
        state = SupportResistanceSymbolState(cached_regime_timeline=_cached_regime())
        state.pending_outcomes.append(
            PendingOutcome(
                setup="support_bounce",
                zone_key="support",
                origin_date=date(2025, 1, 1),
                origin_session_index=-1,
                target=103,
                stop=97,
                entry_price=100,
                frozen={"entry_close": 100, "entry_atr": 2, "stop_price": 97, "target_price": 103},
            )
        )

        advance_symbol(
            state,
            _bar(1, high=104, low=96, close=100),
            self.signal,
            self.risk,
            emit_signals=False,
        )

        self.assertEqual(state.stats["support_bounce"].resolved, 0)
        advance_symbol(state, _bar(2, high=101, low=95, close=96), self.signal, self.risk, emit_signals=False)
        self.assertEqual(state.stats["support_bounce"].resolved, 0)
        advance_symbol(state, _bar(3, high=100, low=94, close=95), self.signal, self.risk, emit_signals=False)
        stats = state.stats["support_bounce"]
        self.assertEqual((stats.wins, stats.losses, stats.censored), (0, 1, 0))
        self.assertAlmostEqual(stats.posterior, 1 / 3)

    def test_candidate_score_excludes_outcome_that_resolves_on_signal_day(self) -> None:
        state = SupportResistanceSymbolState()
        state.history.append(_bar(0, high=103, low=102, close=102.5))
        state.zones["support"] = _zone("support", "support", 100)
        state.pending_outcomes.append(
            PendingOutcome(
                setup="support_bounce",
                zone_key="old-support",
                origin_date=date(2024, 12, 20),
                origin_session_index=-1,
                target=103,
                stop=97,
                entry_price=103,
                exit_reason="stop",
            )
        )

        advance_symbol(
            state,
            _bar(1, high=104, low=96, close=102),
            self.signal,
            self.risk,
            emit_signals=False,
        )

        candidate = next(
            event
            for event in state.events
            if event["event_type"] == "candidate" and event["setup"] == "support_bounce"
        )
        self.assertEqual(candidate["score"], 0.5)
        self.assertEqual(candidate["score_evidence"]["resolved_samples"], 0)
        self.assertEqual(state.stats["support_bounce"].losses, 1)

    def test_entry_zone_is_frozen_for_exit(self) -> None:
        snapshot = _bar(1, high=101, low=96, close=97)
        snapshot.update(
            {
                "position": 10,
                "avg_entry_price": 105,
                "position_holding_days": 3,
                "entry_signal_features": {
                    "support_resistance": {
                        "zone_key": "support",
                        "zone": {"lower": 99, "upper": 101},
                        "entry_atr": 2,
                        "entry_close": 105,
                        "target_price": 115,
                    }
                },
            }
        )

        decision = advance_symbol(
            SupportResistanceSymbolState(),
            snapshot,
            self.signal,
            self.risk,
        )

        self.assertEqual(decision["action"], "SELL")
        self.assertEqual(decision["support_resistance"]["exit_stop_price"], 102)

    def test_backtest_and_paper_replay_emit_the_same_signal(self) -> None:
        params = normalize_strategy_params(
            "support_resistance",
            {"universe": {"symbols": ["TEST"], "selection_mode": "manual"}},
        )
        runtime = {
            "strategy_id": "strategy",
            "strategy_type": "support_resistance",
            "params": params,
        }
        timeline = [
            {
                "zone_key": "support",
                "effective_from": date(2024, 12, 1),
                "effective_to": None,
                "source_kind": "low",
                "role": "support",
                "status": "active",
                "center": 100.0,
                "lower": 99.0,
                "upper": 101.0,
                "atr": 2.0,
                "pivot_keys": ["support:1", "support:2"],
                "pivot_count": 2,
                "touch_count": 2,
                "first_pivot_date": date(2024, 12, 1),
                "last_pivot_date": date(2024, 12, 20),
                "valid_from": date(2024, 12, 23),
            },
            {
                "zone_key": "resistance",
                "effective_from": date(2024, 12, 1),
                "effective_to": None,
                "source_kind": "high",
                "role": "resistance",
                "status": "active",
                "center": 106.0,
                "lower": 105.0,
                "upper": 107.0,
                "atr": 2.0,
                "pivot_keys": ["resistance:1", "resistance:2"],
                "pivot_count": 2,
                "touch_count": 2,
                "first_pivot_date": date(2024, 12, 1),
                "last_pivot_date": date(2024, 12, 20),
                "valid_from": date(2024, 12, 23),
            },
        ]
        previous = {"symbol": "TEST", **_bar(0, high=103, low=102, close=102.5)}
        current = {"symbol": "TEST", **_bar(1, high=103, low=100.5, close=102)}

        market = {
            "TEST": {
                **current,
                "recent_bars": [previous, current],
                "support_resistance_hydration": {
                    "zone_timeline": timeline,
                    "regime_timeline": _cached_regime(),
                    "lifecycle_events": [],
                },
            }
        }
        backtest_signals = evaluate_native_signals(runtime, market)
        paper_signals = evaluate_native_signals(runtime, market)

        self.assertEqual(len(backtest_signals), 1)
        self.assertEqual(len(paper_signals), 1)
        self.assertEqual(backtest_signals[0].action, paper_signals[0].action)
        self.assertEqual(
            backtest_signals[0].metadata["support_resistance"],
            paper_signals[0].metadata["support_resistance"],
        )

    def test_cached_timeline_replays_invalidation_on_its_effective_date(self) -> None:
        timeline = [
            {
                "zone_key": "support",
                "effective_from": date(2024, 12, 1),
                "effective_to": date(2025, 1, 2),
                "source_kind": "low",
                "role": "support",
                "status": "active",
                "center": 100.0,
                "lower": 99.0,
                "upper": 101.0,
                "atr": 2.0,
                "pivot_keys": ["support:1", "support:2"],
                "pivot_count": 2,
                "touch_count": 2,
                "first_pivot_date": date(2024, 12, 1),
                "last_pivot_date": date(2024, 12, 20),
                "valid_from": date(2024, 12, 23),
            },
            {
                "zone_key": "support",
                "effective_from": date(2025, 1, 3),
                "effective_to": None,
                "source_kind": "low",
                "role": "support",
                "status": "expired",
                "center": 100.0,
                "lower": 99.0,
                "upper": 101.0,
                "atr": 2.0,
                "pivot_keys": ["support:1", "support:2"],
                "pivot_count": 2,
                "touch_count": 2,
                "first_pivot_date": date(2024, 12, 1),
                "last_pivot_date": date(2024, 12, 20),
                "valid_from": date(2024, 12, 23),
            },
        ]
        state = SupportResistanceSymbolState(cached_zone_timeline=timeline)

        for offset in (1, 2, 3):
            advance_symbol(
                state,
                _bar(offset, high=103, low=98 if offset == 2 else 102, close=98 if offset == 2 else 102.5),
                self.signal,
                self.risk,
                emit_signals=False,
            )

        invalidations = [
            event for event in state.events if event["event_type"] == "invalidation"
        ]
        self.assertEqual(
            invalidations,
            [
                {
                    "event_date": "2025-01-03",
                    "event_type": "invalidation",
                    "zone_key": "support",
                    "role": "support",
                    "reason": "close_break",
                    "phase_start": "2025-01-02",
                    "effective_to": "2025-01-02",
                    "close": 98.0,
                    "broken_zone_keys": ["support"],
                }
            ],
        )
        self.assertNotIn("support", state.zones)

    def test_weighted_theil_sen_requires_two_pivots_and_filters_extreme_slope(self) -> None:
        def pivot(index: int, price: float) -> Pivot:
            return Pivot(
                pivot_key=f"low:{index}",
                kind="low",
                session_index=index,
                trade_date=date(2025, 1, 1) + timedelta(days=index),
                confirmed_on=date(2025, 1, 4) + timedelta(days=index),
                price=price,
                atr=1.0,
            )

        self.assertIsNone(_fit_pivot_line([pivot(0, 100)], 20, self.signal))
        self.assertIsNotNone(
            _fit_pivot_line([pivot(0, 100), pivot(10, 101)], 20, self.signal)
        )
        fit = _fit_pivot_line(
            [pivot(0, 100), pivot(10, 101), pivot(20, 102), pivot(15, 110)],
            20,
            self.signal,
        )
        self.assertIsNotNone(fit)
        inliers, center, slope, _, _ = fit
        self.assertEqual([item.session_index for item in inliers], [0, 10, 20])
        self.assertAlmostEqual(center, 102.0)
        self.assertAlmostEqual(slope, 0.1)
        self.assertIsNone(
            _fit_pivot_line(
                [pivot(0, 100), pivot(10, 104), pivot(20, 108)],
                20,
                self.signal,
            )
        )

    def test_independent_low_and_high_lines_keep_at_most_one_zone_per_role(self) -> None:
        state = SupportResistanceSymbolState(
            history=[_bar(index, high=106, low=96, close=101) for index in range(21)]
        )
        for kind, prices in (("low", (98.0, 99.0, 100.0)), ("high", (106.0, 105.0, 104.0))):
            for index, price in zip((0, 10, 20), prices):
                state.pivots.append(
                    Pivot(
                        pivot_key=f"{kind}:{index}",
                        kind=kind,
                        session_index=index,
                        trade_date=date(2025, 1, 1) + timedelta(days=index),
                        confirmed_on=date(2025, 1, 4) + timedelta(days=index),
                        price=price,
                        atr=1.0,
                    )
                )
        _rebuild_zones(state, state.history[-1], self.signal)
        self.assertEqual(len(state.zones), 2)
        by_role = {zone.role: zone for zone in state.zones.values()}
        self.assertAlmostEqual(by_role["support"].slope_per_session, 0.1)
        self.assertAlmostEqual(by_role["resistance"].slope_per_session, -0.1)

    def test_new_high_pivot_line_below_close_is_initialized_as_support(self) -> None:
        state = SupportResistanceSymbolState(
            pivots=[
                Pivot(
                    pivot_key=f"high:{index}",
                    kind="high",
                    session_index=index,
                    trade_date=date(2025, 1, 1) + timedelta(days=index),
                    confirmed_on=date(2025, 1, 4) + timedelta(days=index),
                    price=100.0,
                    atr=1.0,
                )
                for index in (0, 10, 20)
            ],
            history=[_bar(index, high=106, low=99, close=105) for index in range(21)],
        )

        _rebuild_zones(state, state.history[-1], self.signal)

        zone = next(iter(state.zones.values()))
        self.assertEqual(zone.source_kind, "high")
        self.assertEqual(zone.role, "support")

    def test_zone_prices_match_persisted_numeric_precision(self) -> None:
        state = SupportResistanceSymbolState(
            pivots=[
                Pivot(
                    pivot_key="low:1",
                    kind="low",
                    session_index=0,
                    trade_date=date(2025, 1, 1),
                    confirmed_on=date(2025, 1, 4),
                    price=100.12345678916,
                    atr=1.23456789016,
                ),
                Pivot(
                    pivot_key="low:2",
                    kind="low",
                    session_index=10,
                    trade_date=date(2025, 1, 11),
                    confirmed_on=date(2025, 1, 5),
                    price=100.12345678916,
                    atr=1.23456789016,
                ),
                Pivot(
                    pivot_key="low:3",
                    kind="low",
                    session_index=20,
                    trade_date=date(2025, 1, 21),
                    confirmed_on=date(2025, 1, 24),
                    price=100.12345678916,
                    atr=1.23456789016,
                ),
            ],
            history=[_bar(index, high=102, low=99, close=100) for index in range(21)],
        )
        signal = dict(self.signal)
        signal.update({"zone_half_width_atr": 0.5})
        bar = {**state.history[-1], "atr_14": 1.23456789016}

        _rebuild_zones(state, bar, signal)

        zone = next(iter(state.zones.values()))
        self.assertEqual(zone.center, 100.1234567892)
        self.assertEqual(zone.atr, 1.2345678902)
        detector = normalized_detector_params({"signal": self.signal})
        self.assertEqual(detector["implementation_revision"], 14)
        self.assertEqual(detector["regime_logic_revision"], 4)

    def test_rebuild_rejects_geometry_rounded_to_zero_before_recording_a_zone(self) -> None:
        for price, atr, expected_lower in (
            (0.50000000004, 1.0, None),
            (0.50000000006, 1.0, 0.0000000001),
            (1.0, 0.00000000004, None),
        ):
            with self.subTest(price=price, atr=atr):
                state = SupportResistanceSymbolState(
                    history=[_bar(index, high=2, low=0.25, close=1) for index in range(21)],
                    pivots=[
                        Pivot(
                            pivot_key=f"low:{index}", kind="low", session_index=index,
                            trade_date=date(2025, 1, 1) + timedelta(days=index),
                            confirmed_on=date(2025, 1, 4) + timedelta(days=index),
                            price=price, atr=1.0,
                        )
                        for index in (0, 10, 20)
                    ],
                )
                _rebuild_zones(state, {**state.history[-1], "atr_14": atr}, self.signal)

                if expected_lower is None:
                    self.assertEqual(state.zones, {})
                    self.assertEqual(state.zone_versions, [])
                else:
                    self.assertEqual(len(state.zones), 1)
                    self.assertEqual(next(iter(state.zones.values())).lower, expected_lower)

    def test_inner_edge_channel_selection_projection_and_inclusive_price_gate(self) -> None:
        far_support = _zone("far-support", "support", 95)
        near_support = _zone("near-support", "support", 100)
        near_support.slope_per_session = 0.25
        resistance = _zone("resistance", "resistance", 110)
        resistance.slope_per_session = 0.5

        channel = build_entry_channel(
            [far_support, resistance, near_support],
            105,
            date(2025, 1, 2),
        )

        self.assertTrue(channel["valid"])
        self.assertEqual(channel["support_zone_key"], "near-support")
        self.assertEqual((channel["lower"], channel["upper"]), (101.0, 109.0))
        projected = project_entry_channel(channel)
        self.assertEqual((projected["lower"], projected["upper"]), (101.25, 109.5))
        self.assertTrue(entry_price_is_inside_channel(projected, 101.25)[0])
        self.assertTrue(entry_price_is_inside_channel(projected, 109.5)[0])
        self.assertFalse(entry_price_is_inside_channel(projected, 109.5001)[0])

    def test_paper_holding_period_counts_trading_sessions(self) -> None:
        snapshots = {
            "TEST": {
                "recent_bars": [
                    {"dt_ny": date(2025, 1, 3)},
                    {"dt_ny": date(2025, 1, 6)},
                    {"dt_ny": date(2025, 1, 7)},
                ]
            }
        }
        positions = {
            "TEST": VirtualPosition(
                symbol="TEST",
                qty=1,
                avg_entry_price=100,
                entry_trade_date=date(2025, 1, 3),
            )
        }

        _inject_virtual_positions(
            snapshots,
            positions,
            date(2025, 1, 7),
            use_trading_days=True,
        )

        self.assertEqual(snapshots["TEST"]["position_holding_days"], 2)


class SupportResistanceSchemaContractTests(unittest.TestCase):
    def test_sql_and_orm_share_named_constraints_indexes_and_instrument_foreign_keys(self) -> None:
        sql = (
            Path(__file__).resolve().parents[1]
            / "utils"
            / "create_zzzzzz_support_resistance.sql"
        ).read_text(encoding="utf-8")
        tables = (
            SupportResistanceMaterialization.__table__,
            SupportResistanceZoneVersion.__table__,
            SupportResistanceRegimeVersion.__table__,
            SupportResistanceRunMaterialization.__table__,
            SupportResistanceRunEvent.__table__,
            SupportResistanceMaterializationEvent.__table__,
        )

        expected_names = {
            item.name
            for table in tables
            for item in (*table.constraints, *table.indexes)
            if item.name
        }
        for name in expected_names:
            self.assertIn(name, sql)

        for table in (
            SupportResistanceZoneVersion.__table__,
            SupportResistanceRegimeVersion.__table__,
            SupportResistanceRunEvent.__table__,
            SupportResistanceMaterializationEvent.__table__,
        ):
            foreign_key = next(iter(table.c.instrument_id.foreign_keys))
            self.assertEqual(foreign_key.target_fullname, "instruments.id")
            self.assertEqual(foreign_key.ondelete, "SET NULL")

        regime_unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in SupportResistanceRegimeVersion.__table__.constraints
            if constraint.name
            in {
                "uq_support_resistance_regime_versions_identity",
                "uq_support_resistance_regime_versions_effective_from",
            }
        }
        self.assertEqual(
            regime_unique_columns,
            {
                ("materialization_id", "instrument_id", "version"),
                ("materialization_id", "instrument_id", "effective_from"),
            },
        )

        self.assertTrue(
            {"sic_code", "sic_description", "sic_source", "sic_asof"}
            <= set(Instrument.__table__.c.keys())
        )


class SupportResistancePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(
                Instrument(
                    id=1,
                    share_class_figi="TEST-FIGI",
                    ticker_canonical="TEST",
                    exchange="XNYS",
                )
            )
        self.params = normalize_strategy_params("support_resistance", {})
        self.runtime = {
            "strategy_id": "runtime",
            "strategy_type": "support_resistance",
            "params": self.params,
        }

    def _new_run(self, db) -> StrategyRun:
        strategy = Strategy(
            id=uuid.uuid4(),
            strategy_key=f"sr-{uuid.uuid4()}",
            name="SR",
            strategy_type="support_resistance",
            params=self.params,
            version=1,
            status="draft",
        )
        run = StrategyRun(
            id=uuid.uuid4(),
            strategy=strategy,
            strategy_version=1,
            mode="backtest",
            status="running",
            window_start=date(2025, 1, 1),
            window_end=date(2025, 3, 1),
            config_snapshot=self.params,
        )
        db.add_all([strategy, run])
        db.flush()
        return run

    def _state(self) -> SupportResistanceState:
        state = SupportResistanceState()
        symbol_state = SupportResistanceSymbolState()
        symbol_state.history.append(_bar(0, high=102, low=99, close=100))
        symbol_state.history.append(_bar(9, high=103, low=100, close=101))
        symbol_state.regime_versions.append(
            {
                "version": 1,
                "effective_from": "2025-01-01",
                "regime": "transition",
                "lower_zone_key": "zone",
                "upper_zone_key": None,
                "reason_code": "missing_boundary",
                "evidence": {"reason_code": "missing_boundary"},
            }
        )
        symbol_state.zone_versions.append(
            {
                **_zone("zone", "support", 100).snapshot(),
                "status": "active",
                "effective_from": "2025-01-10",
            }
        )
        symbol_state.events.append(
            {
                "event_date": "2025-01-12",
                "event_type": "touch",
                "zone_key": "zone",
                "role": "support",
                "lower": 99,
                "upper": 101,
            }
        )
        state.symbols["TEST"] = symbol_state
        return state

    @patch("src.services.support_resistance_persistence_service._instrument_ids", return_value={"TEST": 1})
    def test_native_view_streams_the_same_persisted_audit(self, _ids) -> None:
        import json
        from types import SimpleNamespace
        import numpy as np
        from src.services.native_support_state import NativeSupportState
        from src.services.prepared_dataset_service import PREPARED_INTEGER_FIELDS, PREPARED_INTEGER_INDEX

        state = self._state()
        original = state.symbols["TEST"]
        integers = np.zeros((2, len(PREPARED_INTEGER_FIELDS)), dtype=np.int64)
        integers[:, PREPARED_INTEGER_INDEX["instrument_id"]] = 1
        integers[:, PREPARED_INTEGER_INDEX["dt_ordinal"]] = [row["dt_ny"].toordinal() for row in original.history]
        support = {
            key: {
                "instrument_id": np.ones(len(getattr(original, key)), dtype=np.int64),
                "symbol_id": np.zeros(len(getattr(original, key)), dtype=np.int64),
                "payload_json": [json.dumps(row, default=str) for row in getattr(original, key)],
            }
            for key in ("events", "zone_versions", "regime_versions")
        }
        events = support["events"]
        events.update({
            "materialization_event": np.ones(len(original.events), dtype=np.uint8),
            "event_date_ordinal": np.array([
                date.fromisoformat(row["event_date"]).toordinal()
                for row in original.events
            ], dtype=np.int32),
            "event_type": [row["event_type"] for row in original.events],
            "zone_key": [row.get("zone_key") or "" for row in original.events],
            "setup": [row.get("setup") or "" for row in original.events],
            "score": np.array([
                row.get("score", np.nan) for row in original.events
            ], dtype=float),
            "posterior_sample_count": np.array([-1] * len(original.events), dtype=np.int64),
            "lower_price": np.array([
                row.get("lower", np.nan) for row in original.events
            ], dtype=float),
            "upper_price": np.array([
                row.get("upper", np.nan) for row in original.events
            ], dtype=float),
        })
        native = NativeSupportState(SimpleNamespace(symbols=["TEST"], support_resistance=support), SimpleNamespace(integers=integers))

        def write_batches(db, model, rows, **kwargs):
            if model is SupportResistanceRunEvent:
                self.assertNotIsInstance(rows, list)
            return _insert_in_batches(db, model, rows, **kwargs)

        with self.Session.begin() as db, patch(
            "src.services.support_resistance_persistence_service._insert_in_batches", side_effect=write_batches,
        ):
            runs = []
            caches = []
            for value in (native, state):
                run = self._new_run(db)
                runs.append(run)
                caches.append(persist_support_resistance_run(
                    db, run=run, runtime=self.runtime, state=value, symbols=["TEST"],
                    coverage_start=date(2025, 1, 1), coverage_end=date(2025, 3, 1), batch_size=1,
                ))
            self.assertEqual(caches[0].id, caches[1].id)
            self.assertEqual(caches[0].statistics["zone_version_count"], 1)
            self.assertEqual(caches[0].statistics["regime_version_count"], 1)
            self.assertEqual(
                db.scalar(select(SupportResistanceMaterializationEvent.payload)),
                original.events[0],
            )
            self.assertEqual(
                [db.scalar(select(func.count()).select_from(SupportResistanceRunEvent).where(
                    SupportResistanceRunEvent.run_id == run.id
                )) for run in runs],
                [0, 0],
            )

    def test_instrument_ids_use_unique_point_in_time_symbol_history(self) -> None:
        with self.Session.begin() as db:
            db.add_all(
                [
                    Instrument(
                        id=2,
                        share_class_figi="OLD-FIGI",
                        ticker_canonical="NEW",
                        exchange="XNYS",
                    ),
                    Instrument(
                        id=3,
                        share_class_figi="REUSED-FIGI",
                        ticker_canonical="LATEST",
                        exchange="XNYS",
                    ),
                ]
            )
            db.execute(
                text(
                    """
                    CREATE TABLE symbol_history (
                        instrument_id BIGINT NOT NULL,
                        symbol TEXT NOT NULL,
                        valid_from DATE NOT NULL,
                        valid_to DATE,
                        is_primary BOOLEAN NOT NULL
                    )
                    """
                )
            )
            db.execute(
                text(
                    """
                    INSERT INTO symbol_history (
                        instrument_id, symbol, valid_from, valid_to, is_primary
                    )
                    VALUES
                        (2, 'OLD', '2024-01-01', '2025-06-30', TRUE),
                        (2, 'AMBIG', '2024-01-01', '2025-06-30', TRUE),
                        (3, 'AMBIG', '2025-06-01', NULL, TRUE),
                        (3, 'SECONDARY', '2024-01-01', NULL, FALSE)
                    """
                )
            )

        with self.Session() as db:
            resolved = _instrument_ids(
                db,
                ["TEST", "OLD", "AMBIG", "SECONDARY"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 12, 31),
            )

        self.assertEqual(resolved, {"TEST": 1, "OLD": 2})

    def test_reused_ticker_regime_rows_keep_native_instrument_identity(self) -> None:
        state = SupportResistanceState()
        for instrument_id in (11, 12):
            state.symbols[str(instrument_id)] = SupportResistanceSymbolState(
                instrument_id=instrument_id,
                symbol="SAME",
                history=[{"dt_ny": date(2025, 1, 1)}],
                regime_versions=[
                    {
                        "version": 1,
                        "effective_from": "2025-01-01",
                        "regime": "transition",
                        "reason_code": f"initial-{instrument_id}",
                    }
                ],
            )

        with self.Session() as db:
            db.add_all(
                [
                    Instrument(
                        id=11,
                        share_class_figi="SAME-OLD-FIGI",
                        ticker_canonical="SAME-OLD",
                        exchange="XNYS",
                    ),
                    Instrument(
                        id=12,
                        share_class_figi="SAME-NEW-FIGI",
                        ticker_canonical="SAME-NEW",
                        exchange="XNYS",
                    ),
                ]
            )
            with patch(
                "src.services.support_resistance_persistence_service._instrument_ids",
                return_value={},
            ):
                materialization = persist_support_resistance_run(
                    db,
                    run=self._new_run(db),
                    runtime=self.runtime,
                    state=state,
                    symbols=["SAME"],
                    coverage_start=date(2025, 1, 1),
                    coverage_end=date(2025, 1, 1),
                )
            db.commit()
            rows = db.scalars(
                select(SupportResistanceRegimeVersion)
                .where(
                    SupportResistanceRegimeVersion.materialization_id
                    == materialization.id
                )
                .order_by(SupportResistanceRegimeVersion.instrument_id)
            ).all()
            hydrated = hydrate_state_from_materialization(db, materialization)

        self.assertEqual(
            [(row.instrument_id, row.symbol, row.version) for row in rows],
            [(11, "SAME", 1), (12, "SAME", 1)],
        )
        self.assertEqual(set(hydrated.symbols), {"11", "12"})
        current_payload = support_resistance_hydration_payload(
            hydrated,
            {"SAME": {"instrument_id": 12}},
        )
        self.assertEqual(
            current_payload["SAME"]["regime_timeline"][0]["reason_code"],
            "initial-12",
        )

    def test_postgres_batches_use_psycopg_copy_with_canonical_json(self) -> None:
        db = MagicMock()
        db.get_bind.return_value.dialect.name = "postgresql"
        db.get_bind.return_value.dialect.driver = "psycopg"
        raw = MagicMock()
        db.connection.return_value.connection.driver_connection = raw
        writer = raw.cursor.return_value.__enter__.return_value.copy.return_value.__enter__.return_value
        run_id = uuid.uuid4()
        materialization_id = uuid.uuid4()
        rows = [
            {
                "run_id": run_id,
                "materialization_id": materialization_id,
                "instrument_id": 1,
                "symbol": "TEST",
                "event_date": date(2025, 1, 2),
                "event_type": "touch",
                "zone_key": "zone",
                "setup": None,
                "selected": False,
                "score": 0.5,
                "posterior_sample_count": 2,
                "lower_price": 99.0,
                "upper_price": 101.0,
                "payload": {"z": 2, "a": 1},
            }
        ]

        written = _insert_in_batches(
            db,
            SupportResistanceRunEvent,
            rows,
            batch_size=1,
        )

        self.assertEqual(written, 1)
        statement = raw.cursor.return_value.__enter__.return_value.copy.call_args.args[0]
        self.assertTrue(statement.startswith("COPY support_resistance_run_events (id,run_id,"))
        copied = writer.write_row.call_args.args[0]
        self.assertIsInstance(copied[0], uuid.UUID)
        self.assertEqual(copied[-1], '{"a":1,"z":2}')
        db.execute.assert_not_called()

    def test_support_resistance_copy_checks_cancellation_before_each_batch(self) -> None:
        db = MagicMock()
        with self.assertRaisesRegex(RuntimeError, "before support/resistance COPY batch"):
            _insert_in_batches(
                db,
                SupportResistanceRunEvent,
                [{"run_id": uuid.uuid4(), "payload": {}}],
                batch_size=1,
                cancel_check=lambda: True,
            )
        db.get_bind.assert_not_called()

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_invalid_event_geometry_is_rejected_before_detail_insert(self, _ids) -> None:
        state = self._state()
        state.symbols["TEST"].events[0]["lower"] = float("nan")
        with self.Session() as db:
            run = self._new_run(db)
            with self.assertRaisesRegex(
                SupportResistanceMaterializationBuildError,
                "event geometry exceeds",
            ):
                persist_support_resistance_run(
                    db,
                    run=run,
                    runtime=self.runtime,
                    state=state,
                    symbols=["TEST"],
                    coverage_start=date(2025, 1, 1),
                    coverage_end=date(2025, 3, 1),
                )
            db.rollback()
            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceRunEvent)),
                0,
            )
            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceZoneVersion)),
                0,
            )

    def test_regime_timeline_integrity_rejects_invalid_version_sequences(self) -> None:
        sessions = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]

        def version(number: int, effective_from: date, regime: str) -> dict:
            return {
                "version": number,
                "effective_from": effective_from.isoformat(),
                "regime": regime,
            }

        _validate_regime_versions(
            "TEST",
            sessions,
            [version(1, sessions[0], "transition"), version(2, sessions[2], "uptrend")],
        )
        invalid_sequences = (
            [],
            [version(1, sessions[1], "transition")],
            [version(1, sessions[0], "transition"), version(2, sessions[0], "uptrend")],
            [version(1, sessions[0], "transition"), version(2, sessions[2], "transition")],
            [version(1, sessions[0], "transition"), version(3, sessions[2], "uptrend")],
            [version(1, sessions[0], "transition"), version(2, date(2025, 1, 4), "uptrend")],
            [version(1, sessions[0], "unknown")],
        )
        for items in invalid_sequences:
            with self.subTest(items=items), self.assertRaises(ValueError):
                _validate_regime_versions("TEST", sessions, items)

    def test_expired_zone_tombstone_is_not_projected_to_coverage_end(self) -> None:
        materialization = SupportResistanceMaterialization(
            id=uuid.uuid4(),
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 1, 3),
        )
        zone = _zone("expired", "support", 100)
        zone.anchor_session_index = 0
        zone.anchor_center = 100
        zone.anchor_lower = 99
        zone.anchor_upper = 101
        zone.slope_per_session = -10
        state = SupportResistanceState(
            symbols={
                "TEST": SupportResistanceSymbolState(
                    history=[
                        _bar(0, high=103, low=99, close=101),
                        _bar(1, high=103, low=99, close=101),
                        _bar(2, high=103, low=99, close=101),
                    ],
                    zone_versions=[
                        {
                            **zone.snapshot(),
                            "status": "expired",
                            "effective_from": "2025-01-02",
                        }
                    ],
                )
            }
        )

        row = list(_zone_version_rows(materialization, state, {"TEST": 1}))[0]

        self.assertEqual(row["projection_end"], date(2025, 1, 2))
        self.assertEqual(row["center_price"], 100)
        self.assertEqual(row["end_center_price"], 100)

    def test_unrepresentable_zone_geometry_fails_before_database_insert(self) -> None:
        materialization = SupportResistanceMaterialization(
            id=uuid.uuid4(),
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 1, 1),
        )
        zone = _zone("overflow", "support", 100)
        zone.center = 100_000_000_000_000.0
        zone.lower = 99_999_999_999_999.0
        zone.upper = 100_000_000_000_001.0
        zone.anchor_center = zone.center
        zone.anchor_lower = zone.lower
        zone.anchor_upper = zone.upper
        zone.anchor_session_index = 0
        state = SupportResistanceState(
            symbols={
                "TEST": SupportResistanceSymbolState(
                    history=[_bar(0, high=103, low=99, close=101)],
                    zone_versions=[
                        {
                            **zone.snapshot(),
                            "status": "active",
                            "effective_from": "2025-01-01",
                        }
                    ],
                )
            }
        )

        with self.assertRaisesRegex(ValueError, "NUMERIC\\(24,10\\) domain"):
            list(_zone_version_rows(materialization, state, {"TEST": 1}))

    def test_duplicate_zone_effective_date_fails_before_database_insert(self) -> None:
        materialization = SupportResistanceMaterialization(
            id=uuid.uuid4(),
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 1, 2),
        )
        zone = _zone("duplicate", "support", 100)
        state = SupportResistanceState(
            symbols={
                "TEST": SupportResistanceSymbolState(
                    history=[
                        _bar(0, high=103, low=99, close=101),
                        _bar(1, high=103, low=99, close=101),
                    ],
                    zone_versions=[
                        {**zone.snapshot(), "status": "expired", "effective_from": "2025-01-02"},
                        {**zone.snapshot(), "status": "active", "effective_from": "2025-01-02"},
                    ],
                )
            }
        )

        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            list(_zone_version_rows(materialization, state, {"TEST": 1}))

    def test_regime_report_attributes_fills_and_realized_returns(self) -> None:
        with self.Session() as db:
            run = self._new_run(db)
            db.add_all(
                [
                    Transaction(
                        strategy_id=run.strategy_id,
                        run_id=run.id,
                        instrument_id=1,
                        ts=datetime(2025, 1, 2, tzinfo=UTC),
                        symbol="TEST",
                        side="BUY",
                        qty=10,
                        price=100,
                        fee=1,
                        meta={
                            "entry_signal_features": {
                                "support_resistance": {
                                    "regime": "uptrend",
                                    "selected_setup": "support_bounce",
                                }
                            }
                        },
                    ),
                    Transaction(
                        strategy_id=run.strategy_id,
                        run_id=run.id,
                        instrument_id=1,
                        ts=datetime(2025, 1, 6, tzinfo=UTC),
                        symbol="TEST",
                        side="SELL",
                        qty=10,
                        price=110,
                        fee=1,
                        meta={"reason": "confirmed downtrend regime"},
                    ),
                ]
            )
            db.flush()
            audit = _trade_results_by_regime(db, run.id)
            self.assertEqual(audit["filledCounts"], {"uptrend/support_bounce": 1})
            self.assertGreater(
                audit["realizedReturns"]["uptrend/support_bounce"]["mean"],
                0,
            )
            self.assertEqual(len(audit["downtrendExitPerformance"]["exits"]), 1)

    def test_historical_v2_strategy_cannot_start_a_new_backtest(self) -> None:
        with self.Session() as db:
            params = copy.deepcopy(self.params)
            params["metadata"]["algorithm_version"] = "pivot-slope-atr-v2"
            strategy = Strategy(
                id=uuid.uuid4(),
                strategy_key=f"sr-v2-{uuid.uuid4()}",
                name="Historical SR v2",
                strategy_type="support_resistance",
                params=params,
                version=1,
                status="draft",
            )
            db.add(strategy)
            db.commit()
            with self.assertRaises(HTTPException) as raised:
                create_backtest(
                    BacktestCreate(
                        strategy_id=strategy.id,
                        start_date=date(2025, 1, 1),
                        end_date=date(2025, 1, 31),
                    ),
                    db,
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["code"], "historical_strategy_read_only")

    def test_v3_api_rejects_a_missing_regime_timeline_but_v2_remains_empty(self) -> None:
        with self.Session() as db:
            materialization = SupportResistanceMaterialization(
                id=uuid.uuid4(),
                cache_key="missing-regimes-v3",
                algorithm_version="pivot-slope-regime-v3",
                detector_params={},
                universe_hash="universe",
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 1, 31),
                price_semantics="forward_adjusted_preferred_unadjusted_fallback",
                status="completed",
            )
            with self.assertRaises(ValueError):
                _build_regime_intervals(db, materialization, [])
            materialization.algorithm_version = "pivot-slope-atr-v2"
            self.assertEqual(_build_regime_intervals(db, materialization, []), [])

    def test_regime_api_starts_at_the_first_persisted_state_session(self) -> None:
        class FakeDialect:
            name = "postgresql"

        class FakeBind:
            dialect = FakeDialect()

        class FakeResult:
            def all(self):
                return [
                    (211, date(2016, 10, 26)),
                    (211, date(2016, 10, 27)),
                    (211, date(2016, 10, 28)),
                ]

        class FakeSession:
            bind = FakeBind()

            def execute(self, *_args, **_kwargs):
                return FakeResult()

        materialization = SupportResistanceMaterialization(
            id=uuid.uuid4(),
            algorithm_version="pivot-slope-regime-v3",
            coverage_start=date(2016, 10, 26),
            coverage_end=date(2016, 10, 28),
        )
        version = SupportResistanceRegimeVersion(
            id=uuid.uuid4(),
            materialization_id=materialization.id,
            instrument_id=211,
            symbol="AMN",
            version=1,
            effective_from=date(2016, 10, 27),
            regime="transition",
            reason_code="first_identity_session",
            evidence={},
        )

        intervals = _build_regime_intervals(FakeSession(), materialization, [version])

        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["start_date"], "2016-10-27")
        self.assertEqual(intervals[0]["end_date"], "2016-10-28")
        self.assertEqual(intervals[0]["session_count"], 2)

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_cache_hit_reuses_shared_rows_and_run_delete_keeps_cache(self, _ids) -> None:
        with self.Session() as db:
            first_run = self._new_run(db)
            first = persist_support_resistance_run(
                db,
                run=first_run,
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()
            self.assertIsNone(
                find_reusable_materialization(
                    db,
                    runtime=self.runtime,
                    symbols=["TEST"],
                    coverage_start=date(2025, 1, 15),
                    coverage_end=date(2025, 2, 15),
                )
            )
            second_run = self._new_run(db)
            second = persist_support_resistance_run(
                db,
                run=second_run,
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()

            self.assertEqual(first.id, second.id)
            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceMaterialization)),
                1,
            )
            db.delete(first_run)
            db.commit()
            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceMaterialization)),
                1,
            )
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(SupportResistanceRunEvent)
                    .where(SupportResistanceRunEvent.run_id == first_run.id)
                ),
                0,
            )
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(SupportResistanceRunMaterialization)
                    .where(SupportResistanceRunMaterialization.run_id == first_run.id)
                ),
                0,
            )

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_bulk_persistence_reports_batches_and_exact_rows(self, _ids) -> None:
        progress: list[tuple[str, int, int]] = []
        performance: dict = {}
        state = self._state()
        base_event = dict(state.symbols["TEST"].events[0])
        for index, event_type in enumerate(
            ["candidate", "selection", "breakout", "retest", "invalidation", "role_transition", "score_outcome"],
            start=1,
        ):
            state.symbols["TEST"].events.append(
                {
                    **base_event,
                    "event_date": f"2025-01-{12 + index:02d}",
                    "event_type": event_type,
                    "score": index / 10,
                    "score_evidence": {"resolved_samples": index},
                }
            )

        with self.Session() as db:
            run = self._new_run(db)
            materialization = persist_support_resistance_run(
                db,
                run=run,
                runtime=self.runtime,
                state=state,
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
                performance=performance,
                progress_callback=lambda stage, completed, total: progress.append(
                    (stage, completed, total)
                ),
                batch_size=2,
            )
            db.commit()

            versions = db.scalars(
                select(SupportResistanceZoneVersion).where(
                    SupportResistanceZoneVersion.materialization_id == materialization.id
                )
            ).all()
            regime_versions = db.scalars(
                select(SupportResistanceRegimeVersion).where(
                    SupportResistanceRegimeVersion.materialization_id == materialization.id
                )
            ).all()
            events = db.scalars(
                select(SupportResistanceRunEvent)
                .where(SupportResistanceRunEvent.run_id == run.id)
                .order_by(SupportResistanceRunEvent.event_date)
            ).all()
            materialization_events = db.scalars(
                select(SupportResistanceMaterializationEvent).where(
                    SupportResistanceMaterializationEvent.materialization_id == materialization.id
                )
            ).all()

            self.assertEqual(len(versions), 1)
            self.assertEqual(len(regime_versions), 1)
            self.assertEqual(regime_versions[0].regime, "transition")
            self.assertEqual(len(events), 6)
            self.assertEqual(len(materialization_events), 2)
            version = versions[0]
            self.assertEqual(
                {
                    "symbol": version.symbol,
                    "zone_key": version.zone_key,
                    "version": version.version,
                    "effective_from": version.effective_from,
                    "effective_to": version.effective_to,
                    "role": version.role,
                    "status": version.status,
                    "center_price": float(version.center_price),
                    "lower_price": float(version.lower_price),
                    "upper_price": float(version.upper_price),
                    "projection_end": version.projection_end,
                    "pivot_count": version.pivot_count,
                    "touch_count": version.touch_count,
                },
                {
                    "symbol": "TEST",
                    "zone_key": "zone",
                    "version": 1,
                    "effective_from": date(2025, 1, 10),
                    "effective_to": None,
                    "role": "support",
                    "status": "active",
                    "center_price": 100.0,
                    "lower_price": 99.0,
                    "upper_price": 101.0,
                    "projection_end": date(2025, 1, 10),
                    "pivot_count": 3,
                    "touch_count": 3,
                },
            )
            self.assertEqual(
                {item.event_type for item in [*events, *materialization_events]},
                {"touch", "candidate", "selection", "breakout", "retest", "invalidation", "role_transition", "score_outcome"},
            )
            source_events = sorted(state.symbols["TEST"].events, key=lambda item: item["event_date"])
            persisted_events = sorted(
                [*events, *materialization_events], key=lambda item: item.event_date
            )
            self.assertEqual(
                [
                    {
                        "event_date": item.event_date.isoformat(),
                        "event_type": item.event_type,
                        "zone_key": item.zone_key,
                        "setup": item.setup,
                        "selected": item.selected,
                        "score": float(item.score) if item.score is not None else None,
                        "posterior_sample_count": item.posterior_sample_count,
                        "lower_price": float(item.lower_price) if item.lower_price is not None else None,
                        "upper_price": float(item.upper_price) if item.upper_price is not None else None,
                        "payload": item.payload,
                    }
                    for item in persisted_events
                ],
                [
                    {
                        "event_date": item["event_date"],
                        "event_type": item["event_type"],
                        "zone_key": item.get("zone_key"),
                        "setup": item.get("setup"),
                        "selected": item["event_type"] == "selection",
                        "score": item.get("score"),
                        "posterior_sample_count": (item.get("score_evidence") or {}).get(
                            "resolved_samples"
                        ),
                        "lower_price": float(item["lower"]),
                        "upper_price": float(item["upper"]),
                        "payload": item,
                    }
                    for item in source_events
                ],
            )
            self.assertEqual(events[-1].posterior_sample_count, 7)
            self.assertEqual(performance["support_resistance_zone_versions"], 1)
            self.assertEqual(performance["support_resistance_regime_versions"], 1)
            self.assertEqual(performance["support_resistance_run_events"], 6)
            self.assertEqual(performance["support_resistance_materialization_events"], 2)
            self.assertFalse(performance["support_resistance_cache_reused"])
            self.assertGreaterEqual(performance["support_resistance_persist_total_ms"], 0.0)
            self.assertEqual(progress[0], ("zone_versions", 0, 10))
            self.assertEqual(progress[-1], ("run_events", 10, 10))

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_partial_bulk_insert_rolls_back_without_run_events(self, _ids) -> None:
        with self.Session() as db:
            first_run = self._new_run(db)
            persist_support_resistance_run(
                db,
                run=first_run,
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()

            second_run = self._new_run(db)
            second_state = self._state()
            second_state.symbols["TEST"].events.append({
                **second_state.symbols["TEST"].events[0],
                "event_type": "candidate",
            })

            def insert_one_then_fail(session, model, rows, **_kwargs):
                first = next(iter(rows))
                session.execute(insert(model), [first])
                raise RuntimeError("synthetic batch failure")

            with patch(
                "src.services.support_resistance_persistence_service._insert_in_batches",
                side_effect=insert_one_then_fail,
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic batch failure"):
                    persist_support_resistance_run(
                        db,
                        run=second_run,
                        runtime=self.runtime,
                        state=second_state,
                        symbols=["TEST"],
                        coverage_start=date(2025, 1, 1),
                        coverage_end=date(2025, 3, 1),
                    )
            db.rollback()

            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(SupportResistanceRunEvent)
                    .where(SupportResistanceRunEvent.run_id == second_run.id)
                ),
                0,
            )
    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_invalidated_materialization_is_not_reused(self, _ids) -> None:
        with self.Session() as db:
            first = persist_support_resistance_run(
                db,
                run=self._new_run(db),
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()
            first.invalidated_at = datetime.now(UTC)
            db.commit()
            second = persist_support_resistance_run(
                db,
                run=self._new_run(db),
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()
            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceMaterialization)),
                2,
            )
            self.assertNotEqual(first.id, second.id)

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_zone_width_parameter_change_creates_cache_miss(self, _ids) -> None:
        with self.Session() as db:
            persist_support_resistance_run(
                db,
                run=self._new_run(db),
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()

            changed_runtime = copy.deepcopy(self.runtime)
            changed_runtime["params"] = normalize_strategy_params(
                "support_resistance",
                {"signal": {"zone_half_width_atr": 0.6}},
            )
            persist_support_resistance_run(
                db,
                run=self._new_run(db),
                runtime=changed_runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()

            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceMaterialization)),
                2,
            )

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_failed_materialization_is_persisted_but_never_reused(self, _ids) -> None:
        with self.Session() as db:
            with patch(
                "src.services.support_resistance_persistence_service._write_zone_versions",
                side_effect=RuntimeError("synthetic zone write failure"),
            ):
                with self.assertRaises(SupportResistanceMaterializationBuildError) as raised:
                    persist_support_resistance_run(
                        db,
                        run=self._new_run(db),
                        runtime=self.runtime,
                        state=self._state(),
                        symbols=["TEST"],
                        coverage_start=date(2025, 1, 1),
                        coverage_end=date(2025, 3, 1),
                    )
            db.rollback()
            failed = record_failed_materialization_after_rollback(db, raised.exception)
            db.commit()

            self.assertEqual(failed.status, "failed")
            self.assertIn("synthetic zone write failure", failed.error_message)
            reusable = find_reusable_materialization(
                db,
                runtime=self.runtime,
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            self.assertIsNone(reusable)

    def test_paper_build_failure_happens_before_any_order_submission(self) -> None:
        class BrokerStub:
            def __init__(self) -> None:
                self.submissions: list[dict] = []

            def get_account(self):
                return {
                    "id": "paper-account",
                    "status": "ACTIVE",
                    "cash": "100000",
                    "equity": "100000",
                    "buying_power": "100000",
                }

            def list_positions(self):
                return []

            def list_orders(self, *, status: str):
                return []

            def submit_order(self, **kwargs):
                self.submissions.append(kwargs)
                return {"id": "should-not-submit", "status": "accepted"}

        with self.Session() as db:
            params = normalize_strategy_params(
                "support_resistance",
                {"universe": {"symbols": ["TEST"], "selection_mode": "manual"}},
            )
            strategy = Strategy(
                id=uuid.uuid4(),
                strategy_key="paper-support-resistance",
                name="Paper SR",
                strategy_type="support_resistance",
                params=params,
                version=1,
                status="active",
            )
            account = PaperTradingAccount(id=uuid.uuid4(), name="Paper")
            portfolio = StrategyPortfolio(
                id=uuid.uuid4(),
                paper_account=account,
                name="default",
                status="active",
            )
            db.add_all([strategy, account, portfolio])
            db.commit()
            broker = BrokerStub()
            snapshot = {
                "symbol": "TEST",
                **_bar(1, high=103, low=100, close=102, volume=200),
            }
            snapshot["recent_bars"] = [snapshot]

            with (
                patch(
                    "src.services.paper_trading_service.build_broker_account_isolation_report",
                    return_value={
                        "status": "ok",
                        "active_external_order_count": 0,
                        "active_system_untracked_order_count": 0,
                        "active_external_position_count": 0,
                        "position_mismatch_count": 0,
                        "warnings": [],
                    },
                ),
                patch(
                    "src.services.paper_trading_service.load_feature_market_data",
                    return_value={"TEST": snapshot},
                ),
                patch(
                    "src.services.support_resistance_persistence_service._instrument_ids",
                    return_value={"TEST": 1},
                ),
                patch(
                    "src.services.support_resistance_persistence_service._write_zone_versions",
                    side_effect=RuntimeError("synthetic paper materialization failure"),
                ),
                self.assertLogs("paper_trading", level="ERROR"),
            ):
                with self.assertRaises(SupportResistanceMaterializationBuildError):
                    run_paper_trading(
                        db,
                        strategy.id,
                        date(2025, 1, 2),
                        alpaca_client=broker,
                        submit_orders=True,
                    )

            self.assertEqual(broker.submissions, [])
            failed_run = db.execute(
                select(StrategyRun)
                .where(StrategyRun.strategy_id == strategy.id)
                .order_by(StrategyRun.created_at.desc())
            ).scalars().first()
            self.assertEqual(failed_run.status, "failed")
            self.assertEqual(
                db.scalar(select(func.count()).select_from(Signal)),
                0,
            )
            failed_materialization = db.execute(
                select(SupportResistanceMaterialization)
                .where(SupportResistanceMaterialization.status == "failed")
            ).scalars().one()
            self.assertIn("synthetic paper materialization failure", failed_materialization.error_message)

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    def test_audit_api_filters_entry_zone_and_returns_empty_state(self, _ids) -> None:
        with self.Session() as db:
            empty_run = self._new_run(db)
            db.commit()
            empty = get_backtest_support_resistance(
                empty_run.id,
                db=db,
                symbol=None,
                zone_key=None,
                start_date=None,
                end_date=None,
            )
            self.assertIsNone(empty.materialization)
            self.assertEqual(empty.zone_versions, [])
            self.assertEqual(empty.regime_intervals, [])
            self.assertEqual(empty.events, [])

            run = self._new_run(db)
            state = self._state()
            state.symbols["TEST"].events.append(
                {
                    "event_date": "2025-01-13",
                    "event_type": "candidate",
                    "zone_key": "zone",
                    "setup": "support_bounce",
                    "score": 0.5,
                    "score_evidence": {"resolved_samples": 0},
                    "zone": _zone("zone", "support", 100).snapshot(),
                }
            )
            persist_support_resistance_run(
                db,
                run=run,
                runtime=self.runtime,
                state=state,
                symbols=["TEST"],
                coverage_start=date(2025, 1, 1),
                coverage_end=date(2025, 3, 1),
            )
            db.commit()
            linked = db.scalar(
                select(SupportResistanceRunMaterialization).where(
                    SupportResistanceRunMaterialization.run_id == run.id
                )
            )
            db.add(
                Instrument(
                    id=2,
                    share_class_figi="OTHER-FIGI",
                    ticker_canonical="OTHER",
                    exchange="XNYS",
                )
            )
            db.add(
                SupportResistanceRegimeVersion(
                    materialization_id=linked.materialization_id,
                    instrument_id=2,
                    symbol="OTHER",
                    version=1,
                    effective_from=date(2025, 1, 1),
                    regime="transition",
                    reason_code="test_other_symbol",
                    evidence={},
                )
            )
            db.commit()
            with patch(
                "src.api.backtests._build_regime_intervals",
                wraps=_build_regime_intervals,
            ) as build_intervals:
                detail = get_backtest_support_resistance(
                    run.id,
                    db=db,
                    symbol="test",
                    zone_key="zone",
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 2, 1),
                )
            queried_regime_versions = build_intervals.call_args.args[2]
            self.assertEqual({version.symbol for version in queried_regime_versions}, {"TEST"})
            self.assertEqual(detail.materialization["status"], "completed")
            self.assertEqual(len(detail.zone_versions), 1)
            self.assertEqual(detail.zone_versions[0]["zone_key"], "zone")
            self.assertEqual(len(detail.regime_intervals), 1)
            self.assertEqual(detail.regime_intervals[0]["regime"], "transition")
            self.assertEqual(len(detail.events), 2)
            candidate = next(event for event in detail.events if event["event_type"] == "candidate")
            self.assertEqual(candidate["posterior_sample_count"], 0)

            with patch(
                "src.api.backtests._build_regime_intervals",
                wraps=_build_regime_intervals,
            ) as build_all_intervals:
                full_detail = get_backtest_support_resistance(
                    run.id,
                    db=db,
                    symbol=None,
                    zone_key=None,
                    start_date=None,
                    end_date=None,
                )
            all_regime_versions = build_all_intervals.call_args.args[2]
            self.assertEqual({version.symbol for version in all_regime_versions}, {"OTHER", "TEST"})
            self.assertEqual(
                {interval["symbol"] for interval in full_detail.regime_intervals},
                {"OTHER", "TEST"},
            )

            no_symbol_match = get_backtest_support_resistance(
                run.id,
                db=db,
                symbol="MISSING",
                zone_key=None,
                start_date=None,
                end_date=None,
            )
            self.assertEqual(no_symbol_match.regime_intervals, [])

            with self.assertRaises(HTTPException) as raised:
                get_backtest_support_resistance(
                    run.id,
                    db=db,
                    symbol=None,
                    zone_key=None,
                    start_date=date(2025, 2, 1),
                    end_date=date(2025, 1, 1),
                )
            self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
