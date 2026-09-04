from __future__ import annotations

from datetime import UTC, date, datetime
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from src.api.backtests import (
    delete_backtest,
    get_backtest_support_resistance,
    list_backtests,
)
from src.api.research import delete_research_backtest
from src.models.tables import (
    BacktestJob,
    Base,
    ExperimentCandidate,
    ExperimentRound,
    ExperimentTrial,
    PortfolioSnapshot,
    ResearchExperiment,
    Signal,
    Strategy,
    StrategyRun,
    SupportResistanceMaterialization,
    SupportResistanceMaterializationEvent,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    Transaction,
)


class BacktestDeleteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.strategy = Strategy(
            strategy_key="delete-backtest",
            name="Delete Backtest",
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

    def make_run(self, *, status: str = "completed", source: str = "manual") -> StrategyRun:
        now = datetime.now(UTC)
        run = StrategyRun(
            strategy_id=self.strategy.id,
            strategy_version=1,
            mode="backtest",
            status=status,
            requested_at=now,
            finished_at=now if status in {"completed", "failed", "cancelled"} else None,
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 2),
            initial_cash=100_000,
            final_equity=101_000,
            config_snapshot={},
            summary_metrics={"total_return": 0.01},
        )
        self.db.add(run)
        self.db.flush()
        self.db.add(BacktestJob(
            run_id=run.id,
            source=source,
            status=status,
            payload={},
            progress={},
        ))
        self.db.flush()
        return run

    def make_experiment(self) -> ResearchExperiment:
        experiment = ResearchExperiment(
            workflow_run_id=f"workflow-{uuid4()}",
            idempotency_key=str(uuid4()),
            status="completed",
            spec={},
            run_manifest={},
            progress={"completed": 1, "failed": 0, "queued": 0, "running": 0},
            report={},
        )
        self.db.add(experiment)
        self.db.flush()
        return experiment

    def test_manual_delete_removes_run_artifacts_and_retains_shared_materialization(self) -> None:
        run = self.make_run()
        now = datetime.now(UTC)
        self.db.add_all([
            Transaction(
                strategy_id=self.strategy.id,
                run_id=run.id,
                ts=now,
                symbol="AAPL",
                side="BUY",
                qty=1,
                price=100,
                fee=0,
                meta={},
            ),
            Signal(
                run_id=run.id,
                strategy_id=self.strategy.id,
                ts=now,
                symbol="AAPL",
                signal="BUY",
                features={},
            ),
            PortfolioSnapshot(
                run_id=run.id,
                ts=now,
                cash=99_900,
                equity=100_000,
                positions={},
                metrics={},
            ),
        ])
        materialization = SupportResistanceMaterialization(
            cache_key="a" * 64,
            algorithm_version="v3",
            detector_params={},
            universe_hash="b" * 64,
            symbols=["AAPL"],
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 1, 2),
            price_semantics="adjusted",
            status="completed",
            statistics={},
        )
        self.db.add(materialization)
        self.db.flush()
        self.db.add(SupportResistanceRunMaterialization(
            run_id=run.id,
            materialization_id=materialization.id,
        ))
        shared_event = SupportResistanceMaterializationEvent(
            materialization_id=materialization.id,
            symbol="AAPL",
            event_date=date(2025, 1, 1),
            event_type="touch",
            payload={},
        )
        self.db.add(shared_event)
        run_id = run.id
        materialization_id = materialization.id
        self.db.commit()
        shared_event_id = shared_event.id

        result = delete_backtest(run_id, self.db)

        self.assertTrue(result.deleted)
        self.assertIsNone(self.db.get(StrategyRun, run_id))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Transaction)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Signal)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(PortfolioSnapshot)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(BacktestJob)), 0)
        self.assertIsNotNone(self.db.get(SupportResistanceMaterialization, materialization_id))
        self.assertIsNotNone(
            self.db.get(SupportResistanceMaterializationEvent, shared_event_id)
        )

    def test_active_run_cannot_be_deleted(self) -> None:
        run = self.make_run(status="running")
        run_id = run.id
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            delete_backtest(run_id, self.db)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNotNone(self.db.get(StrategyRun, run_id))

    def test_support_resistance_api_merges_and_filters_shared_and_run_events(self) -> None:
        run = self.make_run()
        materialization = SupportResistanceMaterialization(
            cache_key="c" * 64,
            algorithm_version="legacy-test",
            detector_params={},
            universe_hash="d" * 64,
            symbols=["AAPL", "MSFT"],
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 1, 5),
            price_semantics="adjusted",
            audit_schema_version=2,
            status="completed",
            statistics={},
        )
        self.db.add(materialization)
        self.db.flush()
        self.db.add_all([
            SupportResistanceRunMaterialization(
                run_id=run.id,
                materialization_id=materialization.id,
            ),
            SupportResistanceMaterializationEvent(
                materialization_id=materialization.id,
                symbol="AAPL",
                event_date=date(2025, 1, 2),
                event_type="touch",
                zone_key="zone-a",
                payload={"source": "shared"},
            ),
            SupportResistanceRunEvent(
                run_id=run.id,
                materialization_id=materialization.id,
                symbol="AAPL",
                event_date=date(2025, 1, 3),
                event_type="candidate",
                zone_key="zone-a",
                payload={"source": "run"},
            ),
            SupportResistanceMaterializationEvent(
                materialization_id=materialization.id,
                symbol="MSFT",
                event_date=date(2025, 1, 2),
                event_type="touch",
                zone_key="zone-b",
                payload={"source": "filtered"},
            ),
        ])
        self.db.commit()

        result = get_backtest_support_resistance(
            run.id,
            self.db,
            symbol="aapl",
            zone_key="zone-a",
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 3),
        )

        self.assertEqual(
            [(event["event_type"], event["payload"]["source"]) for event in result.events],
            [("touch", "shared"), ("candidate", "run")],
        )

    def test_manual_list_excludes_research_runs(self) -> None:
        manual = self.make_run(source="manual")
        research = self.make_run(source="research")
        self.db.commit()

        items = list_backtests(db=self.db, strategy_id=None, mode="backtest", status_filter=None, limit=50, offset=0)

        self.assertEqual([item.id for item in items], [manual.id])
        self.assertNotIn(research.id, [item.id for item in items])

    def test_manual_delete_rejects_research_owned_run(self) -> None:
        run = self.make_run(source="research")
        run_id = run.id
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            delete_backtest(run_id, self.db)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNotNone(self.db.get(StrategyRun, run_id))

    def test_research_delete_preserves_trial_evidence_and_marks_tombstone(self) -> None:
        experiment = self.make_experiment()
        run = self.make_run(source="research")
        trial = ExperimentTrial(
            experiment_id=experiment.id,
            backtest_run_id=run.id,
            trial_key="trial-1",
            ordinal=1,
            status="completed",
            sample_kind="out_of_sample",
            cost_scenario="base",
            params={"signal.window": 20},
            params_hash="d" * 64,
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 2),
            cost_config={},
            metrics={"total_return": 0.01},
        )
        self.db.add(trial)
        self.db.commit()

        delete_research_backtest(experiment.id, run.id, self.db)

        retained = self.db.get(ExperimentTrial, trial.id)
        assert retained is not None
        self.assertIsNone(retained.backtest_run_id)
        self.assertEqual(retained.metrics["total_return"], 0.01)
        self.assertIn("backtest_deleted_at", retained.metrics)
        self.assertIsNone(self.db.get(StrategyRun, run.id))

    def test_research_delete_clears_candidate_verification_link(self) -> None:
        experiment = self.make_experiment()
        round_item = ExperimentRound(
            experiment_id=experiment.id,
            ordinal=1,
            status="completed",
            proposal={},
            validation_issues=[],
            result_summary={},
        )
        self.db.add(round_item)
        self.db.flush()
        run = self.make_run(source="verification")
        candidate = ExperimentCandidate(
            experiment_id=experiment.id,
            round_id=round_item.id,
            ordinal=1,
            parameter_overrides={},
            params={},
            params_hash="e" * 64,
            aggregate_metrics={"verification": {"status": "completed", "runId": str(run.id)}},
        )
        self.db.add(candidate)
        self.db.commit()

        delete_research_backtest(experiment.id, run.id, self.db)

        retained = self.db.get(ExperimentCandidate, candidate.id)
        assert retained is not None
        verification = retained.aggregate_metrics["verification"]
        self.assertIsNone(verification["runId"])
        self.assertIn("deletedAt", verification)
        self.assertIsNone(self.db.get(StrategyRun, run.id))


if __name__ == "__main__":
    unittest.main()
