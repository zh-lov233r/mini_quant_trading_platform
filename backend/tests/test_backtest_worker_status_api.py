from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import os
from unittest.mock import patch
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.api.backtests import BacktestCreate, create_backtest, get_backtest_worker_status
from src.models.tables import BacktestJob, BacktestWorkerManager, Base, Strategy


class BacktestWorkerStatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BACKTEST_WORKER_CONCURRENCY": "2",
                "BACKTEST_INTRA_RUN_THREADS": "4",
            },
        )
        self.env_patcher.start()
        self.cpu_patcher = patch(
            "src.services.backtest_worker_config.available_cpu_count",
            return_value=8,
        )
        self.cpu_patcher.start()
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        self.cpu_patcher.stop()
        self.env_patcher.stop()

    def test_reports_live_leader_and_active_worker(self) -> None:
        self.db.add(
            BacktestWorkerManager(
                manager_id="manager-1",
                hostname="test-host",
                pid=100,
                status="running",
                is_leader=True,
                heartbeat_at=datetime.now(UTC),
                worker_pid=101,
            )
        )
        self.db.commit()
        result = get_backtest_worker_status(self.db)
        self.assertTrue(result.automation_available)
        self.assertEqual(result.manager_state, "running")
        self.assertEqual(result.live_managers, 1)
        self.assertTrue(result.worker_active)
        self.assertEqual(result.execution_model, "process")
        self.assertEqual(result.configured_concurrency, 2)
        self.assertEqual(result.intra_run_execution_model, "thread")
        self.assertEqual(result.configured_intra_run_threads, 4)
        self.assertEqual(result.effective_intra_run_threads, 4)
        self.assertEqual(result.available_slots, 2)

    def test_stale_manager_is_unavailable(self) -> None:
        self.db.add(
            BacktestWorkerManager(
                manager_id="manager-stale",
                hostname="test-host",
                pid=100,
                status="idle",
                is_leader=True,
                heartbeat_at=datetime.now(UTC) - timedelta(seconds=30),
            )
        )
        self.db.commit()
        result = get_backtest_worker_status(self.db)
        self.assertFalse(result.automation_available)
        self.assertEqual(result.manager_state, "unavailable")
        self.assertEqual(result.live_managers, 0)

    def test_create_remains_queued_when_manager_is_unavailable(self) -> None:
        strategy = Strategy(
            strategy_key="offline-queue-test",
            name="offline queue test",
            strategy_type="trend",
            params={},
            status="active",
            version=1,
        )
        self.db.add(strategy)
        self.db.commit()
        result = create_backtest(
            BacktestCreate(
                strategy_id=strategy.id,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 2),
            ),
            self.db,
        )
        worker_status = get_backtest_worker_status(self.db)
        self.assertEqual(result.status, "queued")
        self.assertIsNotNone(result.progress)
        self.assertFalse(worker_status.automation_available)
        self.assertEqual(worker_status.queued_jobs, 1)

    def test_available_slots_follow_active_jobs_and_never_go_negative(self) -> None:
        strategy = Strategy(
            strategy_key="worker-capacity-test",
            name="worker capacity test",
            strategy_type="trend",
            params={},
            status="active",
            version=1,
        )
        self.db.add(strategy)
        self.db.commit()
        for day in (1, 3, 5):
            create_backtest(
                BacktestCreate(
                    strategy_id=strategy.id,
                    start_date=date(2025, 1, day),
                    end_date=date(2025, 1, day + 1),
                ),
                self.db,
            )
        jobs = list(self.db.execute(select(BacktestJob).order_by(BacktestJob.created_at, BacktestJob.id)).scalars())

        self.assertEqual(get_backtest_worker_status(self.db).available_slots, 2)
        jobs[0].status = "running"
        self.db.commit()
        one_active = get_backtest_worker_status(self.db)
        self.assertEqual(one_active.active_jobs, 1)
        self.assertEqual(one_active.available_slots, 1)

        jobs[1].status = "running"
        jobs[2].status = "running"
        self.db.commit()
        saturated = get_backtest_worker_status(self.db)
        self.assertEqual(saturated.active_jobs, 3)
        self.assertEqual(saturated.available_slots, 0)


if __name__ == "__main__":
    unittest.main()
