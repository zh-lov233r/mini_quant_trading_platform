from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.models.tables import PaperTradingAccount, StrategyAllocation, StrategyPortfolio
from src.services.paper_trading_service import (
    MultiStrategyPaperTradingResult,
    run_multi_strategy_paper_trading,
)


NEW_YORK = ZoneInfo("America/New_York")
PAPER_TRADING_TRIGGER_ACTIVATION = "activation"


@dataclass(slots=True)
class PortfolioActivationResult:
    portfolio: StrategyPortfolio
    trade_date: date
    execution: MultiStrategyPaperTradingResult


def latest_ready_paper_trading_trade_date(
    db: Session,
    *,
    max_trade_date: date | None = None,
) -> date | None:
    target_trade_date = max_trade_date or datetime.now(NEW_YORK).date()
    return db.execute(
        text(
            """
            SELECT MAX(ready_dates.trade_date)
            FROM (
                SELECT e.dt_ny AS trade_date
                FROM eod_bars e
                LEFT JOIN daily_features f
                  ON f.instrument_id = e.instrument_id
                 AND f.dt_ny = e.dt_ny
                WHERE e.dt_ny <= :max_trade_date
                GROUP BY e.dt_ny
                HAVING COUNT(*) > 0
                   AND COUNT(f.instrument_id) = COUNT(*)
            ) AS ready_dates
            """
        ),
        {"max_trade_date": target_trade_date},
    ).scalar()


def run_live_paper_trading_for_portfolio(
    db: Session,
    portfolio_id: UUID | str,
    *,
    trigger: str = PAPER_TRADING_TRIGGER_ACTIVATION,
) -> PortfolioActivationResult:
    portfolio = db.get(StrategyPortfolio, _normalize_uuid(portfolio_id))
    if portfolio is None:
        raise ValueError("strategy portfolio not found")

    account = db.get(PaperTradingAccount, portfolio.paper_account_id)
    if account is None:
        raise ValueError("paper account not found")
    if account.status != "active":
        raise ValueError("paper account must be active before live paper trading can run")
    if portfolio.status != "active":
        raise ValueError("strategy portfolio must be active before live paper trading can run")

    latest_trade_date = latest_ready_paper_trading_trade_date(db)
    if latest_trade_date is None:
        raise ValueError("no fully ready daily_features trade date is available for live paper trading")

    execution = run_multi_strategy_paper_trading(
        db,
        latest_trade_date,
        portfolio_name=portfolio.name,
        submit_orders=True,
        continue_on_error=False,
        trigger=trigger,
    )
    db.refresh(portfolio)
    return PortfolioActivationResult(
        portfolio=portfolio,
        trade_date=latest_trade_date,
        execution=execution,
    )


def activate_portfolio_for_live_trading(
    db: Session,
    portfolio_id: UUID | str,
) -> PortfolioActivationResult:
    portfolio = db.get(StrategyPortfolio, _normalize_uuid(portfolio_id))
    if portfolio is None:
        raise ValueError("strategy portfolio not found")
    if portfolio.status == "active":
        raise ValueError("strategy portfolio already active")

    allocations = db.execute(
        select(StrategyAllocation).where(StrategyAllocation.portfolio_name == portfolio.name)
    ).scalars().all()
    if not allocations:
        raise ValueError("no strategy allocations found for portfolio")

    portfolio.status = "active"
    for allocation in allocations:
        allocation.status = "active"
    db.flush()

    return run_live_paper_trading_for_portfolio(
        db,
        portfolio.id,
        trigger=PAPER_TRADING_TRIGGER_ACTIVATION,
    )


def _normalize_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
