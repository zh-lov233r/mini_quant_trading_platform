from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import Strategy, StrategyAllocation


DEFAULT_PORTFOLIO_NAME = "default"


@dataclass(slots=True)
class AllocationSummary:
    strategy_id: str
    strategy_name: str
    portfolio_name: str
    allocation_pct: float
    capital_base: float | None
    allow_fractional: bool
    auto_run_enabled: bool
    status: str


def normalize_portfolio_name(portfolio_name: str | None) -> str:
    normalized = str(portfolio_name or DEFAULT_PORTFOLIO_NAME).strip()
    return normalized or DEFAULT_PORTFOLIO_NAME


def list_strategy_allocations(
    db: Session,
    *,
    portfolio_name: str | None = None,
    status: str | None = None,
) -> list[StrategyAllocation]:
    stmt = select(StrategyAllocation).order_by(
        StrategyAllocation.portfolio_name.asc(),
        StrategyAllocation.created_at.asc(),
    )
    if portfolio_name is not None:
        stmt = stmt.where(StrategyAllocation.portfolio_name == normalize_portfolio_name(portfolio_name))
    if status is not None:
        stmt = stmt.where(StrategyAllocation.status == status)
    return db.execute(stmt).scalars().all()


def list_active_portfolio_allocations(
    db: Session,
    *,
    portfolio_name: str | None = None,
) -> list[StrategyAllocation]:
    return list_strategy_allocations(
        db,
        portfolio_name=portfolio_name,
        status="active",
    )


def get_strategy_allocation(
    db: Session,
    strategy_id: UUID | str,
    *,
    portfolio_name: str | None = None,
    status: str | None = "active",
) -> StrategyAllocation | None:
    stmt = select(StrategyAllocation).where(
        StrategyAllocation.strategy_id == strategy_id,
        StrategyAllocation.portfolio_name == normalize_portfolio_name(portfolio_name),
    )
    if status is not None:
        stmt = stmt.where(StrategyAllocation.status == status)
    return db.execute(stmt).scalars().first()


def list_allocated_strategies(
    db: Session,
    *,
    portfolio_name: str | None = None,
    auto_run_enabled: bool | None = None,
) -> list[tuple[Strategy, StrategyAllocation]]:
    normalized_portfolio = normalize_portfolio_name(portfolio_name)
    stmt = (
        select(Strategy, StrategyAllocation)
        .join(StrategyAllocation, StrategyAllocation.strategy_id == Strategy.id)
        .where(Strategy.status == "active")
        .where(StrategyAllocation.status == "active")
        .where(StrategyAllocation.portfolio_name == normalized_portfolio)
        .order_by(StrategyAllocation.created_at.asc(), Strategy.created_at.asc())
    )
    if auto_run_enabled is not None:
        stmt = stmt.where(StrategyAllocation.auto_run_enabled == auto_run_enabled)
    rows = db.execute(stmt).all()
    return [(strategy, allocation) for strategy, allocation in rows]


def validate_portfolio_allocations(
    allocations: Iterable[StrategyAllocation],
) -> float:
    total = 0.0
    for allocation in allocations:
        total += float(allocation.allocation_pct or 0)
    if total > 1.000001:
        raise ValueError(
            f"active allocation_pct total exceeds 1.0 for portfolio: {total:.6f}"
        )
    return total


def to_allocation_summary(
    allocation: StrategyAllocation,
    *,
    strategy_name: str | None = None,
) -> AllocationSummary:
    return AllocationSummary(
        strategy_id=str(allocation.strategy_id),
        strategy_name=strategy_name or getattr(allocation.strategy, "name", "") or "",
        portfolio_name=allocation.portfolio_name,
        allocation_pct=float(allocation.allocation_pct or 0),
        capital_base=(
            float(allocation.capital_base)
            if allocation.capital_base is not None
            else None
        ),
        allow_fractional=bool(allocation.allow_fractional),
        auto_run_enabled=bool(allocation.auto_run_enabled),
        status=allocation.status,
    )
