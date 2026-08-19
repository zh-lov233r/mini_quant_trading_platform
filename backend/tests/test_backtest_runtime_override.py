from __future__ import annotations

import copy
import sys
import unittest
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.tables import Strategy, StrategyRun
from src.services.backtest_engine import ExecutionStats, run_backtest
from src.services.strategy_engine import SignalEvent
from src.services.strategy_registry import MEAN_REVERSION_DEFAULTS


class BacktestSession:
    def __init__(self, strategy):
        self.strategy = strategy
        self.run = None

    def get(self, model, object_id):
        if model is Strategy and str(object_id) == str(self.strategy.id):
            return self.strategy
        if model is StrategyRun and self.run is not None and str(object_id) == str(self.run.id):
            return self.run
        return None

    def add(self, item):
        if isinstance(item, StrategyRun):
            self.run = item

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, item):
        if isinstance(item, StrategyRun) and item.id is None:
            item.id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class BacktestRuntimeOverrideTests(unittest.TestCase):
    def test_override_is_snapshotted_without_mutating_strategy_and_fills_next_open(self):
        base_params = copy.deepcopy(MEAN_REVERSION_DEFAULTS)
        strategy = SimpleNamespace(
            id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            strategy_key="mean-reversion",
            name="Mean Reversion",
            version=3,
            status="draft",
            strategy_type="mean_reversion",
            params=base_params,
        )
        override = copy.deepcopy(base_params)
        override["risk"]["position_size_pct"] = 0.07
        snapshots = {
            date(2026, 1, 5): {
                "AAPL": {
                    "dt_ny": date(2026, 1, 5),
                    "ts": datetime(2026, 1, 5, 21, tzinfo=UTC),
                    "open": 100.0,
                    "close": 105.0,
                }
            },
            date(2026, 1, 6): {
                "AAPL": {
                    "dt_ny": date(2026, 1, 6),
                    "ts": datetime(2026, 1, 6, 21, tzinfo=UTC),
                    "open": 110.0,
                    "close": 112.0,
                }
            },
        }
        signal = SignalEvent(
            strategy_id=str(strategy.id),
            ts=snapshots[date(2026, 1, 5)]["AAPL"]["ts"],
            symbol="AAPL",
            action="BUY",
            reason="explicit timing regression",
        )
        handler_calls = 0
        observed_fills = []

        def handler(_runtime, _snapshots):
            nonlocal handler_calls
            handler_calls += 1
            return [signal] if handler_calls == 1 else []

        def record_buy(**kwargs):
            if kwargs["signals"]:
                observed_fills.append(
                    (kwargs["signals"][0].ts, kwargs["trade_day"], kwargs["execution_prices"]["AAPL"])
                )
            return ExecutionStats()

        db = BacktestSession(strategy)
        with (
            patch("src.services.backtest_engine._load_feature_snapshots_by_date", return_value=snapshots),
            patch("src.services.backtest_engine._load_split_adjustments_by_date", return_value={}),
            patch("src.services.backtest_engine._load_close_maps_by_symbol", return_value={}),
            patch("src.services.backtest_engine._apply_split_adjustments"),
            patch("src.services.backtest_engine._inject_backtest_positions"),
            patch("src.services.backtest_engine._attach_recent_history"),
            patch("src.services.backtest_engine._apply_sell_signals", return_value=ExecutionStats()),
            patch("src.services.backtest_engine._apply_buy_signals", side_effect=record_buy),
            patch.dict("src.services.backtest_engine.STRATEGY_HANDLERS", {"mean_reversion": handler}),
        ):
            result = run_backtest(
                db,
                strategy.id,
                date(2026, 1, 5),
                date(2026, 1, 6),
                universe_symbols=["AAPL"],
                runtime_params_override=override,
            )

        self.assertEqual("next_session_open", db.run.summary_metrics["execution_lag"])
        self.assertEqual([(signal.ts, date(2026, 1, 6), 110.0)], observed_fills)
        self.assertEqual(0.07, db.run.config_snapshot["risk"]["position_size_pct"])
        self.assertEqual(base_params, strategy.params)
        self.assertEqual("completed", result.status)


if __name__ == "__main__":
    unittest.main()
