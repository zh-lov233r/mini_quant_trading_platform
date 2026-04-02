from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.models.tables import (
    PaperTradingAccount,
    Strategy,
    StrategyAllocation,
    StrategyPortfolio,
)
from src.services.paper_account_service import (
    build_paper_account_overview,
    ensure_default_paper_account,
    ensure_default_strategy_portfolio,
    list_paper_accounts,
    list_strategy_portfolios,
    normalize_account_name,
    rename_strategy_portfolio,
)
from src.services.strategy_allocation_service import validate_portfolio_allocations


class PaperTradingAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    broker: str = Field(default="alpaca")
    mode: str = Field(default="paper")
    api_key_env: str = Field(default="ALPACA_API_KEY", min_length=1, max_length=128)
    secret_key_env: str = Field(default="ALPACA_SECRET_KEY", min_length=1, max_length=128)
    base_url: str = Field(default="https://paper-api.alpaca.markets", min_length=1, max_length=255)
    timeout_seconds: float = Field(default=20, gt=0)
    notes: str | None = Field(default=None, max_length=500)
    status: str = Field(default="active")


class PaperTradingAccountOut(BaseModel):
    id: UUID
    name: str
    broker: str
    mode: str
    api_key_env: str
    secret_key_env: str
    base_url: str
    timeout_seconds: float
    notes: str | None = None
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StrategyPortfolioCreate(BaseModel):
    paper_account_id: UUID
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    strategy_ids: list[UUID] = Field(default_factory=list)
    status: str = Field(default="active")


class StrategyPortfolioRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class StrategyPortfolioOut(BaseModel):
    id: UUID
    paper_account_id: UUID
    paper_account_name: str | None = None
    name: str
    description: str | None = None
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PortfolioStrategyOverviewOut(BaseModel):
    strategy_id: str
    strategy_name: str
    strategy_type: str
    strategy_status: str
    allocation_pct: float
    capital_base: float | None = None
    allow_fractional: bool
    allocation_status: str
    notes: str | None = None
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_requested_at: datetime | None = None
    latest_run_equity: float | None = None


class StrategyPortfolioOverviewOut(BaseModel):
    id: str
    paper_account_id: str
    name: str
    description: str | None = None
    status: str
    allocation_count: int
    active_allocation_count: int
    allocated_strategy_count: int
    active_allocation_pct_total: float
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_requested_at: datetime | None = None
    latest_run_equity: float | None = None
    strategies: list[PortfolioStrategyOverviewOut]


class PaperTradingAccountOverviewOut(BaseModel):
    account: PaperTradingAccountOut
    portfolio_count: int
    active_portfolio_count: int
    active_allocation_count: int
    active_strategy_count: int
    portfolios: list[StrategyPortfolioOverviewOut]


router = APIRouter(prefix="/api", tags=["paper-accounts"])


def _to_account_out(account: PaperTradingAccount) -> PaperTradingAccountOut:
    return PaperTradingAccountOut(
        id=account.id,
        name=account.name,
        broker=account.broker,
        mode=account.mode,
        api_key_env=account.api_key_env,
        secret_key_env=account.secret_key_env,
        base_url=account.base_url,
        timeout_seconds=float(account.timeout_seconds or 20),
        notes=account.notes,
        status=account.status,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _to_portfolio_out(
    portfolio: StrategyPortfolio,
    *,
    paper_account_name: str | None = None,
) -> StrategyPortfolioOut:
    return StrategyPortfolioOut(
        id=portfolio.id,
        paper_account_id=portfolio.paper_account_id,
        paper_account_name=paper_account_name,
        name=portfolio.name,
        description=portfolio.description,
        status=portfolio.status,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
    )


@router.get("/paper-accounts", response_model=list[PaperTradingAccountOut])
def get_paper_accounts(
    db: Session = Depends(get_db),
    status_filter: Optional[str] = Query(default=None, alias="status"),
):
    ensure_default_paper_account(db)
    ensure_default_strategy_portfolio(db)
    return [_to_account_out(item) for item in list_paper_accounts(db, status=status_filter)]


@router.post("/paper-accounts", response_model=PaperTradingAccountOut, status_code=status.HTTP_201_CREATED)
def create_paper_account(payload: PaperTradingAccountCreate, db: Session = Depends(get_db)):
    normalized_name = normalize_account_name(payload.name)
    existing = db.execute(
        select(PaperTradingAccount).where(PaperTradingAccount.name == normalized_name)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="paper account name already exists")

    account = PaperTradingAccount(
        name=normalized_name,
        broker=payload.broker,
        mode=payload.mode,
        api_key_env=payload.api_key_env.strip(),
        secret_key_env=payload.secret_key_env.strip(),
        base_url=payload.base_url.strip(),
        timeout_seconds=payload.timeout_seconds,
        notes=(payload.notes or "").strip() or None,
        status=payload.status,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return _to_account_out(account)


@router.get("/paper-accounts/{account_id}/overview", response_model=PaperTradingAccountOverviewOut)
def get_paper_account_overview(account_id: UUID, db: Session = Depends(get_db)):
    try:
        payload = build_paper_account_overview(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaperTradingAccountOverviewOut.model_validate(payload)


@router.get("/strategy-portfolios", response_model=list[StrategyPortfolioOut])
def get_strategy_portfolios(
    db: Session = Depends(get_db),
    paper_account_id: UUID | None = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
):
    ensure_default_paper_account(db)
    ensure_default_strategy_portfolio(db)
    rows = db.execute(
        select(StrategyPortfolio, PaperTradingAccount.name)
        .join(PaperTradingAccount, PaperTradingAccount.id == StrategyPortfolio.paper_account_id)
        .order_by(StrategyPortfolio.created_at.asc(), StrategyPortfolio.name.asc())
    ).all()

    items: list[StrategyPortfolioOut] = []
    for portfolio, account_name in rows:
        if paper_account_id is not None and portfolio.paper_account_id != paper_account_id:
            continue
        if status_filter is not None and portfolio.status != status_filter:
            continue
        items.append(_to_portfolio_out(portfolio, paper_account_name=account_name))
    return items


@router.post("/strategy-portfolios", response_model=StrategyPortfolioOut, status_code=status.HTTP_201_CREATED)
def create_strategy_portfolio(payload: StrategyPortfolioCreate, db: Session = Depends(get_db)):
    account = db.get(PaperTradingAccount, payload.paper_account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")

    existing = db.execute(
        select(StrategyPortfolio).where(StrategyPortfolio.name == payload.name.strip())
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="strategy portfolio name already exists; first version requires globally unique portfolio names",
        )

    portfolio = StrategyPortfolio(
        paper_account_id=payload.paper_account_id,
        name=payload.name.strip(),
        description=(payload.description or "").strip() or None,
        status=payload.status,
    )
    db.add(portfolio)
    db.flush()

    selected_strategy_ids = list(dict.fromkeys(payload.strategy_ids))
    if selected_strategy_ids:
        strategy_rows = db.execute(
            select(Strategy).where(Strategy.id.in_(selected_strategy_ids))
        ).scalars().all()
        found_ids = {strategy.id for strategy in strategy_rows}
        missing_ids = [str(item) for item in selected_strategy_ids if item not in found_ids]
        if missing_ids:
            raise HTTPException(
                status_code=404,
                detail=f"strategies not found: {', '.join(missing_ids)}",
            )

        equal_weight = 1.0 / len(selected_strategy_ids)
        allocation_rows: list[StrategyAllocation] = []
        for strategy_id in selected_strategy_ids:
            allocation = StrategyAllocation(
                strategy_id=strategy_id,
                portfolio_name=portfolio.name,
                allocation_pct=equal_weight,
                capital_base=None,
                allow_fractional=1,
                notes="Auto-created when strategy portfolio was initialized",
                status="active",
            )
            db.add(allocation)
            allocation_rows.append(allocation)

        try:
            validate_portfolio_allocations(allocation_rows)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.commit()
    db.refresh(portfolio)
    return _to_portfolio_out(portfolio, paper_account_name=account.name)


@router.patch("/strategy-portfolios/{portfolio_id}", response_model=StrategyPortfolioOut)
def update_strategy_portfolio(
    portfolio_id: UUID,
    payload: StrategyPortfolioRename,
    db: Session = Depends(get_db),
):
    try:
        portfolio = rename_strategy_portfolio(db, portfolio_id, name=payload.name)
    except ValueError as exc:
        message = str(exc)
        if message == "strategy portfolio not found":
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=409, detail=message) from exc

    account = db.get(PaperTradingAccount, portfolio.paper_account_id)
    return _to_portfolio_out(
        portfolio,
        paper_account_name=account.name if account is not None else None,
    )
