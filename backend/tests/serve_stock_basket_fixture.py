"""Disposable browser fixture: no real database, vendors, workers or broker calls.

PYTHONPATH=backend .venv/bin/python backend/tests/serve_stock_basket_fixture.py
Build the frontend with NEXT_PUBLIC_API_BASE_URL=http://localhost:18080, serve on 3103.
"""
from datetime import date, datetime, timezone
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api.stock_baskets import router as baskets
from src.api.stock_screening import router as screening
from src.core.db import get_db
from src.models.tables import Instrument, InstrumentMarketCap, StockBasket


def create_fixture_app():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (Instrument, InstrumentMarketCap, StockBasket):
        model.__table__.create(engine)
    symbols = [f"{index:06}.SZ" for index in range(1, 5556)]
    with Session(engine) as db:
        db.add_all([Instrument(id=index, ticker_canonical=ticker, share_class_figi=f"TEST:{ticker}",
            name=f"Fixture {ticker}", exchange="XSHE", currency="CNY", vendor_source="tushare",
            vendor_payload={"industry": "银行" if index % 2 else "科技"})
            for index, ticker in enumerate(symbols, 1)])
        db.add(Instrument(id=6000, ticker_canonical="AAPL", share_class_figi="TEST:AAPL", name="Apple fixture",
                          exchange="XNAS", currency="USD", vendor_source="massive", sic_description="Computers"))
        db.add_all([InstrumentMarketCap(instrument_id=index, amount=index * 100000000, currency="CNY",
            source="tushare", data_date=date(2026, 9, 2), retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            vendor_payload={"fixture": True}) for index in range(1, 5501)])
        db.add(StockBasket(name="QA 5555 — disposable", description="Browser fixture; no real data", symbols=symbols, status="draft"))
        db.commit()
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3103"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(baskets)
    app.include_router(screening)

    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    return app


if __name__ == "__main__":
    with patch("src.api.stock_baskets.ensure_default_common_stock_basket"):
        uvicorn.run(create_fixture_app(), host="127.0.0.1", port=18080, log_level="warning")
