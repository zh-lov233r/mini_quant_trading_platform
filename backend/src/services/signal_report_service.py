"""Immutable reports and bounded, snapshot-only chart queries."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models.tables import SignalScanRun, SignalScanStrategyRun, SignalObservation, SignalReport, SignalMarketSession, SignalReportRenderJob
from src.services.signal_report_assets import json_value, put_bytes, read_json
from src.services.signal_scan_service import TERMINAL

UTC=timezone.utc

def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def observation_dict(row, child=None):
    out={c.name:json_value(getattr(row,c.name)) for c in row.__table__.columns}
    if child:
        out.update(strategy_id=str(child.strategy_id),strategy_type=child.strategy_type,
                   strategy_version=child.version,params_hash=child.params_hash,
                   algorithm_revision=child.algorithm_revision,strategy_name=child.runtime.get("name"))
    return out


def enqueue_report(db,scan,language,request_key,automatic=False):
    existing=db.scalar(select(SignalReport).where(SignalReport.request_key==request_key))
    if existing:return existing
    if scan.status not in TERMINAL or scan.purged_at:
        raise ValueError("scan is not terminal or its content has expired")
    report=SignalReport(id=uuid4(),scan_id=scan.id,request_key=request_key,language=language,
        automatic=automatic,retention_sessions=scan.manifest["plan"]["retention_sessions"] if automatic else None)
    db.add(report);db.flush()
    return report


def expiry(db:Session,market:str,published:datetime,retention:int):
    from zoneinfo import ZoneInfo
    local=utc(published).astimezone(ZoneInfo("Asia/Shanghai" if market=="CN" else "America/New_York"))
    days={r.session_date:r for r in db.scalars(select(SignalMarketSession).where(
        SignalMarketSession.market==market,SignalMarketSession.session_date>=local.date()).order_by(SignalMarketSession.session_date))}
    day=local.date();count=0
    for _ in range(740):
        row=days.get(day)
        if row is None:return None,"calendar_incomplete"
        if row.is_open:
            if row.opens_at is None or row.closes_at is None:return None,"calendar_incomplete"
            if utc(row.opens_at)>utc(published):
                count+=1
                if count==retention:return utc(row.closes_at),None
        day+=timedelta(days=1)
    return None,"calendar_incomplete"


def compare_previous(db, scan, observations):
    candidate = db.scalar(select(SignalReport).join(SignalScanRun,SignalReport.scan_id==SignalScanRun.id).where(
        SignalScanRun.market==scan.market, SignalScanRun.session_date<scan.session_date,
        SignalScanRun.plan_id==scan.plan_id if scan.plan_id else SignalScanRun.manifest["basket_id"].as_string()==scan.manifest.get("basket_id"),
        SignalReport.published_at.is_not(None),SignalReport.expired_at.is_(None)
    ).order_by(SignalScanRun.session_date.desc(),SignalReport.published_at.desc()).limit(1))
    if candidate is None or not candidate.document:
        return dict(status="not_comparable",reason="no_previous_comparable_report")
    previous=db.get(SignalScanRun,candidate.scan_id)
    def signature(row):
        children=list(db.scalars(select(SignalScanStrategyRun).where(SignalScanStrategyRun.scan_id==row.id)))
        return (sorted(m["instrument_id"] for m in row.manifest["members"]),
            sorted((str(c.strategy_id),c.version,c.params_hash,c.algorithm_revision) for c in children))
    if previous.status!="completed" or scan.status!="completed" or signature(previous)!=signature(scan):
        return dict(status="not_comparable",reason="coverage_membership_or_strategy_changed",previous_report_id=str(candidate.id))
    def keys(items):
        return {(o["instrument_id"],o["strategy_id"],o["event_type"]) for o in items if o["passes_signal_filters"]}
    current=keys(observations);before=keys(candidate.document["observations"])
    return dict(status="compared",previous_report_id=str(candidate.id),previous_session=previous.session_date.isoformat(),
        added=[list(x) for x in sorted(current-before)],removed=[list(x) for x in sorted(before-current)],continuing=len(current&before))


def build_document(db,report):
    scan=db.get(SignalScanRun,report.scan_id)
    children=list(db.scalars(select(SignalScanStrategyRun).where(SignalScanStrategyRun.scan_id==scan.id).order_by(SignalScanStrategyRun.id)))
    child_map={c.id:c for c in children}
    observations=[observation_dict(o,child_map[o.strategy_run_id]) for o in db.scalars(
        select(SignalObservation).where(SignalObservation.scan_id==scan.id).order_by(
        SignalObservation.strategy_run_id,SignalObservation.strategy_rank.asc().nulls_last(),SignalObservation.instrument_id,SignalObservation.id))]
    passing=[o for o in observations if o["passes_signal_filters"]]
    summary=dict(observation_count=len(passing),audit_observation_count=len(observations),
        instrument_count=len({o["instrument_id"] for o in passing}),strategy_count=len(children),
        completed_strategies=sum(c.status=="completed" for c in children),status=scan.status)
    from src.services.signal_report_rendering import publication_url
    published=datetime.now(UTC)
    if report.retention_sessions:
        report.expires_at,report.retention_reason=expiry(db,scan.market,published,report.retention_sessions)
    return dict(schema_version=1,template_version=1,report_id=str(report.id),scan_id=str(scan.id),
        name=scan.name,language=report.language,market=scan.market,report_url=publication_url(report.id),session_date=scan.session_date.isoformat(),
        published_at=published.isoformat(),source=scan.manifest,summary=summary,coverage=scan.coverage,
        strategy_sections=[dict(id=str(c.id),name=c.runtime.get("name"),strategy_type=c.strategy_type,
            version=c.version,status=c.status,error=c.error,coverage=c.coverage) for c in children],
        observations=observations,chart_specs=scan.assets,
        changes=compare_previous(db,scan,observations),
        limitations=["Report/chart slices are reproducible; complete scan inputs are not retained.",
                     "Missing evidence is not reconstructed. Historical basket membership is not inferred."],
        expires_at=json_value(report.expires_at),retention_sessions=report.retention_sessions)


def publish_report(db,report):
    if report.document is None:
        report.document=build_document(db,report)
        report.summary={**report.document["summary"],"name":report.document["name"],
            "market":report.document["market"],"session_date":report.document["session_date"]}
        report.published_at=datetime.fromisoformat(report.document["published_at"])
    enqueue_render(db,report)
    report.status="completed";report.finished_at=datetime.now(UTC);report.lease_expires_at=None


def enqueue_render(db,report):
    job=db.scalar(select(SignalReportRenderJob).where(SignalReportRenderJob.report_id==report.id))
    if job is None:
        job=SignalReportRenderJob(report_id=report.id)
        db.add(job)
    elif job.status in ("failed","completed"):
        job.status="queued";job.attempts=0;job.error=None;job.finished_at=None
    db.flush()
    return job


def render_report(db,job):
    from src.services.signal_report_rendering import render
    from src.services.signal_report_retention import report_lock
    report_lock(db,job.report_id,True)
    report=db.get(SignalReport,job.report_id)
    if report.expired_at:
        job.status="expired";job.lease_expires_at=None
        return
    assets=dict(report.assets or {});errors={}
    for fmt in ("json","csv","html","pdf"):
        if fmt in assets:continue
        try:assets[fmt]=put_bytes(render(report.document,fmt),fmt)
        except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:errors[fmt]=str(exc)[:1000]
    report.assets=assets;report.render_errors=errors
    job.status="queued" if errors and job.attempts<3 else "failed" if errors else "completed"
    job.error="; ".join(f"{fmt}: {error}" for fmt,error in errors.items()) or None
    job.finished_at=datetime.now(UTC) if job.status!="queued" else None
    job.lease_expires_at=None


def chart_snapshot(db,report,instrument_id,strategy_run_id,signal_id,limit=120,before=None):
    if report.expired_at or not report.document:raise ValueError("report_expired_or_not_ready")
    return observation_chart_snapshot(db,report.scan_id,report.document["chart_specs"],report.document["market"],
        report.document["source"]["price_semantics"],instrument_id,strategy_run_id,signal_id,limit,before,report.id)


def scan_chart_snapshot(db,scan,instrument_id,strategy_run_id,signal_id,limit=120,before=None):
    if scan.purged_at or scan.status not in TERMINAL:raise ValueError("scan_content_expired_or_not_ready")
    return observation_chart_snapshot(db,scan.id,scan.assets,scan.market,scan.manifest["price_semantics"],
        instrument_id,strategy_run_id,signal_id,limit,before)


def observation_chart_snapshot(db,scan_id,assets,market,price_semantics,instrument_id,strategy_run_id,signal_id,limit,before,report_id=None):
    observation=db.get(SignalObservation,signal_id)
    if observation is None or observation.scan_id!=scan_id or observation.instrument_id!=instrument_id or observation.strategy_run_id!=strategy_run_id:
        raise ValueError("signal does not belong to this scan, instrument and strategy")
    key=assets.get(str(instrument_id))
    if key!=observation.snapshot_ref:raise ValueError("snapshot identity mismatch")
    stored=read_json(key)["bars"]
    as_of=observation.session_date.isoformat()
    eligible=[r for r in stored if r["dt_ny"]<=as_of]
    if before and (before>as_of or before<eligible[0]["dt_ny"]):raise ValueError("window outside snapshot")
    selected=[r for r in eligible if before is None or r["dt_ny"]<before][-limit:]
    indicators=[]
    child=db.get(SignalScanStrategyRun,strategy_run_id)
    signal=child.runtime["params"]["signal"]
    if child.strategy_type=="trend":
        keys=[f"{signal[k]['kind']}_{signal[k]['window']}" for k in ("fast_indicator","slow_indicator")]
    elif child.strategy_type=="mean_reversion":keys=[f"zscore_{signal['lookback_window']}"]
    elif child.strategy_type=="momentum_breakout":keys=["sma_20","ret_20d","volume_sma_20"]
    else:keys=[]
    for k in keys:
        pane="price" if k.startswith(("sma_","ema_")) else "indicator"
        indicators.append(dict(key=k,unit="price" if pane=="price" else "ratio" if k=="ret_20d" else "volume" if k.startswith("volume") else "zscore",
            pane=pane,thresholds=[-signal["zscore_entry"],signal["zscore_entry"],-signal["zscore_exit"],signal["zscore_exit"]] if k.startswith("zscore_") else [],values=[dict(time=r["dt_ny"],value=r.get(k)) for r in selected]))
    return dict(report_id=str(report_id) if report_id else None,scan_id=str(scan_id),snapshot_id=key,instrument_id=instrument_id,symbol_as_of=observation.symbol_as_of,
        market=market,session_timezone="Asia/Shanghai" if market=="CN" else "America/New_York",
        timeframe="1d",price_semantics=price_semantics,as_of_session=as_of,
        available_from=eligible[0]["dt_ny"],available_to=as_of,bars=[{**r,"trade_date":r["dt_ny"]} for r in selected],
        indicator_series=indicators,selected_signal_id=str(signal_id),observation=observation_dict(observation,child),
        next_cursor=selected[0]["dt_ny"] if selected and selected[0]["dt_ny"]>eligible[0]["dt_ny"] else None)
