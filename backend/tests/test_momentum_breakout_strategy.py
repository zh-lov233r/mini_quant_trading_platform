from __future__ import annotations

import copy
import sys
import unittest
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.tables import PortfolioSnapshot, Signal, Strategy, StrategyRun, Transaction
from src.services.backtest_engine import STRATEGY_HANDLERS as BACKTEST_HANDLERS
from src.services.backtest_engine import run_backtest
from src.services.paper_trading_service import STRATEGY_HANDLERS as PAPER_HANDLERS
from src.services.strategy_engine import STRATEGY_HANDLERS
from src.services.strategy_registry import (
    MOMENTUM_BREAKOUT_DEFAULTS,
    build_strategy_catalog,
    is_engine_ready,
    normalize_strategy_params,
    required_feature_keys,
)


class RecordingBacktestSession:
    def __init__(self, strategy: SimpleNamespace) -> None:
        self.strategy = strategy
        self.run: StrategyRun | None = None
        self.signals: list[Signal] = []
        self.transactions: list[Transaction] = []
        self.snapshots: list[PortfolioSnapshot] = []

    def get(self, model, object_id):
        if model is Strategy and str(object_id) == str(self.strategy.id):
            return self.strategy
        if model is StrategyRun and self.run is not None and str(object_id) == str(self.run.id):
            return self.run
        return None

    def add(self, item) -> None:
        if isinstance(item, StrategyRun):
            self.run = item
        elif isinstance(item, Signal):
            self.signals.append(item)
        elif isinstance(item, Transaction):
            self.transactions.append(item)
        elif isinstance(item, PortfolioSnapshot):
            self.snapshots.append(item)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def refresh(self, item) -> None:
        if isinstance(item, StrategyRun) and item.id is None:
            item.id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _runtime(*, symbols: list[str]) -> dict:
    params = copy.deepcopy(MOMENTUM_BREAKOUT_DEFAULTS)
    params["universe"]["symbols"] = symbols
    params["universe"]["selection_mode"] = "manual"
    return {
        "strategy_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "strategy_type": "momentum_breakout",
        "params": params,
    }


def _snapshot(
    symbol: str,
    trade_date: date,
    *,
    open_price: float,
    close: float,
    sma_20: float,
    ret_20d: float,
    volume: float,
    volume_sma_20: float,
    timestamp: datetime | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "asset_type": "CS",
        "dt_ny": trade_date,
        "ts": timestamp or datetime(trade_date.year, trade_date.month, trade_date.day, 21, tzinfo=UTC),
        "open": open_price,
        "high": max(open_price, close),
        "low": min(open_price, close),
        "close": close,
        "sma_20": sma_20,
        "ret_20d": ret_20d,
        "volume": volume,
        "volume_sma_20": volume_sma_20,
        "atr_14": 2.0,
        "position": 0.0,
        "avg_entry_price": None,
    }


class MomentumBreakoutStrategyTests(unittest.TestCase):
    def test_catalog_registers_engine_ready_strategy_without_activation(self) -> None:
        catalog_item = next(
            item for item in build_strategy_catalog() if item["strategy_type"] == "momentum_breakout"
        )
        normalized = normalize_strategy_params("momentum_breakout", catalog_item["defaults"])

        self.assertTrue(catalog_item["engine_ready"])
        self.assertTrue(is_engine_ready("momentum_breakout", normalized))
        self.assertNotIn("status", catalog_item)
        self.assertNotIn("status", catalog_item["defaults"])
        self.assertEqual(
            ["close", "sma_20", "ret_20d", "volume", "volume_sma_20"],
            required_feature_keys("momentum_breakout", normalized),
        )

    def test_shared_handler_is_deterministic_timezone_aware_and_used_by_both_consumers(self) -> None:
        runtime = _runtime(symbols=["ZZZ", "AAPL"])
        trade_date = date(2026, 1, 5)
        snapshots = {
            "ZZZ": _snapshot(
                "ZZZ",
                trade_date,
                open_price=50.0,
                close=53.0,
                sma_20=50.0,
                ret_20d=0.11,
                volume=180.0,
                volume_sma_20=100.0,
                timestamp=datetime(2026, 1, 5, 16),
            ),
            "AAPL": _snapshot(
                "AAPL",
                trade_date,
                open_price=100.0,
                close=103.0,
                sma_20=100.0,
                ret_20d=0.12,
                volume=160.0,
                volume_sma_20=100.0,
                timestamp=datetime(2026, 1, 5, 16, tzinfo=timezone(timedelta(hours=-5))),
            ),
        }

        self.assertIs(STRATEGY_HANDLERS, BACKTEST_HANDLERS)
        self.assertIs(STRATEGY_HANDLERS, PAPER_HANDLERS)
        backtest_events = BACKTEST_HANDLERS["momentum_breakout"](runtime, copy.deepcopy(snapshots))
        paper_events = PAPER_HANDLERS["momentum_breakout"](runtime, copy.deepcopy(snapshots))

        self.assertEqual(["AAPL", "ZZZ"], [event.symbol for event in backtest_events])
        self.assertEqual(
            [(event.symbol, event.action, event.reason, event.score) for event in backtest_events],
            [(event.symbol, event.action, event.reason, event.score) for event in paper_events],
        )
        self.assertEqual(datetime(2026, 1, 5, 21, tzinfo=UTC), backtest_events[0].ts)
        self.assertEqual(datetime(2026, 1, 5, 16, tzinfo=UTC), backtest_events[1].ts)
        self.assertEqual(
            "forward_adjusted_fallback_unadjusted",
            backtest_events[0].metadata["price_semantics"],
        )
        self.assertAlmostEqual(1.75, backtest_events[0].score)

    def test_backtest_uses_day_t_signals_t_plus_one_open_fills_and_explicit_costs(self) -> None:
        strategy_params = _runtime(symbols=["AAPL"])["params"]
        strategy_params["risk"]["position_size_pct"] = 0.50
        strategy_params["risk"]["take_profit_pct"] = 0.10
        strategy = SimpleNamespace(
            id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            strategy_key="momentum-breakout",
            name="Momentum Breakout",
            version=1,
            status="draft",
            strategy_type="momentum_breakout",
            params=strategy_params,
        )
        first_day = date(2026, 1, 5)
        second_day = date(2026, 1, 6)
        third_day = date(2026, 1, 7)
        snapshots = {
            first_day: {
                "AAPL": _snapshot(
                    "AAPL",
                    first_day,
                    open_price=98.0,
                    close=103.0,
                    sma_20=100.0,
                    ret_20d=0.12,
                    volume=160.0,
                    volume_sma_20=100.0,
                )
            },
            second_day: {
                "AAPL": _snapshot(
                    "AAPL",
                    second_day,
                    open_price=100.0,
                    close=112.0,
                    sma_20=103.0,
                    ret_20d=0.15,
                    volume=170.0,
                    volume_sma_20=100.0,
                )
            },
            third_day: {
                "AAPL": _snapshot(
                    "AAPL",
                    third_day,
                    open_price=120.0,
                    close=120.0,
                    sma_20=110.0,
                    ret_20d=-0.01,
                    volume=90.0,
                    volume_sma_20=100.0,
                )
            },
        }
        db = RecordingBacktestSession(strategy)

        with (
            patch("src.services.backtest_engine._load_feature_snapshots_by_date", return_value=snapshots),
            patch("src.services.backtest_engine._load_split_adjustments_by_date", return_value={}),
            patch("src.services.backtest_engine._load_close_maps_by_symbol", return_value={}),
        ):
            result = run_backtest(
                db,
                strategy.id,
                first_day,
                third_day,
                initial_cash=1_000.0,
                commission_bps=0.0,
                commission_min=1.0,
                slippage_bps=10.0,
                universe_symbols=["AAPL"],
            )

        self.assertEqual(["BUY", "SELL"], [signal.signal for signal in db.signals])
        self.assertEqual(
            [
                datetime(2026, 1, 5, 21, tzinfo=UTC),
                datetime(2026, 1, 6, 21, tzinfo=UTC),
            ],
            [signal.ts for signal in db.signals],
        )
        self.assertEqual(["BUY", "SELL"], [transaction.side for transaction in db.transactions])
        self.assertEqual([second_day.isoformat(), third_day.isoformat()], [
            transaction.meta["execution_trade_date"] for transaction in db.transactions
        ])
        self.assertEqual(
            [first_day.isoformat(), second_day.isoformat()],
            [transaction.meta["signal_ts"][:10] for transaction in db.transactions],
        )
        self.assertAlmostEqual(100.1, float(db.transactions[0].price))
        self.assertAlmostEqual(119.88, float(db.transactions[1].price))
        self.assertAlmostEqual(4.985014985014986, float(db.transactions[0].qty))
        self.assertEqual([1.0, 1.0], [float(transaction.fee) for transaction in db.transactions])
        self.assertAlmostEqual(1.0967032967032968, result.total_slippage)
        self.assertAlmostEqual(1096.6035964035964, result.final_equity)
        self.assertAlmostEqual(0.09660359640359628, result.total_return)
        self.assertEqual("next_session_open", db.run.summary_metrics["execution_lag"])
        self.assertEqual(
            {"commission_bps": 0.0, "commission_min": 1.0, "slippage_bps": 10.0},
            db.run.summary_metrics["cost_model"],
        )
        self.assertEqual(2, result.signal_count)
        self.assertEqual(2, result.trade_count)
        self.assertEqual(0, db.run.summary_metrics["pending_signal_count"])
        self.assertEqual({}, db.snapshots[-1].positions)


if __name__ == "__main__":
    unittest.main()
