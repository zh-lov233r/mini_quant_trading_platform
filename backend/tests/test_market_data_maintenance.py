from __future__ import annotations

from datetime import date
import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.tables import (
    BacktestJob,
    Base,
    ResearchExperiment,
    Strategy,
    StrategyRun,
    SupportResistanceMaterialization,
)
from src.services.backtest_job_service import enqueue_backtest_job
from src.services.market_data_maintenance_service import (
    MarketDataMaintenanceError,
    acquire_market_data_read_lock,
    active_market_data_work_counts,
    assert_market_data_submission_allowed,
    begin_market_data_draining,
    begin_market_data_update,
    finish_market_data_maintenance,
    invalidate_support_resistance_materializations,
    load_market_data_maintenance_state,
)


class MarketDataMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.strategy = Strategy(
            strategy_key="maintenance-test",
            name="maintenance-test",
            strategy_type="trend",
            version=1,
            params={},
            status="draft",
        )
        self.db.add(self.strategy)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _run(self) -> StrategyRun:
        run = StrategyRun(
            strategy_id=self.strategy.id,
            strategy_version=1,
            mode="backtest",
            status="queued",
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 2),
            initial_cash=100_000,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def test_draining_rejects_new_manual_work_but_existing_work_can_finish(self) -> None:
        existing_run = self._run()
        existing_job = enqueue_backtest_job(
            self.db,
            run=existing_run,
            payload={"strategy_id": str(self.strategy.id)},
        )
        experiment = ResearchExperiment(
            workflow_run_id="workflow-1",
            idempotency_key="maintenance-experiment",
            status="running",
            spec={},
            run_manifest={},
            progress={},
            report={},
        )
        self.db.add(experiment)
        self.db.commit()

        owner = uuid.uuid4()
        begin_market_data_draining(self.db, owner)
        self.db.commit()
        with self.assertRaises(MarketDataMaintenanceError):
            assert_market_data_submission_allowed(self.db)
        with self.assertRaises(MarketDataMaintenanceError):
            enqueue_backtest_job(
                self.db,
                run=self._run(),
                payload={"strategy_id": str(self.strategy.id)},
            )
        self.db.rollback()
        self.assertEqual(
            active_market_data_work_counts(self.db),
            {"backtest_jobs": 1, "research_experiments": 1},
        )

        existing_job.status = "completed"
        existing_run.status = "completed"
        experiment.status = "completed"
        self.db.commit()
        begin_market_data_update(self.db, owner)
        self.db.commit()
        self.assertEqual(load_market_data_maintenance_state(self.db).status, "updating")

    def test_failed_maintenance_stays_blocked_until_successful_rerun(self) -> None:
        first_owner = uuid.uuid4()
        begin_market_data_draining(self.db, first_owner)
        finish_market_data_maintenance(self.db, first_owner, error=RuntimeError("quality failed"))
        self.db.commit()

        with self.assertRaises(MarketDataMaintenanceError):
            assert_market_data_submission_allowed(self.db)
        with self.assertRaises(MarketDataMaintenanceError):
            acquire_market_data_read_lock(self.db)

        second_owner = uuid.uuid4()
        begin_market_data_draining(self.db, second_owner)
        begin_market_data_update(self.db, second_owner)
        finish_market_data_maintenance(self.db, second_owner)
        self.db.commit()
        assert_market_data_submission_allowed(self.db)

    def test_invalidation_preserves_history_and_allows_new_current_row(self) -> None:
        common = {
            "cache_key": "a" * 64,
            "algorithm_version": "pivot-slope-regime-v3",
            "detector_params": {},
            "universe_hash": "b" * 64,
            "symbols": ["AAA"],
            "coverage_start": date(2025, 1, 1),
            "coverage_end": date(2025, 1, 31),
            "price_semantics": "forward_adjusted",
            "status": "completed",
        }
        first = SupportResistanceMaterialization(**common)
        self.db.add(first)
        self.db.commit()

        self.assertEqual(invalidate_support_resistance_materializations(self.db), 1)
        self.db.add(SupportResistanceMaterialization(**common))
        self.db.commit()
        self.assertEqual(
            self.db.query(SupportResistanceMaterialization).count(),
            2,
        )
        self.db.refresh(first)
        self.assertIsNotNone(first.invalidated_at)


if __name__ == "__main__":
    unittest.main()
