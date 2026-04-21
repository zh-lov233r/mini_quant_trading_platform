from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.strategies import delete_strategy  # noqa: E402
from src.models.tables import (  # noqa: E402
    Base,
    PaperTradingAccount,
    Strategy,
    StrategyAllocation,
    StrategyPortfolio,
    Transaction,
)
from src.services.strategy_delete_service import (  # noqa: E402
    STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX,
    StrategyDeleteBrokerPosition,
    close_strategy_delete_broker_positions,
    inspect_strategy_delete_broker_positions,
)


class StubDeleteAlpacaClient:
    def __init__(
        self,
        *,
        positions: list[dict] | None = None,
        submit_order_response: dict | None = None,
        get_order_responses: dict[str, dict] | None = None,
    ) -> None:
        self.positions = positions or []
        self.submit_order_response = submit_order_response or {}
        self.get_order_responses = get_order_responses or {}
        self.submissions: list[dict] = []

    def list_positions(self):
        return [dict(item) for item in self.positions]

    def submit_order(self, **kwargs):
        self.submissions.append(kwargs)
        return dict(self.submit_order_response)

    def get_order(self, order_id: str, *, nested: bool | None = None):
        return dict(self.get_order_responses[order_id])


def _make_order(
    *,
    order_id: str,
    symbol: str,
    side: str,
    qty: float,
    status: str,
    filled_qty: float | None = None,
) -> dict[str, str]:
    payload = {
        "id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": str(qty),
        "status": status,
    }
    if filled_qty is not None:
        payload["filled_qty"] = str(filled_qty)
    return payload


class StrategyDeleteFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, future=True)
        self.db: Session = self.session_factory()

        self.account = PaperTradingAccount(
            id=uuid4(),
            name="delete-test-account",
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
            name="delete-test-portfolio",
            status="active",
        )
        self.strategy = Strategy(
            id=uuid4(),
            strategy_key="delete-flow",
            name="Delete Flow Strategy",
            strategy_type="double_bottom",
            params={"risk": {}, "signal": {}, "universe": {"symbols": ["AAPL"]}},
            status="active",
            version=1,
        )
        self.allocation = StrategyAllocation(
            id=uuid4(),
            strategy_id=self.strategy.id,
            portfolio_name=self.portfolio.name,
            allocation_pct=1.0,
            status="active",
        )
        self.transaction = Transaction(
            id=uuid4(),
            strategy_id=self.strategy.id,
            run_id=None,
            ts=datetime(2026, 4, 21, 18, 0, tzinfo=timezone.utc),
            symbol="AAPL",
            side="BUY",
            qty=5,
            price=10,
            fee=0,
            order_id="local-buy-order",
            meta={
                "source": "alpaca_paper",
                "portfolio_name": self.portfolio.name,
                "client_order_id": "paper-delete-test-port-deadbeef-20260421-AAPL-buy",
                "broker_status": "filled",
                "paper_fill_applied": True,
                "filled_qty": 5,
            },
        )
        self.db.add_all(
            [
                self.account,
                self.portfolio,
                self.strategy,
                self.allocation,
                self.transaction,
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_inspect_strategy_delete_broker_positions_returns_closable_holdings(self) -> None:
        client = StubDeleteAlpacaClient(
            positions=[
                {
                    "symbol": "AAPL",
                    "side": "long",
                    "qty": "5",
                }
            ]
        )

        with patch(
            "src.services.strategy_delete_service.build_alpaca_client_for_account",
            return_value=client,
        ):
            positions = inspect_strategy_delete_broker_positions(self.db, self.strategy.id)

        self.assertEqual(len(positions), 1)
        position = positions[0]
        self.assertEqual(position.paper_account_name, self.account.name)
        self.assertEqual(position.symbol, "AAPL")
        self.assertAlmostEqual(position.strategy_qty, 5.0)
        self.assertAlmostEqual(position.broker_qty, 5.0)
        self.assertAlmostEqual(position.close_qty or 0.0, 5.0)
        self.assertEqual(position.close_side, "sell")
        self.assertTrue(position.can_close)
        self.assertEqual(position.portfolio_names, (self.portfolio.name,))

    def test_close_strategy_delete_broker_positions_submits_cleanup_order(self) -> None:
        client = StubDeleteAlpacaClient(
            submit_order_response=_make_order(
                order_id="cleanup-order-1",
                symbol="AAPL",
                side="sell",
                qty=5,
                status="new",
            ),
            get_order_responses={
                "cleanup-order-1": _make_order(
                    order_id="cleanup-order-1",
                    symbol="AAPL",
                    side="sell",
                    qty=5,
                    status="filled",
                    filled_qty=5,
                )
            },
        )
        broker_position = StrategyDeleteBrokerPosition(
            paper_account_id=self.account.id,
            paper_account_name=self.account.name,
            symbol="AAPL",
            portfolio_names=(self.portfolio.name,),
            strategy_qty=5.0,
            broker_qty=5.0,
            close_qty=5.0,
            close_side="sell",
        )

        with patch(
            "src.services.strategy_delete_service.build_alpaca_client_for_account",
            return_value=client,
        ):
            orders = close_strategy_delete_broker_positions(
                self.db,
                self.strategy.id,
                broker_positions=[broker_position],
            )

        self.assertEqual(len(orders), 1)
        self.assertEqual(len(client.submissions), 1)
        self.assertEqual(client.submissions[0]["symbol"], "AAPL")
        self.assertEqual(client.submissions[0]["side"], "sell")
        self.assertEqual(client.submissions[0]["qty"], 5.0)
        self.assertTrue(
            str(client.submissions[0]["client_order_id"]).startswith("paper-cleanup-")
        )

    def test_delete_strategy_requires_confirmation_when_positions_exist(self) -> None:
        broker_position = StrategyDeleteBrokerPosition(
            paper_account_id=self.account.id,
            paper_account_name=self.account.name,
            symbol="AAPL",
            portfolio_names=(self.portfolio.name,),
            strategy_qty=5.0,
            broker_qty=5.0,
            close_qty=5.0,
            close_side="sell",
        )

        with patch(
            "src.api.strategies.inspect_strategy_delete_broker_positions",
            return_value=[broker_position],
        ):
            with self.assertRaises(HTTPException) as captured:
                delete_strategy(self.strategy.id, close_positions=False, db=self.db)

        self.assertEqual(captured.exception.status_code, 409)
        self.assertIn(STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX, str(captured.exception.detail))
        self.assertIsNotNone(self.db.get(Strategy, self.strategy.id))

    def test_delete_strategy_closes_positions_then_deletes_when_confirmed(self) -> None:
        broker_position = StrategyDeleteBrokerPosition(
            paper_account_id=self.account.id,
            paper_account_name=self.account.name,
            symbol="AAPL",
            portfolio_names=(self.portfolio.name,),
            strategy_qty=5.0,
            broker_qty=5.0,
            close_qty=5.0,
            close_side="sell",
        )

        with patch(
            "src.api.strategies.inspect_strategy_delete_broker_positions",
            return_value=[broker_position],
        ), patch(
            "src.api.strategies.close_strategy_delete_broker_positions",
            return_value=[{"id": "cleanup-order-1"}],
        ) as mock_close:
            result = delete_strategy(self.strategy.id, close_positions=True, db=self.db)

        self.assertEqual(result.strategy_id, self.strategy.id)
        self.assertEqual(result.deleted_transactions, 1)
        self.assertEqual(result.deleted_allocations, 1)
        self.assertIsNone(self.db.get(Strategy, self.strategy.id))
        mock_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
