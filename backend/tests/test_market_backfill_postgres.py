"""Database-backed checks write only connection-local temporary tables."""
from contextlib import nullcontext
from datetime import date, datetime, timezone
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch
import psycopg
from backend.utils import signal_market_state as ready
from backend.utils.check_market_data_quality import load_market_counts, choose_latest_complete_day
from backend.utils.backfill_missing_eod_from_massive import _load_missing_symbols, _stage_and_upsert_rows


@skipUnless(os.getenv('MARKET_DATA_TEST_DATABASE_URL'), 'requires MARKET_DATA_TEST_DATABASE_URL; writes only temporary tables')
class MarketBackfillPostgresTests(TestCase):
    def setUp(self):
        self.conn=psycopg.connect(os.environ['MARKET_DATA_TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://'))
        self.conn.execute('CREATE TEMP TABLE instruments (id bigint PRIMARY KEY,currency text,asset_type text,ticker_canonical text,listed_at date,delisted_at date,is_active boolean)')
        self.conn.execute('CREATE TEMP TABLE symbol_history (instrument_id bigint,symbol text,valid_from date,valid_to date,is_primary boolean)')
        for name in ('eod_bars','daily_features','signal_data_ready','signal_market_sessions'):
            self.conn.execute(f'CREATE TEMP TABLE {name} (LIKE public.{name} INCLUDING ALL)')
        self.conn.execute("INSERT INTO instruments SELECT n,'USD','CS','TEST'||n,'2020-01-01',NULL,true FROM generate_series(1,100) n")
        self.conn.execute("INSERT INTO instruments VALUES (101,'CNY','CS','CNTEST','2020-01-01',NULL,true)")
        self.conn.execute("INSERT INTO symbol_history SELECT id,ticker_canonical,'2020-01-01',NULL,true FROM instruments")

    def tearDown(self):
        self.conn.close()

    def bars(self,day,count):
        self.conn.execute("""INSERT INTO eod_bars(instrument_id,ts_utc,open_u,high_u,low_u,close_u,open_fa,high_fa,low_fa,close_fa,volume)
          SELECT n,%s,10,10,10,10,10,10,10,10,100 FROM generate_series(1,%s) n""",(datetime.combine(day,datetime.min.time().replace(hour=21),timezone.utc),count))
        self.conn.execute("INSERT INTO daily_features(instrument_id,dt_ny,asof) SELECT instrument_id,dt_ny,asof FROM eod_bars ON CONFLICT DO NOTHING")

    def test_market_counts_never_combine_currencies(self):
        self.bars(date(2026,9,3),100)
        self.bars(date(2026,9,4),100)
        self.conn.execute("INSERT INTO eod_bars(instrument_id,ts_utc) VALUES(101,'2026-09-03 21:00Z')")
        counts=load_market_counts(self.conn,'USD',date(2026,9,4))
        latest,baseline=choose_latest_complete_day(counts)
        self.assertEqual(latest.trade_date,date(2026,9,4))
        self.assertEqual(baseline,100)
        self.assertEqual(load_market_counts(self.conn,'CNY',date(2026,9,4))[0].rows,1)

    def test_partial_session_blocked_then_weekend_revalidates_previous_day(self):
        self.bars(date(2026,9,3),100)
        self.bars(date(2026,9,4),1)
        self.conn.execute("INSERT INTO signal_data_ready(market,session_date,version,coverage,completed_at,valid) VALUES('US','2026-09-04','old','{}',now(),true)")
        with patch.object(ready.psycopg,'connect',side_effect=lambda _:nullcontext(self.conn)):
            ready.publish_ready('unused','US',date(2026,9,5),date(2026,9,5))
        self.assertFalse(self.conn.execute("SELECT valid FROM signal_data_ready WHERE market='US'").fetchone()[0])
        self.conn.execute("""INSERT INTO eod_bars(instrument_id,ts_utc,open_u,high_u,low_u,close_u,open_fa,high_fa,low_fa,close_fa,volume)
          SELECT n,'2026-09-04 21:00Z',10,10,10,10,10,10,10,10,100 FROM generate_series(2,100) n""")
        self.conn.execute("INSERT INTO daily_features(instrument_id,dt_ny,asof) SELECT instrument_id,dt_ny,asof FROM eod_bars ON CONFLICT DO NOTHING")
        with patch.object(ready.psycopg,'connect',side_effect=lambda _:nullcontext(self.conn)):
            ready.publish_ready('unused','US',date(2026,9,5),date(2026,9,5))
        self.assertTrue(self.conn.execute("SELECT valid FROM signal_data_ready WHERE market='US'").fetchone()[0])

    def test_existing_bars_refresh_and_exclude_cn(self):
        self.bars(date(2026,9,4),100)
        self.assertEqual(_load_missing_symbols(self.conn,date(2026,9,4)),[])
        symbols=_load_missing_symbols(self.conn,date(2026,9,4),refresh_existing=True)
        self.assertEqual(len(symbols),100)
        changed=_stage_and_upsert_rows(self.conn,[(1,datetime(2026,9,4,21,tzinfo=timezone.utc),12,12,12,12,200,12,1)])
        self.assertEqual(changed,1)
        self.assertEqual(self.conn.execute('SELECT close_u,volume FROM eod_bars WHERE instrument_id=1').fetchone(),(12,200))
        self.assertEqual(_stage_and_upsert_rows(self.conn,[(1,datetime(2026,9,4,21,tzinfo=timezone.utc),12,12,12,12,200,12,1)]),0)
