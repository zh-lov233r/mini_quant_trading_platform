"""Run only against an explicitly supplied isolated PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,skipUnless
from unittest.mock import patch
from uuid import uuid4
import os
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import sessionmaker
from src.models.tables import Base,Strategy,StockBasket,Instrument,MarketDataMaintenanceState,SignalScanRun,SignalObservation,SignalReport
from src.schemas.signals import ScanCreate
from src.services.signal_scan_service import enqueue_scan,execute_scan
from src.services.signal_scan_job_service import claim
from src.services.signal_report_service import enqueue_report,publish_report,chart_snapshot,scan_chart_snapshot,enqueue_render,render_report
from src.services.signal_report_assets import read_json

@skipUnless(os.getenv('SIGNAL_TEST_DATABASE_URL'),'requires an isolated SIGNAL_TEST_DATABASE_URL')
class SignalPostgresTests(TestCase):
    def setUp(self):
        self.engine=create_engine(os.environ['SIGNAL_TEST_DATABASE_URL']);self.factory=sessionmaker(self.engine,expire_on_commit=False)
        self.temp=TemporaryDirectory();self.env=patch.dict(os.environ,{'SIGNAL_REPORT_STORAGE_DIR':self.temp.name});self.env.start()
        with self.factory() as db:
            db.merge(MarketDataMaintenanceState(id=1,status='ready'))
            symbol='S'+uuid4().hex[:9].upper()
            instrument=Instrument(share_class_figi=str(uuid4()),ticker_canonical=symbol,exchange='XNAS',asset_type='CS',currency='USD');db.add(instrument);db.flush()
            self.instrument=instrument.id
            basket=StockBasket(name=str(uuid4()),symbols=[symbol]);db.add(basket);db.flush();self.basket=basket.id
            from src.services.strategy_registry import normalize_strategy_params
            self.strategies=[]
            for threshold in (0,1):
                s=Strategy(name='Trend '+str(threshold),strategy_key=str(uuid4()),strategy_type='trend',params=normalize_strategy_params('trend',{'signal':{'min_strength_score':threshold,'fast_indicator':{'kind':'ema','window':15},'slow_indicator':{'kind':'sma','window':20}}}))
                db.add(s);db.flush();self.strategies.append(s.id)
            for index in range(32):
                day=date(2026,7,1)+timedelta(days=index)
                db.execute(text('INSERT INTO eod_bars(instrument_id,ts_utc,open_u,high_u,low_u,close_u,open_fa,high_fa,low_fa,close_fa,volume) VALUES(:id,:ts,10,12,9,11,10,12,9,11,200)'),{'id':instrument.id,'day':day,'ts':datetime(day.year,day.month,day.day,21,tzinfo=timezone.utc)})
                db.execute(text('INSERT INTO daily_features(instrument_id,dt_ny,ema_15,sma_20,atr_14,adv_20) VALUES(:id,:day,:fast,10,1,100)'),{'id':instrument.id,'day':day,'fast':11 if index==31 else 9})
            from src.models.tables import SignalDataReady
            db.merge(SignalDataReady(market="US",session_date=day,version="test-initial",coverage={},valid=False))
            db.commit();self.session=day
    def tearDown(self):
        self.env.stop();self.temp.cleanup();self.engine.dispose()
    def test_scan_snapshot_report_and_concurrent_claim(self):
        with self.factory() as db:
            scan=enqueue_scan(db,ScanCreate(basket_id=self.basket,strategy_ids=self.strategies,market='US',session_date=self.session,name='隔离测试报告'),str(uuid4()));db.commit();identity=scan.id
        def acquire(owner):
            with self.factory() as db:
                row=claim(db,SignalScanRun,owner);return None if row is None else row.id
        with ThreadPoolExecutor(2) as pool:claims=list(pool.map(acquire,['one','two']))
        self.assertEqual(claims.count(identity),1)
        with self.factory() as db,self.factory() as reader:
            scan=db.get(SignalScanRun,identity);execute_scan(db,scan,reader);db.commit()
            observations=list(db.scalars(select(SignalObservation).where(SignalObservation.scan_id==identity)))
            self.assertEqual(len(observations),2,scan.coverage);self.assertEqual(len({o.strategy_run_id for o in observations}),2)
            self.assertEqual(len(scan.assets),1)
            self.assertFalse(list(db.scalars(select(SignalReport).where(SignalReport.scan_id==identity))))
            first=observations[0]
            preview=scan_chart_snapshot(db,scan,self.instrument,first.strategy_run_id,first.id,limit=10)
            self.assertIsNone(preview['report_id']);self.assertEqual(preview['scan_id'],str(identity))
            self.assertEqual(len(preview['bars']),10)
            earlier=scan_chart_snapshot(db,scan,self.instrument,first.strategy_run_id,first.id,limit=10,before=preview['next_cursor'])
            self.assertLess(earlier['bars'][-1]['trade_date'],preview['bars'][0]['trade_date'])
            self.assertFalse(list(db.scalars(select(SignalReport).where(SignalReport.scan_id==identity))))
            report=enqueue_report(db,scan,'zh-CN',str(uuid4()));publish_report(db,report);db.commit()
            with self.factory() as observer:
                published=observer.get(SignalReport,report.id)
                self.assertEqual(published.status,'completed');self.assertIsNotNone(published.document)
                self.assertEqual(published.assets,{})
            job=enqueue_render(db,report);job.attempts=1;render_report(db,job);db.commit()
            self.assertEqual(report.render_errors,{})
            first=observations[0]
            old=chart_snapshot(db,report,self.instrument,first.strategy_run_id,first.id)
            db.execute(text('UPDATE eod_bars SET close_fa=777 WHERE instrument_id=:id'),{'id':self.instrument});db.commit()
            again=chart_snapshot(db,report,self.instrument,first.strategy_run_id,first.id)
            self.assertEqual(old,again);self.assertEqual(again['bars'][-1]['close'],11)
            self.assertEqual(preview,scan_chart_snapshot(db,scan,self.instrument,first.strategy_run_id,first.id,limit=10))
            with self.assertRaises(ValueError):chart_snapshot(db,report,self.instrument+100,first.strategy_run_id,first.id)

    def test_readiness_commits_and_scheduled_report_reconciliation(self):
        from src.models.tables import SignalDataReady,SignalScanPlan,SignalReportDelivery
        from src.services.signal_scan_service import strategy_snapshots
        from src.services.signal_scan_job_service import schedule_ready,reconcile
        from src.services.signal_report_retention import cleanup
        from src.services.signal_report_assets import asset_path
        from src.models.tables import SignalMarketSession
        with self.factory() as db:
            plan=SignalScanPlan(name='scheduled isolated',market='US',basket_id=self.basket,strategies=strategy_snapshots(db,self.strategies),enabled=True,recipients=['isolated@example.com']);db.add(plan);db.commit();plan_id=plan.id
        # Uncommitted completion is invisible to a different scheduler connection.
        with self.factory() as writer,self.factory() as scheduler,patch.dict(os.environ,{'SIGNAL_SCAN_SCHEDULER_ENABLED':'true'}):
            writer.merge(SignalDataReady(market='US',session_date=self.session,version='test',coverage={'scope':'market'},valid=True));writer.flush()
            self.assertEqual(schedule_ready(scheduler),0);scheduler.commit();writer.commit()
            self.assertEqual(schedule_ready(scheduler),1);scheduler.commit()
            self.assertEqual(schedule_ready(scheduler),0);scheduler.commit()
        with self.factory() as db,self.factory() as reader:
            scan=db.scalar(select(SignalScanRun).where(SignalScanRun.plan_id==plan_id));execute_scan(db,scan,reader);db.commit()
            reconcile(db);db.commit();report=db.scalar(select(SignalReport).where(SignalReport.scan_id==scan.id));self.assertTrue(report.automatic)
            publish_report(db,report);db.commit()
            with patch.dict(os.environ,{'SIGNAL_REPORT_DELIVERY_ENABLED':'true','SIGNAL_SMTP_HOST':'mock','SIGNAL_SMTP_FROM':'isolated@example.com'}):
                reconcile(db);db.commit();reconcile(db);db.commit()
            self.assertEqual(len(list(db.scalars(select(SignalReportDelivery).where(SignalReportDelivery.report_id==report.id)))),1)
            self.assertEqual(len(list(db.scalars(select(SignalReport).where(SignalReport.scan_id==scan.id)))),1)
            key=next(iter(scan.assets.values()));self.assertTrue(asset_path(key).exists())
            report.published_at=datetime(2026,1,5,22,tzinfo=timezone.utc)
            for n in range(4):
                d=date(2026,1,5)+timedelta(days=n)
                db.merge(SignalMarketSession(market='US',session_date=d,is_open=True,source='fixture',opens_at=datetime(d.year,d.month,d.day,14,30,tzinfo=timezone.utc),closes_at=datetime(d.year,d.month,d.day,21,tzinfo=timezone.utc)))
            db.commit();cleanup(db,apply=True,now=datetime(2026,1,9,tzinfo=timezone.utc));db.commit()
            self.assertEqual(report.status,'expired');self.assertIsNone(report.document);self.assertFalse(asset_path(key).exists())
            self.assertEqual(list(db.scalars(select(SignalObservation).where(SignalObservation.scan_id==scan.id))),[])
            self.assertEqual(db.scalar(select(SignalReportDelivery).where(SignalReportDelivery.report_id==report.id)).status,'expired')
            self.assertEqual(cleanup(db,apply=True,now=datetime(2026,1,9,tzinfo=timezone.utc)),[])
            db.get(SignalScanPlan,plan_id).enabled=False;db.commit()
