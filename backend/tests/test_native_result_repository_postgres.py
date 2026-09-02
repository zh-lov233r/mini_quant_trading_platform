from __future__ import annotations

from copy import deepcopy
from datetime import date
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.models.tables import (
    Base,
    Instrument,
    PortfolioSnapshot,
    Signal,
    Strategy,
    StrategyRun,
    Transaction,
)
from src.services import native_result_repository
from src.services.native_result_repository import (
    NativePersistenceCancelledError,
    persist_native_result,
)
from backend.tests.test_native_result_repository import _result


POSTGRES_URL = os.getenv("QUANT_COPY_TEST_DATABASE_URL", "")


@unittest.skipUnless(
    POSTGRES_URL,
    "set QUANT_COPY_TEST_DATABASE_URL to run the isolated PostgreSQL COPY tests",
)
class NativeResultPostgresIntegrationTests(unittest.TestCase):
    """Exercise psycopg COPY only against the dedicated disposable test database."""

    @classmethod
    def setUpClass(cls) -> None:
        url = make_url(POSTGRES_URL)
        if (
            url.drivername != "postgresql+psycopg"
            or url.host not in {"127.0.0.1", "localhost"}
            or url.database != "quant_cpp_copy_test"
        ):
            raise RuntimeError(
                "QUANT_COPY_TEST_DATABASE_URL must use postgresql+psycopg, "
                "a loopback host, and database quant_cpp_copy_test"
            )
        cls.engine = create_engine(url, pool_pre_ping=True)
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        self.db = Session(self.engine)
        suffix = uuid4().hex
        self.instrument = Instrument(
            share_class_figi=f"BBG{suffix}",
            ticker_canonical="AAA",
            exchange="XNYS",
            asset_type="CS",
            currency="USD",
            market="stocks",
        )
        self.strategy = Strategy(
            strategy_key=f"native-copy-{suffix}",
            name="native-copy",
            strategy_type="trend",
            params={},
            status="active",
        )
        self.db.add_all([self.instrument, self.strategy])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _run(self) -> StrategyRun:
        run = StrategyRun(
            strategy_id=self.strategy.id,
            strategy_version=1,
            mode="backtest",
            status="running",
            window_start=date(2025, 1, 1),
            window_end=date(2025, 1, 3),
            initial_cash=1_000,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _result(self, *, reason: str = "native buy", signal_count: int = 1):
        result = deepcopy(_result(signal_reason=reason))
        instrument_id = int(self.instrument.id)
        result.trades["instrument_id"] = [instrument_id]
        if signal_count == 1:
            result.signals["instrument_id"] = [instrument_id]
            return result
        base_timestamp = int(result.signals["timestamp_us"][0])
        for name, values in list(result.signals.items()):
            value = values[0]
            result.signals[name] = [value] * signal_count
        result.signals["timestamp_us"] = [
            base_timestamp + index for index in range(signal_count)
        ]
        result.signals["instrument_id"] = [instrument_id] * signal_count
        return result

    def _counts(self, run_id) -> tuple[int, int, int]:
        return (
            self.db.scalar(
                select(func.count()).select_from(Signal).where(Signal.run_id == run_id)
            ),
            self.db.scalar(
                select(func.count()).select_from(Transaction).where(Transaction.run_id == run_id)
            ),
            self.db.scalar(
                select(func.count())
                .select_from(PortfolioSnapshot)
                .where(PortfolioSnapshot.run_id == run_id)
            ),
        )

    def test_copy_supports_all_persist_levels_and_idempotent_retry(self) -> None:
        expected = {
            "full": (1, 1, 2),
            "trades": (0, 1, 2),
            "summary": (0, 0, 2),
        }
        for level, counts in expected.items():
            with self.subTest(level=level):
                run = self._run()
                persist_native_result(
                    self.db,
                    run_id=run.id,
                    strategy_id=self.strategy.id,
                    result=self._result(reason=level),
                    persist_level=level,
                )
                self.db.commit()
                self.assertEqual(self._counts(run.id), counts)
                persist_native_result(
                    self.db,
                    run_id=run.id,
                    strategy_id=self.strategy.id,
                    result=self._result(reason=f"{level}-retry"),
                    persist_level=level,
                )
                self.db.commit()
                self.assertEqual(self._counts(run.id), counts)

    def test_second_copy_batch_cancellation_rolls_back_replacement(self) -> None:
        run = self._run()
        persist_native_result(
            self.db,
            run_id=run.id,
            strategy_id=self.strategy.id,
            result=self._result(reason="existing"),
            persist_level="full",
        )
        self.db.commit()
        callback_count = 0

        def cancel_before_second_signal_batch() -> bool:
            nonlocal callback_count
            callback_count += 1
            return callback_count == 3

        with self.assertRaises(NativePersistenceCancelledError):
            persist_native_result(
                self.db,
                run_id=run.id,
                strategy_id=self.strategy.id,
                result=self._result(reason="replacement", signal_count=5_001),
                persist_level="full",
                cancel_check=cancel_before_second_signal_batch,
            )
        self.db.rollback()
        self.assertEqual(self._counts(run.id), (1, 1, 2))
        self.assertEqual(
            self.db.scalar(select(Signal.reason).where(Signal.run_id == run.id)),
            "existing",
        )

    def test_failure_after_first_real_copy_rolls_back_and_can_retry(self) -> None:
        run = self._run()
        persist_native_result(
            self.db,
            run_id=run.id,
            strategy_id=self.strategy.id,
            result=self._result(reason="existing"),
            persist_level="full",
        )
        self.db.commit()
        real_copy = native_result_repository._copy_rows
        copy_count = 0

        def copy_then_fail(*args, **kwargs):
            nonlocal copy_count
            real_copy(*args, **kwargs)
            copy_count += 1
            if copy_count == 1:
                raise RuntimeError("injected failure after signal COPY")

        with patch.object(native_result_repository, "_copy_rows", side_effect=copy_then_fail):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                persist_native_result(
                    self.db,
                    run_id=run.id,
                    strategy_id=self.strategy.id,
                    result=self._result(reason="failed replacement"),
                    persist_level="full",
                )
        self.db.rollback()
        self.assertEqual(self._counts(run.id), (1, 1, 2))

        persist_native_result(
            self.db,
            run_id=run.id,
            strategy_id=self.strategy.id,
            result=self._result(reason="successful retry"),
            persist_level="full",
        )
        self.db.commit()
        self.assertEqual(
            self.db.scalar(select(Signal.reason).where(Signal.run_id == run.id)),
            "successful retry",
        )


if __name__ == "__main__":
    unittest.main()
