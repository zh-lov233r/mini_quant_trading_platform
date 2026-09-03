from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.utils import run_daily_market_backfill
from backend.utils.market_data_maintenance_guard import require_market_data_maintenance_owner


class DailyMarketBackfillRunnerTests(unittest.TestCase):
    def test_write_child_requires_parent_maintenance_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "run_daily_market_backfill.py"):
                require_market_data_maintenance_owner("postgresql://local/hzy")

    def test_write_child_joins_matching_updating_owner(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            "updating",
            "owner-token",
        )
        with patch.dict(
            "os.environ",
            {"MARKET_DATA_MAINTENANCE_OWNER": "owner-token"},
            clear=True,
        ), patch(
            "backend.utils.market_data_maintenance_guard.psycopg.connect",
            return_value=connection,
        ):
            owner = require_market_data_maintenance_owner("postgresql://local/hzy")

        self.assertEqual(owner, "owner-token")

    def test_child_process_receives_database_url_only_through_environment(self) -> None:
        database_url = "postgresql://user:secret@localhost/hzy"

        with patch.object(run_daily_market_backfill.subprocess, "run") as run_mock, patch(
            "builtins.print"
        ) as print_mock, patch.object(
            run_daily_market_backfill,
            "MAINTENANCE_OWNER_TOKEN",
            "owner-token",
        ):
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
        self.assertEqual(env["MARKET_DATA_MAINTENANCE_OWNER"], "owner-token")

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

    def test_apply_enters_maintenance_and_reopens_only_after_quality_gate(self) -> None:
        args = SimpleNamespace(
            database_url="postgresql://local/hzy",
            start_date="2026-08-20",
            end_date="2026-08-26",
            lookback_days=14,
            cutoff_hour_ny=20,
            skip_security_master=True,
            skip_sic=True,
            skip_ticker_events=True,
            skip_vwap=True,
            skip_short_interest=True,
            full_reference_refresh=False,
            skip_features=True,
            skip_adjustments=True,
            skip_corporate_actions=True,
            dry_run=False,
            skip_quality_check=False,
            strict_quality_check=False,
        )
        coverage = run_daily_market_backfill.CoverageWindow(
            date(2026, 8, 26), date(2026, 8, 26)
        )

        with patch.object(run_daily_market_backfill, "parse_args", return_value=args), patch.object(
            run_daily_market_backfill, "_load_latest_coverage", return_value=coverage
        ), patch.object(run_daily_market_backfill, "_run_script"), patch.object(
            run_daily_market_backfill, "_run_quality_check"
        ) as quality_check, patch.object(
            run_daily_market_backfill, "MaintenanceWindow"
        ) as maintenance_class, patch.object(run_daily_market_backfill, "load_dotenv"):
            run_daily_market_backfill.main()

        maintenance = maintenance_class.return_value
        maintenance.start.assert_called_once_with()
        quality_check.assert_called_once()
        maintenance.succeed.assert_called_once_with()
