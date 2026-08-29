from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.utils import run_daily_market_backfill


class DailyMarketBackfillRunnerTests(unittest.TestCase):
    def test_child_process_receives_database_url_only_through_environment(self) -> None:
        database_url = "postgresql://user:secret@localhost/hzy"

        with patch.object(run_daily_market_backfill.subprocess, "run") as run_mock, patch(
            "builtins.print"
        ) as print_mock:
            run_daily_market_backfill._run_step(
                "test-step",
                Path("child.py"),
                ["--start-date", "2026-08-19"],
                database_url=database_url,
            )

        command = run_mock.call_args.args[0]
        env = run_mock.call_args.kwargs["env"]
        rendered_output = " ".join(
            str(arg) for call in print_mock.call_args_list for arg in call.args
        )

        self.assertNotIn(database_url, command)
        self.assertNotIn(database_url, rendered_output)
        self.assertEqual(env["DATABASE_URL"], database_url)
        self.assertEqual(env["PAPER_TRADING_SCHEDULER_ENABLED"], "false")
        self.assertEqual(env["PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS"], "false")

    def test_dry_run_preserves_enrichment_order_without_security_master_writes(self) -> None:
        args = SimpleNamespace(
            database_url="postgresql://local/hzy",
            start_date="2026-08-20",
            end_date="2026-08-26",
            lookback_days=14,
            cutoff_hour_ny=20,
            skip_security_master=False,
            skip_sic=False,
            skip_ticker_events=False,
            skip_vwap=False,
            skip_short_interest=False,
            full_reference_refresh=True,
            skip_features=False,
            skip_adjustments=False,
            skip_corporate_actions=False,
            dry_run=True,
            skip_quality_check=False,
            strict_quality_check=False,
        )
        coverage = run_daily_market_backfill.CoverageWindow(
            date(2026, 8, 26), date(2026, 8, 26)
        )

        with patch.object(run_daily_market_backfill, "parse_args", return_value=args), patch.object(
            run_daily_market_backfill, "_load_latest_coverage", return_value=coverage
        ), patch.object(run_daily_market_backfill, "_run_script") as run_script, patch.object(
            run_daily_market_backfill, "_run_quality_check"
        ), patch.object(run_daily_market_backfill, "load_dotenv"):
            run_daily_market_backfill.main()

        steps = [call.args[0] for call in run_script.call_args_list]
        self.assertEqual(
            steps,
            [
                "sync-sic-reference",
                "sync-ticker-events",
                "fill-eod-gaps",
                "fill-vwap-gaps",
                "sync-short-interest",
            ],
        )
        rendered_args = {call.args[0]: call.args[2] for call in run_script.call_args_list}
        self.assertIn("--full-refresh", rendered_args["sync-sic-reference"])
        self.assertIn("--dry-run", rendered_args["fill-vwap-gaps"])
        self.assertIn("--skip-security-master", rendered_args["fill-eod-gaps"])
