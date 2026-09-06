"""Bounded dashboard reads; never load report documents or chart assets."""
from sqlalchemy import select,func,or_
from src.models.tables import SignalReport,SignalReportDelivery,SignalScanRun,SignalScanPlan,SignalDataReady,SignalReportRenderJob
from src.services.signal_report_assets import json_value
from src.services.signal_schema_service import missing_signal_tables


def dashboard_signals(db):
    if missing_signal_tables(db):
        return dict(available=False,reports=[],enabled_plans=0,waiting_data=0,queued=0,
            running=0,failed=0,report_errors=0,delivery_errors=0)
    rows=list(db.execute(select(SignalReport.id,SignalReport.summary,SignalReport.expires_at,
        SignalReport.retention_reason,SignalReport.status,SignalReport.published_at).where(
        SignalReport.expired_at.is_(None),SignalReport.published_at.is_not(None)).order_by(SignalReport.published_at.desc()).limit(5)))
    ids=[r.id for r in rows]
    deliveries={}
    if ids:
        for identity,status,count in db.execute(select(SignalReportDelivery.report_id,SignalReportDelivery.status,func.count()).where(
            SignalReportDelivery.report_id.in_(ids)).group_by(SignalReportDelivery.report_id,SignalReportDelivery.status)):
            deliveries.setdefault(identity,{})[status]=count
    enabled=db.scalar(select(func.count()).select_from(SignalScanPlan).where(SignalScanPlan.enabled.is_(True))) or 0
    states=dict(db.execute(select(SignalScanRun.status,func.count()).group_by(SignalScanRun.status)).all())
    failed_delivery=db.scalar(select(func.count()).select_from(SignalReportDelivery).where(SignalReportDelivery.status.in_(["failed","unknown"]))) or 0
    failed_render=select(SignalReportRenderJob.report_id).where(SignalReportRenderJob.status=="failed")
    report_failures=db.scalar(select(func.count()).select_from(SignalReport).where(SignalReport.expired_at.is_(None),or_(SignalReport.status=="failed",SignalReport.render_errors!={},SignalReport.id.in_(failed_render)))) or 0
    ready_markets=set(db.scalars(select(SignalDataReady.market).where(SignalDataReady.valid.is_(True)).distinct()))
    waiting=db.scalar(select(func.count()).select_from(SignalScanPlan).where(SignalScanPlan.enabled.is_(True),SignalScanPlan.market.not_in(ready_markets))) or 0
    return dict(available=True,reports=[dict(id=str(r.id),summary=r.summary,status=r.status,published_at=json_value(r.published_at),
        expires_at=json_value(r.expires_at),retention_reason=r.retention_reason,deliveries=deliveries.get(r.id,{})) for r in rows],
        enabled_plans=enabled,waiting_data=waiting,queued=states.get("queued",0),running=states.get("running",0),
        failed=states.get("failed",0)+states.get("partial",0),report_errors=report_failures,delivery_errors=failed_delivery)
