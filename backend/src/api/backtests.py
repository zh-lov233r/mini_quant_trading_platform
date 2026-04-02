from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.db import SessionLocal, get_db
from src.models.tables import PortfolioSnapshot, Signal, StockBasket, Strategy, StrategyRun, Transaction
from src.services.backtest_engine import BacktestResult, run_backtest
from src.services.stock_basket_service import DEFAULT_COMMON_STOCK_BASKET_NAME


class BacktestCreate(BaseModel):
    strategy_id: UUID = Field(..., description="策略 ID")
    basket_id: Optional[UUID] = Field(default=None, description="股票组合 ID，用于覆盖策略 universe")
    start_date: date = Field(..., description="回测开始日期")
    end_date: date = Field(..., description="回测结束日期")
    initial_cash: float = Field(default=100_000.0, gt=0, description="初始资金")
    benchmark_symbol: Optional[str] = Field(default=None, description="对标基准，如 SPY")
    commission_bps: Optional[float] = Field(default=None, ge=0)
    commission_min: Optional[float] = Field(default=None, ge=0)
    slippage_bps: Optional[float] = Field(default=None, ge=0)


class BacktestRunOut(BaseModel):
    id: UUID
    strategy_id: UUID
    strategy_name: Optional[str] = None
    basket_id: Optional[str] = None
    basket_name: Optional[str] = None
    strategy_version: int
    mode: str
    status: str
    requested_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    initial_cash: Optional[float] = None
    final_equity: Optional[float] = None
    benchmark_symbol: Optional[str] = None
    summary_metrics: dict[str, Any]
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BacktestDetailOut(BacktestRunOut):
    latest_snapshot: Optional[dict[str, Any]] = None
    transaction_count: int
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    transactions: list[dict[str, Any]] = Field(default_factory=list)


def _serialize_snapshot(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    return {
        "ts": snapshot.ts.isoformat() if snapshot.ts else None,
        "cash": float(snapshot.cash),
        "equity": float(snapshot.equity),
        "gross_exposure": float(snapshot.gross_exposure or 0),
        "net_exposure": float(snapshot.net_exposure or 0),
        "drawdown": float(snapshot.drawdown) if snapshot.drawdown is not None else None,
        "positions": snapshot.positions or {},
        "metrics": snapshot.metrics or {},
    }


def _serialize_transaction(txn: Transaction) -> dict[str, Any]:
    return {
        "id": str(txn.id),
        "ts": txn.ts.isoformat() if txn.ts else None,
        "symbol": txn.symbol,
        "side": txn.side,
        "qty": float(txn.qty),
        "price": float(txn.price),
        "fee": float(txn.fee or 0),
        "order_id": txn.order_id,
        "meta": txn.meta or {},
    }


def _serialize_signal(signal: Signal) -> dict[str, Any]:
    return {
        "id": str(signal.id),
        "ts": signal.ts.isoformat() if signal.ts else None,
        "symbol": signal.symbol,
        "signal": signal.signal,
        "score": float(signal.score) if signal.score is not None else None,
        "reason": signal.reason,
        "features": signal.features or {},
    }


router = APIRouter(prefix="/api/backtests", tags=["backtests"])


def _run_backtest_in_background(
    run_id: UUID,
    strategy_id: UUID,
    start_date: date,
    end_date: date,
    initial_cash: float,
    benchmark_symbol: str | None,
    commission_bps: float | None,
    commission_min: float | None,
    slippage_bps: float | None,
    basket_symbols: list[str] | None,
    basket_metadata: dict[str, Any] | None,
) -> None:
    db = SessionLocal()
    try:
        run_backtest(
            db=db,
            strategy_id=strategy_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            benchmark_symbol=benchmark_symbol,
            commission_bps=commission_bps,
            commission_min=commission_min,
            slippage_bps=slippage_bps,
            universe_symbols=basket_symbols,
            universe_metadata=basket_metadata,
            existing_run_id=run_id,
        )
    except Exception:
        # The backtest service marks the run as failed when execution raises.
        db.rollback()
    finally:
        db.close()


def _to_backtest_run_out(run: StrategyRun, strategy_name: str | None = None) -> BacktestRunOut:
    universe = (run.config_snapshot or {}).get("universe") or {}
    basket = universe.get("basket") if isinstance(universe, dict) else {}
    selection_mode = universe.get("selection_mode") if isinstance(universe, dict) else None
    default_label = universe.get("default_label") if isinstance(universe, dict) else None
    return BacktestRunOut(
        id=run.id,
        strategy_id=run.strategy_id,
        strategy_name=strategy_name or getattr(run.strategy, "name", None),
        basket_id=str(basket.get("id")) if isinstance(basket, dict) and basket.get("id") else None,
        basket_name=(
            str(basket.get("name"))
            if isinstance(basket, dict) and basket.get("name")
            else (
                str(default_label)
                if selection_mode == "all_common_stock" and default_label
                else (DEFAULT_COMMON_STOCK_BASKET_NAME if selection_mode == "all_common_stock" else None)
            )
        ),
        strategy_version=run.strategy_version,
        mode=run.mode,
        status=run.status,
        requested_at=run.requested_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        window_start=run.window_start,
        window_end=run.window_end,
        initial_cash=float(run.initial_cash) if run.initial_cash is not None else None,
        final_equity=float(run.final_equity) if run.final_equity is not None else None,
        benchmark_symbol=run.benchmark_symbol,
        summary_metrics=run.summary_metrics or {},
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _to_backtest_detail_out(
    run: StrategyRun,
    strategy_name: str | None,
    latest_snapshot: PortfolioSnapshot | None,
    transaction_count: int,
    equity_curve: list[PortfolioSnapshot],
    signals: list[Signal],
    transactions: list[Transaction],
) -> BacktestDetailOut:
    base = _to_backtest_run_out(run, strategy_name)
    dump = base.model_dump() if hasattr(base, "model_dump") else base.dict()
    return BacktestDetailOut(
        **dump,
        latest_snapshot=_serialize_snapshot(latest_snapshot) if latest_snapshot is not None else None,
        transaction_count=transaction_count,
        equity_curve=[_serialize_snapshot(snapshot) for snapshot in equity_curve],
        signals=[_serialize_signal(signal) for signal in signals],
        transactions=[_serialize_transaction(txn) for txn in transactions],
    )


@router.post("", response_model=BacktestRunOut, status_code=status.HTTP_201_CREATED)
def create_backtest(
    payload: BacktestCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    basket = None
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

    run = StrategyRun(
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        mode="backtest",
        status="queued",
        window_start=payload.start_date,
        window_end=payload.end_date,
        initial_cash=payload.initial_cash,
        benchmark_symbol=payload.benchmark_symbol,
        config_snapshot={
            "submit_payload": {
                "basket_id": str(payload.basket_id) if payload.basket_id else None,
                "basket_name": basket.name if basket is not None else None,
                "commission_bps": payload.commission_bps,
                "commission_min": payload.commission_min,
                "slippage_bps": payload.slippage_bps,
            }
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(
        _run_backtest_in_background,
        run.id,
        strategy.id,
        payload.start_date,
        payload.end_date,
        payload.initial_cash,
        payload.benchmark_symbol,
        payload.commission_bps,
        payload.commission_min,
        payload.slippage_bps,
        basket_symbols,
        basket_metadata,
    )

    return _to_backtest_run_out(run, strategy.name)


@router.get("", response_model=list[BacktestRunOut])
def list_backtests(
    db: Session = Depends(get_db),
    strategy_id: Optional[UUID] = Query(default=None),
    mode: Optional[str] = Query(default="backtest"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(StrategyRun, Strategy.name).join(Strategy, Strategy.id == StrategyRun.strategy_id)

    if strategy_id:
        stmt = stmt.where(StrategyRun.strategy_id == strategy_id)
    if mode:
        stmt = stmt.where(StrategyRun.mode == mode)
    if status_filter:
        stmt = stmt.where(StrategyRun.status == status_filter)

    stmt = stmt.order_by(StrategyRun.requested_at.desc(), StrategyRun.created_at.desc())
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    return [_to_backtest_run_out(run, strategy_name) for run, strategy_name in rows]


@router.get("/{run_id}", response_model=BacktestDetailOut)
def get_backtest(run_id: UUID, db: Session = Depends(get_db)):
    row = db.execute(
        select(StrategyRun, Strategy.name)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == run_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="backtest not found")

    run, strategy_name = row
    latest_snapshot = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.ts.desc())
        .limit(1)
    ).scalars().first()
    equity_curve = db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.run_id == run_id)
        .order_by(PortfolioSnapshot.ts.asc())
    ).scalars().all()
    signals = db.execute(
        select(Signal)
        .where(Signal.run_id == run_id)
        .order_by(Signal.ts.asc(), Signal.symbol.asc())
    ).scalars().all()
    transactions = db.execute(
        select(Transaction)
        .where(Transaction.run_id == run_id)
        .order_by(Transaction.ts.desc())
    ).scalars().all()
    transaction_count = db.execute(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.run_id == run_id)
    ).scalar_one()

    return _to_backtest_detail_out(
        run=run,
        strategy_name=strategy_name,
        latest_snapshot=latest_snapshot,
        transaction_count=int(transaction_count),
        equity_curve=equity_curve,
        signals=signals,
        transactions=transactions,
    )
