from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.tables import Base, Strategy, StrategyRun, Transaction  # noqa: E402
from src.services.paper_account_service import _transaction_net_cash_flow  # noqa: E402
from src.services.paper_trading_service import (  # noqa: E402
    VirtualSubportfolioConfig,
    _rebuild_virtual_subportfolio_state,
    _submit_paper_order,
    _sync_strategy_pending_orders,
)
from src.services.strategy_engine import SignalEvent  # noqa: E402


class StubAlpacaClient:
    def __init__(
        self,
        *,
        submit_order_response: dict | None = None,
        get_order_responses: dict[str, dict] | None = None,
    ) -> None:
        self.submit_order_response = submit_order_response or {}
        self.get_order_responses = get_order_responses or {}
        self.submissions: list[dict] = []

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
    filled_qty: float = 0.0,
    filled_avg_price: float | None = None,
    submitted_at: str = "2026-04-14T20:00:00Z",
    updated_at: str | None = None,
    filled_at: str | None = None,
) -> dict[str, str]:
    payload = {
        "id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": str(qty),
        "status": status,
        "filled_qty": str(filled_qty),
        "submitted_at": submitted_at,
    }
    if updated_at is not None:
        payload["updated_at"] = updated_at
    if filled_at is not None:
        payload["filled_at"] = filled_at
    if filled_avg_price is not None:
        payload["filled_avg_price"] = str(filled_avg_price)
    return payload


class PaperTradingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, future=True)
        self.db: Session = self.session_factory()
        self.strategy, self.run = self._create_strategy_and_run()

    def tearDown(self) -> None:
        self.db.close()

    def test_submit_order_without_fill_keeps_virtual_state_unchanged(self) -> None:
        client = StubAlpacaClient(
            submit_order_response=_make_order(
                order_id="order-accepted",
                symbol="AAPL",
                side="buy",
                qty=5,
                status="accepted",
            )
        )

        outcome = _submit_paper_order(
            db=self.db,
            strategy=self.strategy,
            run=self.run,
            trade_date=date(2026, 4, 14),
            client=client,
            event=self._buy_event(),
            submit_orders=True,
            qty=5,
            reference_price=10,
            client_order_id="client-order-accepted",
            portfolio_name="default",
            allocation_pct=1.0,
        )

        txn = self._single_transaction()
        self.assertEqual(outcome.status, "submitted")
        self.assertEqual(outcome.qty, 5)
        self.assertEqual(outcome.filled_qty, 0.0)
        self.assertIsNone(outcome.execution_price)
        self.assertEqual(float(txn.qty), 5.0)
        self.assertEqual(float(txn.price), 0.0)
        self.assertEqual((txn.meta or {}).get("broker_status"), "accepted")
        self.assertFalse((txn.meta or {}).get("paper_fill_applied"))
        self.assertEqual(_transaction_net_cash_flow(txn), 0.0)

        sleeve = self._rebuild_state(capital_base=1000.0)
        self.assertEqual(sleeve.cash, 1000.0)
        self.assertEqual(sleeve.equity, 1000.0)
        self.assertEqual(sleeve.positions_by_symbol, {})

    def test_submit_order_with_immediate_fill_updates_virtual_ledger(self) -> None:
        client = StubAlpacaClient(
            submit_order_response=_make_order(
                order_id="order-filled",
                symbol="AAPL",
                side="buy",
                qty=5,
                status="filled",
                filled_qty=5,
                filled_avg_price=10,
                filled_at="2026-04-14T20:00:05Z",
            )
        )

        with self.assertLogs("paper_trading", level="INFO") as captured:
            outcome = _submit_paper_order(
                db=self.db,
                strategy=self.strategy,
                run=self.run,
                trade_date=date(2026, 4, 14),
                client=client,
                event=self._buy_event(),
                submit_orders=True,
                qty=5,
                reference_price=10,
                client_order_id="client-order-filled",
                portfolio_name="default",
                allocation_pct=1.0,
            )

        txn = self._single_transaction()
        self.assertEqual(outcome.filled_qty, 5.0)
        self.assertEqual(outcome.execution_price, 10.0)
        self.assertTrue((txn.meta or {}).get("paper_fill_applied"))
        self.assertEqual(float(txn.qty), 5.0)
        self.assertEqual(float(txn.price), 10.0)
        self.assertEqual(_transaction_net_cash_flow(txn), -50.0)

        sleeve = self._rebuild_state(capital_base=1000.0)
        self.assertAlmostEqual(sleeve.cash, 950.0)
        self.assertAlmostEqual(sleeve.equity, 1000.0)
        self.assertIn("AAPL", sleeve.positions_by_symbol)
        self.assertAlmostEqual(sleeve.positions_by_symbol["AAPL"].qty, 5.0)
        self.assertAlmostEqual(sleeve.positions_by_symbol["AAPL"].avg_entry_price, 10.0)
        self.assertTrue(
            any(
                "Paper trading transaction event=submitted" in message
                and "client_order_id=client-order-filled" in message
                and "broker_status=filled" in message
                for message in captured.output
            )
        )

    def test_sync_pending_order_promotes_fill_without_duplicate_transaction(self) -> None:
        submit_client = StubAlpacaClient(
            submit_order_response=_make_order(
                order_id="order-later-fill",
                symbol="AAPL",
                side="buy",
                qty=5,
                status="accepted",
            )
        )
        _submit_paper_order(
            db=self.db,
            strategy=self.strategy,
            run=self.run,
            trade_date=date(2026, 4, 14),
            client=submit_client,
            event=self._buy_event(),
            submit_orders=True,
            qty=5,
            reference_price=10,
            client_order_id="client-order-later-fill",
            portfolio_name="default",
            allocation_pct=1.0,
        )

        sync_client = StubAlpacaClient(
            get_order_responses={
                "order-later-fill": _make_order(
                    order_id="order-later-fill",
                    symbol="AAPL",
                    side="buy",
                    qty=5,
                    status="filled",
                    filled_qty=3,
                    filled_avg_price=11,
                    submitted_at="2026-04-14T20:00:00Z",
                    filled_at="2026-04-14T20:02:00Z",
                )
            }
        )
        with self.assertLogs("paper_trading", level="INFO") as captured:
            _sync_strategy_pending_orders(
                self.db,
                strategy_id=self.strategy.id,
                portfolio_name="default",
                client=sync_client,
            )

        transactions = self.db.execute(select(Transaction)).scalars().all()
        self.assertEqual(len(transactions), 1)
        txn = transactions[0]
        self.assertTrue((txn.meta or {}).get("paper_fill_applied"))
        self.assertEqual((txn.meta or {}).get("broker_status"), "filled")
        self.assertEqual(float(txn.qty), 3.0)
        self.assertEqual(float(txn.price), 11.0)
        self.assertEqual(_transaction_net_cash_flow(txn), -33.0)

        sleeve = self._rebuild_state(capital_base=1000.0, current_price=11.0)
        self.assertAlmostEqual(sleeve.cash, 967.0)
        self.assertAlmostEqual(sleeve.equity, 1000.0)
        self.assertIn("AAPL", sleeve.positions_by_symbol)
        self.assertAlmostEqual(sleeve.positions_by_symbol["AAPL"].qty, 3.0)
        self.assertTrue(
            any(
                "Paper trading transaction event=reconciled" in message
                and "order_id=order-later-fill" in message
                and "broker_status=filled" in message
                for message in captured.output
            )
        )

    def test_legacy_pending_alpaca_rows_do_not_count_as_fills(self) -> None:
        legacy_txn = Transaction(
            id=uuid4(),
            strategy_id=self.strategy.id,
            run_id=self.run.id,
            ts=datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc),
            symbol="AAPL",
            side="BUY",
            qty=5,
            price=10,
            fee=0,
            order_id="legacy-pending",
            meta={
                "source": "alpaca_paper",
                "portfolio_name": "default",
                "broker_status": "accepted",
                "filled_qty": 0,
                "reference_price": 10,
            },
        )
        self.db.add(legacy_txn)
        self.db.commit()

        sleeve = self._rebuild_state(capital_base=1000.0)
        self.assertEqual(_transaction_net_cash_flow(legacy_txn), 0.0)
        self.assertEqual(sleeve.cash, 1000.0)
        self.assertEqual(sleeve.positions_by_symbol, {})

    def _create_strategy_and_run(self) -> tuple[Strategy, StrategyRun]:
        strategy = Strategy(
            id=uuid4(),
            strategy_key="unit-paper-trading",
            name="Unit Paper Trading",
            strategy_type="double_bottom",
            params={"risk": {}, "signal": {}, "universe": {"symbols": ["AAPL"]}},
            status="active",
            version=1,
        )
        self.db.add(strategy)
        self.db.commit()

        run = StrategyRun(
            id=uuid4(),
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode="paper",
            status="running",
            started_at=datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc),
            window_start=date(2026, 4, 14),
            window_end=date(2026, 4, 14),
            config_snapshot={},
            summary_metrics={},
        )
        self.db.add(run)
        self.db.commit()
        return strategy, run

    def _buy_event(self) -> SignalEvent:
        return SignalEvent(
            strategy_id=str(self.strategy.id),
            ts=datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc),
            symbol="AAPL",
            action="BUY",
            reason="unit-test buy",
            metadata={"setup": "unit"},
        )

    def _single_transaction(self) -> Transaction:
        return self.db.execute(select(Transaction)).scalars().one()

    def _rebuild_state(
        self,
        *,
        capital_base: float,
        current_price: float = 10.0,
    ):
        return _rebuild_virtual_subportfolio_state(
            self.db,
            self.strategy.id,
            VirtualSubportfolioConfig(
                portfolio_name="default",
                allocation_pct=1.0,
                capital_base=capital_base,
                allow_fractional=True,
                source="unit-test",
            ),
            {"AAPL": current_price},
        )


if __name__ == "__main__":
    unittest.main()
