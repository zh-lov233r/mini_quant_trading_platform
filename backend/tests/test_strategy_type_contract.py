from __future__ import annotations

import unittest

from pydantic import ValidationError

from src.api.strategies import StrategyCatalogItem, StrategyRuntimeOut


class StrategyTypeContractTests(unittest.TestCase):
    def test_catalog_rejects_an_unregistered_strategy_type(self) -> None:
        with self.assertRaises(ValidationError):
            StrategyCatalogItem.model_validate(
                {
                    "strategy_type": "future_strategy",
                    "label": "Future",
                    "description": "not registered",
                    "engine_ready": False,
                    "defaults": {},
                }
            )

    def test_runtime_response_rejects_an_unregistered_strategy_type(self) -> None:
        with self.assertRaises(ValidationError):
            StrategyRuntimeOut.model_validate(
                {
                    "strategy_id": "strategy-1",
                    "strategy_key": "future_strategy",
                    "display_name": "Future",
                    "name": "Future",
                    "version": 1,
                    "status": "draft",
                    "strategy_type": "future_strategy",
                    "engine_ready": False,
                    "params": {},
                }
            )


if __name__ == "__main__":
    unittest.main()
