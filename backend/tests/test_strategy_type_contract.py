from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import get_args
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import quant_kernel
from pydantic import ValidationError

from src.api.strategies import (
    StrategyCatalogItem, StrategyCreate, StrategyOut, StrategyProposal,
    StrategyRuntimeOut, StrategyValidationOut, validate_strategy,
)
from src.schemas.dashboard import DashboardStrategyEvidence
from src.schemas.research import CategoryStrategyProposal
from src.services.strategy_registry import build_runtime_payload, build_strategy_catalog, normalize_strategy_params
from src.services.strategy_service import validate_strategy_params
from src.services.strategy_types import StrategyType


class StrategyTypeContractTests(unittest.TestCase):
    def test_native_catalog_types_defaults_and_response_contract(self) -> None:
        native = list(quant_kernel.catalog())
        self.assertEqual({item["strategy_type"] for item in native}, set(get_args(StrategyType)))
        self.assertNotIn("custom", get_args(StrategyType))
        self.assertEqual(build_strategy_catalog(), native)
        for item in native:
            with self.subTest(strategy_type=item["strategy_type"]):
                self.assertNotIn("engine_ready", item)
                StrategyCatalogItem.model_validate(item)
                self.assertEqual(normalize_strategy_params(item["strategy_type"], item["defaults"]), item["defaults"])
        for model in (StrategyCatalogItem, StrategyOut, StrategyRuntimeOut, StrategyValidationOut, DashboardStrategyEvidence):
            self.assertNotIn("engine_ready", model.model_json_schema()["properties"])
        catalog = build_strategy_catalog()
        catalog[0]["defaults"]["risk"]["max_positions"] = 999
        self.assertNotEqual(build_strategy_catalog()[0]["defaults"]["risk"]["max_positions"], 999)

    def test_type_errors_are_not_masked_by_missing_fields(self) -> None:
        valid = [
            (StrategyCatalogItem, build_strategy_catalog()[0]),
            (StrategyCreate, dict(name="Test", strategy_type="trend", params={})),
            (StrategyProposal, dict(name="Test", strategy_type="trend", symbols=["AAPL"])),
            (CategoryStrategyProposal, dict(name="Test", description="Test", strategyType="trend")),
            (StrategyRuntimeOut, dict(strategy_id="id", strategy_key="key", display_name="Test", name="Test", version=1, status="draft", strategy_type="trend", params={})),
        ]
        for model, payload in valid:
            model.model_validate(payload)
            key = "strategyType" if "strategyType" in payload else "strategy_type"
            for kind in ("custom", "future_strategy"):
                with self.subTest(model=model.__name__, kind=kind):
                    with self.assertRaises(ValidationError) as error:
                        model.model_validate({**payload, key: kind, "engine_ready": True})
                    self.assertEqual([e["loc"] for e in error.exception.errors()], [(key,)])

    def test_validation_response_keeps_normalized_parameters(self) -> None:
        response = validate_strategy(StrategyCreate(name="Test", strategy_type="mean_reversion", params={}), db=MagicMock())
        self.assertEqual(set(response.model_dump()), {"valid", "strategy_type", "normalized_params"})
        self.assertTrue(response.normalized_params["signal"])

    def test_normalizes_once_and_preserves_trend_feature_gate(self) -> None:
        with patch("src.services.strategy_service.normalize_strategy_params", wraps=normalize_strategy_params) as normalize:
            validate_strategy_params(MagicMock(), strategy_type="mean_reversion", params={}, description=None)
        normalize.assert_called_once()
        with patch("src.services.strategy_service.load_feature_support", return_value={"trend": {"ema_windows": [], "sma_windows": []}}):
            with self.assertRaisesRegex(ValueError, "unsupported fast indicator"):
                validate_strategy_params(MagicMock(), strategy_type="trend", params={}, description=None)

    def test_invalid_database_params_fail_before_execution_or_broker_calls(self) -> None:
        from src.services.backtest_engine import run_backtest
        from src.services.paper_trading_service import run_paper_trading
        from src.services.signal_scan_service import strategy_snapshots
        for kind, params in (("custom", {}), ("future_strategy", {}), ("trend", []), ("trend", None), ("trend", {"risk": {"max_positions": 0}})):
            with self.subTest(kind=kind, params=params):
                db, broker = MagicMock(), MagicMock()
                db.get.return_value = SimpleNamespace(id=uuid4(), strategy_key="key", name="Test", version=1, status="draft", strategy_type=kind, params=params)
                for action in (lambda: build_runtime_payload(db.get.return_value), lambda: run_backtest(db, "id", date(2025, 1, 1), date(2025, 1, 2)), lambda: run_paper_trading(db, "id", date(2025, 1, 1), alpaca_client=broker, submit_orders=False), lambda: strategy_snapshots(db, [uuid4()])):
                    with self.assertRaises((TypeError, ValueError)):
                        action()
                self.assertEqual(broker.mock_calls, [])
                db.add.assert_not_called()
