from __future__ import annotations

import copy
import os
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from src.core.agent_auth import require_agent_service
from src.api.research import agent_router as research_agent_router
from src.api.strategies import agent_router as strategy_agent_router
from src.schemas.research import ExperimentSpec
from src.services.research_experiment_service import expand_experiment
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

    def test_expansion_rejects_parameter_out_of_range(self):
        self.strategy.strategy_type = "mean_reversion"
        self.strategy.params = copy.deepcopy(MEAN_REVERSION_DEFAULTS)
        spec = ExperimentSpec.model_validate(experiment_payload(values=[1.5]))
        with self.assertRaisesRegex(ValueError, "risk.position_size_pct"):
            expand_experiment(self.db, spec)


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
