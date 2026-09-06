"""Idempotent retention for scheduled reports; manual reports protect shared scans."""
from datetime import datetime,timezone
from sqlalchemy import select,delete,text
from src.models.tables import SignalReport,SignalObservation,SignalScanRun,SignalReportDelivery,SignalReportRenderJob
from src.services.signal_report_service import expiry,utc
from src.services.signal_report_assets import asset_path


def report_lock(db,report_id,shared=False):
    if db.bind.dialect.name=="postgresql":
        key=int.from_bytes(report_id.bytes[:8],"big",signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock"+("_shared" if shared else "")+"(:key)"),{"key":key})


def cleanup(db,*,apply=False,now=None):
    now=now or datetime.now(timezone.utc);targets=[]
    reports=list(db.scalars(select(SignalReport).where(SignalReport.automatic.is_(True),SignalReport.published_at.is_not(None),SignalReport.status!="expired")))
    for report in reports:
        if report.status in ("running","queued"):continue
        scan=db.get(SignalScanRun,report.scan_id)
        due,reason=expiry(db,scan.market,report.published_at,report.retention_sessions)
        if report.expired_at is None and apply:
            report.expires_at=due;report.retention_reason=reason
        if report.expired_at is None and (due is None or utc(due)>now):continue
        if assets_in_use(db,report.id):continue
        report_lock(db,report.id)
        db.refresh(report)
        if assets_in_use(db,report.id):continue
        targets.append(dict(report_id=str(report.id),scan_id=str(scan.id),expires_at=str(due),exports=len(report.assets or {})))
        if not apply:continue
        # Publish tombstone first. The next pass can resume interrupted filesystem cleanup.
        report.expired_at=report.expired_at or now;report.status="expiring";report.document=None
        db.commit()
        report_lock(db,scan.id)
        for fmt,key in (report.assets or {}).items():
            other=db.scalar(select(SignalReport.id).where(SignalReport.id!=report.id,SignalReport.expired_at.is_(None),SignalReport.assets[fmt].as_string()==key).limit(1))
            if other is None:asset_path(key,fmt).unlink(missing_ok=True)
        report.assets={}
        for delivery in db.scalars(select(SignalReportDelivery).where(SignalReportDelivery.report_id==report.id,SignalReportDelivery.status=="queued")):
            delivery.status="expired"
        for job in db.scalars(select(SignalReportRenderJob).where(SignalReportRenderJob.report_id==report.id,SignalReportRenderJob.status=="queued")):
            job.status="expired"
        live=db.scalar(select(SignalReport.id).where(SignalReport.scan_id==scan.id,SignalReport.expired_at.is_(None)).limit(1))
        if live is None and scan.status in ("completed","partial","failed"):
            for key in (scan.assets or {}).values():
                shared=any(key in (other.assets or {}).values() for other in db.scalars(select(SignalScanRun).where(SignalScanRun.id!=scan.id,SignalScanRun.purged_at.is_(None))))
                if not shared:asset_path(key).unlink(missing_ok=True)
            db.execute(delete(SignalObservation).where(SignalObservation.scan_id==scan.id))
            scan.assets={};scan.purged_at=now
        report.status="expired"
    return targets


def assets_in_use(db,report_id):
    return any(db.scalar(select(model.id).where(model.report_id==report_id,model.status.in_(states)).limit(1))
        for model,states in ((SignalReportDelivery,("running","sending")),(SignalReportRenderJob,("running",))))
