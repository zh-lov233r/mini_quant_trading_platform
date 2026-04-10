from __future__ import annotations

import sys
import unittest
from datetime import date, datetime as real_datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.paper_trading_scheduler import (  # noqa: E402
    NEW_YORK,
    PaperTradingDailyScheduler,
    PaperTradingSchedulerConfig,
    SchedulablePortfolio,
)


class PaperTradingDailySchedulerTests(unittest.TestCase):
    def test_poll_once_waits_until_features_are_ready(self) -> None:
        scheduler = PaperTradingDailyScheduler(
            PaperTradingSchedulerConfig(
                enabled=True,
                run_time_ny=time(hour=23, minute=30),
                poll_seconds=60.0,
                submit_orders=True,
                continue_on_error=True,
            )
        )

        with patch(
            "src.services.paper_trading_scheduler._latest_ready_daily_features_trade_date",
            return_value=None,
        ) as mock_ready_date, patch(
            "src.services.paper_trading_scheduler._load_schedulable_portfolios",
        ) as mock_targets, patch(
            "src.services.paper_trading_scheduler.run_multi_strategy_paper_trading",
        ) as mock_run:
            scheduler._poll_once()

        mock_ready_date.assert_called_once()
        mock_targets.assert_not_called()
        mock_run.assert_not_called()

    def test_poll_once_runs_latest_ready_trade_date_after_midnight(self) -> None:
        scheduler = PaperTradingDailyScheduler(
            PaperTradingSchedulerConfig(
                enabled=True,
                run_time_ny=time(hour=23, minute=30),
                poll_seconds=60.0,
                submit_orders=True,
                continue_on_error=True,
            )
        )
        ready_trade_date = date(2026, 4, 10)
        now_ny = real_datetime(2026, 4, 11, 0, 5, tzinfo=NEW_YORK)
        portfolio = SchedulablePortfolio(
            account_id="acct-1",
            account_name="test-account",
            portfolio_name="growth",
        )
        session_context = MagicMock()
        session_context.__enter__.return_value = object()
        session_context.__exit__.return_value = False

        with patch(
            "src.services.paper_trading_scheduler.datetime",
        ) as mock_datetime, patch(
            "src.services.paper_trading_scheduler._latest_ready_daily_features_trade_date",
            return_value=ready_trade_date,
        ), patch(
            "src.services.paper_trading_scheduler._load_schedulable_portfolios",
            return_value=[portfolio],
        ), patch(
            "src.services.paper_trading_scheduler._has_scheduler_run_for_trade_date",
            return_value=False,
        ) as mock_has_run, patch(
            "src.services.paper_trading_scheduler.SessionLocal",
            return_value=session_context,
        ), patch(
            "src.services.paper_trading_scheduler.run_multi_strategy_paper_trading",
            return_value=SimpleNamespace(completed_runs=1, total_runs=1),
        ) as mock_run:
            mock_datetime.now.return_value = now_ny
            mock_datetime.combine.side_effect = (
                lambda trade_date, run_time, tzinfo=None: real_datetime.combine(
                    trade_date,
                    run_time,
                    tzinfo=tzinfo,
                )
            )
            scheduler._poll_once()

        mock_has_run.assert_called_once_with("growth", ready_trade_date)
        mock_run.assert_called_once()
        run_args, run_kwargs = mock_run.call_args
        self.assertEqual(run_args[1], ready_trade_date)
        self.assertEqual(run_kwargs["portfolio_name"], "growth")
        self.assertEqual(run_kwargs["submit_orders"], True)
        self.assertEqual(run_kwargs["auto_run_only"], True)


if __name__ == "__main__":
    unittest.main()
