import unittest
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api.stock_screening import screening_db
from src.services.stock_screening_service import StockFilters, StockSearch, screen_stocks, matching_symbols, industry_options
from src.models.tables import Instrument, InstrumentMarketCap
from utils.refresh_stock_market_caps import normalize_cap, store_snapshot


class StockScreeningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Instrument.__table__.create(self.engine)
        InstrumentMarketCap.__table__.create(self.engine)
        self.db = Session(self.engine)
        for identity, ticker, vendor, industry, cap, active in [
            (1, "AAPL", "massive", "Computers", 200000000, True),
            (2, "MSFT", "massive", "Software", None, True),
            (3, "600000.SH", "tushare", "银行", 300000000, True),
            (4, "OLD", "massive", "Computers", 100000000, False),
            (5, "A_B", "massive", "Computers", 100000000, True),
        ]:
            currency = "CNY" if vendor == "tushare" else "USD"
            self.db.add(Instrument(id=identity, share_class_figi=f"id-{identity}", ticker_canonical=ticker,
                                   exchange="XNAS", name=f"Name {ticker}", currency=currency, locale="us",
                                   vendor_source=vendor, sic_description=industry if vendor == "massive" else None,
                                   vendor_payload={"industry": industry} if vendor == "tushare" else {}, is_active=active))
            if cap:
                self.db.add(InstrumentMarketCap(instrument_id=identity, amount=cap, currency=currency,
                    source=vendor, data_date=date(2026, 9, 2), retrieved_at=datetime.now(timezone.utc), vendor_payload={}))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_search_pagination_literal_query_and_all_resolution(self):
        result = screen_stocks(self.db, StockSearch(market="US", limit=1)).model_dump(mode="json")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["missing_market_cap"], 1)
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL"])
        self.assertEqual(result["items"][0]["cap_data_date"], "2026-09-02")
        all_symbols = matching_symbols(self.db, StockFilters(market="US"))
        self.assertEqual(all_symbols, ["AAPL", "A_B", "MSFT"])
        result = screen_stocks(self.db, StockSearch(query="_")).model_dump()
        self.assertEqual([item["ticker"] for item in result["items"]], ["A_B"])

    def test_industry_currency_missing_and_inclusive_cap_bounds(self):
        self.assertEqual(industry_options(self.db, "CN"), ["银行"])
        result = screen_stocks(self.db, StockSearch(market="US", min_cap=200000000, max_cap=200000000)).model_dump()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["missing_market_cap"], 1)
        self.assertEqual(result["items"][0]["ticker"], "AAPL")
        result = screen_stocks(self.db, StockSearch(market="CN", industry="银行")).model_dump()
        self.assertEqual(result["items"][0]["industry_source"], "Tushare")
        self.assertEqual(result["items"][0]["currency"], "CNY")
        self.assertEqual(result["items"][0]["market_cap"], 300000000)

    def test_validates_boundaries_and_reports_missing_schema(self):
        for params in [{"min_cap": 1}, {"industry": "Computers"}, {"limit": 101}, {"offset": -1},
                       {"market": "US", "min_cap": 10, "max_cap": 1}, {"market": "US", "min_cap": "NaN"}]:
            with self.subTest(params=params), self.assertRaises(ValidationError):
                StockSearch(**params)
        self.db.rollback()
        InstrumentMarketCap.__table__.drop(self.engine)
        with self.assertRaises(HTTPException) as raised:
            screening_db(self.db)
        self.assertEqual(raised.exception.status_code, 503)


class MarketCapSnapshotTests(unittest.TestCase):
    def test_units_identity_missing_and_currency(self):
        now = datetime.now(timezone.utc)
        cn = SimpleNamespace(vendor_source="tushare", ticker_canonical="600000.SH", currency="CNY")
        payload = {"ts_code": "600000.SH", "trade_date": "20260902", "total_mv": "12345.67"}
        args = dict(data_date=date(2026, 9, 2), retrieved_at=now)
        self.assertEqual(normalize_cap(cn, payload, **args)["amount"], Decimal("123456700"))
        for change in [{"ts_code": "000001.SZ"}, {"trade_date": "20260901"}, {"total_mv": None},
                       {"total_mv": "NaN"}, {"total_mv": -1}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_cap(cn, {**payload, **change}, **args)
        us = SimpleNamespace(vendor_source="massive", ticker_canonical="AAPL", currency="USD", share_class_figi="figi")
        payload = dict(ticker="AAPL", share_class_figi="figi", currency_name="usd", market_cap=123456789)
        self.assertEqual(normalize_cap(us, payload, **args)["amount"], Decimal("123456789"))
        for change in [{"share_class_figi": "different"}, {"share_class_figi": None}, {"currency_name": "cny"}]:
            with self.assertRaises(ValueError):
                normalize_cap(us, {**payload, **change}, **args)

    def test_idempotency_and_older_or_invalid_input_preserve_snapshot(self):
        engine = create_engine("sqlite://")
        InstrumentMarketCap.__table__.create(engine)
        now = datetime.now(timezone.utc)
        values = dict(amount=Decimal("100"), currency="USD", source="massive", data_date=date(2026, 9, 2),
                      retrieved_at=now, vendor_payload={"market_cap": 100})
        with Session(engine) as db:
            self.assertTrue(store_snapshot(db, 1, values))
            db.commit()
            self.assertFalse(store_snapshot(db, 1, {**values, "retrieved_at": now + timedelta(seconds=1)}))
            self.assertFalse(store_snapshot(db, 1, {**values, "data_date": date(2026, 9, 1), "amount": Decimal("50")}))
            self.assertFalse(store_snapshot(db, 1, {**values, "data_date": None, "amount": Decimal("50")}))
            self.assertEqual(db.get(InstrumentMarketCap, 1).amount, Decimal("100"))
        engine.dispose()
