from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.models.tables import BacktestJob, Base, Strategy, StrategyRun
from src.services.backtest_job_service import (
    _with_worker_performance,
    claim_next_backtest_job,
    enqueue_backtest_job,
    normalize_backtest_progress,
    progress_update_interval_seconds,
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
        self.assertEqual(job.progress["phase"], "preparing")
        self.assertEqual(job.progress["percent"], 0.0)
        self.assertEqual(job.progress["attempt"], 1)
        self.assertIsNone(claim_next_backtest_job(self.db, worker_id="worker-2"))

    def test_two_slots_claim_distinct_jobs_without_changing_queue_order(self) -> None:
        base_time = datetime.now(UTC) - timedelta(minutes=5)
        low_priority_run = self._run()
        low_priority = enqueue_backtest_job(
            self.db,
            run=low_priority_run,
            payload={"strategy_id": str(self.strategy.id)},
            priority=0,
        )
        first_high_run = self._run()
        first_high = enqueue_backtest_job(
            self.db,
            run=first_high_run,
            payload={"strategy_id": str(self.strategy.id)},
            priority=10,
        )
        second_high_run = self._run()
        second_high = enqueue_backtest_job(
            self.db,
            run=second_high_run,
            payload={"strategy_id": str(self.strategy.id)},
            priority=10,
        )
        low_priority.created_at = base_time
        first_high.created_at = base_time + timedelta(seconds=1)
        second_high.created_at = base_time + timedelta(seconds=2)
        self.db.commit()

        first_claim = claim_next_backtest_job(self.db, worker_id="worker-slot-1", lease_seconds=60)
        second_claim = claim_next_backtest_job(self.db, worker_id="worker-slot-2", lease_seconds=60)

        assert first_claim is not None and second_claim is not None
        self.assertEqual(first_claim.id, first_high.id)
        self.assertEqual(second_claim.id, second_high.id)
        self.assertNotEqual(first_claim.id, second_claim.id)
        self.assertEqual(first_claim.claimed_by, "worker-slot-1")
        self.assertEqual(second_claim.claimed_by, "worker-slot-2")
        self.assertEqual(low_priority.status, "queued")

    def test_queued_cancellation_marks_run_terminal(self) -> None:
        run = self._run()
        enqueue_backtest_job(self.db, run=run, payload={"strategy_id": str(self.strategy.id)})
        self.db.commit()
        request_backtest_cancel(self.db, run.id)
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        job = self.db.execute(select(BacktestJob).where(BacktestJob.run_id == run.id)).scalar_one()
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.progress["phase"], "cancelled")
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
        self.assertEqual(job.progress["phase"], "failed")

    def test_expired_lease_retry_resets_progress(self) -> None:
        run = self._run()
        job = enqueue_backtest_job(
            self.db,
            run=run,
            payload={"strategy_id": str(self.strategy.id)},
            max_attempts=2,
        )
        job.status = "running"
        job.attempt = 1
        job.progress = {"phase": "running", "percent": 73.0, "completed_days": 73}
        job.claimed_by = "dead-worker"
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        run.status = "running"
        self.db.commit()

        self.assertEqual(recover_expired_jobs(self.db), 1)
        self.db.refresh(job)
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.progress["phase"], "queued")
        self.assertEqual(job.progress["percent"], 0.0)
        self.assertIsNone(job.progress["completed_days"])

    def test_progress_normalization_caps_phases_and_supports_legacy_rows(self) -> None:
        legacy = normalize_backtest_progress(
            {"percent": 42.6},
            status="running",
            attempt=1,
            max_attempts=2,
        )
        self.assertEqual(legacy["phase"], "running")
        self.assertEqual(legacy["percent"], 42.6)
        self.assertEqual(legacy["attempt"], 1)
        self.assertEqual(legacy["max_attempts"], 2)

        running = normalize_backtest_progress(
            {"phase": "running", "percent": 100},
            status="running",
            attempt=1,
            max_attempts=2,
        )
        finalizing = normalize_backtest_progress(
            {
                "phase": "finalizing",
                "percent": 92.5,
                "finalizing_stage": "run_events",
                "completed_items": 250,
                "total_items": 1_000,
            },
            status="running",
            attempt=1,
            max_attempts=2,
        )
        completed = normalize_backtest_progress(
            {"percent": 12},
            status="completed",
            attempt=1,
            max_attempts=2,
        )
        failed = normalize_backtest_progress(
            {"phase": "running", "percent": 61},
            status="failed",
            attempt=2,
            max_attempts=2,
        )
        self.assertEqual(running["percent"], 85.0)
        self.assertEqual(finalizing["percent"], 92.5)
        self.assertEqual(finalizing["finalizing_stage"], "run_events")
        self.assertEqual(finalizing["completed_items"], 250)
        self.assertEqual(finalizing["total_items"], 1_000)
        self.assertEqual(completed["percent"], 100.0)
        self.assertIsNone(completed["finalizing_stage"])
        self.assertEqual(failed["percent"], 61.0)

    def test_progress_intervals_keep_stage_changes_immediate(self) -> None:
        self.assertEqual(progress_update_interval_seconds({"phase": "running"}), 5.0)
        self.assertEqual(progress_update_interval_seconds({"phase": "finalizing"}), 1.0)
        self.assertEqual(progress_update_interval_seconds({"phase": "completed"}), 0.0)

    def test_worker_performance_adds_queue_and_finalization_without_losing_engine_metrics(self) -> None:
        run = self._run()
        job = enqueue_backtest_job(self.db, run=run, payload={"strategy_id": str(self.strategy.id)})
        job.created_at = datetime.now(UTC) - timedelta(seconds=3)
        job.claimed_at = job.created_at + timedelta(seconds=2)
        metrics = _with_worker_performance(
            {"performance": {"engine_total_ms": 750.0, "rows_loaded": 10}},
            job=job,
            worker_active_ms=1_000.0,
        )

        performance = metrics["performance"]
        self.assertEqual(performance["queue_wait_ms"], 2_000.0)
        self.assertEqual(performance["worker_active_ms"], 1_000.0)
        self.assertEqual(performance["finalization_overhead_ms"], 250.0)
        self.assertEqual(performance["rows_loaded"], 10)


if __name__ == "__main__":
    unittest.main()
