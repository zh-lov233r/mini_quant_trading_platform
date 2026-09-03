from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.models.tables import (
    Base,
    Instrument,
    PortfolioSnapshot,
    Signal,
    Strategy,
    StrategyRun,
    Transaction,
)
from src.services.native_result_repository import (
    NativeResultValidationError,
    persist_native_result,
)
from src.services.backtest_engine import (
    _native_research_metrics,
    _native_universe_membership,
)


def _timestamp_us() -> int:
    return int(datetime(2025, 1, 2, 21, tzinfo=UTC).timestamp() * 1_000_000)


def _result(*, signal_reason: str = "native buy") -> SimpleNamespace:
    timestamp_us = _timestamp_us()
    return SimpleNamespace(
        symbols=["AAA"],
        signals={
            "timestamp_us": [timestamp_us],
            "instrument_id": [101],
            "symbol_id": [0],
            "action": [1],
            "score": [75.5],
            "reason": [signal_reason],
            "metadata_json": ['{"strength":75.5}'],
        },
        trades={
            "signal_timestamp_us": [timestamp_us],
            "execution_timestamp_us": [timestamp_us + 1_000_000],
            "execution_date_ordinal": [date(2025, 1, 3).toordinal()],
            "instrument_id": [101],
            "symbol_id": [0],
            "side": [1],
            "quantity": [10.0],
            "price": [25.0],
            "fee": [1.0],
            "reference_price": [24.95],
            "slippage_bps": [20.0],
            "slippage_cost": [0.5],
            "gross_notional": [250.0],
            "net_cash_flow": [-251.0],
            "reason": [signal_reason],
            "setup_id": ["setup-1"],
            "stage_index": [1],
            "stage_key": ["candidate"],
            "stage_target_pct": [0.25],
            "position_quantity_before": [0.0],
            "position_quantity_after": [10.0],
            "position_average_entry_price_after": [25.0],
            "entry_signal_features_json": ['{"entry_history":[{"stage_index":1}]}'],
        },
        equity={
            "timestamp_us": [timestamp_us, timestamp_us + 86_400_000_000],
            "cash": [1_000.0, 749.0],
            "equity": [1_000.0, 1_010.0],
            "gross_exposure": [0.0, 261.0],
            "drawdown": [0.0, 0.0],
            "positions_json": ["{}", '{"AAA":{"quantity":10.0}}'],
            "metrics_json": ['{"session":1}', '{"session":2}'],
        },
        universe_membership={
            "date_ordinal": [date(2025, 1, 2).toordinal(), date(2025, 1, 3).toordinal()],
            "eligible_count": [1, 0],
            "excluded_asset_type": [0, 0],
            "excluded_exchange": [0, 0],
            "excluded_before_listing": [0, 0],
            "excluded_after_delisting": [0, 1],
            "excluded_price": [0, 0],
            "excluded_liquidity": [0, 0],
            "excluded_history": [0, 0],
        },
    )


class NativeResultRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.instrument = Instrument(
            id=101,
            share_class_figi="BBG000NATIVE",
            ticker_canonical="AAA",
            exchange="XNYS",
            asset_type="CS",
            currency="USD",
            market="stocks",
        )
        self.strategy = Strategy(
            strategy_key="native-persist",
            name="native-persist",
            strategy_type="trend",
            params={},
            status="active",
        )
        self.db.add_all([self.instrument, self.strategy])
        self.db.flush()
        self.run = StrategyRun(
            strategy_id=self.strategy.id,
            strategy_version=1,
            mode="backtest",
            status="running",
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 3),
            initial_cash=1_000,
        )
        self.db.add(self.run)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _counts(self) -> tuple[int, int, int]:
        return (
            self.db.scalar(select(func.count()).select_from(Signal)),
            self.db.scalar(select(func.count()).select_from(Transaction)),
            self.db.scalar(select(func.count()).select_from(PortfolioSnapshot)),
        )

    def test_full_persistence_is_idempotent_and_preserves_typed_audit(self) -> None:
        stats = persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(),
            persist_level="full",
        )
        self.db.commit()
        self.assertEqual((stats.signals, stats.transactions, stats.snapshots), (1, 1, 2))
        self.assertEqual(self._counts(), (1, 1, 2))
        transaction = self.db.scalars(select(Transaction)).one()
        self.assertEqual(transaction.meta["setup_id"], "setup-1")
        self.assertEqual(transaction.meta["entry_signal_features"]["entry_history"][0]["stage_index"], 1)

        persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(signal_reason="retry"),
            persist_level="full",
        )
        self.db.commit()
        self.assertEqual(self._counts(), (1, 1, 2))
        self.assertEqual(self.db.scalars(select(Signal)).one().reason, "retry")

    def test_persist_levels_keep_only_requested_details(self) -> None:
        stats = persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(),
            persist_level="trades",
        )
        self.db.commit()
        self.assertEqual((stats.signals, stats.transactions, stats.snapshots), (0, 1, 2))
        snapshot = self.db.scalars(select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts)).first()
        assert snapshot is not None
        self.assertEqual(snapshot.positions, {})
        self.assertTrue(snapshot.metrics["downsampled"])

        stats = persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(),
            persist_level="summary",
        )
        self.db.commit()
        self.assertEqual((stats.signals, stats.transactions, stats.snapshots), (0, 0, 2))
        self.assertEqual(self._counts(), (0, 0, 2))

    def test_validation_and_cancellation_happen_before_existing_rows_are_deleted(self) -> None:
        persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(signal_reason="existing"),
            persist_level="full",
        )
        self.db.commit()

        invalid = _result()
        invalid.signals["score"] = [float("inf")]
        with self.assertRaises(NativeResultValidationError):
            persist_native_result(
                self.db,
                run_id=self.run.id,
                strategy_id=self.strategy.id,
                result=invalid,
                persist_level="full",
            )
        self.assertEqual(self.db.scalars(select(Signal)).one().reason, "existing")

        with self.assertRaisesRegex(RuntimeError, "cancellation"):
            persist_native_result(
                self.db,
                run_id=self.run.id,
                strategy_id=self.strategy.id,
                result=_result(signal_reason="cancelled replacement"),
                persist_level="full",
                cancel_check=lambda: True,
            )
        self.assertEqual(self.db.scalars(select(Signal)).one().reason, "existing")

    def test_caller_rollback_restores_replaced_details(self) -> None:
        persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(signal_reason="existing"),
            persist_level="full",
        )
        self.db.commit()

        persist_native_result(
            self.db,
            run_id=self.run.id,
            strategy_id=self.strategy.id,
            result=_result(signal_reason="uncommitted replacement"),
            persist_level="full",
        )
        self.db.rollback()
        self.assertEqual(self.db.scalars(select(Signal)).one().reason, "existing")

    def test_rejects_unknown_instrument_and_mismatched_columns(self) -> None:
        unknown = _result()
        unknown.trades["instrument_id"] = [999]
        with self.assertRaisesRegex(NativeResultValidationError, "unknown instruments"):
            persist_native_result(
                self.db,
                run_id=self.run.id,
                strategy_id=self.strategy.id,
                result=unknown,
                persist_level="full",
            )

        mismatched = _result()
        mismatched.equity["cash"] = []
        with self.assertRaisesRegex(NativeResultValidationError, "column lengths differ"):
            persist_native_result(
                self.db,
                run_id=self.run.id,
                strategy_id=self.strategy.id,
                result=mismatched,
                persist_level="summary",
            )

    def test_typed_result_produces_research_and_universe_summary_metrics(self) -> None:
        result = _result()
        research = _native_research_metrics(result, 1_000.0)
        self.assertAlmostEqual(research["turnover"], 250.0 / 1_005.0)
        self.assertEqual(research["max_drawdown_duration_sessions"], 0)
        self.assertEqual(research["symbol_activity_share"], {"AAA": 1.0})

        membership = _native_universe_membership(result, {"minimumHistorySessions": 20})
        assert membership is not None
        annual = membership["annual"]["2025"]
        self.assertEqual(annual["sessions"], 2)
        self.assertEqual(annual["eligible_average"], 0.5)
        self.assertEqual(annual["exclusions"], {"after_delisting": 1})


if __name__ == "__main__":
    unittest.main()
