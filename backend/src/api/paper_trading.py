from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.models.tables import StockBasket, Strategy
from src.services.paper_trading_service import (
    MultiStrategyPaperTradingResult,
    PaperTradingResult,
    PAPER_TRADING_TRIGGER_MANUAL,
    run_multi_strategy_paper_trading,
    run_paper_trading,
)


class PaperTradingRunRequest(BaseModel):
    strategy_id: UUID
    trade_date: date
    portfolio_name: str = Field(default="default", min_length=1, max_length=64)
    basket_id: UUID | None = None
    submit_orders: bool = True


class MultiStrategyPaperTradingRunRequest(BaseModel):
    trade_date: date
    portfolio_name: str = Field(default="default", min_length=1, max_length=64)
    submit_orders: bool = True
    continue_on_error: bool = False


class PaperTradingRunOut(BaseModel):
    run_id: str
    strategy_id: str
    status: str
    trade_date: date
    portfolio_name: str
    allocation_pct: float
    capital_base: float
    signal_count: int
    order_count: int
    submitted_order_count: int
    skipped_order_count: int
    failed_order_count: int
    final_cash: float
    final_equity: float


class MultiStrategyPaperTradingRunOut(BaseModel):
    portfolio_name: str
    trade_date: date
    total_runs: int
    completed_runs: int
    failed_runs: int
    results: list[PaperTradingRunOut]


class LatestPaperTradingTradeDateOut(BaseModel):
    latest_trade_date: date | None = None


router = APIRouter(prefix="/api/paper-trading", tags=["paper-trading"])


def _to_run_out(result: PaperTradingResult) -> PaperTradingRunOut:
    return PaperTradingRunOut(
        run_id=result.run_id,
        strategy_id=result.strategy_id,
        status=result.status,
        trade_date=result.trade_date,
        portfolio_name=result.portfolio_name,
        allocation_pct=result.allocation_pct,
        capital_base=result.capital_base,
        signal_count=result.signal_count,
        order_count=result.order_count,
        submitted_order_count=result.submitted_order_count,
        skipped_order_count=result.skipped_order_count,
        failed_order_count=result.failed_order_count,
        final_cash=result.final_cash,
        final_equity=result.final_equity,
    )


def _to_multi_run_out(result: MultiStrategyPaperTradingResult) -> MultiStrategyPaperTradingRunOut:
    return MultiStrategyPaperTradingRunOut(
        portfolio_name=result.portfolio_name,
        trade_date=result.trade_date,
        total_runs=result.total_runs,
        completed_runs=result.completed_runs,
        failed_runs=result.failed_runs,
        results=[_to_run_out(item) for item in result.results],
    )


@router.get("/latest-trade-date", response_model=LatestPaperTradingTradeDateOut)
def get_latest_paper_trading_trade_date(db: Session = Depends(get_db)):
    latest_trade_date = db.execute(text("SELECT max(dt_ny) FROM daily_features")).scalar()
    return LatestPaperTradingTradeDateOut(latest_trade_date=latest_trade_date)


@router.post("/run", response_model=PaperTradingRunOut, status_code=status.HTTP_201_CREATED)
def create_paper_trading_run(payload: PaperTradingRunRequest, db: Session = Depends(get_db)):
    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")

    basket_symbols = None
    basket_metadata = None
    if payload.basket_id is not None:
        basket = db.get(StockBasket, payload.basket_id)
        if basket is None:
            raise HTTPException(status_code=404, detail="stock basket not found")
        basket_symbols = list(basket.symbols or [])
        if not basket_symbols:
            raise HTTPException(status_code=422, detail="stock basket is empty")
        basket_metadata = {
            "id": str(basket.id),
            "name": basket.name,
            "description": basket.description,
            "status": basket.status,
            "symbol_count": len(basket_symbols),
        }

    try:
        result = run_paper_trading(
            db,
            payload.strategy_id,
            payload.trade_date,
            submit_orders=payload.submit_orders,
            universe_symbols=basket_symbols,
            universe_metadata=basket_metadata,
            portfolio_name=payload.portfolio_name,
            trigger=PAPER_TRADING_TRIGGER_MANUAL,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"paper trading failed: {exc}") from exc

    return _to_run_out(result)


@router.post("/run-multi", response_model=MultiStrategyPaperTradingRunOut, status_code=status.HTTP_201_CREATED)
def create_multi_strategy_paper_trading_run(
    payload: MultiStrategyPaperTradingRunRequest,
    db: Session = Depends(get_db),
):
    try:
        result = run_multi_strategy_paper_trading(
            db,
            payload.trade_date,
            portfolio_name=payload.portfolio_name,
            submit_orders=payload.submit_orders,
            continue_on_error=payload.continue_on_error,
            trigger=PAPER_TRADING_TRIGGER_MANUAL,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"multi-strategy paper trading failed: {exc}") from exc

    return _to_multi_run_out(result)
