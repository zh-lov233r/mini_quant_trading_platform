from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import (
    PaperTradingAccount,
    PortfolioSnapshot,
    Strategy,
    StrategyAllocation,
    StrategyPortfolio,
    StrategyRun,
    Transaction,
)
from src.services.alpaca_services import AlpacaClient, get_alpaca_client
from src.services.strategy_allocation_service import (
    DEFAULT_PORTFOLIO_NAME,
    normalize_portfolio_name,
)


DEFAULT_ACCOUNT_NAME = "default-paper-account"
ENV_FALLBACKS: dict[str, tuple[str, ...]] = {
    "ALPACA_API_KEY": ("ALPACA_KEY",),
    "ALPACA_SECRET_KEY": ("ALPACA_SECRET",),
}


@dataclass(slots=True)
class StrategyAllocationOverview:
    strategy_id: str
    strategy_name: str
    strategy_type: str
    strategy_status: str
    allocation_pct: float
    capital_base: float | None
    allow_fractional: bool
    allocation_status: str
    notes: str | None
    latest_run_id: str | None
    latest_run_status: str | None
    latest_run_requested_at: datetime | None
    latest_run_equity: float | None


@dataclass(slots=True)
class StrategyPortfolioOverview:
    id: str
    paper_account_id: str
    name: str
    description: str | None
    status: str
    allocation_count: int
    active_allocation_count: int
    allocated_strategy_count: int
    active_allocation_pct_total: float
    latest_run_id: str | None
    latest_run_status: str | None
    latest_run_requested_at: datetime | None
    latest_run_equity: float | None
    strategies: list[StrategyAllocationOverview]


def normalize_account_name(name: str | None) -> str:
    normalized = str(name or DEFAULT_ACCOUNT_NAME).strip()
    return normalized or DEFAULT_ACCOUNT_NAME


def ensure_default_paper_account(db: Session) -> PaperTradingAccount:
    account = db.execute(
        select(PaperTradingAccount).where(PaperTradingAccount.name == DEFAULT_ACCOUNT_NAME)
    ).scalars().first()
    if account is None:
        account = PaperTradingAccount(
            name=DEFAULT_ACCOUNT_NAME,
            broker="alpaca",
            mode="paper",
            api_key_env="ALPACA_API_KEY",
            secret_key_env="ALPACA_SECRET_KEY",
            base_url="https://paper-api.alpaca.markets",
            timeout_seconds=20,
            notes="Auto-created default paper trading account",
            status="active",
        )
        db.add(account)
        db.commit()
        db.refresh(account)
    return account


def ensure_default_strategy_portfolio(db: Session) -> StrategyPortfolio:
    default_account = ensure_default_paper_account(db)
    portfolio = db.execute(
        select(StrategyPortfolio).where(
            StrategyPortfolio.name == DEFAULT_PORTFOLIO_NAME
        )
    ).scalars().first()
    if portfolio is None:
        portfolio = StrategyPortfolio(
            paper_account_id=default_account.id,
            name=DEFAULT_PORTFOLIO_NAME,
            description="Default virtual sleeve for paper trading",
            status="active",
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
    return portfolio


def list_paper_accounts(
    db: Session,
    *,
    status: str | None = None,
) -> list[PaperTradingAccount]:
    stmt = select(PaperTradingAccount).order_by(PaperTradingAccount.created_at.asc())
    if status is not None:
        stmt = stmt.where(PaperTradingAccount.status == status)
    return db.execute(stmt).scalars().all()


def list_strategy_portfolios(
    db: Session,
    *,
    paper_account_id: UUID | str | None = None,
    status: str | None = None,
) -> list[StrategyPortfolio]:
    stmt = select(StrategyPortfolio).order_by(
        StrategyPortfolio.created_at.asc(),
        StrategyPortfolio.name.asc(),
    )
    if paper_account_id is not None:
        stmt = stmt.where(StrategyPortfolio.paper_account_id == paper_account_id)
    if status is not None:
        stmt = stmt.where(StrategyPortfolio.status == status)
    return db.execute(stmt).scalars().all()


def rename_strategy_portfolio(
    db: Session,
    portfolio_id: UUID | str,
    *,
    name: str | None,
) -> StrategyPortfolio:
    portfolio = db.get(StrategyPortfolio, portfolio_id)
    if portfolio is None:
        raise ValueError("strategy portfolio not found")

    if portfolio.name == DEFAULT_PORTFOLIO_NAME:
        raise ValueError("default strategy portfolio cannot be renamed")

    normalized_name = normalize_portfolio_name(name)
    if normalized_name == portfolio.name:
        return portfolio

    existing = db.execute(
        select(StrategyPortfolio)
        .where(StrategyPortfolio.id != portfolio.id)
        .where(StrategyPortfolio.name == normalized_name)
    ).scalars().first()
    if existing is not None:
        raise ValueError(
            "strategy portfolio name already exists; first version requires globally unique portfolio names"
        )

    old_name = portfolio.name
    portfolio.name = normalized_name

    allocations = db.execute(
        select(StrategyAllocation).where(StrategyAllocation.portfolio_name == old_name)
    ).scalars().all()
    for allocation in allocations:
        allocation.portfolio_name = normalized_name

    runs = db.execute(
        select(StrategyRun).where(StrategyRun.mode == "paper")
    ).scalars().all()
    affected_run_ids: list[UUID] = []
    for run in runs:
        changed = False

        config_snapshot = dict(run.config_snapshot or {})
        paper_cfg = config_snapshot.get("paper_trading")
        if isinstance(paper_cfg, dict):
            run_portfolio_name = normalize_portfolio_name(paper_cfg.get("portfolio_name"))
            if run_portfolio_name == old_name:
                config_snapshot["paper_trading"] = {
                    **paper_cfg,
                    "portfolio_name": normalized_name,
                }
                run.config_snapshot = config_snapshot
                changed = True

        summary_metrics = dict(run.summary_metrics or {})
        summary_portfolio_name = normalize_portfolio_name(summary_metrics.get("portfolio_name"))
        if summary_portfolio_name == old_name:
            summary_metrics["portfolio_name"] = normalized_name
            run.summary_metrics = summary_metrics
            changed = True

        if changed:
            affected_run_ids.append(run.id)

    if affected_run_ids:
        snapshots = db.execute(
            select(PortfolioSnapshot).where(PortfolioSnapshot.run_id.in_(affected_run_ids))
        ).scalars().all()
        for snapshot in snapshots:
            metrics = dict(snapshot.metrics or {})
            snapshot_portfolio_name = normalize_portfolio_name(metrics.get("portfolio_name"))
            if snapshot_portfolio_name == old_name:
                metrics["portfolio_name"] = normalized_name
                snapshot.metrics = metrics

        transactions = db.execute(
            select(Transaction).where(Transaction.run_id.in_(affected_run_ids))
        ).scalars().all()
        for txn in transactions:
            meta = dict(txn.meta or {})
            txn_portfolio_name = normalize_portfolio_name(meta.get("portfolio_name"))
            if txn_portfolio_name == old_name:
                meta["portfolio_name"] = normalized_name
                txn.meta = meta

    db.commit()
    db.refresh(portfolio)
    return portfolio


def archive_strategy_portfolio(
    db: Session,
    portfolio_id: UUID | str,
) -> StrategyPortfolio:
    portfolio = db.get(StrategyPortfolio, portfolio_id)
    if portfolio is None:
        raise ValueError("strategy portfolio not found")

    if portfolio.name == DEFAULT_PORTFOLIO_NAME:
        raise ValueError("default strategy portfolio cannot be archived")

    if portfolio.status == "archived":
        return portfolio

    portfolio.status = "archived"

    allocations = db.execute(
        select(StrategyAllocation).where(StrategyAllocation.portfolio_name == portfolio.name)
    ).scalars().all()
    for allocation in allocations:
        allocation.status = "archived"

    db.commit()
    db.refresh(portfolio)
    return portfolio


def get_strategy_portfolio_by_name(
    db: Session,
    portfolio_name: str | None,
) -> StrategyPortfolio | None:
    normalized = normalize_portfolio_name(portfolio_name)
    return db.execute(
        select(StrategyPortfolio).where(StrategyPortfolio.name == normalized)
    ).scalars().first()


def require_strategy_portfolio_by_name(
    db: Session,
    portfolio_name: str | None,
) -> StrategyPortfolio:
    portfolio = get_strategy_portfolio_by_name(db, portfolio_name)
    if portfolio is None:
        raise ValueError(
            f"strategy portfolio not found: {normalize_portfolio_name(portfolio_name)}"
        )
    return portfolio


def build_alpaca_client_for_portfolio(
    db: Session,
    portfolio_name: str | None,
) -> AlpacaClient:
    portfolio = require_strategy_portfolio_by_name(db, portfolio_name)
    account = db.get(PaperTradingAccount, portfolio.paper_account_id)
    if account is None:
        raise ValueError(f"paper account not found for portfolio: {portfolio.name}")
    return build_alpaca_client_for_account(account)


def build_alpaca_client_for_account(account: PaperTradingAccount) -> AlpacaClient:
    return get_alpaca_client(
        api_key=_required_env_value(account.api_key_env),
        secret_key=_required_env_value(account.secret_key_env),
        base_url=account.base_url,
        timeout_seconds=float(account.timeout_seconds or 20),
    )


def build_paper_account_overview(
    db: Session,
    account_id: UUID | str,
) -> dict[str, Any]:
    account = db.get(PaperTradingAccount, account_id)
    if account is None:
        raise ValueError("paper account not found")

    portfolios = list_strategy_portfolios(db, paper_account_id=account.id)
    portfolio_names = {portfolio.name for portfolio in portfolios}
    allocations = db.execute(
        select(StrategyAllocation, Strategy)
        .join(Strategy, Strategy.id == StrategyAllocation.strategy_id)
        .where(StrategyAllocation.portfolio_name.in_(portfolio_names or {DEFAULT_PORTFOLIO_NAME}))
        .order_by(StrategyAllocation.portfolio_name.asc(), StrategyAllocation.created_at.asc())
    ).all()
    runs = db.execute(
        select(StrategyRun)
        .where(StrategyRun.mode == "paper")
        .order_by(StrategyRun.requested_at.desc())
        .limit(200)
    ).scalars().all()

    latest_run_by_portfolio: dict[str, StrategyRun] = {}
    latest_run_by_portfolio_strategy: dict[tuple[str, str], StrategyRun] = {}
    for run in runs:
        paper_cfg = (run.config_snapshot or {}).get("paper_trading", {})
        run_portfolio_name = str(paper_cfg.get("portfolio_name") or "").strip()
        if run_portfolio_name not in portfolio_names:
            continue
        if run_portfolio_name not in latest_run_by_portfolio:
            latest_run_by_portfolio[run_portfolio_name] = run
        key = (run_portfolio_name, str(run.strategy_id))
        if key not in latest_run_by_portfolio_strategy:
            latest_run_by_portfolio_strategy[key] = run

    rows_by_portfolio: dict[str, list[tuple[StrategyAllocation, Strategy]]] = {}
    for allocation, strategy in allocations:
        rows_by_portfolio.setdefault(allocation.portfolio_name, []).append((allocation, strategy))

    portfolio_summaries: list[dict[str, Any]] = []
    active_portfolio_count = 0
    active_allocation_count = 0
    active_strategy_count = 0

    for portfolio in portfolios:
        portfolio_rows = rows_by_portfolio.get(portfolio.name, [])
        active_rows = [row for row in portfolio_rows if row[0].status == "active"]
        latest_run = latest_run_by_portfolio.get(portfolio.name)
        strategy_items: list[dict[str, Any]] = []

        for allocation, strategy in portfolio_rows:
            strategy_run = latest_run_by_portfolio_strategy.get((portfolio.name, str(strategy.id)))
            strategy_items.append(
                {
                    "strategy_id": str(strategy.id),
                    "strategy_name": strategy.name,
                    "strategy_type": strategy.strategy_type,
                    "strategy_status": strategy.status,
                    "allocation_pct": float(allocation.allocation_pct or 0),
                    "capital_base": (
                        float(allocation.capital_base)
                        if allocation.capital_base is not None
                        else None
                    ),
                    "allow_fractional": bool(allocation.allow_fractional),
                    "allocation_status": allocation.status,
                    "notes": allocation.notes,
                    "latest_run_id": str(strategy_run.id) if strategy_run is not None else None,
                    "latest_run_status": strategy_run.status if strategy_run is not None else None,
                    "latest_run_requested_at": (
                        strategy_run.requested_at if strategy_run is not None else None
                    ),
                    "latest_run_equity": (
                        float(strategy_run.final_equity)
                        if strategy_run is not None and strategy_run.final_equity is not None
                        else _summary_metric_float(strategy_run, "virtual_equity_after")
                    ),
                }
            )

        if portfolio.status == "active":
            active_portfolio_count += 1
        active_allocation_count += len(active_rows)
        active_strategy_count += len({str(strategy.id) for _, strategy in active_rows})

        portfolio_summaries.append(
            {
                "id": str(portfolio.id),
                "paper_account_id": str(account.id),
                "name": portfolio.name,
                "description": portfolio.description,
                "status": portfolio.status,
                "allocation_count": len(portfolio_rows),
                "active_allocation_count": len(active_rows),
                "allocated_strategy_count": len({str(strategy.id) for _, strategy in active_rows}),
                "active_allocation_pct_total": sum(
                    float(allocation.allocation_pct or 0) for allocation, _ in active_rows
                ),
                "latest_run_id": str(latest_run.id) if latest_run is not None else None,
                "latest_run_status": latest_run.status if latest_run is not None else None,
                "latest_run_requested_at": (
                    latest_run.requested_at if latest_run is not None else None
                ),
                "latest_run_equity": (
                    float(latest_run.final_equity)
                    if latest_run is not None and latest_run.final_equity is not None
                    else _summary_metric_float(latest_run, "virtual_equity_after")
                ),
                "strategies": strategy_items,
            }
        )

    return {
        "account": {
            "id": str(account.id),
            "name": account.name,
            "broker": account.broker,
            "mode": account.mode,
            "api_key_env": account.api_key_env,
            "secret_key_env": account.secret_key_env,
            "base_url": account.base_url,
            "timeout_seconds": float(account.timeout_seconds or 20),
            "notes": account.notes,
            "status": account.status,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        },
        "portfolio_count": len(portfolios),
        "active_portfolio_count": active_portfolio_count,
        "active_allocation_count": active_allocation_count,
        "active_strategy_count": active_strategy_count,
        "portfolios": portfolio_summaries,
    }


def _required_env_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        for fallback_name in ENV_FALLBACKS.get(name, ()):
            fallback_value = os.getenv(fallback_name, "").strip()
            if fallback_value:
                return fallback_value
        raise ValueError(f"missing environment variable for paper account credential: {name}")
    return value


def _summary_metric_float(run: StrategyRun | None, key: str) -> float | None:
    if run is None:
        return None
    value = (run.summary_metrics or {}).get(key)
    if value is None:
        return None
    return float(value)
