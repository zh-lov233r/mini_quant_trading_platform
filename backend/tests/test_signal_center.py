from datetime import date,datetime,time,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch,MagicMock
from uuid import uuid4
import json
import os
import smtplib
import quant_kernel
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from src.models.tables import Base,SignalScanRun,SignalReport,SignalReportDelivery,SignalMarketSession,SignalScanPlan,SignalDataReady,Strategy,StockBasket,Instrument,MarketDataMaintenanceState
from src.services.signal_report_service import expiry,publish_report,enqueue_report,render_report,enqueue_render
from src.services.signal_scan_service import enqueue_scan,evaluate_instrument,coverage_reason
from src.services.signal_scan_job_service import schedule_ready,recover,claim
from src.services.signal_report_retention import cleanup
from src.services.signal_report_assets import put_json,read_json,asset_path
from src.services.signal_report_rendering import render
from src.services.signal_report_delivery import send_delivery
from src.schemas.signals import ScanCreate
from backend.tests import test_native_nine_strategy_golden as golden

UTC=timezone.utc

class SignalCenterTests(TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(self.engine);self.db=Session(self.engine)
        self.temp=TemporaryDirectory();self.env=patch.dict(os.environ,{'SIGNAL_REPORT_STORAGE_DIR':self.temp.name,'SIGNAL_REPORT_DELIVERY_ENABLED':'false'});self.env.start()
    def tearDown(self):
        self.db.close();self.engine.dispose();self.env.stop();self.temp.cleanup()
    def scan(self):
        row=SignalScanRun(id=uuid4(),name='测试 <script>',request_key=str(uuid4()),market='US',session_date=date(2026,8,31),status='completed',
            manifest={'plan':{'retention_sessions':3,'recipients':['test@example.com'],'language':'zh-CN'},'price_semantics':'frozen','members':[],'strategies':[]},coverage={},assets={})
        self.db.add(row);self.db.flush();return row
    def calendar(self):
        from zoneinfo import ZoneInfo
        zone=ZoneInfo('America/New_York')
        for n in range(14):
            day=date(2026,8,31)+timedelta(days=n);opened=day.weekday()<5 and day!=date(2026,9,7)
            self.db.add(SignalMarketSession(market='US',session_date=day,is_open=opened,source='test',
                opens_at=datetime.combine(day,time(9,30),zone).astimezone(UTC) if opened else None,closes_at=datetime.combine(day,time(13 if day==date(2026,9,4) else 16),zone).astimezone(UTC) if opened else None))
        self.db.flush()
    def test_expiry_three_complete_sessions_holiday_and_missing_calendar(self):
        self.calendar()
        due,reason=expiry(self.db,'US',datetime(2026,9,2,21,tzinfo=UTC),3)
        self.assertEqual(due,datetime(2026,9,8,20,tzinfo=UTC));self.assertIsNone(reason)
        due,_=expiry(self.db,'US',datetime(2026,9,1,21,tzinfo=UTC),3)
        self.assertEqual(due,datetime(2026,9,4,17,tzinfo=UTC))
        self.assertEqual(expiry(self.db,'CN',datetime.now(UTC),3),(None,'calendar_incomplete'))
    def test_asset_immutable_hash_and_path_validation(self):
        key=put_json({'close':12,'volume':None});self.assertEqual(read_json(key)['volume'],None)
        self.assertEqual(key,put_json({'volume':None,'close':12}))
        with self.assertRaises(ValueError):asset_path('../secret')
        asset_path(key).write_bytes(b'bad')
        with self.assertRaises(ValueError):read_json(key)
    def test_scan_chart_validates_scan_instrument_strategy_and_snapshot_ownership(self):
        from src.services.signal_report_service import scan_chart_snapshot
        scan=self.scan();strategy=uuid4();signal=uuid4()
        observation=SimpleNamespace(scan_id=scan.id,instrument_id=1,strategy_run_id=strategy,snapshot_ref='frozen')
        db=MagicMock();db.get.return_value=observation
        for field,value in [('scan_id',uuid4()),('instrument_id',2),('strategy_run_id',uuid4())]:
            original=getattr(observation,field);setattr(observation,field,value)
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'does not belong'):
                scan_chart_snapshot(db,scan,1,strategy,signal)
            setattr(observation,field,original)
        with self.assertRaisesRegex(ValueError,'snapshot identity mismatch'):
            scan_chart_snapshot(db,scan,1,strategy,signal)

    def test_scan_chart_route_rejects_unfinished_and_purged_scans(self):
        from fastapi import HTTPException
        from src.api.signals import scan_chart
        scan=self.scan();scan.status='running';strategy=uuid4();signal=uuid4()
        with self.assertRaises(HTTPException) as caught:
            scan_chart(scan.id,1,strategy,signal,limit=120,before=None,db=self.db)
        self.assertEqual(caught.exception.status_code,409)
        scan.status='partial';scan.purged_at=datetime.now(UTC)
        with self.assertRaises(HTTPException) as caught:
            scan_chart(scan.id,1,strategy,signal,limit=120,before=None,db=self.db)
        self.assertEqual(caught.exception.status_code,410)
    def test_manual_report_is_explicit_and_rerender_does_not_scan(self):
        scan=self.scan();self.assertEqual(list(self.db.scalars(select(SignalReport))),[])
        report=enqueue_report(self.db,scan,'zh-CN','manual');publish_report(self.db,report);self.db.flush()
        self.assertEqual(report.status,'completed');self.assertEqual(report.assets,{})
        job=enqueue_render(self.db,report);job.attempts=1;render_report(self.db,job)
        self.assertIsNone(report.retention_sessions);self.assertIn('pdf',report.assets)
        self.assertEqual(report.summary['observation_count'],0)
        self.assertEqual(list(self.db.scalars(select(SignalReportDelivery))),[])
        before=json.dumps(report.document,sort_keys=True);publish_report(self.db,report)
        self.assertEqual(before,json.dumps(report.document,sort_keys=True))
        original_html=render(report.document,"html")
        with patch.dict(os.environ,{"SIGNAL_REPORT_PUBLIC_URL":"https://changed.example"}):
            self.assertEqual(render(report.document,"html"),original_html)
        html=render(report.document,'html').decode();self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
    def test_retention_dry_run_and_manual_shared_snapshot_protection(self):
        self.calendar();scan=self.scan();key=put_json({'bars':[]});scan.assets={'1':key}
        auto=enqueue_report(self.db,scan,'en-US','auto',True);publish_report(self.db,auto)
        auto.published_at=datetime(2026,8,31,21,tzinfo=UTC)
        manual=enqueue_report(self.db,scan,'en-US','manual');publish_report(self.db,manual);self.db.commit()
        now=datetime(2026,9,9,tzinfo=UTC)
        self.assertEqual(len(cleanup(self.db,now=now)),1);self.assertIsNone(auto.expired_at)
        cleanup(self.db,apply=True,now=now);self.db.commit()
        self.assertIsNotNone(auto.expired_at);self.assertIsNone(manual.expired_at);self.assertTrue(asset_path(key).exists());self.assertIsNone(scan.purged_at)
        cleanup(self.db,apply=True,now=now);self.db.commit();self.assertTrue(asset_path(key).exists())
    def test_delivery_timeout_after_data_is_unknown_and_not_retried(self):
        report=enqueue_report(self.db,self.scan(),'en-US','report');publish_report(self.db,report)
        row=SignalReportDelivery(report_id=report.id,recipient='test@example.com',status='running',attempts=1)
        self.db.add(row);self.db.flush()
        client=MagicMock();client.mail.return_value=(250,b'ok');client.rcpt.return_value=(250,b'ok');client.data.side_effect=TimeoutError()
        with patch.dict(os.environ,{'SIGNAL_REPORT_DELIVERY_ENABLED':'true','SIGNAL_SMTP_HOST':'test','SIGNAL_SMTP_FROM':'test@example.com'}),patch('smtplib.SMTP',return_value=client):
            send_delivery(self.db,row,report)
        self.assertEqual(row.status,'unknown')
        self.db.commit();self.assertIsNone(claim(self.db,SignalReportDelivery,'other'))
    def test_recover_sending_is_unknown(self):
        report=enqueue_report(self.db,self.scan(),'en-US','report')
        row=SignalReportDelivery(report_id=report.id,recipient='test@example.com',status='sending',attempts=1,lease_expires_at=datetime(2020,1,1,tzinfo=UTC))
        self.db.add(row);self.db.flush();recover(self.db,SignalReportDelivery,datetime.now(UTC));self.assertEqual(row.status,'unknown')
    def test_readiness_latest_only_and_unique_schedule(self):
        plan=SignalScanPlan(name='daily',market='US',basket_id=uuid4(),strategies=[{'strategy_id':str(uuid4())}],enabled=True,recipients=['test@example.com'])
        self.db.add(plan)
        for day,valid in [(1,True),(2,True),(3,False)]:self.db.add(SignalDataReady(market='US',session_date=date(2026,9,day),version=str(day),coverage={},valid=valid))
        self.db.commit()
        def enqueue(db,payload,key,plan):
            row=SignalScanRun(plan_id=plan.id,request_key=key,market='US',name='test',session_date=payload.session_date,manifest={});db.add(row);db.flush();return row
        with patch.dict(os.environ,{'SIGNAL_SCAN_SCHEDULER_ENABLED':'true'}),patch('src.services.signal_scan_job_service.enqueue_scan',side_effect=enqueue) as mocked:
            self.assertEqual(schedule_ready(self.db),1);self.assertEqual(mocked.call_args.args[1].session_date,date(2026,9,2))
            self.assertEqual(schedule_ready(self.db),0)
            plan.enabled=False;self.db.flush();self.assertEqual(schedule_ready(self.db),0)

    def test_maintenance_only_invalidates_its_market(self):
        from src.services.market_data_maintenance_service import begin_market_data_draining,begin_market_data_update
        for market in ('US','CN'):
            self.db.add(SignalDataReady(market=market,session_date=date(2026,9,3),version='v1',coverage={},valid=True))
        self.db.flush();owner=uuid4()
        begin_market_data_draining(self.db,owner)
        begin_market_data_update(self.db,owner,market='CN')
        self.assertEqual(dict(self.db.execute(select(SignalDataReady.market,SignalDataReady.valid)).all()),{'US':True,'CN':False})

    def test_missing_schema_is_explicit_without_hiding_other_dashboard_sections(self):
        from src.services.signal_dashboard_service import dashboard_signals
        from src.api.signals import require_signal_schema
        from fastapi import HTTPException
        from src.models.tables import SignalReportRenderJob
        SignalReportRenderJob.__table__.drop(self.engine)
        self.assertFalse(dashboard_signals(self.db)['available'])
        with self.assertRaises(HTTPException) as caught:require_signal_schema(self.db)
        self.assertEqual(caught.exception.status_code,503)
        self.assertEqual(caught.exception.detail,{'code':'signal_schema_missing','missing_tables':['signal_report_render_jobs']})
        from src.main import readyz
        from fastapi import Response
        response=Response()
        with patch('src.main.SessionLocal',return_value=self.db),patch('src.main.load_backtest_worker_status',return_value={'automation_available':True}):
            result=readyz(response)
        self.assertEqual(response.status_code,503)
        self.assertEqual(result['reason'],'signal_schema_missing')

    def test_render_failure_retries_without_unpublishing_or_changing_document(self):
        report=enqueue_report(self.db,self.scan(),'en-US','publish')
        with patch('src.services.signal_report_rendering.render',side_effect=AssertionError('publication must not render')):
            publish_report(self.db,report);self.db.commit()
        original=json.dumps(report.document,sort_keys=True)
        job=enqueue_render(self.db,report)
        def renderer(doc,fmt):
            if fmt=='pdf':raise OSError('temporary PDF failure')
            return b'fixture export'
        with patch('src.services.signal_report_rendering.render',side_effect=renderer):
            for attempt in (1,2,3):
                job.attempts=attempt;render_report(self.db,job)
                self.assertEqual(report.status,'completed')
                self.assertEqual(job.status,'queued' if attempt<3 else 'failed')
        self.assertEqual(set(report.assets),{'json','csv','html'})
        enqueue_render(self.db,report)
        with patch('src.services.signal_report_rendering.render',return_value=b'pdf fixture') as rendered:
            job.attempts=1;render_report(self.db,job)
        self.assertEqual(rendered.call_args.args[1],'pdf');self.assertEqual(rendered.call_count,1)
        self.assertEqual(report.render_errors,{})
        self.assertEqual(json.dumps(report.document,sort_keys=True),original)

    def test_busy_scans_do_not_starve_publication_delivery_or_rendering(self):
        from sqlalchemy.orm import sessionmaker
        from src.services.signal_scan_job_service import run_once
        report=enqueue_report(self.db,self.scan(),'en-US','queued-report')
        self.db.add(SignalReportDelivery(report_id=report.id,recipient='test@example.com'))
        for _ in range(2):self.scan().status='queued'
        self.db.commit();stages=[]
        def scan(db,row,reader,progress):stages.append('scan');row.status='completed'
        def delivery(db,row,published):
            self.assertEqual(published.status,'completed');self.assertIsNotNone(published.document)
            self.assertEqual(published.assets,{})
            stages.append('email');row.status='completed'
        def rendering(doc,fmt):stages.append(fmt);return b'fixture'
        with patch('src.services.signal_scan_job_service.execute_scan',side_effect=scan),patch('src.services.signal_scan_job_service.send_delivery',side_effect=delivery),patch('src.services.signal_report_rendering.render',side_effect=rendering):
            self.assertTrue(run_once(sessionmaker(self.engine,expire_on_commit=False)))
        self.assertEqual(stages,['scan','email','json','csv','html','pdf'])
        self.db.expire_all()
        self.assertEqual(len(list(self.db.scalars(select(SignalScanRun).where(SignalScanRun.status=='queued')))),1)

    def test_retention_protects_running_render_and_expires_queued_render(self):
        self.calendar();report=enqueue_report(self.db,self.scan(),'en-US','retention',True)
        publish_report(self.db,report);report.published_at=datetime(2026,8,31,21,tzinfo=UTC)
        job=enqueue_render(self.db,report);job.status='running';self.db.commit()
        now=datetime(2026,9,9,tzinfo=UTC)
        self.assertEqual(cleanup(self.db,apply=True,now=now),[])
        job.status='queued';self.db.commit()
        cleanup(self.db,apply=True,now=now);self.db.commit()
        self.assertEqual(job.status,'expired');self.assertIsNone(report.document)


class NativeObservationTests(TestCase):
    def test_nine_strategy_observation_entry_has_no_portfolio_argument(self):
        helper=golden.NativeNineStrategyGoldenTests();helper.setUp();seen=set()
        for kind,dataset,runtime in helper._cases():
            with self.subTest(kind=kind):
                output=quant_kernel.observe_market(dataset,runtime);seen.add(kind)
                self.assertIn('observations',output)
                for event in output['observations']:
                    self.assertNotIn('action',event);self.assertNotIn('position',event['metadata'])
        self.assertEqual(len(seen),9)
    def test_indicator_observation_matches_market_logic_and_missing_fields(self):
        helper=golden.NativeNineStrategyGoldenTests();helper.setUp()
        for kind in ('trend','mean_reversion','momentum_breakout'):
            days=helper._stateless_days(kind);runtime=helper._runtime(kind)
            rows=[{**days[0][1]['S01'],'dt_ny':days[0][0]}]
            output=evaluate_instrument(rows,runtime)
            self.assertEqual(len(output['observations']),1,kind)
            event=output['observations'][0];self.assertEqual(event['direction'],'bullish')
            self.assertEqual(coverage_reason([{**rows[0],'high':float('inf')}],days[0][0],runtime),'invalid_data')
            changed=[{**rows[0],'close':None}]
            self.assertEqual(coverage_reason(changed,days[0][0],runtime),'missing_required_field')

    def test_nine_positive_prefixes_and_instance_isolation(self):
        import numpy as np
        from src.services.prepared_dataset_service import PreparedDataset
        expected={'trend':1,'mean_reversion':1,'momentum_breakout':1,'island_reversal':4,'double_bottom':9,
                  'head_shoulders_bottom':6,'rounded_bottom':88,'v_reversal':61,'support_resistance':26}
        helper=golden.NativeNineStrategyGoldenTests();helper.setUp()
        for kind,dataset,runtime in helper._cases():
            with self.subTest(kind=kind):
                mask=dataset.integers[:,1]==1;ints=dataset.integers[mask];floats=dataset.floats[mask]
                end=expected[kind]
                prefix=PreparedDataset(np.asfortranarray(ints[:end]),np.asfortranarray(floats[:end]),dataset.sidecar)
                before=quant_kernel.observe_market(prefix,runtime)
                self.assertTrue(before['observations'])
                # Different thresholds and a future-bearing input cannot contaminate cold replay state.
                other=json.loads(json.dumps(runtime));other['params']['signal']['min_strength_score']=100
                quant_kernel.observe_market(dataset,other)
                self.assertEqual(before,quant_kernel.observe_market(prefix,runtime))
                for event in before['observations']:
                    self.assertNotIn('action',event)
                    self.assertNotIn('position',event['metadata'])
                if end>1:
                    previous=PreparedDataset(np.asfortranarray(ints[:end-1]),np.asfortranarray(floats[:end-1]),dataset.sidecar)
                    self.assertEqual(quant_kernel.observe_market(previous,runtime)['observations'],[])
