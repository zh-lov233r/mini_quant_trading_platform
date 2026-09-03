"""Bounded read models for the research dashboard.

Fixed database round-trips (no per-account/strategy queries). Counts stay in SQL;
latest evidence uses row_number; activity sources and portfolio rows are bounded.
Strategy params are read once to reuse the registry's actual readiness validation.
No trades, signals, snapshots, external APIs, initialization or execution helpers.
Measure query plans before adding indexes to the existing schema.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import math
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from src.models.tables import (
    BacktestJob, ExperimentCandidate, ExperimentTrial, MarketDataMaintenanceState,
    PaperTradingAccount, ResearchExperiment, Strategy, StrategyAllocation,
    StrategyPortfolio, StrategyRun,
)
from src.schemas.dashboard import DashboardOverview, DashboardStrategyEvidence
from src.services.backtest_worker_status_service import load_backtest_worker_status
from src.services.strategy_registry import is_engine_ready

log = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    # SQLite test fixtures return naive datetimes; production timestamps are UTC aware.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _count(db: Session, model: Any, *conditions: Any) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions))


def _sum(condition: Any):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _verification(value: Any) -> tuple[str | None, list[str]]:
    if not isinstance(value, dict):
        return None, ["invalid_research_evidence"]
    verification = value.get("verification")
    if verification is None:
        return None, []
    if not isinstance(verification, dict) or verification.get("status") not in {
        "queued", "running", "completed", "failed", "cancelled",
    }:
        return None, ["invalid_research_evidence"]
    return verification["status"], []


def _strategy_evidence(db: Session, strategies: list[Any], ready: dict) -> list[DashboardStrategyEvidence]:
    selected = strategies[:10]
    ids = [s.id for s in selected]
    ranked = (
        select(
            StrategyRun.id.label("run_id"), StrategyRun.strategy_id,
            func.row_number().over(
                partition_by=StrategyRun.strategy_id,
                order_by=(StrategyRun.finished_at.desc(), StrategyRun.id.desc()),
            ).label("rn"),
        )
        .join(Strategy, and_(Strategy.id == StrategyRun.strategy_id, Strategy.version == StrategyRun.strategy_version))
        .outerjoin(BacktestJob, BacktestJob.run_id == StrategyRun.id)
        .where(
            StrategyRun.strategy_id.in_(ids), StrategyRun.mode == "backtest",
            StrategyRun.status == "completed",
            or_(BacktestJob.id.is_(None), BacktestJob.source == "manual"),
            ~select(ExperimentTrial.id).where(ExperimentTrial.backtest_run_id == StrategyRun.id).exists(),
        ).subquery()
    )
    runs = {r.strategy_id: r for r in db.execute(
        select(StrategyRun.id, StrategyRun.strategy_id, StrategyRun.config_snapshot,
               StrategyRun.summary_metrics, StrategyRun.window_start, StrategyRun.window_end, StrategyRun.finished_at)
        .join(ranked, ranked.c.run_id == StrategyRun.id).where(ranked.c.rn == 1)
    )}
    candidates = {r.promoted_strategy_id: r for r in db.execute(
        select(ExperimentCandidate.id, ExperimentCandidate.experiment_id,
               ExperimentCandidate.promoted_strategy_id, ExperimentCandidate.aggregate_metrics)
        .where(ExperimentCandidate.promoted_strategy_id.in_(ids))
        .order_by(ExperimentCandidate.updated_at, ExperimentCandidate.id)
    )}
    result = []
    for strategy in selected:
        item = DashboardStrategyEvidence(
            strategy_id=str(strategy.id), name=strategy.name, strategy_type=strategy.strategy_type,
            version=strategy.version, engine_ready=ready[strategy.id], evidence_status="missing",
        )
        run = runs.get(strategy.id)
        if run is not None:
            item.backtest_id = str(run.id)
            item.window_start = run.window_start.isoformat() if run.window_start else None
            item.window_end = run.window_end.isoformat() if run.window_end else None
            item.finished_at = run.finished_at
            item.evidence_status = "available"
            if not isinstance(run.config_snapshot, dict) or not isinstance(strategy.params, dict):
                item.evidence_status = "invalid"
                item.issues.append("invalid_configuration")
            else:
                # A run resolves its own universe (basket/PIT/manual symbols). Compare strategy
                # rules, risk and execution; disclose that the run universe is separate.
                current = {k: v for k, v in strategy.params.items() if k not in {"metadata", "universe"}}
                saved = {k: run.config_snapshot.get(k) for k in current}
                if not current or current != saved:
                    item.evidence_status = "configuration_changed"
            if not isinstance(run.summary_metrics, dict):
                item.issues.append("invalid_metrics")
            elif item.evidence_status == "available":
                for key in ("total_return", "sharpe", "max_drawdown", "trade_count"):
                    value = run.summary_metrics.get(key)
                    if value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        item.issues.append("invalid_metrics")
                    elif key == "trade_count" and (value < 0 or int(value) != value):
                        item.issues.append("invalid_metrics")
                    else:
                        setattr(item, key, int(value) if key == "trade_count" else value)
        candidate = candidates.get(strategy.id)
        if candidate is not None:
            item.candidate_id = str(candidate.id)
            item.experiment_id = str(candidate.experiment_id)
            item.verification_status, issues = _verification(candidate.aggregate_metrics)
            item.issues.extend(issues)
        item.issues = sorted(set(item.issues))
        if item.issues:
            log.warning("Dashboard evidence unavailable strategy_id=%s reasons=%s", strategy.id, item.issues)
        result.append(item)
    return result


def _paper_data(db: Session, ready: dict) -> tuple[dict, dict]:
    eligible_accounts = and_(PaperTradingAccount.status == "active", PaperTradingAccount.mode == "paper")
    eligible_portfolios = and_(eligible_accounts, StrategyPortfolio.status == "active")
    allocation_active = StrategyAllocation.status == "active"
    executable = and_(allocation_active, Strategy.status == "active", Strategy.id.in_([sid for sid, value in ready.items() if value]))
    schedulable = and_(allocation_active, Strategy.status == "active", StrategyAllocation.auto_run_enabled.is_(True))
    paper_cfg = StrategyRun.config_snapshot["paper_trading"]

    def latest_runs(scheduler_only: bool):
        stmt = select(
            StrategyRun.id, StrategyRun.status, StrategyRun.requested_at, StrategyRun.strategy_id,
            paper_cfg["submit_orders"].label("submit_orders"),
            paper_cfg["portfolio_name"].as_string().label("portfolio_name"),
            func.row_number().over(
                partition_by=paper_cfg["portfolio_name"].as_string(),
                order_by=(StrategyRun.requested_at.desc(), StrategyRun.id.desc()),
            ).label("rn"),
        ).where(StrategyRun.mode == "paper")
        if scheduler_only:
            stmt = stmt.where(paper_cfg["trigger"].as_string() == "scheduler")
        return stmt.subquery()

    latest, scheduled = latest_runs(False), latest_runs(True)
    allocations = (
        select(
            StrategyAllocation.portfolio_name,
            _sum(allocation_active).label("allocation_count"),
            func.coalesce(func.sum(case((allocation_active, StrategyAllocation.allocation_pct), else_=0)), 0).label("allocation_total"),
            _sum(executable).label("valid_count"), _sum(schedulable).label("auto_count"),
        ).join(Strategy, Strategy.id == StrategyAllocation.strategy_id)
        .group_by(StrategyAllocation.portfolio_name).subquery()
    )
    portfolios = (
        select(
            StrategyPortfolio.id, StrategyPortfolio.name, PaperTradingAccount.name.label("account_name"),
            StrategyPortfolio.created_at,
            func.coalesce(allocations.c.allocation_count, 0).label("allocation_count"),
            func.coalesce(allocations.c.allocation_total, 0).label("allocation_total"),
            func.coalesce(allocations.c.valid_count, 0).label("valid_count"),
            func.coalesce(allocations.c.auto_count, 0).label("auto_count"),
            latest.c.id.label("run_id"), latest.c.status.label("run_status"),
            latest.c.requested_at.label("run_at"), latest.c.submit_orders,
            latest.c.strategy_id, scheduled.c.status.label("scheduler_status"),
            scheduled.c.requested_at.label("scheduler_at"),
        ).join(PaperTradingAccount, PaperTradingAccount.id == StrategyPortfolio.paper_account_id)
        .outerjoin(allocations, allocations.c.portfolio_name == StrategyPortfolio.name)
        .outerjoin(latest, and_(latest.c.portfolio_name == StrategyPortfolio.name, latest.c.rn == 1))
        .outerjoin(scheduled, and_(scheduled.c.portfolio_name == StrategyPortfolio.name, scheduled.c.rn == 1))
        .where(eligible_portfolios).subquery()
    )
    p = portfolios.c
    summary = db.execute(select(
        func.count().label("total"), _sum(p.allocation_total > 1).label("overallocated"),
        _sum(p.allocation_count == 0).label("no_allocations"), _sum(and_(p.run_id.is_(None), p.valid_count > 0)).label("never_run"),
        _sum(and_(p.auto_count > 0, p.scheduler_status == "failed")).label("scheduler_failed"),
        func.max(case((and_(p.auto_count > 0, p.scheduler_status == "failed"), p.scheduler_at))).label("failed_at"),
    )).one()
    rows = db.execute(select(portfolios, Strategy.name.label("strategy_name"))
        .outerjoin(Strategy, Strategy.id == p.strategy_id)
        .order_by(p.run_at.desc().nulls_last(), p.id).limit(10)).all()
    return {
        "account_count": _count(db, PaperTradingAccount, eligible_accounts),
        "portfolio_count": summary.total,
        "portfolios": [{
            "portfolio_id": str(r.id), "account_name": r.account_name, "name": r.name,
            "allocation_count": r.allocation_count, "allocation_total": float(r.allocation_total),
            "auto_run_eligible": r.auto_count > 0, "latest_run_id": str(r.run_id) if r.run_id else None,
            "latest_strategy_name": r.strategy_name, "latest_run_status": r.run_status,
            "latest_run_at": r.run_at, "submit_orders": r.submit_orders if isinstance(r.submit_orders, bool) else None,
        } for r in rows],
    }, dict(summary._mapping)


def _activity(db: Session) -> list[dict]:
    events = []
    # Four independently bounded sources; a run is represented once, never again as a job.
    for mode in ("backtest", "paper"):
        rows = db.execute(select(StrategyRun.id, StrategyRun.status, StrategyRun.finished_at, Strategy.name)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(StrategyRun.mode == mode, StrategyRun.status.in_(["completed", "failed", "cancelled"]), StrategyRun.finished_at.is_not(None))
            .order_by(StrategyRun.finished_at.desc(), StrategyRun.id.desc()).limit(20))
        for r in rows:
            events.append(dict(id=f"run:{r.id}", category=mode, status=r.status, name=r.name,
                occurred_at=_utc(r.finished_at), href=f"/backtests/{r.id}" if mode == "backtest" else "/paper-trading"))
    for r in db.execute(select(ResearchExperiment.id, ResearchExperiment.status, ResearchExperiment.finished_at)
        .where(ResearchExperiment.finished_at.is_not(None))
        .order_by(ResearchExperiment.finished_at.desc(), ResearchExperiment.id.desc()).limit(20)):
        events.append(dict(id=f"experiment:{r.id}", category="research", status=r.status, name=str(r.id),
            occurred_at=_utc(r.finished_at), href=f"/research/{r.id}"))
    for r in db.execute(select(Strategy.id, Strategy.name, Strategy.created_at, Strategy.updated_at)
        .order_by(Strategy.updated_at.desc(), Strategy.id.desc()).limit(20)):
        events.append(dict(id=f"strategy:{r.id}", category="strategy", status="updated" if r.updated_at != r.created_at else "created",
            name=r.name, occurred_at=_utc(r.updated_at), href=f"/strategies/{r.id}"))
    return sorted(events, key=lambda e: (e["occurred_at"], e["id"]), reverse=True)[:20]


def build_dashboard_overview(db: Session, *, research: dict, scheduler: dict, checked_at: datetime | None = None) -> DashboardOverview:
    now = checked_at or datetime.now(UTC)
    since = now - timedelta(hours=24)
    worker = load_backtest_worker_status(db, checked_at=now)
    strategies = list(db.execute(select(Strategy.id, Strategy.name, Strategy.strategy_type, Strategy.params,
        Strategy.version, Strategy.status, Strategy.updated_at).order_by(Strategy.updated_at.desc(), Strategy.id.desc())))
    ready = {s.id: isinstance(s.params, dict) and is_engine_ready(s.strategy_type, s.params) for s in strategies}
    # Waiting means a trial has not entered the durable queue, not every queued trial.
    waiting = _count(db, ExperimentTrial, ExperimentTrial.status == "queued", ExperimentTrial.backtest_run_id.is_(None))
    run_counts = db.execute(select(
        _sum(StrategyRun.status == "completed").label("completed"),
        _sum(StrategyRun.status == "failed").label("failed"),
        func.max(case((StrategyRun.status == "failed", StrategyRun.finished_at))).label("failed_at"),
    ).where(StrategyRun.mode == "backtest", StrategyRun.finished_at >= since)).one()
    experiments = db.execute(select(func.count().label("total"),
        _sum(ResearchExperiment.status == "running").label("running"),
        _sum(and_(ResearchExperiment.status.in_(["failed", "partially_failed"]), ResearchExperiment.finished_at >= since)).label("failed"),
        func.max(case((and_(ResearchExperiment.status.in_(["failed", "partially_failed"]), ResearchExperiment.finished_at >= since), ResearchExperiment.finished_at))).label("failed_at"),
    )).one()
    candidates = db.execute(select(
        _sum(ExperimentCandidate.pareto_rank.is_not(None)).label("evaluated"),
        _sum(ExperimentCandidate.aggregate_metrics["verification"]["status"].as_string() == "completed").label("verified"),
        func.count(func.distinct(ExperimentCandidate.promoted_strategy_id)).label("promoted"),
    )).one()
    paper, paper_alerts = _paper_data(db, ready)
    paper_strategies = db.scalar(select(func.count(func.distinct(StrategyAllocation.strategy_id)))
        .join(StrategyPortfolio, StrategyPortfolio.name == StrategyAllocation.portfolio_name)
        .join(PaperTradingAccount, PaperTradingAccount.id == StrategyPortfolio.paper_account_id)
        .where(StrategyAllocation.status == "active", StrategyPortfolio.status == "active",
               PaperTradingAccount.status == "active", PaperTradingAccount.mode == "paper"))
    invalid_allocation_rows = db.execute(select(StrategyAllocation.strategy_id, func.count().label("count"))
        .join(Strategy, Strategy.id == StrategyAllocation.strategy_id)
        .where(StrategyAllocation.status == "active", or_(Strategy.status != "active", Strategy.id.in_([sid for sid, value in ready.items() if not value])))
        .group_by(StrategyAllocation.strategy_id)).all()
    invalid_allocations = sum(r.count for r in invalid_allocation_rows)
    allocation_problem_strategies = {r.strategy_id for r in invalid_allocation_rows}
    maintenance = db.get(MarketDataMaintenanceState, 1)  # Never initialize a missing singleton.
    latest_scheduler = db.execute(select(StrategyRun.status, StrategyRun.requested_at)
        .where(StrategyRun.mode == "paper", StrategyRun.config_snapshot["paper_trading"]["trigger"].as_string() == "scheduler")
        .order_by(StrategyRun.requested_at.desc(), StrategyRun.id.desc()).limit(1)).first()
    maintenance_status = maintenance.status if maintenance else "unknown"
    research_status = {"disabled": "disabled", "idle": "healthy", "running": "healthy", "stopping": "degraded", "failed": "failed"}[research["state"]]
    system = [
        dict(key="market_data", status={"ready": "healthy", "draining": "degraded", "updating": "degraded", "failed": "failed", "unknown": "unknown"}[maintenance_status],
             reason=f"maintenance_{maintenance_status}", observed_at=maintenance.finished_at if maintenance else None),
        dict(key="backtest", status="healthy" if worker["automation_available"] else "failed", reason="worker_available" if worker["automation_available"] else "worker_unavailable",
             active=worker["active_jobs"], capacity=worker["configured_concurrency"], queued=worker["queued_jobs"]),
        dict(key="research", status=research_status, reason=f"research_{research['state']}", enabled=research["enabled"],
             active=research["active_trials"], capacity=research["configured_concurrency"], queued=waiting),
        dict(key="scheduler", status=scheduler["status"], reason=f"scheduler_{scheduler['status']}", enabled=scheduler["enabled"], submit_orders=scheduler["submit_orders"],
             last_run_status=latest_scheduler.status if latest_scheduler else None, observed_at=latest_scheduler.requested_at if latest_scheduler else None),
        dict(key="broker", status="unknown", reason="broker_unchecked"),
    ]
    alerts = []

    def alert(code: str, severity: str, count: int, href: str, occurred_at: datetime | None = None):
        if count:
            alerts.append(dict(id=f"dashboard:{code}", code=code, severity=severity, count=count,
                               occurred_at=_utc(occurred_at) if occurred_at else None, href=href))

    alert("overallocated", "critical", paper_alerts["overallocated"], "/paper-trading")
    if maintenance_status in {"failed", "draining", "updating"}:
        alert("maintenance_blocked", "critical" if maintenance_status == "failed" else "warning", 1, "/backtest-tasks", maintenance.updated_at)
    alert("backtest_blocked", "warning", worker["queued_jobs"] if not worker["automation_available"] else 0, "/backtest-tasks")
    alert("research_blocked", "warning", waiting if research_status != "healthy" else 0, "/backtest-tasks")
    alert("backtests_failed", "warning", run_counts.failed, "/backtest-tasks", run_counts.failed_at)
    alert("research_failed", "warning", experiments.failed, "/research", experiments.failed_at)
    # Combine inactive/invalid allocation references and active non-executable strategies into one root category.
    invalid_strategies = sum(s.status == "active" and not ready[s.id] and s.id not in allocation_problem_strategies for s in strategies)
    alert("strategy_configuration", "warning", invalid_strategies, "/strategies")
    alert("allocation_configuration", "warning", invalid_allocations, "/paper-trading")
    alert("scheduler_failed", "critical", paper_alerts["scheduler_failed"], "/paper-trading", paper_alerts["failed_at"])
    alert("no_allocations", "info", paper_alerts["no_allocations"], "/paper-trading")
    alert("never_run", "info", paper_alerts["never_run"], "/paper-trading")
    alerts.sort(key=lambda a: ({"critical": 0, "warning": 1, "info": 2}[a["severity"]],
                              -(a["occurred_at"].timestamp() if a["occurred_at"] else 0), a["id"]))
    return DashboardOverview(
        generated_at=now, system=[dict(s, checked_at=now) for s in system],
        research_kpis=dict(active_strategies=sum(s.status == "active" for s in strategies), running_experiments=experiments.running,
                           running_backtests=worker["active_jobs"], queued_backtests=worker["queued_jobs"]),
        task_summary=dict(waiting_research=waiting, completed_last_24h=run_counts.completed,
                          failed_backtests_last_24h=run_counts.failed, failed_research_last_24h=experiments.failed),
        research_progress=dict(experiments=experiments.total, evaluated_candidates=candidates.evaluated,
                               verified_candidates=candidates.verified, promoted_strategies=candidates.promoted, paper_strategies=paper_strategies),
        strategy_evidence=_strategy_evidence(db, strategies, ready), paper_summary=paper, alerts=alerts, activity=_activity(db),
    )
