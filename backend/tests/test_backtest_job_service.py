from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.models.tables import BacktestJob, Base, Strategy, StrategyRun
from src.services.backtest_job_service import (
    claim_next_backtest_job,
    enqueue_backtest_job,
    recover_expired_jobs,
    request_backtest_cancel,
)


class BacktestJobServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.strategy = Strategy(
            strategy_key="queue-test",
            name="queue-test",
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

    def test_claim_is_stable_and_increments_attempt(self) -> None:
        run = self._run()
        enqueue_backtest_job(self.db, run=run, payload={"strategy_id": str(self.strategy.id)})
        self.db.commit()
        job = claim_next_backtest_job(self.db, worker_id="worker-1", lease_seconds=60)
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.status, "running")
        self.assertEqual(job.attempt, 1)
        self.assertEqual(job.claimed_by, "worker-1")
        self.assertIsNone(claim_next_backtest_job(self.db, worker_id="worker-2"))

    def test_queued_cancellation_marks_run_terminal(self) -> None:
        run = self._run()
        enqueue_backtest_job(self.db, run=run, payload={"strategy_id": str(self.strategy.id)})
        self.db.commit()
        request_backtest_cancel(self.db, run.id)
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        job = self.db.execute(select(BacktestJob).where(BacktestJob.run_id == run.id)).scalar_one()
        self.assertEqual(job.status, "cancelled")
        self.assertIsNotNone(job.cancel_requested_at)

    def test_expired_lease_exhaustion_marks_job_and_run_failed(self) -> None:
        run = self._run()
        job = enqueue_backtest_job(
            self.db,
            run=run,
            payload={"strategy_id": str(self.strategy.id)},
            max_attempts=1,
        )
        job.status = "running"
        job.attempt = 1
        job.claimed_by = "dead-worker"
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        run.status = "running"
        self.db.commit()
        self.assertEqual(recover_expired_jobs(self.db), 1)
        self.db.refresh(job)
        self.db.refresh(run)
        self.assertEqual(job.status, "failed")
        self.assertEqual(run.status, "failed")


if __name__ == "__main__":
    unittest.main()
