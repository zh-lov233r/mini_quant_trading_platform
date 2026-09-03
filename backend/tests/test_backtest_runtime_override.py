from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.services.backtest_engine import run_backtest


class BacktestRuntimeOverrideTests(unittest.TestCase):
    def test_public_entrypoint_normalizes_runtime_override(self) -> None:
        db = MagicMock()
        db.get.return_value = SimpleNamespace(strategy_type="trend")
        override = {"risk": {"position_size_pct": 0.07}}
        runtime = {
            "strategy_type": "trend",
            "params": {"universe": {"symbols": ["MSFT"]}},
            "engine_ready": True,
        }
        with (
            patch("src.services.backtest_engine.build_runtime_payload", return_value=runtime),
            patch(
                "src.services.backtest_engine.normalize_strategy_params",
                return_value=override,
            ) as normalize,
            patch(
                "src.services.backtest_engine.is_engine_ready",
                return_value=True,
            ),
            patch(
                "src.services.backtest_engine.normalize_point_in_time_policy",
                return_value={"enabled": True},
            ),
            self.assertRaisesRegex(ValueError, "provide universe_symbols or universe_policy"),
        ):
            run_backtest(
                db,
                "strategy",
                date(2026, 1, 5),
                date(2026, 1, 6),
                universe_symbols=["AAPL"],
                universe_policy={"enabled": True},
                runtime_params_override=override,
            )

        normalize.assert_called_once_with("trend", override, None)
        self.assertEqual(runtime["params"], override)

    def test_maintenance_lock_is_acquired_before_dataset_read(self) -> None:
        db = MagicMock()
        strategy = SimpleNamespace(id="strategy", version=1, strategy_type="trend")
        run = SimpleNamespace(id="run")
        db.get.side_effect = [strategy, run, run]
        runtime = {
            "strategy_type": "trend",
            "params": {"universe": {"symbols": ["AAPL"]}, "execution": {}},
            "engine_ready": True,
        }
        resolved = SimpleNamespace(
            membership_semantics="current_active_snapshot",
            instrument_ids=[1],
            manifest=lambda: {"instrument_ids": [1]},
        )
        events: list[str] = []

        def acquire_lock(*_args, **_kwargs) -> None:
            events.append("lock")

        def load_dataset(*_args, **_kwargs):
            events.append("dataset")
            raise RuntimeError("stop after ordering check")

        with (
            patch("src.services.backtest_engine.build_runtime_payload", return_value=runtime),
            patch("src.services.backtest_engine.resolve_backtest_universe", return_value=resolved),
            patch(
                "src.services.backtest_engine.build_strategy_catalog",
                return_value=[{"strategy_type": "trend", "algorithm_revision": "test"}],
            ),
            patch(
                "src.services.backtest_engine.acquire_market_data_read_lock",
                side_effect=acquire_lock,
            ) as lock,
            patch(
                "src.services.backtest_engine._load_prepared_dataset",
                side_effect=load_dataset,
            ),
            self.assertRaisesRegex(RuntimeError, "ordering check"),
        ):
            run_backtest(
                db,
                "strategy",
                date(2026, 1, 5),
                date(2026, 1, 6),
                universe_symbols=["AAPL"],
                existing_run_id="run",
            )

        self.assertEqual(events, ["lock", "dataset"])
        lock.assert_called_once_with(db, allow_draining=True)


if __name__ == "__main__":
    unittest.main()
