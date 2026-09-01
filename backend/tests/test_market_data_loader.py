from __future__ import annotations

from datetime import date
import time
import unittest

from src.services.market_data_loader import MarketDataLoader


class _Result:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def mappings(self):
        self.calls.append("mappings")
        return self

    def fetchmany(self, size: int):
        self.calls.append(f"fetchmany:{size}")
        if self.calls.count(f"fetchmany:{size}") == 1:
            return [{"trade_date": date(2025, 1, 2), "symbol": "TEST"}]
        return []


class _Connection:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.dialect = type("Dialect", (), {"name": "postgresql"})()

    def exec_driver_sql(self, statement: str, **options) -> None:
        self.calls.append(f"driver:{statement}:{options}")

    def execution_options(self, **options):
        self.calls.append(f"options:{options}")
        return self

    def execute(self, _statement, _params):
        self.calls.append("execute")
        return _Result(self.calls)

    def close(self) -> None:
        self.calls.append("close")


class MarketDataLoaderTests(unittest.TestCase):
    def test_postgres_read_only_transaction_precedes_server_side_streaming(self) -> None:
        calls: list[str] = []
        connection = _Connection(calls)
        engine = type("Engine", (), {"connect": lambda _self: connection})()
        session = type("Session", (), {"get_bind": lambda _self: engine})()
        performance: dict[str, float] = {}
        loader = MarketDataLoader(
            session,
            statement=object(),
            params={},
            row_factory=lambda row: (
                row["trade_date"],
                row["symbol"],
                {"instrument_id": 1},
            ),
            performance=performance,
            fetch_size=5000,
        )

        self.assertEqual(
            list(loader.iter_days()),
            [(date(2025, 1, 2), {"TEST": {"instrument_id": 1, "history_sessions": 1}})],
        )
        self.assertLess(
            calls.index("driver:SET TRANSACTION READ ONLY:{'execution_options': {'stream_results': False}}"),
            calls.index("options:{'stream_results': True}"),
        )
        self.assertLess(
            calls.index("options:{'stream_results': False}"),
            calls.index("driver:SET TRANSACTION READ ONLY:{'execution_options': {'stream_results': False}}"),
        )
        self.assertEqual(calls[-1], "close")
        self.assertIn("load_market_data_ms", performance)
        self.assertIn("sql_execute_ms", performance)
        self.assertIn("sql_fetch_ms", performance)
        self.assertIn("row_decode_ms", performance)
        self.assertIn("day_grouping_ms", performance)

    def test_consumer_delay_is_not_counted_as_market_data_loading(self) -> None:
        calls: list[str] = []
        connection = _Connection(calls)
        engine = type("Engine", (), {"connect": lambda _self: connection})()
        session = type("Session", (), {"get_bind": lambda _self: engine})()
        performance: dict[str, float] = {}
        loader = MarketDataLoader(
            session,
            statement=object(),
            params={},
            row_factory=lambda row: (
                row["trade_date"],
                row["symbol"],
                {"instrument_id": 1},
            ),
            performance=performance,
        )

        iterator = loader.iter_days()
        self.assertEqual(next(iterator)[0], date(2025, 1, 2))
        time.sleep(0.05)
        with self.assertRaises(StopIteration):
            next(iterator)

        self.assertLess(performance["load_market_data_ms"], 25.0)


if __name__ == "__main__":
    unittest.main()
