from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import MagicMock, patch

from src.services.backtest_engine import BacktestResult, run_backtest


class BacktestRuntimeOverrideTests(unittest.TestCase):
    def test_public_entrypoint_forwards_runtime_override_to_native_runner(self) -> None:
        db = MagicMock()
        override = {"risk": {"position_size_pct": 0.07}}
        expected = BacktestResult(
            run_id="run",
            strategy_id="strategy",
            status="completed",
            initial_cash=100_000.0,
            final_equity=101_000.0,
            total_return=0.01,
            max_drawdown=0.0,
            signal_count=1,
            trade_count=1,
            total_fees=0.0,
            total_slippage=0.0,
        )
        with patch(
            "src.services.native_backtest_service.run_backtest_native",
            return_value=expected,
        ) as native:
            actual = run_backtest(
                db,
                "strategy",
                date(2026, 1, 5),
                date(2026, 1, 6),
                universe_symbols=["AAPL"],
                runtime_params_override=override,
            )
        self.assertIs(expected, actual)
        self.assertEqual(override, native.call_args.kwargs["runtime_params_override"])


if __name__ == "__main__":
    unittest.main()
