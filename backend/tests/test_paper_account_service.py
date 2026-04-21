from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.tables import Base, PaperTradingAccount, Strategy, StrategyPortfolio, Transaction  # noqa: E402
from src.services.paper_account_service import build_broker_account_isolation_report  # noqa: E402


class PaperAccountIsolationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, future=True)
        self.db: Session = self.session_factory()

        self.account = PaperTradingAccount(
            id=uuid4(),
            name="unit-paper-account",
            broker="alpaca",
            mode="paper",
            api_key_env="ALPACA_API_KEY",
            secret_key_env="ALPACA_SECRET_KEY",
            base_url="https://paper-api.alpaca.markets",
            timeout_seconds=20,
            status="active",
        )
        self.portfolio = StrategyPortfolio(
            id=uuid4(),
            paper_account_id=self.account.id,
            name="unit live portfolio",
            status="active",
        )
        self.strategy = Strategy(
            id=uuid4(),
            strategy_key="unit-isolation",
            name="Unit Isolation",
            strategy_type="double_bottom",
            params={"risk": {}, "signal": {}, "universe": {"symbols": ["AAPL"]}},
            status="active",
            version=1,
        )
        self.db.add_all([self.account, self.portfolio, self.strategy])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_report_warns_for_recent_external_orders_without_active_conflict(self) -> None:
        self._add_local_transaction(
            symbol="AAPL",
            side="BUY",
            qty=5.0,
            order_id="local-order-1",
            client_order_id="paper-unit-live-portfolio-deadbeef-20260414-AAPL-buy",
            broker_status="filled",
            fill_applied=True,
        )

        report = build_broker_account_isolation_report(
            self.db,
            self.account,
            raw_positions=[],
            raw_orders=[
                {
                    "id": "local-order-1",
                    "client_order_id": "paper-unit-live-portfolio-deadbeef-20260414-AAPL-buy",
                    "symbol": "AAPL",
                    "side": "buy",
                    "status": "filled",
                },
                {
                    "id": "external-order-1",
                    "client_order_id": "aed882e8-dff4-47c9-ac5d-f9816dfe6753",
                    "symbol": "AAPL",
                    "side": "sell",
                    "status": "filled",
                },
            ],
        )

        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["recent_external_order_count"], 1)
        self.assertEqual(report["active_external_order_count"], 0)
        self.assertFalse(report["active_external_position_count"])
        self.assertEqual(report["orders"][0]["origin"], "local_system")
        self.assertEqual(report["orders"][1]["origin"], "external_api")

    def test_report_blocks_on_untracked_open_orders_and_qty_mismatches(self) -> None:
        self._add_local_transaction(
            symbol="AAPL",
            side="BUY",
            qty=5.0,
            order_id="local-order-2",
            client_order_id="paper-unit-live-portfolio-feedface-20260414-AAPL-buy",
            broker_status="filled",
            fill_applied=True,
        )

        report = build_broker_account_isolation_report(
            self.db,
            self.account,
            raw_positions=[
                {
                    "symbol": "AAPL",
                    "side": "long",
                    "qty": "3",
                    "current_price": "10",
                }
            ],
            raw_orders=[
                {
                    "id": "system-but-missing",
                    "client_order_id": "paper-unit-live-portfolio-cafebabe-20260414-MSFT-buy",
                    "symbol": "MSFT",
                    "side": "buy",
                    "status": "new",
                }
            ],
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["active_system_untracked_order_count"], 1)
        self.assertEqual(report["position_mismatch_count"], 1)
        self.assertEqual(report["orders"][0]["origin"], "system_untracked")
        self.assertEqual(report["orders"][0]["portfolio_name"], self.portfolio.name)
        self.assertEqual(report["positions"][0]["origin"], "qty_mismatch")
        self.assertAlmostEqual(report["positions"][0]["qty_delta"], -2.0)

    def test_report_ignores_system_cleanup_orders_from_delete_flow(self) -> None:
        report = build_broker_account_isolation_report(
            self.db,
            self.account,
            raw_positions=[],
            raw_orders=[
                {
                    "id": "cleanup-order-1",
                    "client_order_id": "paper-cleanup-deadbeef-AAPL-sell-20260421T101530",
                    "symbol": "AAPL",
                    "side": "sell",
                    "status": "filled",
                }
            ],
        )

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["recent_external_order_count"], 0)
        self.assertEqual(report["recent_system_untracked_order_count"], 0)
        self.assertEqual(report["orders"][0]["origin"], "system_cleanup")
        self.assertTrue(report["orders"][0]["managed_by_system"])

    def test_report_blocks_when_cleanup_order_is_still_open(self) -> None:
        report = build_broker_account_isolation_report(
            self.db,
            self.account,
            raw_positions=[],
            raw_orders=[
                {
                    "id": "cleanup-order-open",
                    "client_order_id": "paper-cleanup-deadbeef-AAPL-sell-20260421T101531",
                    "symbol": "AAPL",
                    "side": "sell",
                    "status": "new",
                }
            ],
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["active_cleanup_order_count"], 1)
        self.assertEqual(report["orders"][0]["origin"], "system_cleanup")

    def _add_local_transaction(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        order_id: str,
        client_order_id: str,
        broker_status: str,
        fill_applied: bool,
    ) -> None:
        self.db.add(
            Transaction(
                id=uuid4(),
                strategy_id=self.strategy.id,
                run_id=None,
                ts=datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc),
                symbol=symbol,
                side=side,
                qty=qty,
                price=10,
                fee=0,
                order_id=order_id,
                meta={
                    "source": "alpaca_paper",
                    "portfolio_name": self.portfolio.name,
                    "client_order_id": client_order_id,
                    "broker_status": broker_status,
                    "paper_fill_applied": fill_applied,
                    "filled_qty": qty if fill_applied else 0,
                },
            )
        )
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
