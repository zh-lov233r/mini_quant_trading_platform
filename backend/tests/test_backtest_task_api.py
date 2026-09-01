from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api.backtests import get_backtest_tasks
from src.api.research import cancel_research_trial, get_research_worker_status
from src.models.tables import (
    BacktestJob,
    Base,
    ExperimentCandidate,
    ExperimentRound,
    ExperimentTrial,
    ResearchExperiment,
    Strategy,
    StrategyRun,
)
from src.services.backtest_job_service import request_backtest_cancel
from src.services.research_experiment_service import _finish_cancelled_claim


class BacktestTaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.strategy = Strategy(
            strategy_key="task-center",
            name="Task Center Strategy",
            strategy_type="trend",
            params={},
            status="active",
            version=1,
        )
        self.db.add(self.strategy)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def make_run(self, *, status: str, requested_at: datetime | None = None) -> StrategyRun:
        run = StrategyRun(
            strategy_id=self.strategy.id,
            strategy_version=1,
            mode="backtest",
            status=status,
            requested_at=requested_at or datetime.now(UTC),
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 31),
            initial_cash=100_000,
            config_snapshot={},
            summary_metrics={},
        )
        self.db.add(run)
        self.db.flush()
        return run

    def make_experiment(self, *, name: str = "Research Study") -> ResearchExperiment:
        experiment = ResearchExperiment(
            workflow_run_id=f"workflow-{uuid4()}",
            idempotency_key=str(uuid4()),
            status="running",
            spec={"name": name, "strategyId": str(self.strategy.id)},
            run_manifest={},
            progress={},
            report={},
        )
        self.db.add(experiment)
        self.db.flush()
        return experiment

    def make_trial(self, experiment: ResearchExperiment, *, ordinal: int, status: str = "queued", run: StrategyRun | None = None) -> ExperimentTrial:
        trial = ExperimentTrial(
            experiment_id=experiment.id,
            backtest_run_id=run.id if run is not None else None,
            trial_key=f"trial-{ordinal}",
            ordinal=ordinal,
            status=status,
            sample_kind="out_of_sample",
            cost_scenario="base",
            params={},
            params_hash=f"{ordinal:064x}",
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 31),
            cost_config={},
            metrics={},
        )
        self.db.add(trial)
        self.db.flush()
        return trial

    def test_aggregates_all_sources_without_duplicate_research_run(self) -> None:
        old = datetime.now(UTC) - timedelta(days=2)
        legacy = self.make_run(status="completed", requested_at=old)
        legacy.finished_at = old + timedelta(minutes=1)

        manual = self.make_run(status="queued")
        self.db.add(BacktestJob(
            run_id=manual.id,
            source="manual",
            status="queued",
            payload={},
            progress={"phase": "queued", "percent": 0, "attempt": 0, "max_attempts": 2, "updated_at": datetime.now(UTC).isoformat()},
        ))

        experiment = self.make_experiment()
        waiting = self.make_trial(experiment, ordinal=1)
        research_run = self.make_run(status="running")
        running = self.make_trial(experiment, ordinal=2, status="running", run=research_run)
        self.db.add(BacktestJob(
            run_id=research_run.id,
            experiment_trial_id=running.id,
            source="research",
            status="running",
            attempt=1,
            payload={},
            progress={"phase": "running", "percent": 42, "attempt": 1, "max_attempts": 2, "updated_at": datetime.now(UTC).isoformat()},
        ))

        verification = self.make_run(status="queued")
        round_row = ExperimentRound(experiment_id=experiment.id, ordinal=1, status="running", proposal={}, validation_issues=[], result_summary={})
        self.db.add(round_row)
        self.db.flush()
        candidate = ExperimentCandidate(experiment_id=experiment.id, round_id=round_row.id, ordinal=1, parameter_overrides={}, params={}, params_hash="f" * 64, aggregate_metrics={})
        self.db.add(candidate)
        self.db.flush()
        self.db.add(BacktestJob(run_id=verification.id, source="verification", status="queued", payload={"candidate_id": str(candidate.id)}, progress={}))
        self.db.commit()

        page = get_backtest_tasks(self.db, source=None, stage=None, limit=25, offset=0)

        self.assertEqual(page.total, 5)
        self.assertEqual({item.source for item in page.items}, {"manual", "research", "verification"})
        self.assertEqual(sum(item.run_id == research_run.id for item in page.items), 1)
        waiting_item = next(item for item in page.items if item.trial_id == waiting.id)
        running_item = next(item for item in page.items if item.trial_id == running.id)
        self.assertEqual(waiting_item.stage, "waiting_research")
        self.assertEqual(running_item.stage, "running")
        self.assertEqual(running_item.progress.percent, 42)
        self.assertEqual(page.items[-1].run_id, legacy.id)

    def test_filters_and_paginates_after_active_first_sort(self) -> None:
        for index in range(3):
            run = self.make_run(status="completed", requested_at=datetime.now(UTC) - timedelta(days=index + 1))
            run.finished_at = run.requested_at
        experiment = self.make_experiment()
        self.make_trial(experiment, ordinal=1)
        self.db.commit()

        active = get_backtest_tasks(self.db, source=None, stage="active", limit=25, offset=0)
        history = get_backtest_tasks(self.db, source="manual", stage="completed", limit=2, offset=1)

        self.assertEqual(active.total, 1)
        self.assertEqual(active.items[0].stage, "waiting_research")
        self.assertEqual(history.total, 3)
        self.assertEqual(len(history.items), 2)

    def test_cancel_queued_trial_is_immediate_and_idempotent(self) -> None:
        experiment = self.make_experiment()
        trial = self.make_trial(experiment, ordinal=1)
        self.db.commit()

        first = cancel_research_trial(experiment.id, trial.id, self.db)
        second = cancel_research_trial(experiment.id, trial.id, self.db)

        self.assertEqual(first.status, "cancelled")
        self.assertIsNotNone(first.cancel_requested_at)
        self.assertEqual(second.status, "cancelled")

    def test_cancel_running_trial_before_job_is_cooperative(self) -> None:
        experiment = self.make_experiment()
        trial = self.make_trial(experiment, ordinal=1, status="running")
        self.db.commit()

        requested = cancel_research_trial(experiment.id, trial.id, self.db)
        current = self.db.get(ExperimentTrial, trial.id)
        assert current is not None
        self.assertEqual(requested.status, "running")
        self.assertIsNotNone(requested.cancel_requested_at)
        self.assertTrue(_finish_cancelled_claim(self.db, current, experiment))
        self.assertEqual(self.db.get(ExperimentTrial, trial.id).status, "cancelled")

    def test_cancel_rejects_trial_from_another_experiment(self) -> None:
        owner = self.make_experiment(name="Owner")
        other = self.make_experiment(name="Other")
        trial = self.make_trial(owner, ordinal=1)
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            cancel_research_trial(other.id, trial.id, self.db)

        self.assertEqual(raised.exception.status_code, 404)

    def test_verification_cancel_updates_candidate_state(self) -> None:
        experiment = self.make_experiment()
        round_row = ExperimentRound(experiment_id=experiment.id, ordinal=1, status="completed", proposal={}, validation_issues=[], result_summary={})
        self.db.add(round_row)
        self.db.flush()
        candidate = ExperimentCandidate(experiment_id=experiment.id, round_id=round_row.id, ordinal=1, parameter_overrides={}, params={}, params_hash="a" * 64, aggregate_metrics={"verification": {"status": "queued"}})
        self.db.add(candidate)
        self.db.flush()
        run = self.make_run(status="queued")
        self.db.add(BacktestJob(run_id=run.id, source="verification", status="queued", payload={"candidate_id": str(candidate.id)}, progress={}))
        self.db.commit()

        request_backtest_cancel(self.db, run.id)
        self.db.refresh(candidate)

        self.assertEqual(candidate.aggregate_metrics["verification"]["status"], "cancelled")

    def test_research_worker_status_reports_queue_and_capacity(self) -> None:
        experiment = self.make_experiment()
        self.make_trial(experiment, ordinal=1)
        self.db.commit()
        worker = SimpleNamespace(status_snapshot=lambda enabled: {
            "enabled": enabled,
            "state": "idle",
            "configured_concurrency": 2,
            "active_trials": 0,
            "available_slots": 2,
        })
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(research_experiment_worker=worker)))

        with patch.dict("os.environ", {"RESEARCH_WORKER_ENABLED": "true"}):
            status = get_research_worker_status(request, self.db)

        self.assertTrue(status.enabled)
        self.assertEqual(status.state, "idle")
        self.assertEqual(status.queued_trials, 1)


if __name__ == "__main__":
    unittest.main()
