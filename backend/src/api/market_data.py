from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.services.data_service import get_historical_data


class CandleBarOut(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


class CandleSeriesOut(BaseModel):
    symbol: str
    adjusted: bool
    start_date: date
    end_date: date
    bar_count: int
    bars: list[CandleBarOut]


router = APIRouter(prefix="/api/market-data", tags=["market-data"])


@router.get("/candles", response_model=CandleSeriesOut)
def get_candles(
    symbol: str = Query(..., min_length=1, max_length=32),
    start_date: date = Query(...),
    end_date: date = Query(...),
    adjusted: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    normalized_symbol = str(symbol).strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=422, detail="symbol is required")
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date cannot be after end_date")

    bars_by_symbol = get_historical_data(
        db,
        [normalized_symbol],
        start_date,
        end_date,
        adjusted=adjusted,
    )
    raw_bars = bars_by_symbol.get(normalized_symbol, [])

    bars = [
        CandleBarOut(
            trade_date=bar.trade_date,
            open=float(bar.open_px),
            high=float(bar.high_px),
            low=float(bar.low_px),
            close=float(bar.close_px),
            volume=bar.volume,
        )
        for bar in raw_bars
        if (
            bar.open_px is not None
            and bar.high_px is not None
            and bar.low_px is not None
            and bar.close_px is not None
        )
    ]

    return CandleSeriesOut(
        symbol=normalized_symbol,
        adjusted=adjusted,
        start_date=start_date,
        end_date=end_date,
        bar_count=len(bars),
        bars=bars,
    )
