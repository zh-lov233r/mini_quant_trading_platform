from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from src.api.backtests import (
    _benchmark_symbol_for_run,
    _load_comparison_curves_read_only,
    get_backtest_comparison_curves,
)
from src.services.data_service import HistoricalBar


def curve(symbol: str, values: list[float]) -> list[dict[str, object]]:
    return [
        {
            "ts": f"2025-01-{index + 2:02d}T21:00:00+00:00",
            "symbol": symbol,
            "close": value,
            "equity": value * 1000,
            "return": (value / values[0]) - 1,
        }
        for index, value in enumerate(values)
    ]


class BacktestComparisonCurvesApiTests(unittest.TestCase):
    def test_a_share_run_uses_domestic_indices_and_replaces_us_benchmark(self) -> None:
        db = MagicMock()
        run = SimpleNamespace(
            id=uuid4(),
            initial_cash=100_000,
            benchmark_symbol="SPY",
            config_snapshot={},
            summary_metrics={
                "symbols_loaded": ["000001.SZ", "600000.SH"],
                "comparison_curves": {
                    "SPY": curve("SPY", [100, 101]),
                    "000001.SH": curve("000001.SH", [3000, 3030]),
                    "399001.SZ": curve("399001.SZ", [10000, 10200]),
                },
            },
        )

        result = _load_comparison_curves_read_only(db, run, max_points=1500)

        self.assertEqual(set(result), {"000001.SH", "399001.SZ"})
        self.assertEqual(_benchmark_symbol_for_run(run), "000001.SH")
        db.execute.assert_not_called()

    def test_cached_curves_are_downsampled_and_preserve_endpoints(self) -> None:
        db = MagicMock()
        run = SimpleNamespace(
            id=uuid4(),
            initial_cash=100_000,
            summary_metrics={
                "comparison_curves": {
                    "SPY": curve("SPY", [100, 102, 98, 105]),
                    "QQQ": curve("QQQ", [100, 104, 97, 110]),
                }
            },
        )

        result = _load_comparison_curves_read_only(db, run, max_points=3)

        self.assertEqual(set(result), {"SPY", "QQQ"})
        self.assertLessEqual(len(result["QQQ"]), 3)
        self.assertEqual(result["QQQ"][0]["equity"], 100_000)
        self.assertAlmostEqual(result["QQQ"][-1]["equity"], 110_000)
        db.execute.assert_not_called()
        db.commit.assert_not_called()

    @patch("src.api.backtests.get_historical_data")
    def test_missing_qqq_is_computed_read_only(self, get_historical_data: MagicMock) -> None:
        db = MagicMock()
        snapshots = [
            SimpleNamespace(ts=datetime(2025, 1, 2, 21, tzinfo=UTC)),
            SimpleNamespace(ts=datetime(2025, 1, 3, 21, tzinfo=UTC)),
        ]
        db.execute.return_value.scalars.return_value.all.return_value = snapshots
        get_historical_data.return_value = {
            "QQQ": [
                HistoricalBar("QQQ", 1, snapshots[0].ts, date(2025, 1, 2), None, None, None, 500, None, None),
                HistoricalBar("QQQ", 1, snapshots[1].ts, date(2025, 1, 3), None, None, None, 550, None, None),
            ]
        }
        run = SimpleNamespace(
            id=uuid4(),
            initial_cash=100_000,
            summary_metrics={"comparison_curves": {"SPY": curve("SPY", [100, 101])}},
        )

        result = _load_comparison_curves_read_only(db, run, max_points=1500)

        self.assertAlmostEqual(result["QQQ"][-1]["return"], 0.1)
        self.assertAlmostEqual(result["QQQ"][-1]["equity"], 110_000)
        get_historical_data.assert_called_once_with(
            db,
            ["QQQ"],
            date(2025, 1, 2),
            date(2025, 1, 3),
            adjusted=True,
        )
        db.commit.assert_not_called()

    def test_missing_run_returns_404(self) -> None:
        db = MagicMock()
        db.get.return_value = None

        with self.assertRaises(HTTPException) as context:
            get_backtest_comparison_curves(uuid4(), 1500, db)

        self.assertEqual(context.exception.status_code, 404)
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
