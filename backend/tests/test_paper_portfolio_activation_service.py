from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.tables import (  # noqa: E402
    Base,
    PaperTradingAccount,
    Strategy,
    StrategyAllocation,
    StrategyPortfolio,
)
from src.services.paper_portfolio_activation_service import (  # noqa: E402
    PAPER_TRADING_TRIGGER_ACTIVATION,
    activate_portfolio_for_live_trading,
    run_live_paper_trading_for_portfolio,
)
from src.services.paper_trading_service import (  # noqa: E402
    MultiStrategyPaperTradingResult,
    PaperTradingResult,
)


class PaperPortfolioActivationServiceTests(unittest.TestCase):
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
        self.strategy = Strategy(
            id=uuid4(),
            strategy_key="unit-portfolio-activation",
            name="Unit Portfolio Activation",
            strategy_type="double_bottom",
            params={"risk": {}, "signal": {}, "universe": {"symbols": ["AAPL"]}},
            status="active",
            version=1,
        )
        self.portfolio = StrategyPortfolio(
            id=uuid4(),
            paper_account_id=self.account.id,
            name="unit-live-portfolio",
            status="archived",
        )
        self.allocation = StrategyAllocation(
            id=uuid4(),
            strategy_id=self.strategy.id,
            portfolio_name=self.portfolio.name,
            allocation_pct=1.0,
            allow_fractional=1,
            auto_run_enabled=True,
            status="archived",
        )
        self.db.add_all([self.account, self.strategy, self.portfolio, self.allocation])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_activate_portfolio_flips_status_and_runs_live_orders(self) -> None:
        expected_result = self._multi_result(self.portfolio.name, date(2026, 4, 21))

        with (
            patch(
                "src.services.paper_portfolio_activation_service.latest_ready_paper_trading_trade_date",
                return_value=date(2026, 4, 21),
            ),
            patch(
                "src.services.paper_portfolio_activation_service.run_multi_strategy_paper_trading",
                return_value=expected_result,
            ) as run_mock,
        ):
            result = activate_portfolio_for_live_trading(self.db, str(self.portfolio.id))

        refreshed_portfolio = self.db.get(StrategyPortfolio, self.portfolio.id)
        refreshed_allocation = self.db.execute(
            select(StrategyAllocation).where(StrategyAllocation.id == self.allocation.id)
        ).scalars().one()

        self.assertEqual(refreshed_portfolio.status, "active")
        self.assertEqual(refreshed_allocation.status, "active")
        self.assertEqual(result.trade_date, date(2026, 4, 21))
        self.assertEqual(result.execution.portfolio_name, self.portfolio.name)

        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["portfolio_name"], self.portfolio.name)
        self.assertTrue(kwargs["submit_orders"])
        self.assertFalse(kwargs["continue_on_error"])
        self.assertEqual(kwargs["trigger"], PAPER_TRADING_TRIGGER_ACTIVATION)

    def test_run_live_paper_trading_requires_ready_trade_date(self) -> None:
        self.portfolio.status = "active"
        self.allocation.status = "active"
        self.db.commit()

        with patch(
            "src.services.paper_portfolio_activation_service.latest_ready_paper_trading_trade_date",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "no fully ready daily_features trade date is available for live paper trading",
            ):
                run_live_paper_trading_for_portfolio(self.db, str(self.portfolio.id))

    def _multi_result(
        self,
        portfolio_name: str,
        trade_date: date,
    ) -> MultiStrategyPaperTradingResult:
        return MultiStrategyPaperTradingResult(
            portfolio_name=portfolio_name,
            trade_date=trade_date,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            results=[
                PaperTradingResult(
                    run_id=str(uuid4()),
                    strategy_id=str(self.strategy.id),
                    status="completed",
                    trade_date=trade_date,
                    portfolio_name=portfolio_name,
                    allocation_pct=1.0,
                    capital_base=1000.0,
                    signal_count=1,
                    order_count=1,
                    submitted_order_count=1,
                    skipped_order_count=0,
                    failed_order_count=0,
                    final_cash=900.0,
                    final_equity=1000.0,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
