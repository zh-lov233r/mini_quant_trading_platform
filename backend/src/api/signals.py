"""Thin Signal Center HTTP boundary. No work executes in a GET request."""
from datetime import datetime,timezone
from typing import Literal
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,HTTPException,Header,Query,Response
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from src.core.db import get_db
from src.models.tables import SignalScanPlan,SignalScanRun,SignalScanStrategyRun,SignalObservation,SignalReport,SignalReportDelivery,SignalReportRenderJob
from src.schemas.signals import (ScanCreate,PlanCreate,PlanToggle,ReportCreate,DeliveryCreate,
    ScanOut,ScanPage,PlanOut,PlanPage,ReportOut,ReportPage,ObservationOut,ObservationPage,SignalChartOut,ScanDetailOut,DeliveryOut,QueuedOut)
from src.services.signal_scan_service import enqueue_scan,create_plan,strategy_snapshots,members
from src.services.signal_report_service import enqueue_report,enqueue_render,observation_dict,chart_snapshot,scan_chart_snapshot
from src.services.signal_report_assets import json_value,read_bytes
from src.services.signal_report_delivery import enqueue_deliveries,validate_smtp
from src.services.signal_report_retention import report_lock
from src.services.signal_schema_service import missing_signal_tables

def require_signal_schema(db:Session=Depends(get_db)):
    missing=missing_signal_tables(db)
    if missing:
        raise HTTPException(503,{"code":"signal_schema_missing","missing_tables":missing})


router=APIRouter(prefix="/api",tags=["signals"],dependencies=[Depends(require_signal_schema)],
    responses={503:{"description":"Signal Center schema has not been installed"}})


def out(row):return {c.name:json_value(getattr(row,c.name)) for c in row.__table__.columns}

def get(db,model,identity):
    row=db.get(model,identity)
    if row is None:raise HTTPException(404,"not_found")
    return row

def commit(db,fn):
    try:
        result=fn();db.commit();return result
    except ValueError as exc:
        db.rollback();raise HTTPException(422,str(exc)) from exc

def report_available(db,identity):
    report=get(db,SignalReport,identity);report_lock(db,identity,True)
    db.refresh(report)
    if report.expired_at:raise HTTPException(410,"report_expired")
    return report

def page(db,model,stmt,limit,offset):
    total=db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    return dict(items=[out(r) for r in db.scalars(stmt.limit(limit).offset(offset))],total=total)


@router.get("/signal-scan-plans",response_model=PlanPage)
def plans(db:Session=Depends(get_db),limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
    return page(db,SignalScanPlan,select(SignalScanPlan).order_by(SignalScanPlan.created_at.desc()),limit,offset)

@router.post("/signal-scan-plans",status_code=201,response_model=PlanOut)
def add_plan(payload:PlanCreate,db:Session=Depends(get_db)):
    return commit(db,lambda:out(create_plan(db,payload)))

@router.patch("/signal-scan-plans/{identity}",response_model=PlanOut)
def update_plan(identity:UUID,payload:PlanCreate,db:Session=Depends(get_db)):
    row=get(db,SignalScanPlan,identity)
    def save():
        members(db,payload.basket_id,payload.market)
        if payload.enabled:validate_smtp(payload.recipients)
        for key in ("name","market","basket_id","language","recipients","retention_sessions","enabled"):
            setattr(row,key,getattr(payload,key))
        row.strategies=strategy_snapshots(db,payload.strategy_ids);row.updated_at=datetime.now(timezone.utc)
        return out(row)
    return commit(db,save)

@router.post("/signal-scan-plans/{identity}/enabled",response_model=PlanOut)
def toggle_plan(identity:UUID,payload:PlanToggle,db:Session=Depends(get_db)):
    row=get(db,SignalScanPlan,identity)
    def save():
        if payload.enabled:validate_smtp(row.recipients)
        row.enabled=payload.enabled;row.updated_at=datetime.now(timezone.utc);return out(row)
    return commit(db,save)

@router.post("/signal-scans",status_code=202,response_model=ScanOut)
def scan(payload:ScanCreate,db:Session=Depends(get_db),idempotency_key:str|None=Header(None,max_length=128)):
    return commit(db,lambda:out(enqueue_scan(db,payload,idempotency_key or str(uuid4()))))

@router.get("/signal-scans",response_model=ScanPage)
def scans(db:Session=Depends(get_db),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    data=page(db,SignalScanRun,select(SignalScanRun).order_by(SignalScanRun.created_at.desc()),limit,offset)
    for item in data["items"]:item.pop("manifest");item.pop("assets")
    return data

@router.get("/signal-scans/{identity}",response_model=ScanDetailOut)
def scan_detail(identity:UUID,db:Session=Depends(get_db)):
    row=get(db,SignalScanRun,identity)
    return {**out(row),"strategies":[out(c) for c in db.scalars(select(SignalScanStrategyRun).where(SignalScanStrategyRun.scan_id==identity))]}


def observations(db,scan_id,limit,offset,strategy_run_id,instrument_id,passes,q,group="strategy"):
    if isinstance(passes,str):passes=None if passes=="all" else passes=="true"
    stmt=select(SignalObservation).where(SignalObservation.scan_id==scan_id)
    if strategy_run_id:stmt=stmt.where(SignalObservation.strategy_run_id==strategy_run_id)
    if instrument_id:stmt=stmt.where(SignalObservation.instrument_id==instrument_id)
    if passes is not None:stmt=stmt.where(SignalObservation.passes_signal_filters==passes)
    if q:stmt=stmt.where(SignalObservation.symbol_as_of.ilike(f"%{q}%"))
    if group=="instrument":stmt=stmt.order_by(SignalObservation.symbol_as_of)
    stmt=stmt.order_by(SignalObservation.strategy_run_id,SignalObservation.strategy_rank.asc().nulls_last(),SignalObservation.instrument_id,SignalObservation.id)
    result=page(db,SignalObservation,stmt,limit,offset)
    children={str(c.id):c for c in db.scalars(select(SignalScanStrategyRun).where(SignalScanStrategyRun.scan_id==scan_id))}
    for item in result["items"]:
        child=children[item["strategy_run_id"]]
        item.update(strategy_name=child.runtime.get("name"),strategy_type=child.strategy_type,strategy_version=child.version)
        evidence=item.pop("evidence");item["reason"]=evidence.get("reason")
    return result

@router.get("/signal-scans/{identity}/observations",response_model=ObservationPage)
def scan_observations(identity:UUID,db:Session=Depends(get_db),limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),strategy_run_id:UUID|None=None,instrument_id:int|None=None,passes:Literal["true","false","all"]="true",q:str=Query("",max_length=100),group:Literal["strategy","instrument"]="strategy"):
    row=get(db,SignalScanRun,identity)
    if row.purged_at:raise HTTPException(410,"scan_content_expired")
    return observations(db,identity,limit,offset,strategy_run_id,instrument_id,passes,q,group)

@router.post("/signal-scans/{identity}/reports",status_code=202,response_model=ReportOut)
def make_report(identity:UUID,payload:ReportCreate,db:Session=Depends(get_db),idempotency_key:str|None=Header(None,max_length=128)):
    scan=get(db,SignalScanRun,identity);report_lock(db,scan.id);db.refresh(scan)
    return commit(db,lambda:out(enqueue_report(db,scan,payload.language,idempotency_key or str(uuid4()))))


@router.get("/signal-scans/{identity}/instruments/{instrument_id}/chart",response_model=SignalChartOut,
    responses={409:{"description":"Scan is not finished"},410:{"description":"Scan content expired"}})
def scan_chart(identity:UUID,instrument_id:int,strategy_run_id:UUID,signal_id:UUID,limit:int=Query(120,ge=1,le=1000),before:str|None=None,db:Session=Depends(get_db)):
    report_lock(db,identity,True)
    row=get(db,SignalScanRun,identity)
    if row.purged_at:raise HTTPException(410,"scan_content_expired")
    if row.status not in ("completed","partial","failed"):raise HTTPException(409,"scan_not_finished")
    try:return scan_chart_snapshot(db,row,instrument_id,strategy_run_id,signal_id,limit,before)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc

@router.get("/signal-reports",response_model=ReportPage)
def reports(db:Session=Depends(get_db),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    result=page(db,SignalReport,select(SignalReport).order_by(SignalReport.created_at.desc()),limit,offset)
    for item in result["items"]:item.pop("document");item.pop("assets")
    return result

@router.get("/signal-reports/{identity}",response_model=ReportOut)
def report(identity:UUID,db:Session=Depends(get_db)):
    row=report_available(db,identity);result=out(row);doc=result.pop("document")
    result["formats"]=list(result.pop("assets"))
    result["document"]={k:v for k,v in (doc or {}).items() if k not in ("observations","chart_specs","source")}
    result["deliveries"]=[out(d) for d in db.scalars(select(SignalReportDelivery).where(SignalReportDelivery.report_id==identity))]
    job=db.scalar(select(SignalReportRenderJob).where(SignalReportRenderJob.report_id==identity))
    result["render_status"]=job.status if job else None
    result["render_error"]=job.error if job else None
    return result

@router.get("/signal-reports/{identity}/observations",response_model=ObservationPage)
def report_observations(identity:UUID,db:Session=Depends(get_db),limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),strategy_run_id:UUID|None=None,instrument_id:int|None=None,passes:Literal["true","false","all"]="true",q:str=Query("",max_length=100),group:Literal["strategy","instrument"]="strategy"):
    row=report_available(db,identity)
    return observations(db,row.scan_id,limit,offset,strategy_run_id,instrument_id,passes,q,group)

@router.get("/signal-reports/{identity}/observations/{signal_id}",response_model=ObservationOut)
def observation(identity:UUID,signal_id:UUID,db:Session=Depends(get_db)):
    report=report_available(db,identity);row=get(db,SignalObservation,signal_id)
    if row.scan_id!=report.scan_id:raise HTTPException(404,"signal_not_in_report")
    return observation_dict(row,db.get(SignalScanStrategyRun,row.strategy_run_id))

@router.get("/signal-reports/{identity}/instruments/{instrument_id}/chart",response_model=SignalChartOut)
def chart(identity:UUID,instrument_id:int,strategy_run_id:UUID,signal_id:UUID,limit:int=Query(120,ge=1,le=1000),before:str|None=None,db:Session=Depends(get_db)):
    row=report_available(db,identity)
    try:return chart_snapshot(db,row,instrument_id,strategy_run_id,signal_id,limit,before)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc

@router.get("/signal-reports/{identity}/export",response_class=Response,responses={200:{"content":{mime:{"schema":{"type":"string","format":"binary"}} for mime in ("application/json","text/csv","text/html","application/pdf")}},409:{"description":"Requested format is not ready"},410:{"description":"Report expired"}})
def export(identity:UUID,format:Literal["json","csv","html","pdf"],db:Session=Depends(get_db)):
    row=report_available(db,identity)
    if format not in row.assets:raise HTTPException(409,{"code":"format_not_ready","error":row.render_errors.get(format)})
    content=read_bytes(row.assets[format],format)
    mime={"json":"application/json","html":"text/html","pdf":"application/pdf","csv":"text/csv"}[format]
    return Response(content,media_type=mime,headers={"Content-Disposition":f'attachment; filename="signal-{identity}.{format}"',"ETag":f'"{row.assets[format]}"'})

@router.post("/signal-reports/{identity}/render",status_code=202,response_model=ReportOut)
def rerender(identity:UUID,db:Session=Depends(get_db)):
    row=report_available(db,identity)
    if row.status in ("running","queued"):return out(row)
    if row.published_at is None:
        row.status="queued";row.attempts=0
    else:
        enqueue_render(db,row)
    db.commit();return out(row)

@router.post("/signal-reports/{identity}/deliveries",status_code=202,response_model=QueuedOut)
def deliver(identity:UUID,payload:DeliveryCreate,db:Session=Depends(get_db)):
    row=report_available(db,identity)
    return commit(db,lambda:(enqueue_deliveries(db,row,payload.recipients) or {"status":"queued"}))

@router.post("/signal-reports/{identity}/deliveries/{delivery_id}/retry",status_code=202,response_model=DeliveryOut)
def retry_delivery(identity:UUID,delivery_id:UUID,db:Session=Depends(get_db)):
    report_available(db,identity);row=get(db,SignalReportDelivery,delivery_id)
    if row.report_id!=identity:raise HTTPException(404,"delivery_not_in_report")
    if row.status!="failed":raise HTTPException(409,"only_confirmed_failed_deliveries_can_retry")
    row.status="queued";row.attempts=0;db.commit();return out(row)
