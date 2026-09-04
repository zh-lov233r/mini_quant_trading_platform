from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.services.stock_screening_service import (
    StockFilters, StockSearch, StockSearchResult, industry_options, matching_symbols, screen_stocks,
)

router = APIRouter(prefix="/api/stock-screening", tags=["stock-screening"])


def screening_db(db: Session = Depends(get_db)):
    if not inspect(db.connection()).has_table("instrument_market_caps"):
        raise HTTPException(503, "Stock screening schema is not installed; apply create_instrument_market_caps.sql after approval")
    return db


@router.get("/stocks", response_model=StockSearchResult)
def search_stocks(filters: Annotated[StockSearch, Query()], db: Session = Depends(screening_db)):
    return screen_stocks(db, filters)


@router.get("/industries", response_model=list[str])
def get_industries(market: Literal["US", "CN"], db: Session = Depends(screening_db)):
    return industry_options(db, market)


@router.post("/symbols", response_model=list[str])
def resolve_symbols(filters: StockFilters, db: Session = Depends(screening_db)):
    """Read-only resolution; never saves a basket or contacts a vendor."""
    return matching_symbols(db, filters)
