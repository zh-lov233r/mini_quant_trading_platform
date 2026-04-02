from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.models.tables import Strategy, StrategyAllocation, StrategyPortfolio
from src.services.paper_account_service import (
    ensure_default_strategy_portfolio,
    get_strategy_portfolio_by_name,
)
from src.services.strategy_allocation_service import (
    normalize_portfolio_name,
    validate_portfolio_allocations,
)


class StrategyAllocationUpsert(BaseModel):
    strategy_id: UUID = Field(..., description="策略 ID")
    portfolio_name: str = Field(default="default", min_length=1, max_length=64)
    allocation_pct: float = Field(..., ge=0, le=1, description="该策略占组合的虚拟资金比例")
    capital_base: float | None = Field(default=None, ge=0, description="可选固定虚拟本金")
    allow_fractional: bool = Field(default=True)
    notes: str | None = Field(default=None, max_length=500)
    status: str = Field(default="active")


class StrategyAllocationOut(BaseModel):
    id: UUID
    strategy_id: UUID
    strategy_name: str | None = None
    portfolio_name: str
    paper_account_id: UUID | None = None
    paper_account_name: str | None = None
    allocation_pct: float
    capital_base: float | None = None
    allow_fractional: bool
    notes: str | None = None
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


router = APIRouter(prefix="/api/strategy-allocations", tags=["strategy-allocations"])


def _to_allocation_out(
    allocation: StrategyAllocation,
    *,
    strategy_name: str | None = None,
    portfolio: StrategyPortfolio | None = None,
) -> StrategyAllocationOut:
    return StrategyAllocationOut(
        id=allocation.id,
        strategy_id=allocation.strategy_id,
        strategy_name=strategy_name or getattr(allocation.strategy, "name", None),
        portfolio_name=allocation.portfolio_name,
        paper_account_id=portfolio.paper_account_id if portfolio is not None else None,
        paper_account_name=(
            getattr(getattr(portfolio, "paper_account", None), "name", None)
            if portfolio is not None
            else None
        ),
        allocation_pct=float(allocation.allocation_pct or 0),
        capital_base=float(allocation.capital_base) if allocation.capital_base is not None else None,
        allow_fractional=bool(allocation.allow_fractional),
        notes=allocation.notes,
        status=allocation.status,
        created_at=allocation.created_at,
        updated_at=allocation.updated_at,
    )


@router.get("", response_model=list[StrategyAllocationOut])
def list_strategy_allocations(
    db: Session = Depends(get_db),
    portfolio_name: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
):
    ensure_default_strategy_portfolio(db)
    stmt = (
        select(StrategyAllocation, Strategy.name, StrategyPortfolio)
        .join(Strategy, Strategy.id == StrategyAllocation.strategy_id)
        .outerjoin(StrategyPortfolio, StrategyPortfolio.name == StrategyAllocation.portfolio_name)
        .order_by(StrategyAllocation.portfolio_name.asc(), StrategyAllocation.created_at.asc())
    )
    if portfolio_name:
        stmt = stmt.where(StrategyAllocation.portfolio_name == normalize_portfolio_name(portfolio_name))
    if status_filter:
        stmt = stmt.where(StrategyAllocation.status == status_filter)
    rows = db.execute(stmt).all()
    return [
        _to_allocation_out(allocation, strategy_name=strategy_name, portfolio=portfolio)
        for allocation, strategy_name, portfolio in rows
    ]


@router.post("", response_model=StrategyAllocationOut, status_code=status.HTTP_200_OK)
def upsert_strategy_allocation(payload: StrategyAllocationUpsert, db: Session = Depends(get_db)):
    ensure_default_strategy_portfolio(db)
    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")

    portfolio_name = normalize_portfolio_name(payload.portfolio_name)
    portfolio = get_strategy_portfolio_by_name(db, portfolio_name)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="strategy portfolio not found")
    existing = db.execute(
        select(StrategyAllocation)
        .where(StrategyAllocation.strategy_id == payload.strategy_id)
        .where(StrategyAllocation.portfolio_name == portfolio_name)
    ).scalars().first()

    if existing is None:
        allocation = StrategyAllocation(
            strategy_id=payload.strategy_id,
            portfolio_name=portfolio_name,
            allocation_pct=payload.allocation_pct,
            capital_base=payload.capital_base,
            allow_fractional=1 if payload.allow_fractional else 0,
            notes=(payload.notes or "").strip() or None,
            status=payload.status,
        )
        db.add(allocation)
    else:
        existing.allocation_pct = payload.allocation_pct
        existing.capital_base = payload.capital_base
        existing.allow_fractional = 1 if payload.allow_fractional else 0
        existing.notes = (payload.notes or "").strip() or None
        existing.status = payload.status
        allocation = existing

    db.flush()
    active_allocations = db.execute(
        select(StrategyAllocation)
        .where(StrategyAllocation.portfolio_name == portfolio_name)
        .where(StrategyAllocation.status == "active")
    ).scalars().all()
    try:
        validate_portfolio_allocations(active_allocations)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.commit()
    db.refresh(allocation)
    return _to_allocation_out(allocation, strategy_name=strategy.name, portfolio=portfolio)
