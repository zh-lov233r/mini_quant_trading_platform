import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.stock_baskets import StockBasketCreate, update_stock_basket
from src.models.tables import StockBasket


class StockBasketUpdateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        StockBasket.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.item = StockBasket(name="Original", symbols=["AAPL"], status="active")
        self.db.add(self.item)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_update_preserves_identity_and_normalizes_symbols(self):
        original_id = self.item.id
        result = update_stock_basket(original_id, StockBasketCreate(
            name=" Revised ", description="  Watchlist  ", symbols=[" msft ", "MSFT", "600000.sh"], status="draft",
        ), self.db)
        self.assertEqual(result.id, original_id)
        self.assertEqual(result.name, "Revised")
        self.assertEqual(result.symbols, ["MSFT", "600000.SH"])
        self.assertEqual(result.symbol_count, 2)
        self.assertEqual(result.description, "Watchlist")
        self.assertEqual(self.db.get(StockBasket, original_id).status, "draft")

    def test_rejects_duplicate_missing_and_empty_without_changes(self):
        self.db.add(StockBasket(name="Taken", symbols=["MSFT"], status="active"))
        self.db.commit()
        for identity, payload, expected in [
            (self.item.id, dict(name="Taken", symbols=["MSFT"]), 409),
            (uuid4(), dict(name="Missing", symbols=["MSFT"]), 404),
            (self.item.id, dict(name="Empty", symbols=[" "]), 422),
        ]:
            with self.subTest(expected=expected), self.assertRaises(HTTPException) as raised:
                update_stock_basket(identity, StockBasketCreate(**payload), self.db)
            self.assertEqual(raised.exception.status_code, expected)
        self.assertEqual(self.item.name, "Original")
        with self.assertRaises(ValidationError):
            StockBasketCreate(name=" ", symbols=["AAPL"])

    def test_system_managed_basket_cannot_be_overwritten(self):
        self.item.name = "All Common Stock"
        self.db.commit()
        with self.assertRaises(HTTPException) as raised:
            update_stock_basket(self.item.id, StockBasketCreate(name="Renamed", symbols=["MSFT"]), self.db)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.item.symbols, ["AAPL"])

    def test_conflicting_commit_rolls_back(self):
        db = MagicMock()
        db.get.return_value = self.item
        db.execute.return_value.scalars.return_value.first.return_value = None
        db.commit.side_effect = IntegrityError("update", {}, Exception("conflict"))
        with self.assertRaises(HTTPException) as raised:
            update_stock_basket(self.item.id, StockBasketCreate(name="Changed", symbols=["MSFT"]), db)
        self.assertEqual(raised.exception.status_code, 409)
        db.rollback.assert_called_once()
