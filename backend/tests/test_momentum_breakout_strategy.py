from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta, timezone
import unittest

from src.services.backtest_engine import FEATURE_RANGE_SQL
from src.services.strategy_engine import FEATURE_SNAPSHOT_SQL, evaluate_native_signals
from src.services.strategy_registry import (
    MOMENTUM_BREAKOUT_DEFAULTS,
    build_strategy_catalog,
    is_engine_ready,
    normalize_strategy_params,
    required_feature_keys,
)


def _runtime(*, symbols: list[str]) -> dict:
    params = copy.deepcopy(MOMENTUM_BREAKOUT_DEFAULTS)
    params["universe"]["symbols"] = symbols
    params["universe"]["selection_mode"] = "manual"
    return {
        "strategy_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "strategy_type": "momentum_breakout",
        "params": params,
    }


def _snapshot(symbol: str, trade_date: date, *, timestamp: datetime | None = None) -> dict:
    return {
        "symbol": symbol,
        "asset_type": "CS",
        "dt_ny": trade_date,
        "ts": timestamp,
        "open": 100.0,
        "high": 104.0,
        "low": 99.0,
        "close": 103.0,
        "sma_20": 100.0,
        "ret_20d": 0.12,
        "volume": 160.0,
        "volume_sma_20": 100.0,
        "atr_14": 2.0,
        "position": 0.0,
        "avg_entry_price": None,
    }


class MomentumBreakoutStrategyTests(unittest.TestCase):
    def test_catalog_registers_engine_ready_strategy_without_activation(self) -> None:
        catalog_item = next(
            item for item in build_strategy_catalog()
            if item["strategy_type"] == "momentum_breakout"
        )
        normalized = normalize_strategy_params("momentum_breakout", catalog_item["defaults"])
        self.assertTrue(catalog_item["engine_ready"])
        self.assertTrue(is_engine_ready("momentum_breakout", normalized))
        self.assertNotIn("status", catalog_item)
        self.assertEqual(
            ["open", "close", "sma_20", "ret_20d", "volume", "volume_sma_20", "atr_14"],
            required_feature_keys("momentum_breakout", normalized),
        )
        self.assertEqual(
            {"timeframe": "1d", "rebalance": "daily", "run_at": "close"},
            normalized["execution"],
        )

    def test_market_data_paths_prefer_forward_adjusted_ohlc(self) -> None:
        for query in (FEATURE_SNAPSHOT_SQL, FEATURE_RANGE_SQL):
            for price_field in ("open", "high", "low", "close"):
                self.assertIn(
                    f"COALESCE(bars.{price_field}_fa, bars.{price_field}_u) AS {price_field}",
                    query,
                )

    def test_native_day_is_deterministic_scored_and_shared(self) -> None:
        runtime = _runtime(symbols=["ZZZ", "AAPL"])
        trade_date = date(2026, 1, 5)
        snapshots = {
            "ZZZ": _snapshot("ZZZ", trade_date, timestamp=datetime(2026, 1, 5, 16)),
            "AAPL": _snapshot(
                "AAPL",
                trade_date,
                timestamp=datetime(2026, 1, 5, 16, tzinfo=timezone(timedelta(hours=-5))),
            ),
        }
        first = evaluate_native_signals(runtime, copy.deepcopy(snapshots))
        second = evaluate_native_signals(runtime, copy.deepcopy(snapshots))
        self.assertEqual(["AAPL", "ZZZ"], [event.symbol for event in first])
        self.assertEqual(
            [(event.symbol, event.action, event.reason, event.score) for event in first],
            [(event.symbol, event.action, event.reason, event.score) for event in second],
        )
        self.assertEqual(datetime(2026, 1, 5, 21, tzinfo=UTC), first[0].ts)
        self.assertEqual(datetime(2026, 1, 5, 16, tzinfo=UTC), first[1].ts)
        self.assertEqual(1, first[0].metadata["strength"]["rank"])

    def test_missing_timestamp_uses_new_york_close(self) -> None:
        runtime = _runtime(symbols=["AAPL"])
        expected_by_date = {
            date(2026, 1, 5): datetime(2026, 1, 5, 21, tzinfo=UTC),
            date(2026, 7, 6): datetime(2026, 7, 6, 20, tzinfo=UTC),
        }
        for trade_date, expected in expected_by_date.items():
            events = evaluate_native_signals(runtime, {"AAPL": _snapshot("AAPL", trade_date)})
            self.assertEqual(expected, events[0].ts)


if __name__ == "__main__":
    unittest.main()
