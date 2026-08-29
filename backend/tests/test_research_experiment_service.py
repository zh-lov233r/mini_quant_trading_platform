from __future__ import annotations

import copy
import os
import sys
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from src.core.agent_auth import require_agent_service
from src.api.research import agent_router as research_agent_router
from src.api.strategies import agent_router as strategy_agent_router
from src.schemas.research import (
    CategoryStudyValidationRequest,
    ExperimentSpec,
    ExperimentTokenUsageUpdate,
)
from src.services.adaptive_research_service import nondominated_sort
from src.services.research_experiment_service import (
    _commit_trial_and_finalize_experiment,
    _recovery_stop_code,
    enforce_experiment_stop_policy,
    expand_experiment,
    update_experiment_token_usage,
)
from src.services.strategy_service import StrategyCreateConflictError, create_strategy_version
from src.services.strategy_registry import MEAN_REVERSION_DEFAULTS, TREND_DEFAULTS


class FakeSession:
    def __init__(self, strategy):
        self.strategy = strategy

    def get(self, model, object_id):
        return self.strategy if str(object_id) == str(self.strategy.id) else None


class ExistingResult:
    def __init__(self, item):
        self.item = item

    def scalars(self):
        return self

    def first(self):
        return self.item


class ExistingStrategySession:
    def __init__(self, item):
        self.item = item

    def execute(self, _statement):
        return ExistingResult(self.item)


def experiment_payload(*, values: list[float]) -> dict:
    return {
        "name": "Trend robustness",
        "hypothesis": "Test position-size sensitivity out of sample.",
        "strategyId": str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        "symbols": ["msft", "AAPL", "MSFT"],
        "inSample": {"startDate": "2020-01-01", "endDate": "2021-12-31"},
        "outOfSample": {"startDate": "2022-01-01", "endDate": "2023-12-31"},
        "parameterGrid": {"risk.position_size_pct": values},
        "costScenarios": [
            {"name": "base", "commissionBps": 0, "commissionMin": 0, "slippageBps": 1},
            {"name": "stress", "commissionBps": 2, "commissionMin": 1, "slippageBps": 8},
        ],
        "initialCash": 100000,
        "benchmarkSymbol": "SPY",
    }


class ResearchExperimentServiceTests(unittest.TestCase):
    def setUp(self):
        self.strategy = SimpleNamespace(
            id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            strategy_type="trend",
            params=copy.deepcopy(TREND_DEFAULTS),
        )
        self.db = FakeSession(self.strategy)

    @patch("src.services.research_experiment_service.validate_strategy_params", side_effect=lambda _db, **kwargs: kwargs["params"])
    def test_expansion_is_deterministic_and_deduplicates_symbols(self, _validate):
        spec = ExperimentSpec.model_validate(experiment_payload(values=[0.05, 0.1]))
        original_params = copy.deepcopy(self.strategy.params)
        first, symbols, _universe = expand_experiment(self.db, spec)
        second, _, _ = expand_experiment(self.db, spec)

        self.assertEqual(8, len(first))
        self.assertEqual(["AAPL", "MSFT"], symbols)
        self.assertEqual([item["trialKey"] for item in first], [item["trialKey"] for item in second])
        self.assertEqual(list(range(8)), [item["ordinal"] for item in first])
        self.assertEqual(original_params, self.strategy.params)
        self.assertEqual(0.05, first[0]["params"]["risk"]["position_size_pct"])

    @patch("src.services.research_experiment_service.validate_strategy_params", side_effect=lambda _db, **kwargs: kwargs["params"])
    def test_expansion_rejects_more_than_configured_trial_limit(self, _validate):
        spec = ExperimentSpec.model_validate(experiment_payload(values=[index / 100 for index in range(13)]))
        with self.assertRaisesRegex(ValueError, "more than 50 trials"):
            expand_experiment(self.db, spec)

    def test_spec_rejects_overlapping_windows(self):
        payload = experiment_payload(values=[0.1])
        payload["outOfSample"]["startDate"] = "2021-01-01"
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            ExperimentSpec.model_validate(payload)

    def test_spec_requires_grid_and_base_plus_stress_costs(self):
        payload = experiment_payload(values=[0.1])
        payload["parameterGrid"] = {}
        with self.assertRaisesRegex(ValueError, "parameterGrid"):
            ExperimentSpec.model_validate(payload)
        payload = experiment_payload(values=[0.1])
        payload["costScenarios"] = payload["costScenarios"][:1]
        with self.assertRaisesRegex(ValueError, "stress scenario"):
            ExperimentSpec.model_validate(payload)

    def test_spec_validates_bounded_stop_policy(self):
        payload = experiment_payload(values=[0.1])
        payload["stopPolicy"] = {
            "maxDurationSeconds": 1800,
            "tokenBudget": 50000,
            "targetMetric": {
                "metric": "total_return",
                "operator": "gte",
                "value": 0.05,
                "sampleKind": "out_of_sample",
                "costScenario": "base",
            },
        }
        spec = ExperimentSpec.model_validate(payload)
        self.assertEqual(1800, spec.stop_policy.max_duration_seconds)
        self.assertEqual(50000, spec.stop_policy.token_budget)
        payload["stopPolicy"] = {"maxDurationSeconds": 30}
        with self.assertRaisesRegex(ValueError, "greater than or equal to 60"):
            ExperimentSpec.model_validate(payload)

    @patch("src.services.research_experiment_service._finalize_if_ready")
    @patch("src.services.research_experiment_service._refresh_progress")
    def test_token_budget_stops_queued_trials_and_records_reason(self, refresh, finalize):
        now = datetime(2026, 8, 25, tzinfo=UTC)
        experiment = SimpleNamespace(
            id=uuid.uuid4(),
            workflow_run_id="workflow-1",
            status="running",
            spec={"stopPolicy": {"tokenBudget": 5000}},
            run_manifest={
                "policyStartedAt": now.isoformat(),
                "tokenUsage": {"totalTokens": 5000},
            },
        )
        db = MagicMock()
        reason = enforce_experiment_stop_policy(db, experiment, now=now)
        self.assertEqual("token_budget_reached", reason)
        self.assertEqual("token_budget_reached", experiment.run_manifest["termination"]["reason"])
        self.assertTrue(experiment.run_manifest["termination"]["earlyStopped"])
        db.execute.assert_called_once()
        refresh.assert_called_once_with(db, experiment)
        finalize.assert_called_once_with(db, experiment)

    @patch("src.services.research_experiment_service._finalize_if_ready")
    @patch("src.services.research_experiment_service._refresh_progress")
    def test_time_limit_stops_experiment_from_worker_sweep(self, refresh, finalize):
        now = datetime(2026, 8, 25, tzinfo=UTC)
        experiment = SimpleNamespace(
            id=uuid.uuid4(),
            workflow_run_id="workflow-1",
            status="queued",
            spec={"stopPolicy": {"maxDurationSeconds": 60}},
            run_manifest={"policyStartedAt": (now - timedelta(seconds=61)).isoformat()},
        )
        db = MagicMock()

        reason = enforce_experiment_stop_policy(db, experiment, now=now)

        self.assertEqual("time_limit_reached", reason)
        condition = experiment.run_manifest["termination"]["triggeredConditions"][0]
        self.assertEqual(61, condition["elapsedSeconds"])
        refresh.assert_called_once_with(db, experiment)
        finalize.assert_called_once_with(db, experiment)

    @patch("src.services.research_experiment_service._finalize_if_ready")
    @patch("src.services.research_experiment_service._refresh_progress")
    def test_out_of_sample_target_stops_on_completed_matching_trial(self, refresh, finalize):
        now = datetime(2026, 8, 25, tzinfo=UTC)
        trial = SimpleNamespace(
            id=uuid.uuid4(),
            backtest_run_id=uuid.uuid4(),
            metrics={"total_return": 0.08},
        )
        rows = MagicMock()
        rows.scalars.return_value = [trial]
        db = MagicMock()
        db.execute.side_effect = [rows, MagicMock()]
        experiment = SimpleNamespace(
            id=uuid.uuid4(),
            workflow_run_id="workflow-1",
            status="running",
            spec={
                "stopPolicy": {
                    "targetMetric": {
                        "metric": "total_return",
                        "operator": "gte",
                        "value": 0.05,
                        "sampleKind": "out_of_sample",
                        "costScenario": "base",
                    }
                }
            },
            run_manifest={"policyStartedAt": now.isoformat()},
        )

        reason = enforce_experiment_stop_policy(db, experiment, now=now)

        self.assertEqual("target_reached", reason)
        condition = experiment.run_manifest["termination"]["triggeredConditions"][0]
        self.assertEqual(0.08, condition["observed"])
        self.assertEqual(str(trial.id), condition["trialId"])

    @patch("src.services.research_experiment_service.enforce_experiment_stop_policy")
    @patch("src.services.research_experiment_service.get_experiment")
    def test_usage_sync_is_absolute_monotonic_and_owned_by_workflow(self, get_experiment, enforce):
        experiment = SimpleNamespace(
            workflow_run_id="workflow-1",
            status="running",
            run_manifest={"tokenUsage": {"inputTokens": 100, "totalTokens": 120}},
        )
        get_experiment.return_value = experiment
        db = MagicMock()

        update_experiment_token_usage(
            db,
            uuid.uuid4(),
            ExperimentTokenUsageUpdate(
                workflowRunId="workflow-1",
                inputTokens=90,
                outputTokens=10,
                totalTokens=100,
            ),
        )

        self.assertEqual(100, experiment.run_manifest["tokenUsage"]["inputTokens"])
        self.assertEqual(120, experiment.run_manifest["tokenUsage"]["totalTokens"])
        enforce.assert_called_once_with(db, experiment)
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(experiment)

    def test_expansion_rejects_parameter_out_of_range(self):
        self.strategy.strategy_type = "mean_reversion"
        self.strategy.params = copy.deepcopy(MEAN_REVERSION_DEFAULTS)
        spec = ExperimentSpec.model_validate(experiment_payload(values=[1.5]))
        with self.assertRaisesRegex(ValueError, "risk.position_size_pct"):
            expand_experiment(self.db, spec)

    @patch("src.services.research_experiment_service.enforce_experiment_stop_policy")
    @patch("src.services.research_experiment_service._finalize_if_ready")
    def test_worker_commits_terminal_trial_before_cross_worker_finalize(self, finalize, enforce):
        events = []
        experiment = SimpleNamespace(id=uuid.uuid4())

        class FinalizeSession:
            def commit(self):
                events.append("commit")

            def expire_all(self):
                events.append("expire")

            def get(self, model, object_id):
                events.append("reload")
                return experiment

        finalize.side_effect = lambda _db, _experiment: events.append("finalize")

        enforce.side_effect = lambda _db, _experiment: events.append("enforce")

        _commit_trial_and_finalize_experiment(FinalizeSession(), experiment)

        self.assertEqual(["commit", "expire", "reload", "enforce", "finalize", "commit"], events)

    def test_worker_restart_does_not_requeue_trial_after_policy_stop(self):
        experiment = SimpleNamespace(
            status="running",
            run_manifest={
                "termination": {
                    "earlyStopped": True,
                    "reason": "token_budget_reached",
                }
            },
        )

        self.assertEqual("policy_stopped", _recovery_stop_code(experiment))
        experiment.run_manifest = {}
        self.assertIsNone(_recovery_stop_code(experiment))


class AgentAuthTests(unittest.TestCase):
    def test_agent_service_requires_enabled_integration_and_matching_token(self):
        with patch.dict(
            os.environ,
            {"QUANT_AGENT_INTEGRATION_ENABLED": "true", "QUANT_AGENT_SERVICE_TOKEN": "expected"},
            clear=False,
        ):
            require_agent_service("Bearer expected")
            with self.assertRaises(HTTPException) as raised:
                require_agent_service("Bearer wrong")
        self.assertEqual(401, raised.exception.status_code)

    def test_agent_service_surface_has_no_broker_or_order_routes(self):
        paths = {
            route.path
            for router in (strategy_agent_router, research_agent_router)
            for route in router.routes
        }
        self.assertTrue(paths)
        self.assertFalse(any("order" in path or "paper-trading" in path for path in paths))

    def test_old_grid_mutation_routes_are_removed(self):
        methods_by_path = {
            route.path: set(route.methods or set())
            for route in research_agent_router.routes
        }
        self.assertNotIn("/api/agent/research/experiments/validate", methods_by_path)
        self.assertNotIn("/api/agent/research/experiments", methods_by_path)
        self.assertIn("/api/agent/research/category-studies/validate", methods_by_path)
        self.assertIn("/api/agent/research/category-studies", methods_by_path)


class AdaptiveResearchContractTests(unittest.TestCase):
    def test_selected_strategy_type_cannot_be_changed(self):
        payload = {
            "workflowRunId": "workflow-1",
            "goal": "Research trend",
            "strategyType": "trend",
            "strategy": {"name": "Draft", "description": "Draft", "strategyType": "mean_reversion", "overrides": {}},
            "name": "Study",
            "hypothesis": "Test",
            "symbols": ["AAPL"],
            "inSample": {"startDate": "2020-01-01", "endDate": "2021-01-01"},
            "outOfSample": {"startDate": "2022-01-01", "endDate": "2023-01-01"},
            "costScenarios": [{"name": "base"}, {"name": "stress"}],
            "searchPolicy": {"maxRounds": 3, "maxTrials": 48, "objectives": [
                {"metric": "oos_total_return", "direction": "maximize"},
                {"metric": "oos_max_drawdown", "direction": "minimize"},
            ]},
            "initialCandidates": [{"overrides": {"risk.position_size_pct": 0.1}, "rationale": "base"}],
        }
        with self.assertRaisesRegex(ValueError, "cannot change"):
            CategoryStudyValidationRequest.model_validate(payload)

    def test_pareto_sort_handles_directions_ties_and_missing_metrics(self):
        objectives = [
            {"metric": "oos_total_return", "direction": "maximize"},
            {"metric": "oos_max_drawdown", "direction": "minimize"},
        ]
        ranks = nondominated_sort(
            [
                ("a", {"oos_total_return": 0.10, "oos_max_drawdown": 0.20}),
                ("b", {"oos_total_return": 0.12, "oos_max_drawdown": 0.25}),
                ("c", {"oos_total_return": 0.08, "oos_max_drawdown": 0.30}),
                ("d", {"oos_total_return": None, "oos_max_drawdown": 0.10}),
            ],
            objectives,
        )
        self.assertEqual(1, ranks["a"])
        self.assertEqual(1, ranks["b"])
        self.assertEqual(2, ranks["c"])
        self.assertIsNone(ranks["d"])


class StrategyIdempotencyTests(unittest.TestCase):
    @patch("src.services.strategy_service.validate_strategy_params", side_effect=lambda _db, **kwargs: kwargs["params"])
    def test_same_key_replays_only_the_same_strategy_request(self, _validate):
        existing = SimpleNamespace(
            name="Draft",
            strategy_type="mean_reversion",
            status="draft",
            params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
        )
        db = ExistingStrategySession(existing)
        replayed = create_strategy_version(
            db,
            name="Draft",
            strategy_type="mean_reversion",
            params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
            description="",
            status="draft",
            idempotency_key="same-key",
        )
        self.assertIs(existing, replayed)
        with self.assertRaises(StrategyCreateConflictError):
            create_strategy_version(
                db,
                name="Different Draft",
                strategy_type="mean_reversion",
                params=copy.deepcopy(MEAN_REVERSION_DEFAULTS),
                description="",
                status="draft",
                idempotency_key="same-key",
            )
