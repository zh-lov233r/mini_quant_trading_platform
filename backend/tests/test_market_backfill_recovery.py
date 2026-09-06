from datetime import date
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from unittest import TestCase
from unittest.mock import MagicMock, patch
import subprocess

from backend.utils import backfill_adjusted_prices as adjustments
from backend.utils import run_daily_market_backfill as runner
from backend.utils import massive_enrichment_common as network
from backend.utils.signal_market_state import coverage_is_ready, publish_ready


class MarketBackfillRecoveryTests(TestCase):
    def test_new_split_repairs_pre_event_history(self):
        bars = [adjustments.BarRow(date(2026, 9, 3), 100, 100, 100, 100),
                adjustments.BarRow(date(2026, 9, 4), 50, 50, 50, 50)]
        old = adjustments._compute_adjusted_rows(1, bars, [])
        current = adjustments._compute_adjusted_rows(1, bars,
            [adjustments.ActionRow(date(2026, 9, 4), 'split', 1, 2, None)])
        changed = adjustments.changed_adjustment_rows(current, {r[1]: r[2:] for r in old})
        self.assertEqual(changed[0][1], date(2026, 9, 3))
        self.assertEqual(changed[0][7], 50)
        self.assertEqual(adjustments.changed_adjustment_rows(current, {r[1]: r[2:] for r in current}), [])

    def test_dividend_updates_previous_close_for_daily_return(self):
        bars = [adjustments.BarRow(date(2026, 9, 3), 63.04, 63.04, 63.04, 63.04),
                adjustments.BarRow(date(2026, 9, 4), 62.68, 62.68, 62.68, 62.68)]
        rows = adjustments._compute_adjusted_rows(1, bars,
            [adjustments.ActionRow(date(2026, 9, 4), 'cash_dividend', None, None, .32)])
        self.assertAlmostEqual(rows[0][7], 62.72)
        self.assertAlmostEqual(rows[1][7] / rows[0][7] - 1, -0.0006377551020407823)

    def test_catchup_keeps_recheck_window_and_older_gaps(self):
        args = SimpleNamespace(lookback_days=14,end_date='2026-09-08',start_date=None,cutoff_hour_ny=20)
        start, end, _ = runner._resolve_date_range(args, runner.CoverageWindow(date(2026,9,4),date(2026,9,4)))
        self.assertEqual((start,end),(date(2026,8,26),date(2026,9,8)))
        start, _, _ = runner._resolve_date_range(args, runner.CoverageWindow(date(2026,8,1),date(2026,8,1)))
        self.assertEqual(start,date(2026,8,2))

    def test_partial_market_and_stale_features_cannot_publish(self):
        args = dict(expected=100,present=1,bars=1,features=1,adjusted=1,valid=1,stale=0)
        self.assertFalse(coverage_is_ready(**args))
        args.update(present=100,bars=100,features=100,adjusted=100,valid=100)
        self.assertTrue(coverage_is_ready(**args))
        args['stale']=1
        self.assertFalse(coverage_is_ready(**args))
        args.update(stale=0,expected=0)
        self.assertFalse(coverage_is_ready(**args))

    def test_weekend_revalidates_previous_ready_session(self):
        from datetime import datetime, timezone
        from backend.utils import signal_market_state
        conn = MagicMock()
        def query(sql, params=None):
            result = MagicMock()
            if 'UNION SELECT' in sql:
                result.fetchall.return_value = [(date(2026,9,4),)]
            elif 'SELECT is_open' in sql:
                result.fetchone.return_value = None
            elif 'count(f.instrument_id)' in sql:
                result.fetchone.return_value = (100,100,100,100,0,datetime(2026,9,4,tzinfo=timezone.utc))
            elif 'SELECT count(*),count(b.instrument_id)' in sql:
                result.fetchone.return_value = (100,100)
            return result
        conn.execute.side_effect = query
        conn.__enter__.return_value = conn
        with patch.object(signal_market_state.psycopg,'connect',return_value=conn):
            publish_ready('unused','US',date(2026,9,5),date(2026,9,5))
        inserts = [call for call in conn.execute.call_args_list if 'INSERT INTO signal_data_ready' in call.args[0]]
        self.assertEqual(len(inserts),1)
        self.assertEqual(inserts[0].args[1][1],date(2026,9,4))

    def test_dns_and_rate_limit_retry_without_leaking_url(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value=b'{"results":[1]}'
        errors = [URLError('DNS failed'),HTTPError('https://api.massive.com/?apiKey=secret',429,'limited',{},BytesIO()),response]
        with patch.object(network.request,'urlopen',side_effect=errors) as fetch, patch.object(network.time,'sleep') as sleep:
            self.assertEqual(network.fetch_json('https://api.massive.com',api_key='secret'),{'results':[1]})
        self.assertEqual(fetch.call_count,3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2])

    def test_auth_error_does_not_retry_or_expose_credentials(self):
        error = HTTPError('https://api.massive.com/?apiKey=secret',403,'secret',{},BytesIO(b'secret'))
        with patch.object(network.request,'urlopen',side_effect=error) as fetch, patch.object(network.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'^Massive HTTP 403$'):
                network.fetch_json('https://api.massive.com',api_key='secret')
        self.assertEqual(fetch.call_count,1)
        sleep.assert_not_called()

    def test_step_failure_keeps_reason_and_redacts_credentials(self):
        error = subprocess.CalledProcessError(1,['child'],stderr='RuntimeError: DNS failed apiKey=secret\npostgresql://user:password@host/db')
        with patch.object(runner.subprocess,'run',side_effect=error),patch('builtins.print'):
            with self.assertRaises(RuntimeError) as captured:
                runner._run_step('sync-security-master',runner.REPO_ROOT/'child.py',[],database_url='postgresql://user:password@host/db')
        message=str(captured.exception)
        self.assertIn('step=sync-security-master',message)
        self.assertIn('DNS failed',message)
        self.assertNotIn('secret',message)
        self.assertNotIn('password',message)

    def test_refresh_rejects_invalid_vendor_bar_before_staging(self):
        from datetime import datetime, timezone
        from backend.utils.backfill_missing_eod_from_massive import GroupedDailyBar, _build_stage_rows
        bar=GroupedDailyBar('TEST',datetime(2026,9,4,21,tzinfo=timezone.utc),10,9,8,10,100,None,1)
        with self.assertRaisesRegex(ValueError,'Invalid vendor OHLCV'):
            _build_stage_rows([('TEST',1)],{'TEST':bar})
