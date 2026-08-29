from __future__ import annotations

import sys
import unittest
import uuid
import copy
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, func, select
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
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    SupportResistanceZoneVersion,
)
from src.services.support_resistance_persistence_service import (  # noqa: E402
    SupportResistanceMaterializationBuildError,
    find_reusable_materialization,
    persist_support_resistance_run,
    record_failed_materialization_after_rollback,
)
from src.services.support_resistance_service import (  # noqa: E402
    BreakoutRecord,
    PendingOutcome,
    Pivot,
    SupportResistanceSymbolState,
    Zone,
    SupportResistanceState,
    _match_zone,
    _rebuild_zones,
    advance_symbol,
    normalized_detector_params,
)
from src.services.strategy_engine import (  # noqa: E402
    generate_stateful_backtest_signals,
    generate_support_resistance_replay_signals,
)
from src.services.paper_trading_service import (  # noqa: E402
    VirtualPosition,
    _inject_virtual_positions,
    run_paper_trading,
)
from src.api.backtests import get_backtest_support_resistance  # noqa: E402
from src.services.adaptive_research_service import _catalog_item  # noqa: E402


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
        pivot_keys=(f"{key}:1", f"{key}:2"),
        pivot_count=2,
        touch_count=2,
        first_pivot_date=date(2024, 12, 1),
        last_pivot_date=date(2024, 12, 20),
        valid_from=date(2024, 12, 23),
    )


class SupportResistanceStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        params = normalize_strategy_params("support_resistance", {})
        self.signal = params["signal"]
        self.risk = params["risk"]

    def test_rejects_all_entry_modes_disabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            normalize_strategy_params(
                "support_resistance",
                {
                    "signal": {
                        "support_bounce_enabled": False,
                        "resistance_breakout_enabled": False,
                        "breakout_retest_enabled": False,
                    }
                },
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
        self.assertTrue(catalog["defaults"]["signal"]["resistance_breakout_enabled"])
        self.assertTrue(catalog["defaults"]["signal"]["breakout_retest_enabled"])

    def test_pivot_is_confirmed_only_after_right_hand_bars(self) -> None:
        state = SupportResistanceSymbolState()
        self.signal.update(
            {
                "pivot_left_bars": 1,
                "pivot_right_bars": 2,
                "min_touches": 2,
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

    def test_same_day_candidates_emit_one_buy_with_deterministic_tie_break(self) -> None:
        state = SupportResistanceSymbolState()
        state.history.append(_bar(-1, high=106, low=102, close=103.2))
        state.zones = {
            "support": _zone("support", "support", 100),
            "breakout": _zone("breakout", "resistance", 102),
            "retest": _zone("retest", "resistance", 104),
        }
        state.breakouts["retest"] = BreakoutRecord(
            zone_key="retest",
            breakout_date=date(2024, 12, 31),
            breakout_session_index=0,
            breakout_volume=200,
            original_lower=103,
            original_upper=105,
        )
        current = _bar(1, high=107, low=100, close=105.5, volume=160)

        decision = advance_symbol(state, current, self.signal, self.risk)

        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "BUY")
        self.assertEqual(decision["support_resistance"]["selected_setup"], "breakout_retest")
        self.assertEqual(
            set(decision["support_resistance"]["candidate_setups"]),
            {"support_bounce", "resistance_breakout", "breakout_retest"},
        )
        self.assertEqual(
            len([event for event in state.events if event["event_type"] == "selection"]),
            1,
        )

    def test_beta_score_uses_only_resolved_prior_events_and_both_hit_is_loss(self) -> None:
        state = SupportResistanceSymbolState()
        state.pending_outcomes.append(
            PendingOutcome(
                setup="support_bounce",
                zone_key="support",
                origin_date=date(2025, 1, 1),
                origin_session_index=-1,
                target=103,
                stop=97,
            )
        )

        advance_symbol(
            state,
            _bar(1, high=104, low=96, close=100),
            self.signal,
            self.risk,
            emit_signals=False,
        )

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

    def test_retest_can_trigger_when_direct_breakout_entry_is_disabled(self) -> None:
        signal = dict(self.signal)
        signal.update(
            {
                "support_bounce_enabled": False,
                "resistance_breakout_enabled": False,
                "breakout_retest_enabled": True,
            }
        )
        state = SupportResistanceSymbolState()
        state.history.append(_bar(0, high=102, low=100, close=101))
        state.zones["resistance"] = _zone("resistance", "resistance", 101)

        breakout_decision = advance_symbol(
            state,
            _bar(1, high=104, low=102, close=103, volume=200),
            signal,
            self.risk,
        )
        self.assertIsNone(breakout_decision)
        self.assertIn("resistance", state.breakouts)
        self.assertTrue(
            any(event["event_type"] == "breakout" for event in state.events)
        )

        state.zones["resistance"] = _zone("resistance", "resistance", 101)
        retest_decision = advance_symbol(
            state,
            _bar(2, high=103, low=101.5, close=102.2, volume=150),
            signal,
            self.risk,
        )

        self.assertIsNotNone(retest_decision)
        self.assertEqual(
            retest_decision["support_resistance"]["selected_setup"],
            "breakout_retest",
        )
        self.assertTrue(any(event["event_type"] == "retest" for event in state.events))

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
            }
        ]
        previous = {"symbol": "TEST", **_bar(0, high=103, low=102, close=102.5)}
        current = {"symbol": "TEST", **_bar(1, high=103, low=100.5, close=102)}

        backtest_state = SupportResistanceState(
            symbols={
                "TEST": SupportResistanceSymbolState(
                    cached_zone_timeline=list(timeline)
                )
            }
        )
        generate_stateful_backtest_signals(
            runtime,
            {"TEST": previous},
            backtest_state,
            emit_signals=False,
        )
        backtest_signals = generate_stateful_backtest_signals(
            runtime,
            {"TEST": current},
            backtest_state,
        )

        paper_state = SupportResistanceState(
            symbols={
                "TEST": SupportResistanceSymbolState(
                    cached_zone_timeline=list(timeline)
                )
            }
        )
        paper_signals = generate_support_resistance_replay_signals(
            runtime,
            {"TEST": {**current, "recent_bars": [previous, current]}},
            paper_state,
        )

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
                _bar(offset, high=103, low=102, close=102.5),
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
                }
            ],
        )
        self.assertNotIn("support", state.zones)

    def test_exact_membership_is_reserved_from_neighboring_cluster_match(self) -> None:
        exact = _zone("exact", "support", 100.0)
        exact.pivot_keys = ("low:exact-1", "low:exact-2")
        rebuilt = {}
        memberships = {
            exact.pivot_keys,
            ("low:neighbor-1", "low:neighbor-2"),
        }

        neighbor_match = _match_zone(
            [exact],
            "low",
            100.5,
            1.0,
            rebuilt,
            pivot_keys=("low:neighbor-1", "low:neighbor-2"),
            reserved_memberships=memberships,
        )
        exact_match = _match_zone(
            [exact],
            "low",
            100.0,
            1.0,
            rebuilt,
            pivot_keys=exact.pivot_keys,
            reserved_memberships=memberships,
        )

        self.assertIsNone(neighbor_match)
        self.assertIs(exact_match, exact)

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
                    session_index=1,
                    trade_date=date(2025, 1, 2),
                    confirmed_on=date(2025, 1, 5),
                    price=100.12345678916,
                    atr=1.23456789016,
                ),
            ],
            history=[_bar(0, high=102, low=99, close=100)],
        )
        signal = dict(self.signal)
        signal.update({"cluster_radius_atr": 1.0, "zone_half_width_atr": 0.5})
        bar = {**state.history[-1], "atr_14": 1.23456789016}

        _rebuild_zones(state, bar, signal)

        zone = next(iter(state.zones.values()))
        self.assertEqual(zone.center, 100.1234567892)
        self.assertEqual(zone.atr, 1.2345678902)
        self.assertEqual(normalized_detector_params({"signal": self.signal})["implementation_revision"], 2)

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
            SupportResistanceRunMaterialization.__table__,
            SupportResistanceRunEvent.__table__,
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
            SupportResistanceRunEvent.__table__,
        ):
            foreign_key = next(iter(table.c.instrument_id.foreign_keys))
            self.assertEqual(foreign_key.target_fullname, "instruments.id")
            self.assertEqual(foreign_key.ondelete, "SET NULL")

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

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    @patch(
        "src.services.support_resistance_persistence_service.source_data_fingerprint",
        return_value="fingerprint-a",
    )
    def test_cache_hit_reuses_shared_rows_and_run_delete_keeps_cache(self, _fingerprint, _ids) -> None:
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
            second_run = self._new_run(db)
            second = persist_support_resistance_run(
                db,
                run=second_run,
                runtime=self.runtime,
                state=self._state(),
                symbols=["TEST"],
                coverage_start=date(2025, 1, 15),
                coverage_end=date(2025, 2, 15),
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
    def test_source_fingerprint_change_creates_cache_miss(self, _ids) -> None:
        with self.Session() as db:
            with patch(
                "src.services.support_resistance_persistence_service.source_data_fingerprint",
                return_value="fingerprint-a",
            ):
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
            with patch(
                "src.services.support_resistance_persistence_service.source_data_fingerprint",
                return_value="fingerprint-b",
            ):
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
            self.assertEqual(
                db.scalar(select(func.count()).select_from(SupportResistanceMaterialization)),
                2,
            )

    @patch(
        "src.services.support_resistance_persistence_service._instrument_ids",
        return_value={"TEST": 1},
    )
    @patch(
        "src.services.support_resistance_persistence_service.source_data_fingerprint",
        return_value="fingerprint-role-params",
    )
    def test_role_transition_parameter_change_creates_cache_miss(self, _fingerprint, _ids) -> None:
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
                {"signal": {"retest_volume_ratio_max": 0.6}},
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
    @patch(
        "src.services.support_resistance_persistence_service.source_data_fingerprint",
        return_value="fingerprint-failed",
    )
    def test_failed_materialization_is_persisted_but_never_reused(self, _fingerprint, _ids) -> None:
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

    @patch(
        "src.services.support_resistance_persistence_service.source_data_fingerprint",
        return_value="fingerprint-after",
    )
    def test_source_change_during_run_rejects_materialization(self, _fingerprint) -> None:
        with self.Session() as db:
            with self.assertRaisesRegex(
                SupportResistanceMaterializationBuildError,
                "source data fingerprint changed",
            ):
                persist_support_resistance_run(
                    db,
                    run=self._new_run(db),
                    runtime=self.runtime,
                    state=self._state(),
                    symbols=["TEST"],
                    coverage_start=date(2025, 1, 1),
                    coverage_end=date(2025, 3, 1),
                    expected_data_fingerprint="fingerprint-before",
                )

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
                    "src.services.paper_trading_service.source_data_fingerprint",
                    return_value="paper-fingerprint",
                ),
                patch(
                    "src.services.support_resistance_persistence_service.source_data_fingerprint",
                    return_value="paper-fingerprint",
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
    @patch(
        "src.services.support_resistance_persistence_service.source_data_fingerprint",
        return_value="fingerprint-api",
    )
    def test_audit_api_filters_entry_zone_and_returns_empty_state(self, _fingerprint, _ids) -> None:
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
            detail = get_backtest_support_resistance(
                run.id,
                db=db,
                symbol="test",
                zone_key="zone",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 2, 1),
            )
            self.assertEqual(detail.materialization["status"], "completed")
            self.assertEqual(len(detail.zone_versions), 1)
            self.assertEqual(detail.zone_versions[0]["zone_key"], "zone")
            self.assertEqual(len(detail.events), 2)
            candidate = next(event for event in detail.events if event["event_type"] == "candidate")
            self.assertEqual(candidate["posterior_sample_count"], 0)

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
