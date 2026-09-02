from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
import gc
import json
import math
from pathlib import Path
import threading
import unittest

import numpy as np
import quant_kernel

from src.services.prepared_dataset_service import (
    PREPARED_FLOAT_FIELDS,
    PREPARED_INTEGER_FIELDS,
    PreparedDataset,
)
from src.services.strategy_registry import normalize_strategy_params
from src.services.backtest_universe_service import point_in_time_entry_eligible


class NativeBacktestKernelTests(unittest.TestCase):
    maxDiff = None

    GOLDEN_PATH = Path(__file__).with_name("fixtures") / "native_pattern_backtest_golden.json"

    def _market_days(self) -> list[tuple[date, dict[str, dict[str, object]]]]:
        raw = [
            (date(2025, 1, 1), 1, "AAA", 10.0, 11.0, 10.0, 0.10, 200.0),
            (date(2025, 1, 1), 2, "BBB", 20.0, 10.0, 10.0, 0.00, 100.0),
            (date(2025, 1, 2), 1, "AAA", 10.0, 9.0, 10.0, -0.10, 100.0),
            (date(2025, 1, 2), 2, "BBB", 20.0, 11.0, 10.0, 0.10, 200.0),
            (date(2025, 1, 3), 1, "AAA", 8.0, 9.0, 10.0, -0.10, 100.0),
            (date(2025, 1, 3), 2, "BBB", 20.0, 20.0, 20.0, 0.00, 100.0),
        ]
        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        for trade_day, instrument_id, symbol, open_price, close, sma, ret, volume in raw:
            if not days or days[-1][0] != trade_day:
                days.append((trade_day, {}))
            days[-1][1][symbol] = {
                "instrument_id": instrument_id,
                "symbol": symbol,
                "asset_type": "CS",
                "dt_ny": trade_day,
                "ts": datetime(
                    trade_day.year,
                    trade_day.month,
                    trade_day.day,
                    21,
                    tzinfo=timezone.utc,
                ),
                "open": open_price,
                "high": max(open_price, close),
                "low": min(open_price, close),
                "close": close,
                "volume": volume,
                "volume_sma_20": 100.0,
                "ret_20d": ret,
                "sma_20": sma,
                "atr_14": 1.0,
                "position": 0.0,
                "avg_entry_price": None,
                "entry_trade_date": None,
                "entry_signal_features": None,
                "position_holding_days": None,
                "recent_bars": [],
            }
        return days

    def _dataset(
        self,
        days: list[tuple[date, dict[str, dict[str, object]]]],
        *,
        split_adjustments: list[list[object]] | None = None,
        order: str = "F",
    ) -> PreparedDataset:
        row_count = sum(len(snapshots) for _, snapshots in days)
        integers = np.full(
            (row_count, len(PREPARED_INTEGER_FIELDS)),
            np.iinfo(np.int64).min,
            dtype="<i8",
            order=order,
        )
        floats = np.full(
            (row_count, len(PREPARED_FLOAT_FIELDS)),
            np.nan,
            dtype="<f8",
            order=order,
        )
        dataset = PreparedDataset(integers, floats, {})
        index = 0
        for _, snapshots in days:
            for snapshot in snapshots.values():
                dataset.encode(index, snapshot)
                index += 1
        dataset.sidecar = {
            **dataset.mapping_sidecar(),
            "corporate_actions": split_adjustments or [],
        }
        return dataset

    def _runtime(self) -> dict[str, object]:
        return {
            "strategy_id": "native-backtest",
            "strategy_type": "momentum_breakout",
            "params": normalize_strategy_params(
                "momentum_breakout",
                {
                    "signal": {"min_strength_score": 0.0},
                    "risk": {"max_positions": 1, "position_size_pct": 0.5},
                },
            ),
            "engine_ready": True,
        }

    def _universe_policy(self) -> dict[str, object]:
        return {
            "type": "point_in_time_liquid",
            "assetTypes": ["CS"],
            "exchanges": ["XASE", "XNAS", "XNYS"],
            "minUnadjustedClose": 5.0,
            "minDollarVolume20": 10_000_000.0,
            "minHistorySessions": 20,
            "membershipAsOf": "signal_close",
            "existingPositionPolicy": "exit_only",
            "delistingValuePolicy": "zero_with_last_close_sensitivity",
        }

    def _momentum_snapshot(
        self,
        trade_day: date,
        instrument_id: int,
        *,
        signal: bool = False,
    ) -> dict[str, object]:
        close = 11.0 if signal else 9.0
        return {
            "instrument_id": instrument_id,
            "symbol": f"S{instrument_id:02d}",
            "asset_type": "CS",
            "exchange": "XNAS",
            "listed_at": date(2020, 1, 1),
            "delisted_at": None,
            "dt_ny": trade_day,
            "ts": datetime(
                trade_day.year,
                trade_day.month,
                trade_day.day,
                21,
                tzinfo=timezone.utc,
            ),
            "open": 10.0,
            "high": 12.0,
            "low": 8.0,
            "close": close,
            "close_unadjusted": 10.0,
            "volume": 200.0 if signal else 100.0,
            "volume_sma_20": 100.0,
            "dollar_volume_20": 20_000_000.0,
            "ret_20d": 0.10 if signal else 0.0,
            "sma_20": 10.0,
            "atr_14": 1.0,
            "position": 0.0,
            "avg_entry_price": None,
            "entry_trade_date": None,
            "entry_signal_features": None,
            "position_holding_days": None,
            "recent_bars": [],
        }

    def _single_symbol_days(
        self,
        strategy_type: str,
    ) -> list[tuple[date, dict[str, dict[str, object]]]]:
        snapshots: list[dict[str, object]] = []
        for offset in range(3):
            trade_day = date(2025, 2, offset + 1)
            snapshot: dict[str, object] = {
                "instrument_id": 7,
                "symbol": "ONLY",
                "asset_type": "CS",
                "dt_ny": trade_day,
                "ts": datetime(2025, 2, offset + 1, 21, tzinfo=timezone.utc),
                "open": (10.0, 10.0, 8.0)[offset],
                "high": (12.0, 11.0, 9.0)[offset],
                "low": (9.0, 7.0, 7.0)[offset],
                "close": (11.0, 8.0, 8.0)[offset],
                "volume": (200.0, 100.0, 100.0)[offset],
                "volume_sma_20": 100.0,
                "atr_14": 2.0,
                "position": 0.0,
                "avg_entry_price": None,
                "entry_trade_date": None,
                "entry_signal_features": None,
                "position_holding_days": None,
                "recent_bars": [],
            }
            if strategy_type == "trend":
                snapshot.update(
                    {
                        "ema_15": (11.0, 9.0, 9.0)[offset],
                        "sma_200": 10.0,
                        "prev_ema_15": (10.0, 11.0, 9.0)[offset],
                        "prev_sma_200": 10.0,
                    }
                )
            else:
                snapshot.update(
                    {
                        "zscore_20": (-2.0, 0.0, 0.0)[offset],
                        "rsi_14": 40.0,
                    }
                )
            snapshots.append(snapshot)
        return [
            (snapshot["dt_ny"], {"ONLY": snapshot})  # type: ignore[list-item]
            for snapshot in snapshots
        ]

    def _single_symbol_runtime(self, strategy_type: str) -> dict[str, object]:
        return {
            "strategy_id": f"native-{strategy_type}",
            "strategy_type": strategy_type,
            "params": normalize_strategy_params(
                strategy_type,
                {
                    "signal": {"min_strength_score": 0.0},
                    "risk": {"max_positions": 1, "position_size_pct": 0.5},
                },
            ),
            "engine_ready": True,
        }

    @staticmethod
    def _pattern_bar(
        offset: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        *,
        atr: float = 2.0,
    ) -> dict[str, object]:
        return {
            "dt_ny": date(2025, 1, 1) + timedelta(days=offset),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "volume_sma_20": 100.0,
            "atr_14": atr,
        }

    def _pattern_days(
        self,
        bars: list[dict[str, object]],
    ) -> list[tuple[date, dict[str, dict[str, object]]]]:
        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        for index, bar in enumerate(bars):
            trade_day = bar["dt_ny"]
            snapshot = {
                **bar,
                "instrument_id": 1,
                "symbol": "TEST",
                "asset_type": "CS",
                "ts": datetime(
                    trade_day.year,
                    trade_day.month,
                    trade_day.day,
                    21,
                    tzinfo=timezone.utc,
                ),
                "position": 0.0,
                "avg_entry_price": None,
                "entry_trade_date": None,
                "entry_signal_features": None,
                "position_holding_days": None,
                "recent_bars": copy.deepcopy(bars[: index + 1]),
            }
            days.append((trade_day, {"TEST": snapshot}))
        return days

    def _pattern_runtime(
        self,
        strategy_type: str,
        params: dict[str, object] | None = None,
        *,
        max_positions: int = 1,
        position_size_pct: float = 0.5,
    ) -> dict[str, object]:
        raw = copy.deepcopy(params or {})
        raw.setdefault("signal", {})["min_strength_score"] = 0.0
        raw.setdefault("risk", {}).update(
            {"max_positions": max_positions, "position_size_pct": position_size_pct}
        )
        return {
            "strategy_id": f"native-{strategy_type}",
            "strategy_type": strategy_type,
            "params": normalize_strategy_params(strategy_type, raw),
            "engine_ready": True,
        }

    def _pattern_matrix_days(
        self,
        bars: list[dict[str, object]],
        *,
        symbol_count: int = 20,
        session_count: int = 120,
    ) -> list[tuple[date, dict[str, dict[str, object]]]]:
        normalized = copy.deepcopy(bars)
        while len(normalized) < session_count:
            index = len(normalized)
            prior_close = float(normalized[-1]["close"])
            normalized.append(
                self._pattern_bar(
                    index,
                    prior_close,
                    prior_close * 1.01,
                    prior_close * 0.99,
                    prior_close,
                    100.0,
                    atr=float(normalized[-1].get("atr_14") or 2.0),
                )
            )
        normalized = normalized[:session_count]

        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        for day_index, bar in enumerate(normalized):
            trade_day = date(2025, 1, 1) + timedelta(days=day_index)
            snapshots: dict[str, dict[str, object]] = {}
            history = [dict(item) for item in normalized[: day_index + 1]]
            for instrument_id in range(1, symbol_count + 1):
                symbol = f"S{instrument_id:02d}"
                snapshots[symbol] = {
                    **bar,
                    "dt_ny": trade_day,
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "asset_type": "CS",
                    "ts": datetime(
                        trade_day.year,
                        trade_day.month,
                        trade_day.day,
                        21,
                        tzinfo=timezone.utc,
                    ),
                    "position": 0.0,
                    "avg_entry_price": None,
                    "entry_trade_date": None,
                    "entry_signal_features": None,
                    "position_holding_days": None,
                    "recent_bars": history,
                }
            days.append((trade_day, snapshots))
        return days

    def _run_native(
        self,
        days: list[tuple[date, dict[str, dict[str, object]]]],
        runtime: dict[str, object],
        *,
        commission_bps: float = 0.0,
        commission_min: float = 0.0,
        slippage_bps: float = 0.0,
    ) -> quant_kernel.KernelResult:
        return quant_kernel.run_backtest(
            self._dataset(days),
            runtime,
            {
                "initial_cash": 1_000.0,
                "commission_bps": commission_bps,
                "commission_min": commission_min,
                "slippage_bps": slippage_bps,
            },
        )

    def _assert_pattern_golden(
        self,
        result: quant_kernel.KernelResult,
        expected: dict[str, object],
    ) -> None:
        for key, value in expected["summary"].items():
            self.assertAlmostEqual(float(result.summary[key]), float(value), delta=1e-10)
        signal_metadata = [json.loads(value) for value in result.signals["metadata_json"]]
        signal_stages = [
            [
                item.get("setup", {}).get("stage_index"),
                item.get("setup", {}).get("stage_key"),
            ]
            for item in signal_metadata
        ]
        trade_stages = [
            [
                "BUY" if int(side) == 1 else "SELL",
                int(stage_index) or None,
                stage_key or None,
            ]
            for side, stage_index, stage_key in zip(
                result.trades["side"],
                result.trades["stage_index"],
                result.trades["stage_key"],
                strict=True,
            )
        ]
        self.assertEqual(signal_stages, expected["signal_stages"])
        self.assertEqual(trade_stages, expected["trade_stages"])

    def _assert_prepared_day_matches_backtest(
        self,
        dataset: PreparedDataset,
        runtime: dict[str, object],
        backtest: quant_kernel.KernelResult,
        session_index: int,
        *,
        hydration: dict[str, object] | None = None,
    ) -> None:
        positions: dict[str, dict[str, object]] = {}
        for index, raw_session in enumerate(backtest.positions["session_index"]):
            if int(raw_session) != session_index:
                continue
            instrument_id = int(backtest.positions["instrument_id"][index])
            entry_ordinal = int(backtest.positions["entry_date_ordinal"][index])
            positions[str(instrument_id)] = {
                "position": float(backtest.positions["quantity"][index]),
                "avg_entry_price": float(backtest.positions["average_entry_price"][index]),
                "entry_trade_date": date.fromordinal(entry_ordinal),
                "position_holding_days": None,
                "entry_signal_features": json.loads(
                    backtest.positions["entry_signal_features_json"][index]
                ),
            }
        paper = quant_kernel.evaluate_day(
            dataset,
            runtime,
            {
                "positions": positions,
                "support_resistance_hydration": dict(hydration or {}),
            },
        )
        expected_indexes = [
            index
            for index, raw_session in enumerate(backtest.signals["session_index"])
            if int(raw_session) == session_index
        ]
        self.assertEqual(
            paper.signals["instrument_id"].tolist(),
            [int(backtest.signals["instrument_id"][index]) for index in expected_indexes],
        )
        self.assertEqual(
            paper.signals["action"].tolist(),
            [int(backtest.signals["action"][index]) for index in expected_indexes],
        )
        np.testing.assert_allclose(
            paper.signals["score"],
            [float(backtest.signals["score"][index]) for index in expected_indexes],
            atol=1e-10,
            equal_nan=True,
        )
        self.assertEqual(
            list(paper.signals["reason"]),
            [backtest.signals["reason"][index] for index in expected_indexes],
        )
        self.assertEqual(
            list(paper.signals["metadata_json"]),
            [backtest.signals["metadata_json"][index] for index in expected_indexes],
        )

    def test_momentum_t_plus_one_sell_first_and_shared_cash(self) -> None:
        days = self._market_days()
        runtime = self._runtime()
        result = self._run_native(days, runtime)

        native_trades = result.trades
        self.assertEqual(result.summary["signal_count"], 4)
        self.assertEqual(result.summary["trade_count"], 3)
        self.assertAlmostEqual(result.summary["final_equity"], 900.0, delta=1e-10)
        self.assertAlmostEqual(result.summary["max_drawdown"], 0.1, delta=1e-10)
        self.assertEqual(native_trades["side"].tolist(), [1, -1, 1])
        self.assertEqual(native_trades["instrument_id"].tolist(), [1, 1, 2])
        self.assertEqual(native_trades["session_index"].tolist(), [1, 2, 2])
        self.assertEqual(native_trades["signal_session_index"].tolist(), [0, 1, 1])
        np.testing.assert_allclose(native_trades["quantity"], [50.0, 50.0, 22.5], atol=1e-10)
        np.testing.assert_allclose(native_trades["price"], [10.0, 8.0, 20.0], atol=1e-10)

    def test_prepared_day_signal_matches_backtest_signal_for_same_session(self) -> None:
        dataset = self._dataset(self._market_days()[:2])
        runtime = self._runtime()
        backtest = quant_kernel.run_backtest(
            dataset,
            runtime,
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        self._assert_prepared_day_matches_backtest(dataset, runtime, backtest, 1)

    def test_trend_and_mean_reversion_ledgers(self) -> None:
        for strategy_type in ("trend", "mean_reversion"):
            with self.subTest(strategy_type=strategy_type):
                result = self._run_native(
                    self._single_symbol_days(strategy_type),
                    self._single_symbol_runtime(strategy_type),
                )
                self.assertEqual(result.summary["signal_count"], 2)
                self.assertEqual(result.summary["trade_count"], 2)
                self.assertEqual(result.trades["side"].tolist(), [1, -1])
                self.assertEqual(result.trades["session_index"].tolist(), [1, 2])

    def _staged_pattern_cases(
        self,
    ) -> list[tuple[str, list[dict[str, object]], dict[str, object]]]:
        island = [
            self._pattern_bar(0, 121, 122, 119, 120, 100),
            self._pattern_bar(1, 116, 117, 113, 114, 100),
            self._pattern_bar(2, 109, 110, 99, 100, 100),
            self._pattern_bar(3, 95, 96, 92, 93, 70),
            self._pattern_bar(4, 99, 104, 98, 102, 160),
            self._pattern_bar(5, 101, 102, 98, 99, 100),
            self._pattern_bar(6, 100, 103, 99, 102, 100),
        ]
        head_shoulders = [
            self._pattern_bar(0, 15, 16, 14, 15, 100),
            self._pattern_bar(1, 13, 14, 12, 13, 100),
            self._pattern_bar(2, 10.5, 11, 10, 10, 80),
            self._pattern_bar(3, 11, 13, 11, 12, 100),
            self._pattern_bar(4, 9, 10, 8, 8.5, 50),
            self._pattern_bar(5, 11.5, 13.5, 11, 12, 100),
            self._pattern_bar(6, 10.5, 11, 10.2, 10.4, 70),
            self._pattern_bar(7, 11, 12, 10.5, 11.5, 100),
            self._pattern_bar(8, 14.5, 16, 14, 15.5, 160),
            self._pattern_bar(9, 15.5, 16, 15, 15.8, 100),
        ]
        double_bottom = [
            self._pattern_bar(0, 120, 121, 119, 120, 100),
            self._pattern_bar(1, 116, 117, 114, 115, 100),
            self._pattern_bar(2, 111, 112, 108, 110, 100),
            self._pattern_bar(3, 101, 102, 98, 100, 80),
            self._pattern_bar(4, 101, 108, 100, 106, 90),
            self._pattern_bar(5, 107, 112, 105, 109, 95),
            self._pattern_bar(6, 108, 109, 104, 106, 90),
            self._pattern_bar(7, 101, 102, 99, 100, 70),
            self._pattern_bar(8, 102, 105, 101, 104, 90),
            self._pattern_bar(9, 105, 109, 104, 108, 110),
            self._pattern_bar(10, 108, 110, 107, 109, 110),
            self._pattern_bar(11, 109, 110, 105, 107, 70),
            self._pattern_bar(12, 108, 115, 107, 114, 160),
            self._pattern_bar(13, 114, 116, 113, 115, 100),
        ]
        v_reversal = []
        for index in range(60):
            close = 130.0 - index * 0.5
            v_reversal.append(self._pattern_bar(index, close + 1, close + 2, close - 1, close, 100, atr=3))
        v_reversal.extend(
            [
                self._pattern_bar(60, 91, 96, 90, 95, 220, atr=3),
                self._pattern_bar(61, 95, 97, 94, 96, 120, atr=3),
                self._pattern_bar(62, 96, 98, 95, 97, 120, atr=3),
                self._pattern_bar(63, 97.5, 98, 96, 97, 100, atr=3),
                self._pattern_bar(64, 97, 98.5, 96.5, 97.5, 100, atr=3),
                self._pattern_bar(65, 97.5, 98, 96, 97, 100, atr=3),
                self._pattern_bar(66, 99, 101, 98.5, 100, 160, atr=3),
                self._pattern_bar(67, 100, 101, 99, 99.5, 100, atr=3),
                self._pattern_bar(68, 100, 102, 99.5, 101, 100, atr=3),
            ]
        )
        log_bottom = math.log(80)
        curvature = (math.log(110) - log_bottom) / 0.25
        rounded = []
        for index in range(102):
            x = min(index, 100) / 100
            close = math.exp(log_bottom + curvature * (x - 0.5) ** 2)
            low = close * 0.99
            high = close * 1.01
            volume = 100.0
            if index in (85, 92):
                low = close * 0.94
                volume = 70.0
            if index in (83, 90):
                volume = 140.0
            if index == 100:
                close, high, low, volume = 113.0, 114.0, 111.0, 170.0
            if index == 101:
                close, high, low, volume = 113.0, 114.0, 112.0, 100.0
            rounded.append(self._pattern_bar(index, close * 0.995, high, low, close, volume))

        double_bottom_signal = {
            "downtrend_lookback": 3,
            "downtrend_min_drop_pct": 0.15,
            "downtrend_max_up_day_ratio": 0.35,
            "downtrend_min_r_squared": 0.65,
            "min_bottom_spacing": 2,
            "max_bottom_spacing": 6,
            "left_bottom_before_bars": 1,
            "left_bottom_after_bars": 1,
            "bottom_tolerance_pct": 0.03,
            "neckline_min_rebound_pct": 0.05,
            "rebound_up_day_ratio_min": 0.5,
            "second_bottom_volume_ratio_max": 1.0,
            "breakout_volume_ratio_min": 1.2,
            "max_breakout_bars_after_right_bottom": 6,
            "breakout_buffer_pct": 0.005,
            "retest_window": 3,
            "retest_volume_ratio_max": 0.8,
            "support_tolerance_pct": 0.02,
        }
        cases = [
            ("island_reversal", island, {"signal": {"downtrend_lookback": 3}}),
            ("double_bottom", double_bottom, {"signal": double_bottom_signal}),
            (
                "head_shoulders_bottom",
                head_shoulders,
                {"signal": {"pivot_left_bars": 1, "pivot_right_bars": 1, "downtrend_lookback": 2, "min_segment_bars": 2, "max_segment_bars": 10}},
            ),
            ("rounded_bottom", rounded, {"signal": {"min_lookback": 80, "max_lookback": 120, "min_r_squared": 0.70}}),
            ("v_reversal", v_reversal, {}),
        ]
        return cases

    def test_all_staged_pattern_ledgers_match_frozen_golden(self) -> None:
        cases = self._staged_pattern_cases()
        golden = json.loads(self.GOLDEN_PATH.read_text(encoding="utf-8"))
        for strategy_type, bars, params in cases:
            with self.subTest(strategy_type=strategy_type):
                days = self._pattern_days(bars)
                runtime = self._pattern_runtime(strategy_type, params)
                result = self._run_native(days, runtime)
                self._assert_pattern_golden(result, golden[strategy_type])
                self.assertGreater(result.summary["signal_count"], 0)
                self.assertGreater(result.summary["trade_count"], 0)

    def test_all_pattern_prepared_day_signals_match_backtest_sessions(self) -> None:
        for strategy_type, bars, params in self._staged_pattern_cases():
            with self.subTest(strategy_type=strategy_type):
                days = self._pattern_days(bars)
                runtime = self._pattern_runtime(strategy_type, params)
                full_dataset = self._dataset(days)
                backtest = quant_kernel.run_backtest(
                    full_dataset,
                    runtime,
                    {
                        "initial_cash": 1_000.0,
                        "commission_bps": 0.0,
                        "commission_min": 0.0,
                        "slippage_bps": 0.0,
                    },
                )
                signal_sessions = sorted(
                    {int(value) for value in backtest.signals["session_index"]}
                )
                self.assertTrue(signal_sessions)
                for session_index in signal_sessions:
                    self._assert_prepared_day_matches_backtest(
                        self._dataset(days[: session_index + 1]),
                        runtime,
                        backtest,
                        session_index,
                    )

    def test_staged_pattern_20_symbol_120_session_ledgers(self) -> None:
        for strategy_type, bars, params in self._staged_pattern_cases():
            with self.subTest(strategy_type=strategy_type):
                days = self._pattern_matrix_days(bars)
                runtime = self._pattern_runtime(
                    strategy_type,
                    params,
                    max_positions=20,
                    position_size_pct=0.02,
                )
                result = self._run_native(days, runtime)
                self.assertEqual(result.summary["trading_days"], 120)
                self.assertEqual(len(result.equity["session_index"]), 120)

    def test_commission_minimum_and_slippage(self) -> None:
        days = self._market_days()
        runtime = self._runtime()
        result = self._run_native(
            days,
            runtime,
            commission_bps=1.0,
            commission_min=1.0,
            slippage_bps=10.0,
        )
        self.assertEqual(result.trades["side"].tolist(), [1, -1, 1])
        self.assertEqual(result.trades["instrument_id"].tolist(), [1, 1, 2])
        np.testing.assert_allclose(
            result.trades["reference_price"],
            [10.0, 8.0, 20.0],
            atol=1e-10,
        )
        np.testing.assert_allclose(result.trades["slippage_bps"], [10.0] * 3, atol=1e-10)
        np.testing.assert_allclose(result.trades["fee"], [1.0] * 3, atol=1e-10)
        self.assertTrue(all(value for value in result.trades["reason"]))
        self.assertEqual(result.trades["entry_signal_features_json"][1], "")
        self.assertTrue(all(math.isnan(float(value)) for value in result.trades["stage_target_pct"]))

    def test_delisted_missing_position_is_written_off(self) -> None:
        days = self._market_days()
        days[0][1]["AAA"]["delisted_at"] = date(2025, 1, 2)
        days[1][1]["AAA"]["delisted_at"] = date(2025, 1, 2)
        del days[2][1]["AAA"]
        result = quant_kernel.run_backtest(
            self._dataset(days),
            self._runtime(),
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        self.assertAlmostEqual(result.summary["delisting_zero_write_off"], 450.0, delta=1e-10)
        self.assertAlmostEqual(result.summary["final_equity"], 500.0, delta=1e-10)

    def test_missing_next_open_drops_pending_fill(self) -> None:
        days = self._single_symbol_days("mean_reversion")
        days[1][1]["ONLY"]["open"] = None
        days[1][1]["ONLY"]["zscore_20"] = None
        result = quant_kernel.run_backtest(
            self._dataset(days),
            self._single_symbol_runtime("mean_reversion"),
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        self.assertEqual(result.summary["trade_count"], 0)
        self.assertEqual(result.trades["instrument_id"].tolist(), [])

    def test_split_adjustment_changes_quantity_and_average_before_sell(self) -> None:
        days = self._market_days()
        result = quant_kernel.run_backtest(
            self._dataset(days, split_adjustments=[["2025-01-03", 1, 2.0]]),
            self._runtime(),
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        self.assertEqual(result.trades["side"].tolist(), [1, -1, 1])
        self.assertAlmostEqual(float(result.trades["quantity"][1]), 100.0, delta=1e-10)
        self.assertAlmostEqual(result.summary["final_equity"], 1_300.0, delta=1e-10)

    def test_pattern_warmup_advances_state_without_outputs_or_callbacks(self) -> None:
        strategy_type, bars, params = self._staged_pattern_cases()[0]
        days = self._pattern_days(bars)
        start_index = 3
        runtime = self._pattern_runtime(strategy_type, params)
        calls: list[tuple[int, int]] = []

        def control(completed: int, total: int) -> bool:
            calls.append((completed, total))
            return False

        result = quant_kernel.run_backtest(
            self._dataset(days),
            runtime,
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
                "start_date": days[start_index][0],
                "end_date": days[-1][0],
            },
            control,
        )
        formal_days = len(days) - start_index
        self.assertEqual(calls, [(index, formal_days) for index in range(1, formal_days + 1)])
        self.assertEqual(result.summary["trading_days"], len(days) - start_index)
        self.assertEqual(len(result.equity["session_index"]), len(days) - start_index)
        self.assertGreaterEqual(min(result.signals["session_index"]), start_index)
        golden = json.loads(self.GOLDEN_PATH.read_text(encoding="utf-8"))[strategy_type]
        self._assert_pattern_golden(result, golden)

    def test_split_between_pattern_stages_preserves_setup_and_entry_history(self) -> None:
        strategy_type, bars, params = self._staged_pattern_cases()[0]
        days = self._pattern_days(bars)
        split_params = copy.deepcopy(params)
        split_params.setdefault("risk", {}).update(
            {"max_loss_pct": 0.99, "stop_loss_atr": 100.0, "take_profit_atr": 100.0}
        )
        result = quant_kernel.run_backtest(
            self._dataset(
                days,
                split_adjustments=[[days[5][0].isoformat(), 1, 2.0]],
            ),
            self._pattern_runtime(strategy_type, split_params),
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        self.assertEqual(result.trades["side"].tolist(), [1, 1, 1])
        self.assertEqual(result.trades["stage_index"].tolist(), [1, 2, 3])
        self.assertEqual(len(set(result.trades["setup_id"])), 1)
        self.assertAlmostEqual(
            float(result.trades["position_quantity_before"][1]),
            float(result.trades["position_quantity_after"][0]) * 2.0,
            delta=1e-10,
        )
        for index, encoded in enumerate(result.trades["entry_signal_features_json"], start=1):
            self.assertEqual(len(json.loads(encoded)["entry_history"]), index)
        final_features = json.loads(result.positions["entry_signal_features_json"][-1])
        self.assertEqual(len(final_features["entry_history"]), 3)
        self.assertEqual(
            date.fromordinal(int(result.positions["entry_date_ordinal"][-1])),
            days[4][0],
        )

    def test_position_rejects_later_signal_from_different_pattern_setup(self) -> None:
        strategy_type, base_bars, params = self._staged_pattern_cases()[0]
        bars = copy.deepcopy(base_bars)
        for offset in range(len(bars), 45):
            bars.append(self._pattern_bar(offset, 100.0, 101.0, 99.0, 100.0, 100.0))
        bars.extend(
            [
                self._pattern_bar(45, 100.0, 101.0, 99.0, 100.0, 100.0),
                self._pattern_bar(46, 99.0, 100.0, 97.0, 98.0, 100.0),
                self._pattern_bar(47, 97.0, 98.0, 95.0, 96.0, 100.0),
                self._pattern_bar(48, 92.0, 92.0, 91.2, 91.5, 70.0),
                self._pattern_bar(49, 92.0, 93.0, 91.5, 92.0, 100.0),
            ]
        )
        guarded_params = copy.deepcopy(params)
        guarded_params.setdefault("signal", {})["downtrend_min_drop_pct"] = 0.03
        guarded_params.setdefault("risk", {}).update(
            {"max_loss_pct": 0.99, "stop_loss_atr": 100.0, "take_profit_atr": 100.0}
        )
        days = self._pattern_days(bars)
        runtime = self._pattern_runtime(strategy_type, guarded_params)
        result = self._run_native(days, runtime)

        signal_setup_ids = [
            json.loads(value).get("setup", {}).get("setup_id")
            for value in result.signals["metadata_json"]
            if json.loads(value).get("setup", {}).get("setup_id")
        ]
        self.assertEqual(len(set(signal_setup_ids)), 2)
        self.assertEqual(len(set(result.trades["setup_id"])), 1)
        self.assertEqual(result.trades["stage_index"].tolist(), [1, 2, 3])

    def test_staged_buy_noops_when_cash_or_target_is_exhausted(self) -> None:
        island_type, island_bars, island_params = self._staged_pattern_cases()[0]
        no_cash = quant_kernel.run_backtest(
            self._dataset(self._pattern_days(island_bars)),
            self._pattern_runtime(island_type, island_params),
            {
                "initial_cash": 1.0,
                "commission_bps": 0.0,
                "commission_min": 2.0,
                "slippage_bps": 0.0,
            },
        )
        self.assertGreater(no_cash.summary["signal_count"], 0)
        self.assertEqual(no_cash.summary["trade_count"], 0)

        strategy_type, bars, params = self._staged_pattern_cases()[4]
        at_target = quant_kernel.run_backtest(
            self._dataset(self._pattern_days(bars)),
            self._pattern_runtime(strategy_type, params),
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        signal_stage_three = sum(
            json.loads(value).get("setup", {}).get("stage_index") == 3
            for value in at_target.signals["metadata_json"]
        )
        self.assertEqual(signal_stage_three, 2)
        self.assertEqual(at_target.trades["stage_index"].tolist(), [1, 2, 3])

    def test_pattern_backtest_without_callback_releases_gil(self) -> None:
        strategy_type, bars, params = self._staged_pattern_cases()[3]
        days = self._pattern_matrix_days(bars, symbol_count=200, session_count=400)
        dataset = self._dataset(days)
        runtime = self._pattern_runtime(
            strategy_type,
            params,
            max_positions=200,
            position_size_pct=0.001,
        )
        begin = threading.Event()
        ready = threading.Event()
        stop = threading.Event()
        observations = [0]

        def observe() -> None:
            ready.set()
            begin.wait()
            while not stop.is_set():
                observations[0] += 1

        observer = threading.Thread(target=observe)
        observer.start()
        ready.wait()
        begin.set()
        result = quant_kernel.run_backtest(dataset, runtime, {"initial_cash": 1_000.0})
        stop.set()
        observer.join(timeout=2.0)

        self.assertFalse(observer.is_alive())
        self.assertGreater(observations[0], 0)
        self.assertEqual(result.summary["trading_days"], 400)

    def test_support_resistance_backtest_without_callback_releases_gil(self) -> None:
        days = self._pattern_matrix_days(
            [self._pattern_bar(0, 100.0, 101.0, 99.0, 100.0, 100.0)],
            symbol_count=100,
            session_count=400,
        )
        dataset = self._dataset(days)
        runtime = {
            "strategy_id": "native-support-resistance-gil",
            "strategy_type": "support_resistance",
            "params": normalize_strategy_params(
                "support_resistance",
                {
                    "signal": {"min_strength_score": 0.0},
                    "risk": {"max_positions": 100, "position_size_pct": 0.001},
                },
            ),
            "engine_ready": True,
        }
        begin = threading.Event()
        ready = threading.Event()
        stop = threading.Event()
        observations = [0]

        def observe() -> None:
            ready.set()
            begin.wait()
            while not stop.is_set():
                observations[0] += 1

        observer = threading.Thread(target=observe)
        observer.start()
        ready.wait()
        begin.set()
        result = quant_kernel.run_backtest(dataset, runtime, {"initial_cash": 1_000.0})
        stop.set()
        observer.join(timeout=2.0)

        self.assertFalse(observer.is_alive())
        self.assertGreater(observations[0], 0)
        self.assertEqual(result.summary["trading_days"], 400)

    def test_control_callback_cancels_at_daily_boundary(self) -> None:
        calls: list[tuple[int, int]] = []

        def control(completed: int, total: int) -> bool:
            calls.append((completed, total))
            return completed == 2

        with self.assertRaisesRegex(
            quant_kernel.BacktestCancelledError,
            "native backtest cancellation requested",
        ):
            quant_kernel.run_backtest(
                self._dataset(self._market_days()),
                self._runtime(),
                {"initial_cash": 1_000.0},
                control,
            )
        self.assertEqual(calls, [(1, 3), (2, 3)])

    def test_result_columns_are_read_only_views_that_keep_owner_alive(self) -> None:
        result = quant_kernel.run_backtest(
            self._dataset(self._market_days()),
            self._runtime(),
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
            },
        )
        equity = result.equity["equity"]
        self.assertFalse(equity.flags.writeable)
        self.assertFalse(equity.flags.owndata)
        del result
        gc.collect()
        self.assertEqual(equity.tolist(), [1_000.0, 950.0, 900.0])

    def test_rejects_non_columnar_prepared_arrays(self) -> None:
        with self.assertRaisesRegex(ValueError, "Fortran column-major"):
            quant_kernel.run_backtest(
                self._dataset(self._market_days(), order="C"),
                self._runtime(),
                {"initial_cash": 1_000.0},
            )

    def test_dynamic_universe_matches_python_exclusion_order_and_filters_only_buys(self) -> None:
        first_day = date(2025, 3, 1)
        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        for offset in range(20):
            trade_day = first_day + timedelta(days=offset)
            days.append(
                (
                    trade_day,
                    {
                        f"S{instrument_id:02d}": self._momentum_snapshot(
                            trade_day, instrument_id
                        )
                        for instrument_id in range(1, 8)
                    },
                )
            )
        trade_day = first_day + timedelta(days=20)
        snapshots = {
            f"S{instrument_id:02d}": self._momentum_snapshot(
                trade_day, instrument_id, signal=True
            )
            for instrument_id in range(1, 9)
        }
        snapshots["S02"].update({"asset_type": "ETF", "exchange": "OTC"})
        snapshots["S03"].update({"exchange": "OTC", "listed_at": trade_day + timedelta(days=1)})
        snapshots["S04"]["listed_at"] = trade_day + timedelta(days=1)
        snapshots["S05"]["delisted_at"] = trade_day - timedelta(days=1)
        snapshots["S06"].update({"close_unadjusted": None, "dollar_volume_20": None})
        snapshots["S07"]["dollar_volume_20"] = None
        days.append((trade_day, snapshots))
        policy = self._universe_policy()
        runtime = self._runtime()
        runtime["params"]["universe"]["policy"] = policy  # type: ignore[index]

        expected = {
            "eligible_count": 0,
            "excluded_asset_type": 0,
            "excluded_exchange": 0,
            "excluded_before_listing": 0,
            "excluded_after_delisting": 0,
            "excluded_price": 0,
            "excluded_liquidity": 0,
            "excluded_history": 0,
        }
        reason_columns = {
            "asset_type": "excluded_asset_type",
            "exchange": "excluded_exchange",
            "before_listing": "excluded_before_listing",
            "after_delisting": "excluded_after_delisting",
            "price": "excluded_price",
            "liquidity": "excluded_liquidity",
            "history": "excluded_history",
        }
        for instrument_id, snapshot in enumerate(snapshots.values(), start=1):
            oracle_snapshot = dict(snapshot)
            oracle_snapshot["history_sessions"] = 21 if instrument_id < 8 else 1
            eligible, reason = point_in_time_entry_eligible(oracle_snapshot, policy)
            if eligible:
                expected["eligible_count"] += 1
            else:
                expected[reason_columns[str(reason)]] += 1

        result = quant_kernel.run_backtest(
            self._dataset(days),
            runtime,
            {
                "initial_cash": 1_000.0,
                "start_date": first_day,
                "end_date": trade_day,
            },
        )
        membership = result.universe_membership
        self.assertIsNotNone(membership)
        for column, count in expected.items():
            self.assertEqual(int(membership[column][-1]), count)
        self.assertEqual(result.signals["instrument_id"].tolist(), [1])
        self.assertEqual(result.signals["action"].tolist(), [1])

        window_only = quant_kernel.run_backtest(
            self._dataset(days),
            runtime,
            {
                "initial_cash": 1_000.0,
                "start_date": trade_day,
                "end_date": trade_day,
            },
        )
        self.assertEqual(window_only.universe_membership["eligible_count"].tolist(), [1])
        self.assertEqual(window_only.universe_membership["excluded_history"].tolist(), [1])
        self.assertEqual(window_only.signals["instrument_id"].tolist(), [1])

    def test_dynamic_universe_keeps_ineligible_position_exit_only(self) -> None:
        first_day = date(2025, 4, 1)
        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        for offset in range(22):
            trade_day = first_day + timedelta(days=offset)
            snapshot = self._momentum_snapshot(
                trade_day,
                1,
                signal=offset == 19,
            )
            if offset >= 20:
                snapshot.update(
                    {
                        "open": 8.0 if offset == 21 else 10.0,
                        "close": 8.0,
                        "close_unadjusted": 4.0,
                        "ret_20d": -0.10,
                        "volume": 100.0,
                    }
                )
            days.append((trade_day, {"S01": snapshot}))
        policy = self._universe_policy()
        runtime = self._runtime()
        runtime["params"]["universe"]["policy"] = policy  # type: ignore[index]

        result = quant_kernel.run_backtest(
            self._dataset(days),
            runtime,
            {
                "initial_cash": 1_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
                "start_date": first_day,
                "end_date": days[-1][0],
            },
        )

        self.assertEqual(result.signals["action"].tolist(), [1, -1])
        self.assertEqual(result.trades["side"].tolist(), [1, -1])
        self.assertEqual(result.trades["session_index"].tolist(), [20, 21])
        membership = result.universe_membership
        self.assertEqual(membership["eligible_count"][-3:].tolist(), [1, 0, 0])
        self.assertEqual(membership["excluded_price"][-3:].tolist(), [0, 1, 1])

    def test_dynamic_universe_rejects_non_exit_only_position_policy(self) -> None:
        runtime = self._runtime()
        policy = self._universe_policy()
        policy["existingPositionPolicy"] = "liquidate_immediately"
        runtime["params"]["universe"]["policy"] = policy  # type: ignore[index]
        with self.assertRaisesRegex(
            ValueError,
            "universePolicy existingPositionPolicy must be exit_only",
        ):
            quant_kernel.run_backtest(
                self._dataset(self._market_days()),
                runtime,
                {"initial_cash": 1_000.0},
            )

    def test_support_resistance_full_ledger_replays_hydration_and_trades_t_plus_one(self) -> None:
        def snapshot(
            trade_day: date,
            open_price: float,
            high: float,
            low: float,
            close: float,
        ) -> dict[str, object]:
            return {
                "instrument_id": 1,
                "symbol": "TEST",
                "asset_type": "CS",
                "exchange": "XNAS",
                "dt_ny": trade_day,
                "ts": datetime(
                    trade_day.year, trade_day.month, trade_day.day, 21,
                    tzinfo=timezone.utc,
                ),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "close_unadjusted": close,
                "volume": 100.0,
                "volume_sma_20": 100.0,
                "atr_14": 1.0,
                "dollar_volume_20": 100_000_000.0,
            }

        days = [
            (date(2025, 1, 1), {"TEST": snapshot(date(2025, 1, 1), 102.5, 103, 102, 102.5)}),
            (date(2025, 1, 2), {"TEST": snapshot(date(2025, 1, 2), 102, 103, 100.5, 102)}),
            (date(2025, 1, 3), {"TEST": snapshot(date(2025, 1, 3), 102, 104, 101, 103)}),
        ]
        dataset = self._dataset(days)

        def zone(
            zone_key: str,
            role: str,
            center: float,
            lower: float,
            upper: float,
        ) -> dict[str, object]:
            return {
                "zone_key": zone_key,
                "effective_from": "2024-12-01",
                "effective_to": None,
                "source_kind": "low" if role == "support" else "high",
                "role": role,
                "status": "active",
                "center": center,
                "lower": lower,
                "upper": upper,
                "atr": 2.0,
                "anchor_session_index": 0,
                "anchor_center": center,
                "anchor_lower": lower,
                "anchor_upper": upper,
                "slope_per_session": 0.0,
                "fit_residual_atr": 0.0,
                "recency_weight": 0.0,
                "last_inside": False,
                "pivot_keys": [f"{zone_key}:1", f"{zone_key}:2"],
                "pivot_count": 2,
                "touch_count": 2,
                "first_pivot_date": "2024-12-01",
                "last_pivot_date": "2024-12-20",
                "valid_from": "2024-12-23",
            }

        dataset.sidecar["support_resistance_hydration"] = {
            "1": {
                "zone_timeline": [
                    zone("support", "support", 100.0, 99.0, 101.0),
                    zone("resistance", "resistance", 106.0, 105.0, 107.0),
                ],
                "regime_timeline": [
                    {
                        "version": 1,
                        "effective_from": "2024-01-01",
                        "regime": "uptrend",
                        "lower_zone_key": "support",
                        "upper_zone_key": "resistance",
                        "reason_code": "test_fixture",
                        "evidence": {"reason_code": "test_fixture"},
                    }
                ],
            }
        }
        runtime = {
            "strategy_id": "native-support-resistance",
            "strategy_type": "support_resistance",
            "params": normalize_strategy_params(
                "support_resistance",
                {
                    "signal": {"min_strength_score": 0.0},
                    "universe": {"symbols": ["TEST"], "selection_mode": "manual"},
                },
            ),
            "engine_ready": True,
        }
        callbacks: list[tuple[int, int]] = []

        result = quant_kernel.run_backtest(
            dataset,
            runtime,
            {
                "initial_cash": 10_000.0,
                "start_date": date(2025, 1, 2),
                "end_date": date(2025, 1, 3),
            },
            lambda completed, total: callbacks.append((completed, total)) or False,
        )

        self._assert_prepared_day_matches_backtest(
            self._dataset(days[:2]),
            runtime,
            result,
            1,
            hydration=dataset.sidecar["support_resistance_hydration"],
        )

        self.assertEqual(callbacks, [(1, 2), (2, 2)])
        self.assertEqual(result.signals["action"].tolist(), [1])
        self.assertEqual(result.trades["side"].tolist(), [1])
        self.assertEqual(result.trades["signal_session_index"].tolist(), [1])
        self.assertEqual(result.trades["session_index"].tolist(), [2])
        support = result.support_resistance
        self.assertIsNotNone(support)
        self.assertGreater(len(support["events"]["payload_json"]), 0)
        first_signal = json.loads(result.signals["metadata_json"][0])
        self.assertEqual(
            first_signal["support_resistance"]["selected_setup"],
            "support_bounce",
        )


if __name__ == "__main__":
    unittest.main()
