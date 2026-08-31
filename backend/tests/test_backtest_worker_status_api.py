from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api.backtests import BacktestCreate, create_backtest, get_backtest_worker_status
from src.models.tables import BacktestWorkerManager, Base, Strategy


class BacktestWorkerStatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

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


if __name__ == "__main__":
    unittest.main()
