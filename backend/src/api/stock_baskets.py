from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.models.tables import StockBasket
from src.services.stock_basket_service import ensure_default_common_stock_basket


class StockBasketCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="股票组合名称")
    description: Optional[str] = Field(default=None, max_length=500, description="组合说明")
    symbols: list[str] = Field(..., min_length=1, description="股票代码列表")
    status: Literal["draft", "active", "archived"] = "active"


class StockBasketOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    symbols: list[str]
    status: str
    symbol_count: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


router = APIRouter(prefix="/api/stock-baskets", tags=["stock-baskets"])


def _normalize_symbols(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    if not normalized:
        raise ValueError("symbols must contain at least one non-empty ticker")
    return normalized


def _to_stock_basket_out(item: StockBasket) -> StockBasketOut:
    symbols = _normalize_symbols(list(item.symbols or []))
    return StockBasketOut(
        id=item.id,
        name=item.name,
        description=item.description,
        symbols=symbols,
        status=item.status,
        symbol_count=len(symbols),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("", response_model=list[StockBasketOut])
def list_stock_baskets(
    db: Session = Depends(get_db),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    ensure_default_common_stock_basket(db)
    stmt = select(StockBasket).order_by(StockBasket.created_at.desc())
    if status_filter:
        stmt = stmt.where(StockBasket.status == status_filter)
    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return [_to_stock_basket_out(row) for row in rows]


@router.post("", response_model=StockBasketOut, status_code=status.HTTP_201_CREATED)
def create_stock_basket(payload: StockBasketCreate, db: Session = Depends(get_db)):
    try:
        symbols = _normalize_symbols(payload.symbols)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing = db.execute(
        select(StockBasket).where(StockBasket.name == payload.name.strip())
    ).scalars().first()
    if existing:
        if (
            existing.description == (payload.description or None)
            and list(existing.symbols or []) == symbols
            and existing.status == payload.status
        ):
            return _to_stock_basket_out(existing)
        raise HTTPException(status_code=409, detail="stock basket name already exists")

    item = StockBasket(
        name=payload.name.strip(),
        description=(payload.description or "").strip() or None,
        symbols=symbols,
        status=payload.status,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_stock_basket_out(item)


@router.get("/{basket_id}", response_model=StockBasketOut)
def get_stock_basket(basket_id: UUID, db: Session = Depends(get_db)):
    item = db.get(StockBasket, basket_id)
    if item is None:
        raise HTTPException(status_code=404, detail="stock basket not found")
    return _to_stock_basket_out(item)
