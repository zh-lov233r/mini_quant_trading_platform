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
    VirtualSubportfolioState,
    _execute_paper_orders,
    _rebuild_virtual_subportfolio_state,
    _submit_paper_order,
    _sync_strategy_pending_orders,
)
from src.services.signal_strength_service import annotate_and_rank_signals  # noqa: E402
from src.services.staged_entry_service import build_pattern_setup  # noqa: E402
from src.services.strategy_engine import SignalEvent  # noqa: E402
from src.services.strategy_registry import normalize_strategy_params  # noqa: E402


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
        self.assertEqual(
            (txn.meta or {}).get("entry_signal_features", {}).get("setup", {}).get("stage_index"),
            1,
        )

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

    def test_paper_orders_use_shared_strength_order_and_threshold(self) -> None:
        params = normalize_strategy_params(
            "double_bottom",
            {"risk": {"max_positions": 1, "position_size_pct": 0.5}},
        )
        runtime = {"strategy_type": "double_bottom", "params": params}
        signals = [
            self._strength_event("WEAK", 0.25),
            self._strength_event("STRONG", 0.80),
            self._strength_event("MEDIUM", 0.50),
        ]
        annotate_and_rank_signals(runtime, signals)
        client = StubAlpacaClient(
            submit_order_response=_make_order(
                order_id="paper-strength-order",
                symbol="STRONG",
                side="buy",
                qty=50,
                status="filled",
                filled_qty=50,
                filled_avg_price=10,
                filled_at="2026-04-14T20:00:05Z",
            )
        )

        outcomes, sleeve, _broker_cash = _execute_paper_orders(
            db=self.db,
            strategy=self.strategy,
            run=self.run,
            runtime=runtime,
            trade_date=date(2026, 4, 14),
            client=client,
            broker_cash=1000.0,
            sleeve_state=VirtualSubportfolioState(
                cash=1000.0,
                equity=1000.0,
                gross_exposure=0.0,
                net_exposure=0.0,
            ),
            allocation_cfg=VirtualSubportfolioConfig(
                portfolio_name="default",
                allocation_pct=1.0,
                capital_base=1000.0,
                allow_fractional=True,
                source="unit-test",
            ),
            open_orders=[],
            signals=signals,
            snapshots={symbol: {"close": 10.0} for symbol in ("WEAK", "STRONG", "MEDIUM")},
            submit_orders=True,
        )

        self.assertEqual(["STRONG", "MEDIUM", "WEAK"], [item.symbol for item in outcomes])
        self.assertEqual("submitted", outcomes[0].status)
        self.assertEqual("max_positions reached", outcomes[1].reason)
        self.assertEqual("signal strength below minimum threshold", outcomes[2].reason)
        self.assertEqual(1, len(client.submissions))
        self.assertEqual("STRONG", client.submissions[0]["symbol"])
        self.assertEqual(1, sleeve.long_position_count)
        self.assertEqual(1, outcomes[0].signal_strength["rank"])

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
        setup = build_pattern_setup(
            pattern_type="double_bottom",
            symbol="AAPL",
            stage_index=1,
            stage_key="second_bottom",
            risk_cfg={
                "stage_1_target_pct": 0.2,
                "stage_2_target_pct": 0.5,
                "stage_3_target_pct": 1.0,
            },
            anchors={"left_bottom_trade_date": "2026-04-10", "right_bottom_trade_date": "2026-04-14"},
            invalidation_price=9.0,
            setup_id_anchors=("2026-04-10", "2026-04-14"),
        )
        return SignalEvent(
            strategy_id=str(self.strategy.id),
            ts=datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc),
            symbol="AAPL",
            action="BUY",
            reason="unit-test buy",
            metadata={
                "position": 0,
                "setup": setup,
                "strength_inputs": {
                    "bottom_distance_pct": 0.015,
                    "rebound_up_day_ratio": 0.8,
                    "breakout_volume_ratio": 2.25,
                    "breakout_extension_pct": 0.0075,
                    "retest_volume_ratio": 0.4,
                },
            },
        )

    def _strength_event(self, symbol: str, normalized: float) -> SignalEvent:
        signal = normalize_strategy_params("double_bottom", {})["signal"]
        tolerance = signal["bottom_tolerance_pct"]
        rebound_minimum = signal["rebound_up_day_ratio_min"]
        volume_minimum = signal["breakout_volume_ratio_min"]
        breakout_buffer = signal["breakout_buffer_pct"]
        retest_maximum = signal["retest_volume_ratio_max"]
        return SignalEvent(
            strategy_id=str(self.strategy.id),
            ts=datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc),
            symbol=symbol,
            action="BUY",
            reason="paper strength ordering",
            metadata={
                "position": 0,
                "strength_inputs": {
                    "bottom_distance_pct": tolerance * (1.0 - normalized),
                    "rebound_up_day_ratio": rebound_minimum + ((1.0 - rebound_minimum) * normalized),
                    "breakout_volume_ratio": volume_minimum * (1.0 + normalized),
                    "breakout_extension_pct": breakout_buffer * (1.0 + normalized),
                    "retest_volume_ratio": retest_maximum * (1.0 - normalized),
                },
            },
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
