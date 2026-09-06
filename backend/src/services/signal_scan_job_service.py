"""Durable signal jobs. Small independent worker, no portfolio execution imports."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import logging
import os
from threading import Event,Thread
from sqlalchemy import select,update
from src.models.tables import SignalScanRun,SignalReport,SignalReportDelivery,SignalScanPlan,SignalDataReady,SignalReportRenderJob
from src.schemas.signals import ScanCreate
from src.services.signal_scan_service import enqueue_scan,execute_scan
from src.services.market_data_maintenance_service import MarketDataMaintenanceError
from src.services.signal_report_service import enqueue_report,publish_report,render_report,utc
from src.services.signal_report_delivery import enqueue_deliveries,send_delivery

log=logging.getLogger(__name__)
UTC=timezone.utc


def schedule_ready(db):
    if os.getenv("SIGNAL_SCAN_SCHEDULER_ENABLED","false").lower()!="true":return 0
    count=0
    for plan in db.scalars(select(SignalScanPlan).where(SignalScanPlan.enabled.is_(True)).with_for_update(skip_locked=True)):
        ready=db.scalar(select(SignalDataReady).where(SignalDataReady.market==plan.market,SignalDataReady.valid.is_(True)).order_by(SignalDataReady.session_date.desc()).limit(1))
        if ready is None:continue
        if db.scalar(select(SignalScanRun.id).where(SignalScanRun.plan_id==plan.id,SignalScanRun.session_date==ready.session_date)):continue
        try:
            with db.begin_nested():
                enqueue_scan(db,ScanCreate(basket_id=plan.basket_id,strategy_ids=[s["strategy_id"] for s in plan.strategies],
                    market=plan.market,session_date=ready.session_date,name=plan.name),f"schedule:{plan.id}:{ready.session_date}",plan=plan)
                count+=1
        except (ValueError,MarketDataMaintenanceError) as exc:
            log.warning("Signal plan waiting: %s (%s)",plan.id,str(exc))
    return count


def recover(db,model,now):
    for row in db.scalars(select(model).where(model.status.in_(["running","sending"]),model.lease_expires_at<now).with_for_update(skip_locked=True)):
        row.status="unknown" if model is SignalReportDelivery and row.status=="sending" else "queued" if row.attempts<3 else "failed"
        row.error="worker lease expired";row.claimed_by=None;row.lease_expires_at=None
        row.finished_at=now if row.status in ("failed","unknown") else None


def claim(db,model,owner):
    now=datetime.now(UTC);recover(db,model,now)
    row=db.scalar(select(model).where(model.status=="queued").order_by(model.created_at,model.id).with_for_update(skip_locked=True).limit(1))
    if row:
        row.status="running";row.attempts+=1;row.claimed_by=owner;row.lease_expires_at=now+timedelta(seconds=120)
    db.commit();return row


def reconcile(db):
    # Durable reconciliation also closes the crash gap after a scan/report commit.
    scans=db.scalars(select(SignalScanRun).where(SignalScanRun.plan_id.is_not(None),
        SignalScanRun.status.in_(["completed","partial","failed"]),SignalScanRun.purged_at.is_(None)))
    for scan in scans:
        enqueue_report(db,scan,scan.manifest["plan"]["language"],f"automatic:{scan.id}",True)
    for report in db.scalars(select(SignalReport).where(SignalReport.automatic.is_(True),SignalReport.status=="completed",SignalReport.expired_at.is_(None))):
        scan=db.get(SignalScanRun,report.scan_id)
        try:enqueue_deliveries(db,report,scan.manifest["plan"]["recipients"])
        except ValueError as exc:
            for recipient in scan.manifest["plan"]["recipients"]:
                if not db.scalar(select(SignalReportDelivery.id).where(SignalReportDelivery.report_id==report.id,SignalReportDelivery.recipient==recipient)):
                    db.add(SignalReportDelivery(report_id=report.id,recipient=recipient,status="failed",attempts=0,error=str(exc),finished_at=datetime.now(UTC)))
            db.flush()
            log.warning("Signal email unavailable: %s (%s)",report.id,str(exc))


def run_once(factory):
    owner=str(uuid4())
    with factory() as db:
        # One service loop per database; individual claims retain lease protection.
        if db.bind.dialect.name=="postgresql":
            from sqlalchemy import text
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(7314582029)")):return False
        schedule_ready(db);reconcile(db);db.commit()
    worked=False
    # Give every stage one claim per pass, even while scans continue arriving.
    for model in (SignalScanRun,SignalReport,SignalReportDelivery,SignalReportRenderJob):
        with factory() as db:
            row=claim(db,model,owner)
            if row is None:continue
            worked=True
            identity=row.id;stop=Event()
            def beat(model=model,identity=identity,stop=stop):
                while not stop.wait(20):
                    with factory() as hb:
                        hb.execute(update(model).where(model.id==identity,model.claimed_by==owner,
                            model.status.in_(["running","sending"])).values(lease_expires_at=datetime.now(UTC)+timedelta(seconds=120)))
                        hb.commit()
            thread=Thread(target=beat,daemon=True);thread.start()
            try:
                if model is SignalScanRun:
                    progress_time = [0.0]
                    def progress(value):
                        from time import monotonic
                        if monotonic()-progress_time[0]<2:return
                        with factory() as progress_db:
                            progress_db.execute(update(SignalScanRun).where(SignalScanRun.id==identity,SignalScanRun.claimed_by==owner).values(coverage=value))
                            progress_db.commit()
                        progress_time[0]=monotonic()
                    with factory() as reader:execute_scan(db,row,reader,progress)
                elif model is SignalReport:publish_report(db,row)
                elif model is SignalReportRenderJob:render_report(db,row)
                else:
                    from src.services.signal_report_retention import report_lock
                    report_lock(db,row.report_id,True)
                    send_delivery(db,row,db.get(SignalReport,row.report_id))
                # A lost lease cannot publish output over another worker's ownership.
                with db.no_autoflush:
                    current=db.execute(select(model.claimed_by).where(model.id==identity).with_for_update()).scalar_one()
                if current!=owner:raise RuntimeError("signal worker ownership lost")
                db.commit()
                if model is SignalReport:
                    # Publication is durable before SMTP or any export rendering.
                    reconcile(db);db.commit()
            except Exception as exc:
                db.rollback();row=db.get(model,identity)
                if row.claimed_by==owner:
                    row.error=str(exc)[:2000]
                    row.status="unknown" if model is SignalReportDelivery and row.status=="sending" else "queued" if row.attempts<3 else "failed"
                    row.lease_expires_at=None
                    row.finished_at=datetime.now(UTC) if row.status in ("failed","unknown") else None
                    db.commit()
                log.exception("Signal job failed: %s",identity)
            finally:stop.set();thread.join(timeout=1)
    return worked
