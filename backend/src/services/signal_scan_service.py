"""Market-only scans. No imports from backtest execution, paper, or broker services."""
from __future__ import annotations
from collections import Counter
from datetime import date, datetime, timezone
import json
import math
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import quant_kernel
from sqlalchemy import select, text, bindparam
from sqlalchemy.orm import Session

from src.models.tables import (Instrument, StockBasket, Strategy, SignalScanPlan, SignalScanRun,
    SignalScanStrategyRun, SignalObservation, SignalDataReady, SignalMarketSession)
from src.schemas.signals import ScanCreate, PlanCreate
from src.services.feature_snapshot_sql import FEATURE_SNAPSHOT_PROJECTION_SQL
from src.services.prepared_dataset_service import build_in_memory_prepared_dataset
from src.services.strategy_registry import build_runtime_payload, required_feature_keys, build_strategy_catalog, normalize_strategy_params
from src.services.market_data_maintenance_service import assert_market_data_submission_allowed, acquire_market_data_read_lock
from src.services.signal_report_assets import put_json, digest, json_value

TERMINAL = {"completed", "partial", "failed"}
CATALOG = {x["strategy_type"]: x for x in build_strategy_catalog()}
CHART_SESSIONS = 1000


def strategy_snapshots(db: Session, ids: list[UUID]) -> list[dict]:
    result = []
    for identity in ids:
        row = db.get(Strategy, identity)
        if row is None:
            raise ValueError("strategy not found")
        runtime = build_runtime_payload(row)
        # The selected basket is the universe; parameters remain frozen separately.
        result.append(json_value(runtime))
    return result


def members(db: Session, basket_id: UUID, market: str) -> list[dict]:
    basket = db.get(StockBasket, basket_id)
    if basket is None:
        raise ValueError("stock basket not found")
    rows = list(db.scalars(select(Instrument).where(Instrument.ticker_canonical.in_(basket.symbols))))
    if len(rows) != len(set(basket.symbols)) or not rows:
        raise ValueError("basket contains unresolved instruments or is empty")
    if any(("CN" if r.currency == "CNY" else "US") != market for r in rows):
        raise ValueError("a scan must contain instruments from one market")
    return [dict(instrument_id=r.id, symbol=r.ticker_canonical, name=r.name, exchange=r.exchange,
                 asset_type=r.asset_type) for r in sorted(rows, key=lambda r:r.id)]


def create_plan(db: Session, payload: PlanCreate) -> SignalScanPlan:
    from src.services.signal_report_delivery import validate_smtp
    members(db, payload.basket_id, payload.market)
    if payload.enabled:
        validate_smtp(payload.recipients)
    row = SignalScanPlan(name=payload.name, market=payload.market, basket_id=payload.basket_id,
        strategies=strategy_snapshots(db, payload.strategy_ids), language=payload.language,
        recipients=payload.recipients, retention_sessions=payload.retention_sessions, enabled=payload.enabled)
    db.add(row); db.flush()
    return row


def enqueue_scan(db: Session, payload: ScanCreate, request_key: str, *, plan: SignalScanPlan | None = None) -> SignalScanRun:
    existing = db.scalar(select(SignalScanRun).where(SignalScanRun.request_key == request_key))
    if existing:
        return existing
    assert_market_data_submission_allowed(db)
    session = payload.session_date
    ready = db.scalar(select(SignalDataReady).where(SignalDataReady.market == payload.market, SignalDataReady.valid.is_(True))
                      .order_by(SignalDataReady.session_date.desc()).limit(1))
    if session is None:
        if ready is None:
            raise ValueError("no completed market session; choose an explicit historical date")
        session = ready.session_date
    if session > datetime.now(timezone.utc).date():
        raise ValueError("future session is not allowed")
    frozen_members = members(db, payload.basket_id, payload.market)
    runtimes = plan.strategies if plan else strategy_snapshots(db, payload.strategy_ids)
    manifest = dict(schema_version=1, members=frozen_members, basket_id=str(payload.basket_id),
        strategies=runtimes, session_date=session.isoformat(), market=payload.market,
        data_version=ready.version if ready and ready.session_date == session else None,
        member_snapshot_at=datetime.now(timezone.utc).isoformat(),
        price_semantics="forward_adjusted_per_field_fallback_unadjusted",
        initialization="all_available_history_through_session", chart_sessions=CHART_SESSIONS,
        plan=None if plan is None else dict(id=str(plan.id), recipients=plan.recipients,
            retention_sessions=plan.retention_sessions, language=plan.language))
    scan = SignalScanRun(id=uuid4(), plan_id=plan.id if plan else None, request_key=request_key,
        name=payload.name, market=payload.market, session_date=session, manifest=manifest)
    db.add(scan)
    for runtime in runtimes:
        kind = runtime["strategy_type"]
        db.add(SignalScanStrategyRun(scan_id=scan.id, strategy_id=UUID(runtime["strategy_id"]),
            strategy_type=kind, version=runtime["version"], params_hash=digest(runtime["params"]),
            algorithm_revision=str(CATALOG[kind]["algorithm_revision"]), runtime=runtime))
    db.flush()
    return scan


HISTORY_SQL = f"""SELECT i.id AS instrument_id,
    COALESCE((SELECT sh.symbol FROM symbol_history sh WHERE sh.instrument_id=i.id
      AND sh.valid_from <= bars.dt_ny AND (sh.valid_to IS NULL OR sh.valid_to >= bars.dt_ny)
      ORDER BY sh.valid_from DESC LIMIT 1), i.ticker_canonical) AS symbol,
    i.asset_type, i.exchange, i.listed_at, i.delisted_at,
    bars.dt_ny, bars.ts_utc AS ts, bars.close_u AS close_unadjusted, curr.dollar_volume_20,
    {FEATURE_SNAPSHOT_PROJECTION_SQL},
    json_build_object('open',CASE WHEN bars.open_fa IS NULL THEN 'unadjusted' ELSE 'forward_adjusted' END,
      'high',CASE WHEN bars.high_fa IS NULL THEN 'unadjusted' ELSE 'forward_adjusted' END,
      'low',CASE WHEN bars.low_fa IS NULL THEN 'unadjusted' ELSE 'forward_adjusted' END,
      'close',CASE WHEN bars.close_fa IS NULL THEN 'unadjusted' ELSE 'forward_adjusted' END) AS price_source
    FROM eod_bars bars JOIN instruments i ON i.id=bars.instrument_id
    LEFT JOIN daily_features curr ON curr.instrument_id=bars.instrument_id AND curr.dt_ny=bars.dt_ny
    LEFT JOIN LATERAL (SELECT * FROM daily_features f WHERE f.instrument_id=bars.instrument_id
      AND f.dt_ny < bars.dt_ny ORDER BY f.dt_ny DESC LIMIT 1) prev ON TRUE
    WHERE bars.instrument_id IN :ids AND bars.dt_ny <= :session ORDER BY bars.dt_ny,i.id"""


def load_history(db: Session, ids: list[int], session: date) -> dict[int, list[dict]]:
    groups = {i: [] for i in ids}
    for row in db.execute(text(HISTORY_SQL).bindparams(bindparam("ids", expanding=True)),
                          {"ids":ids, "session":session}).mappings():
        groups[row["instrument_id"]].append(dict(row))
    return groups


def coverage_reason(rows: list[dict], session: date, runtime: dict) -> str | None:
    if not rows or rows[-1]["dt_ny"] != session:
        return "missing_session"
    required = required_feature_keys(runtime["strategy_type"], runtime["params"])
    needed = quant_kernel.observation_history_length(runtime)
    if len(rows) < needed:
        return "insufficient_history"
    if any(rows[-1].get(k) is None for k in required):
        return "missing_required_field"
    for row in rows:
        if any(not math.isfinite(float(row[k])) for k in required if row.get(k) is not None):
            return "invalid_data"
        if any(row.get(k) is None for k in ("open","high","low","close")):
            return "missing_ohlc"
        if any(not math.isfinite(float(row[k])) for k in ("open","high","low","close")):
            return "invalid_data"
        if min(row[k] for k in ("open","high","low","close")) <= 0 or row["high"] < max(row["open"],row["close"],row["low"]) or row["low"] > min(row["open"],row["close"],row["high"]):
            return "invalid_data"
    if any(rows[-1].get(k) is None for k in required):
        return "missing_required_field"
    return None


# Only publish market evidence, never execution plans or outcome-study payloads.
PRIVATE_EVIDENCE = {"position", "avg_entry_price", "position_holding_days", "stage_target_pct",
    "stop_price", "target_price", "invalidation_price", "entry_channel", "next_session_phase_geometry",
    "outcomes", "pending_outcomes", "execution", "config", "score_evidence"}

def market_evidence(value):
    if isinstance(value, dict):
        return {k:market_evidence(v) for k,v in value.items() if k not in PRIVATE_EVIDENCE}
    if isinstance(value, list):
        return [market_evidence(v) for v in value]
    return json_value(value)


def evaluate_instrument(rows: list[dict], runtime: dict, dataset=None, market_context=None) -> dict:
    runtime = json.loads(json.dumps(runtime))
    runtime["params"]["universe"] = {"symbols":[rows[-1]["symbol"]], "selection_mode":"explicit"}
    dataset = dataset if dataset is not None else build_in_memory_prepared_dataset(rows)
    dataset.sidecar["market_context"] = market_context or {}
    return dict(quant_kernel.observe_market(dataset, runtime))


def execute_scan(db: Session, scan: SignalScanRun, read_db: Session, heartbeat=lambda progress:None) -> None:
    acquire_market_data_read_lock(read_db, allow_draining=True)
    market_session=read_db.get(SignalMarketSession,(scan.market,scan.session_date))
    confirmed_at=market_session.closes_at if market_session else None
    ranges={}
    children = list(db.scalars(select(SignalScanStrategyRun).where(SignalScanStrategyRun.scan_id == scan.id)))
    # Retry the same frozen membership and parameters; published observations use stable identities.
    unfinished = [c for c in children if c.status != "completed"]
    for child in unfinished:
        normalize_strategy_params(child.strategy_type, child.runtime["params"])
        if child.runtime["strategy_type"] != child.strategy_type:
            raise ValueError("scan runtime strategy type does not match its task")
        if child.algorithm_revision!=str(CATALOG[child.strategy_type]["algorithm_revision"]):
            child.status="failed";child.error="algorithm_revision_changed: create a new scan with the installed strategy revision"
    counters = {c.id:Counter() for c in unfinished}
    collected = {c.id:[] for c in unfinished}
    assets = dict(scan.assets or {})
    identities = [m["instrument_id"] for m in scan.manifest["members"]]
    contexts = {}
    for child in unfinished:
        risk = child.runtime["params"]["risk"]
        if child.strategy_type == "support_resistance" and risk["market_filter_enabled"]:
            symbol = risk["market_filter_symbol"]
            if symbol not in contexts:
                identity = read_db.scalar(select(Instrument.id).where(Instrument.ticker_canonical == symbol))
                history = load_history(read_db,[identity],scan.session_date)[identity] if identity else []
                contexts[symbol] = {str(r["dt_ny"].toordinal()):[float(r["close"]),float(r["sma_200"])] for r in history
                    if r.get("close") and r.get("sma_200")}
    for offset in range(0,len(identities),32):
        histories = load_history(read_db, identities[offset:offset+32], scan.session_date)
        for identity,rows in histories.items():
            ranges[str(identity)]={"from":str(rows[0]["dt_ny"]) if rows else None,"to":str(rows[-1]["dt_ny"]) if rows else None,"sessions":len(rows)}
            dataset = build_in_memory_prepared_dataset(rows) if rows else None
            for child in unfinished:
                if child.status == "failed":
                    continue
                reason = coverage_reason(rows,scan.session_date,child.runtime)
                risk = child.runtime["params"]["risk"]
                context = contexts.get(risk.get("market_filter_symbol"),{})
                if child.strategy_type == "support_resistance" and risk["market_filter_enabled"] and str(scan.session_date.toordinal()) not in context:
                    reason = "missing_market_filter_data"
                if reason:
                    counters[child.id][reason] += 1
                    continue
                try:
                    output = evaluate_instrument(rows,child.runtime,dataset,{"market":context})
                except (ValueError,RuntimeError,TypeError) as exc:
                    child.status="failed"; child.error=str(exc)[:2000]
                    counters[child.id]["calculation_error"] += 1
                    continue
                counters[child.id]["evaluated"] += 1
                events = list(output["observations"])
                if not events:
                    continue
                bars = [json_value(r) for r in [{**r,"session_index":i} for i,r in enumerate(rows)][-CHART_SESSIONS:]]
                asset = put_json({"schema_version":1,"instrument_id":identity,"bars":bars})
                assets[str(identity)] = asset
                for index,event in enumerate(events):
                    evidence = market_evidence(event["metadata"])
                    symbol = str(event["symbol"])
                    audit = market_evidence(output.get("audit",{}).get(symbol,{}))
                    # The audit is frozen before later sessions are loaded.
                    evidence.update(schema_version=1, reason=event["reason"], audit=audit,
                                    observations=market_evidence({k:rows[-1].get(k) for k in set(required_feature_keys(child.strategy_type,child.runtime["params"])) | {"open","high","low","close","volume","atr_14"}}), parameters=child.runtime["params"]["signal"])
                    strength = evidence.get("strength")
                    key = digest([identity,scan.session_date.isoformat(),event["event_type"], evidence.get("setup",{}).get("setup_id"),index])
                    collected[child.id].append(SignalObservation(
                        id=uuid5(NAMESPACE_URL,f"{child.id}:{key}"),scan_id=scan.id,strategy_run_id=child.id,
                        instrument_id=identity,symbol_as_of=symbol,session_date=scan.session_date,
                        confirmed_at=confirmed_at,event_type=event["event_type"],event_kind=event["event_kind"],
                        direction=event.get("direction"),event_key=key,passes_signal_filters=True if strength is None else bool(strength["passes_threshold"]),
                        strength=strength,evidence=evidence,snapshot_ref=asset))
            heartbeat({str(c.id):dict(expected=len(identities),evaluated=counters[c.id]["evaluated"],
                not_evaluated=sum(v for k,v in counters[c.id].items() if k!="evaluated"),
                reasons={k:v for k,v in counters[c.id].items() if k!="evaluated"}) for c in unfinished})
    for child in unfinished:
        count=counters[child.id]; evaluated=count.pop("evaluated",0)
        expected=len(identities)
        if child.status=="failed":
            count["strategy_failed"] += expected-evaluated-sum(count.values())
        else:
            child.status="completed" if evaluated==expected else "partial" if evaluated else "failed"
        child.coverage=dict(expected=expected,evaluated=evaluated,not_evaluated=expected-evaluated,reasons=dict(count))
        observations=collected[child.id]
        ranked=sorted([o for o in observations if o.strength],key=lambda o:(-o.strength["score"],o.instrument_id,o.event_key))
        for rank,o in enumerate(ranked,1):
            o.strategy_rank=rank; o.strength={**o.strength,"rank":rank}
        for observation in observations:
            db.merge(observation)
    completed=sum(c.status=="completed" for c in children)
    scan.status="completed" if completed==len(children) else "partial" if any(c.coverage["evaluated"] for c in children) else "failed"
    scan.coverage={str(c.id):c.coverage for c in children}
    scan.assets=assets
    scan.manifest={**scan.manifest,"input_ranges":ranges,"confirmation_precision":"timestamp" if confirmed_at else "session"}
    scan.finished_at=datetime.now(timezone.utc)
    scan.lease_expires_at=None
