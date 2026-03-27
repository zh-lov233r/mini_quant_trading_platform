from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.models.tables import StockBasket


DEFAULT_COMMON_STOCK_BASKET_NAME = "All Common Stock"
DEFAULT_COMMON_STOCK_BASKET_DESCRIPTION = "系统默认股票组合：当前全部 active US common stocks。"


def load_default_common_stock_symbols(db: Session) -> list[str]:
    rows = db.execute(
        select(StockBasket.symbols).where(StockBasket.name == DEFAULT_COMMON_STOCK_BASKET_NAME)
    ).first()
    if rows and rows[0]:
        return [str(symbol).strip().upper() for symbol in list(rows[0]) if str(symbol).strip()]
    return []


def ensure_default_common_stock_basket(db: Session) -> StockBasket:
    symbols = _query_current_common_stock_symbols(db)
    existing = db.execute(
        select(StockBasket).where(StockBasket.name == DEFAULT_COMMON_STOCK_BASKET_NAME)
    ).scalars().first()

    if existing is not None:
        changed = False
        if existing.description != DEFAULT_COMMON_STOCK_BASKET_DESCRIPTION:
            existing.description = DEFAULT_COMMON_STOCK_BASKET_DESCRIPTION
            changed = True
        if list(existing.symbols or []) != symbols:
            existing.symbols = symbols
            changed = True
        if existing.status != "active":
            existing.status = "active"
            changed = True
        if changed:
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing

    basket = StockBasket(
        name=DEFAULT_COMMON_STOCK_BASKET_NAME,
        description=DEFAULT_COMMON_STOCK_BASKET_DESCRIPTION,
        symbols=symbols,
        status="active",
    )
    db.add(basket)
    db.commit()
    db.refresh(basket)
    return basket


def _query_current_common_stock_symbols(db: Session) -> list[str]:
    symbols = db.execute(
        text(
            """
        SELECT DISTINCT ticker_canonical
        FROM instruments
        WHERE ticker_canonical IS NOT NULL
          AND asset_type = 'CS'
          AND market = 'stocks'
          AND locale = 'us'
          AND is_active = TRUE
        ORDER BY ticker_canonical
        """
        )
    ).scalars().all()
    return [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
