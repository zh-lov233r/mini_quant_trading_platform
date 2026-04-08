from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.db import SessionLocal, get_db
from src.models.tables import PortfolioSnapshot, Signal, StockBasket, Strategy, StrategyRun, Transaction
from src.services.backtest_engine import BacktestResult, run_backtest
from src.services.data_service import get_historical_data
from src.services.stock_basket_service import DEFAULT_COMMON_STOCK_BASKET_NAME

NEW_YORK = ZoneInfo("America/New_York")
DISPLAY_COMPARISON_SYMBOLS = ("SPY", "QQQ")


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
    runtime_ms: Optional[int] = None
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
    comparison_curves: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    transactions: list[dict[str, Any]] = Field(default_factory=list)


def _serialize_snapshot(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    metrics = snapshot.metrics or {}
    return {
        "ts": snapshot.ts.isoformat() if snapshot.ts else None,
        "cash": float(snapshot.cash),
        "equity": float(snapshot.equity),
        "gross_exposure": float(snapshot.gross_exposure or 0),
        "net_exposure": float(snapshot.net_exposure or 0),
        "drawdown": float(snapshot.drawdown) if snapshot.drawdown is not None else None,
        "positions": snapshot.positions or {},
        "metrics": metrics,
        "benchmark_symbol": metrics.get("benchmark_symbol"),
        "benchmark_close": (
            float(metrics["benchmark_close"])
            if metrics.get("benchmark_close") is not None
            else None
        ),
        "benchmark_equity": (
            float(metrics["benchmark_equity"])
            if metrics.get("benchmark_equity") is not None
            else None
        ),
        "benchmark_return": (
            float(metrics["benchmark_return"])
            if metrics.get("benchmark_return") is not None
            else None
        ),
        "benchmark_excess_return": (
            float(metrics["benchmark_excess_return"])
            if metrics.get("benchmark_excess_return") is not None
            else None
        ),
    }


def _normalize_symbols(symbols: list[str | None]) -> list[str]:
    normalized_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol or normalized_symbol in seen:
            continue
        seen.add(normalized_symbol)
        normalized_symbols.append(normalized_symbol)
    return normalized_symbols


def _extract_cached_comparison_curves(summary_metrics: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw_curves = summary_metrics.get("comparison_curves")
    if not isinstance(raw_curves, dict):
        return {}

    cached_curves: dict[str, list[dict[str, Any]]] = {}
    for symbol, points in raw_curves.items():
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol or not isinstance(points, list):
            continue

        normalized_points: list[dict[str, Any]] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            ts = point.get("ts")
            close = point.get("close")
            equity = point.get("equity")
            curve_return = point.get("return")
            if not isinstance(ts, str):
                continue
            normalized_points.append(
                {
                    "ts": ts,
                    "symbol": normalized_symbol,
                    "close": float(close) if close is not None else None,
                    "equity": float(equity) if equity is not None else None,
                    "return": float(curve_return) if curve_return is not None else None,
                }
            )

        if normalized_points:
            cached_curves[normalized_symbol] = normalized_points

    return cached_curves


def _build_comparison_curves_from_bars(
    initial_cash: float | None,
    snapshots: list[PortfolioSnapshot],
    bars_by_symbol: dict[str, Any],
    symbols: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not initial_cash or initial_cash <= 0:
        return {}

    ordered_snapshots = [snapshot for snapshot in snapshots if snapshot.ts is not None]
    if not ordered_snapshots:
        return {}

    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        return {}

    curves: dict[str, list[dict[str, Any]]] = {}
    for symbol in normalized_symbols:
        bars = bars_by_symbol.get(symbol, [])
        close_by_date = {
            bar.trade_date: float(bar.close_px)
            for bar in bars
            if bar.close_px is not None
        }
        if not close_by_date:
            continue

        base_close: float | None = None
        last_close: float | None = None
        points: list[dict[str, Any]] = []
        for snapshot in ordered_snapshots:
            trade_date = snapshot.ts.astimezone(NEW_YORK).date()
            close = close_by_date.get(trade_date, last_close)
            if close is None:
                continue
            last_close = close
            if base_close is None:
                base_close = close
            if base_close <= 0:
                continue

            equity = float(initial_cash) * (close / base_close)
            points.append(
                {
                    "ts": snapshot.ts.isoformat(),
                    "symbol": symbol,
                    "close": close,
                    "equity": equity,
                    "return": (equity / float(initial_cash)) - 1,
                }
            )

        if points:
            curves[symbol] = points

    return curves


def _build_benchmark_snapshot_overrides(
    benchmark_symbol: str | None,
    initial_cash: float | None,
    snapshots: list[PortfolioSnapshot],
    curve_points: list[dict[str, Any]] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not benchmark_symbol or not initial_cash or initial_cash <= 0 or not snapshots or not curve_points:
        return {}, {}

    normalized_symbol = str(benchmark_symbol).strip().upper()
    if not normalized_symbol:
        return {}, {}

    ordered_snapshots = [snapshot for snapshot in snapshots if snapshot.ts is not None]
    if not ordered_snapshots:
        return {}, {}

    point_by_ts = {
        str(point["ts"]): point
        for point in curve_points
        if isinstance(point, dict) and isinstance(point.get("ts"), str)
    }
    if not point_by_ts:
        return {}, {}

    overrides: dict[str, dict[str, Any]] = {}
    benchmark_base_close = next(
        (
            float(point["close"])
            for point in curve_points
            if point.get("close") is not None
        ),
        None,
    )
    benchmark_last_close: float | None = None
    benchmark_last_equity: float | None = None
    benchmark_last_return: float | None = None
    benchmark_points = 0

    for snapshot in ordered_snapshots:
        snapshot_ts = snapshot.ts.isoformat()
        point = point_by_ts.get(snapshot_ts)
        if point is None:
            continue

        benchmark_close = float(point["close"]) if point.get("close") is not None else None
        benchmark_equity = float(point["equity"]) if point.get("equity") is not None else None
        benchmark_return = float(point["return"]) if point.get("return") is not None else None
        if benchmark_close is None or benchmark_equity is None or benchmark_return is None:
            continue

        strategy_return = (float(snapshot.equity) / float(initial_cash)) - 1
        overrides[snapshot_ts] = {
            "benchmark_symbol": normalized_symbol,
            "benchmark_close": benchmark_close,
            "benchmark_equity": benchmark_equity,
            "benchmark_return": benchmark_return,
            "benchmark_excess_return": strategy_return - benchmark_return,
        }
        benchmark_last_close = benchmark_close
        benchmark_last_equity = benchmark_equity
        benchmark_last_return = benchmark_return
        benchmark_points += 1

    summary = {
        "benchmark_symbol": normalized_symbol,
        "benchmark_points": benchmark_points,
        "benchmark_initial_close": benchmark_base_close,
        "benchmark_final_close": benchmark_last_close,
        "benchmark_final_equity": benchmark_last_equity,
        "benchmark_total_return": benchmark_last_return,
    }
    return overrides, summary


def _resolve_comparison_curves_and_summary(
    db: Session,
    run: StrategyRun,
    equity_curve: list[PortfolioSnapshot],
    summary_metrics: dict[str, Any],
    has_stored_benchmark: bool,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, dict[str, Any]], bool]:
    initial_cash = float(run.initial_cash) if run.initial_cash is not None else None
    updated_summary_metrics = dict(summary_metrics)
    benchmark_overrides: dict[str, dict[str, Any]] = {}
    cache_updated = False

    cached_comparison_curves = _extract_cached_comparison_curves(updated_summary_metrics)
    benchmark_symbol_normalized = _normalize_symbols([run.benchmark_symbol])[0] if run.benchmark_symbol else None
    display_symbols = list(DISPLAY_COMPARISON_SYMBOLS)
    required_symbols = _normalize_symbols([*display_symbols, benchmark_symbol_normalized])
    missing_symbols = [symbol for symbol in required_symbols if symbol not in cached_comparison_curves]

    computed_comparison_curves: dict[str, list[dict[str, Any]]] = {}
    ordered_snapshots = [snapshot for snapshot in equity_curve if snapshot.ts is not None]
    if missing_symbols and initial_cash and ordered_snapshots:
        start_date = ordered_snapshots[0].ts.astimezone(NEW_YORK).date()
        end_date = ordered_snapshots[-1].ts.astimezone(NEW_YORK).date()
        bars_by_symbol = get_historical_data(
            db,
            missing_symbols,
            start_date,
            end_date,
            adjusted=True,
        )
        computed_comparison_curves = _build_comparison_curves_from_bars(
            initial_cash,
            equity_curve,
            bars_by_symbol,
            missing_symbols,
        )
        if computed_comparison_curves:
            updated_summary_metrics["comparison_curves"] = {
                **cached_comparison_curves,
                **computed_comparison_curves,
            }
            cache_updated = True

    all_comparison_curves = {
        **cached_comparison_curves,
        **computed_comparison_curves,
    }
    comparison_curves = {
        symbol: all_comparison_curves[symbol]
        for symbol in display_symbols
        if symbol in all_comparison_curves
    }

    if run.benchmark_symbol and not has_stored_benchmark:
        benchmark_overrides, benchmark_summary = _build_benchmark_snapshot_overrides(
            run.benchmark_symbol,
            initial_cash,
            equity_curve,
            all_comparison_curves.get(benchmark_symbol_normalized),
        )
        for key, value in benchmark_summary.items():
            if updated_summary_metrics.get(key) != value:
                updated_summary_metrics[key] = value
                cache_updated = True
        if (
            updated_summary_metrics.get("benchmark_total_return") is not None
            and updated_summary_metrics.get("total_return") is not None
            and updated_summary_metrics.get("excess_return") is None
        ):
            updated_summary_metrics["excess_return"] = (
                float(updated_summary_metrics["total_return"])
                - float(updated_summary_metrics["benchmark_total_return"])
            )
            cache_updated = True

    return comparison_curves, updated_summary_metrics, benchmark_overrides, cache_updated


def _merge_benchmark_fields(
    serialized_snapshot: dict[str, Any],
    benchmark_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    if not benchmark_fields:
        return serialized_snapshot

    metrics = dict(serialized_snapshot.get("metrics") or {})
    merged = dict(serialized_snapshot)
    for key, value in benchmark_fields.items():
        merged[key] = value
        metrics[key] = value
    merged["metrics"] = metrics
    return merged


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
        runtime_ms=_compute_run_runtime_ms(run.started_at, run.finished_at),
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


def _compute_run_runtime_ms(
    started_at: datetime | None,
    finished_at: datetime | None,
) -> int | None:
    if started_at is None or finished_at is None:
        return None
    elapsed_ms = int((finished_at - started_at).total_seconds() * 1000)
    return max(elapsed_ms, 0)


def _to_backtest_detail_out(
    db: Session,
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
    summary_metrics = dict(dump.get("summary_metrics") or {})
    has_stored_benchmark = any(
        (snapshot.metrics or {}).get("benchmark_equity") is not None
        for snapshot in equity_curve
    )
    comparison_curves, summary_metrics, benchmark_overrides, cache_updated = _resolve_comparison_curves_and_summary(
        db,
        run,
        equity_curve,
        summary_metrics,
        has_stored_benchmark,
    )
    if cache_updated:
        run.summary_metrics = summary_metrics
        db.add(run)
        db.commit()

    serialized_equity_curve = [
        _merge_benchmark_fields(
            _serialize_snapshot(snapshot),
            benchmark_overrides.get(snapshot.ts.isoformat()) if snapshot.ts else None,
        )
        for snapshot in equity_curve
    ]
    serialized_latest_snapshot = (
        _merge_benchmark_fields(
            _serialize_snapshot(latest_snapshot),
            benchmark_overrides.get(latest_snapshot.ts.isoformat()) if latest_snapshot and latest_snapshot.ts else None,
        )
        if latest_snapshot is not None
        else None
    )
    return BacktestDetailOut(
        **{**dump, "summary_metrics": summary_metrics},
        latest_snapshot=serialized_latest_snapshot,
        transaction_count=transaction_count,
        equity_curve=serialized_equity_curve,
        comparison_curves=comparison_curves,
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
        db=db,
        run=run,
        strategy_name=strategy_name,
        latest_snapshot=latest_snapshot,
        transaction_count=int(transaction_count),
        equity_curve=equity_curve,
        signals=signals,
        transactions=transactions,
    )
