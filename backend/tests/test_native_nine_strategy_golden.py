from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest

import quant_kernel

from src.services.strategy_registry import normalize_strategy_params
from backend.tests import test_native_backtest_kernel as native_helpers


class NativeNineStrategyGoldenTests(unittest.TestCase):
    """Freeze complete typed-ledger output from the pre-STL native implementation."""

    GOLDEN_PATH = Path(__file__).with_name("fixtures") / "native_nine_strategy_golden.json"
    SYMBOL_COUNT = 20
    SESSION_COUNT = 120
    SEED = 20260902

    def setUp(self) -> None:
        self.helper = native_helpers.NativeBacktestKernelTests()

    @staticmethod
    def _runtime(strategy_type: str) -> dict[str, object]:
        signal: dict[str, object] = {"min_strength_score": 0.0}
        if strategy_type == "trend":
            signal.update({
                "fast_indicator": {"kind": "ema", "window": 15},
                "slow_indicator": {"kind": "sma", "window": 200},
            })
        return {
            "strategy_id": f"golden-{strategy_type}",
            "strategy_type": strategy_type,
            "params": normalize_strategy_params(
                strategy_type,
                {
                    "signal": signal,
                    "risk": {"max_positions": 20, "position_size_pct": 0.02},
                },
            ),
            "engine_ready": True,
        }

    def _stateless_days(
        self,
        strategy_type: str,
    ) -> list[tuple[date, dict[str, dict[str, object]]]]:
        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        for day_index in range(self.SESSION_COUNT):
            trade_day = date(2025, 1, 1) + timedelta(days=day_index)
            phase = day_index % 4
            snapshots: dict[str, dict[str, object]] = {}
            for instrument_id in range(1, self.SYMBOL_COUNT + 1):
                symbol = f"S{instrument_id:02d}"
                close = 10.0
                snapshot: dict[str, object] = {
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "asset_type": "CS",
                    "exchange": "XNAS",
                    "dt_ny": trade_day,
                    "ts": datetime(
                        trade_day.year, trade_day.month, trade_day.day, 21,
                        tzinfo=timezone.utc,
                    ),
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 100.0,
                    "volume_sma_20": 100.0,
                    "atr_14": 1.0,
                }
                if strategy_type == "trend":
                    snapshot.update({
                        "ema_15": (11.0, 11.0, 9.0, 9.0)[phase],
                        "sma_200": 10.0,
                        "prev_ema_15": (9.0, 11.0, 11.0, 9.0)[phase],
                        "prev_sma_200": 10.0,
                        "volume": (200.0, 100.0, 100.0, 100.0)[phase],
                    })
                elif strategy_type == "mean_reversion":
                    snapshot.update({
                        "zscore_20": (-2.0, -2.0, 0.0, 0.0)[phase],
                        "rsi_14": 40.0,
                    })
                else:
                    close = (11.0, 11.0, 8.0, 9.0)[phase]
                    snapshot.update({
                        "open": (10.0, 11.0, 8.0, 9.0)[phase],
                        "high": close + 1.0,
                        "low": close - 1.0,
                        "close": close,
                        "sma_20": 10.0,
                        "ret_20d": (0.10, 0.10, -0.10, 0.0)[phase],
                        "volume": (200.0, 200.0, 100.0, 100.0)[phase],
                    })
                snapshots[symbol] = snapshot
            days.append((trade_day, snapshots))
        return days

    @staticmethod
    def _zone(
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

    def _support_case(self):
        days: list[tuple[date, dict[str, dict[str, object]]]] = []
        cycle = [102.5, 102.0, 103.0, 106.0, 104.0, 102.5]
        for day_index in range(self.SESSION_COUNT):
            trade_day = date(2025, 1, 1) + timedelta(days=day_index)
            close = cycle[day_index % len(cycle)]
            snapshots: dict[str, dict[str, object]] = {}
            for instrument_id in range(1, self.SYMBOL_COUNT + 1):
                symbol = f"S{instrument_id:02d}"
                snapshots[symbol] = {
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "asset_type": "CS",
                    "exchange": "XNAS",
                    "dt_ny": trade_day,
                    "ts": datetime(
                        trade_day.year, trade_day.month, trade_day.day, 21,
                        tzinfo=timezone.utc,
                    ),
                    "open": close,
                    "high": close + 1.0,
                    "low": min(
                        close - 1.0,
                        100.5 if day_index % len(cycle) == 1 else close - 1.0,
                    ),
                    "close": close,
                    "close_unadjusted": close,
                    "volume": 100.0,
                    "volume_sma_20": 100.0,
                    "atr_14": 1.0,
                    "dollar_volume_20": 100_000_000.0,
                }
            days.append((trade_day, snapshots))
        dataset = self.helper._dataset(days)
        dataset.sidecar["support_resistance_hydration"] = {
            str(instrument_id): {
                "zone_timeline": [
                    self._zone(
                        f"support-{instrument_id}", "support", 100.0, 99.0, 101.0
                    ),
                    self._zone(
                        f"resistance-{instrument_id}", "resistance", 106.0, 105.0, 107.0
                    ),
                ],
                "regime_timeline": [{
                    "version": 1,
                    "effective_from": "2024-01-01",
                    "regime": "uptrend",
                    "lower_zone_key": f"support-{instrument_id}",
                    "upper_zone_key": f"resistance-{instrument_id}",
                    "reason_code": "fixture",
                    "evidence": {"reason_code": "fixture"},
                }],
            }
            for instrument_id in range(1, self.SYMBOL_COUNT + 1)
        }
        runtime = self._runtime("support_resistance")
        runtime["params"]["universe"] = {
            "selection_mode": "manual",
            "symbols": [
                f"S{instrument_id:02d}"
                for instrument_id in range(1, self.SYMBOL_COUNT + 1)
            ],
        }
        return dataset, runtime

    @staticmethod
    def _mapping_payload(mapping) -> dict[str, object]:
        return {
            key: value.tolist() if hasattr(value, "tolist") else value
            for key, value in mapping.items()
        }

    @classmethod
    def _fingerprint(cls, result: quant_kernel.KernelResult) -> dict[str, object]:
        def section(mapping) -> dict[str, object]:
            payload = cls._mapping_payload(mapping)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=True,
            ).encode()
            first = next(iter(payload.values()), [])
            return {"rows": len(first), "sha256": sha256(encoded).hexdigest()}

        support = result.support_resistance
        return {
            "summary": result.summary,
            "signals": section(result.signals),
            "trades": section(result.trades),
            "equity": section(result.equity),
            "positions": section(result.positions),
            "support_events": section(support["events"]) if support else None,
            "support_zone_versions": section(support["zone_versions"]) if support else None,
            "support_regime_versions": section(support["regime_versions"]) if support else None,
        }

    def _cases(self):
        for strategy_type in ("trend", "mean_reversion", "momentum_breakout"):
            yield (
                strategy_type,
                self.helper._dataset(self._stateless_days(strategy_type)),
                self._runtime(strategy_type),
            )
        for strategy_type, bars, params in self.helper._staged_pattern_cases():
            days = self.helper._pattern_matrix_days(
                bars,
                symbol_count=self.SYMBOL_COUNT,
                session_count=self.SESSION_COUNT,
            )
            yield (
                strategy_type,
                self.helper._dataset(days),
                self.helper._pattern_runtime(
                    strategy_type,
                    params,
                    max_positions=self.SYMBOL_COUNT,
                    position_size_pct=0.02,
                ),
            )
        dataset, runtime = self._support_case()
        yield "support_resistance", dataset, runtime

    def test_all_nine_full_ledgers_match_frozen_golden(self) -> None:
        golden = json.loads(self.GOLDEN_PATH.read_text(encoding="utf-8"))
        self.assertEqual(golden["seed"], self.SEED)
        for strategy_type, dataset, runtime in self._cases():
            with self.subTest(strategy_type=strategy_type):
                result = quant_kernel.run_backtest(
                    dataset,
                    runtime,
                    {
                        "initial_cash": 100_000.0,
                        "commission_bps": 0.0,
                        "commission_min": 0.0,
                        "slippage_bps": 0.0,
                    },
                )
                self.assertEqual(
                    self._fingerprint(result),
                    golden["strategies"][strategy_type],
                )

    def test_all_nine_ledgers_match_between_one_and_four_threads(self) -> None:
        original_symbol_count = self.SYMBOL_COUNT
        self.SYMBOL_COUNT = 256
        try:
            for strategy_type, dataset, runtime in self._cases():
                with self.subTest(strategy_type=strategy_type):
                    options = {
                        "initial_cash": 100_000.0,
                        "commission_bps": 0.0,
                        "commission_min": 0.0,
                        "slippage_bps": 0.0,
                    }
                    serial = quant_kernel.run_backtest(
                        dataset,
                        runtime,
                        {**options, "thread_count": 1},
                    )
                    parallel = quant_kernel.run_backtest(
                        dataset,
                        runtime,
                        {**options, "thread_count": 4},
                    )
                    self.assertEqual(
                        self._fingerprint(parallel),
                        self._fingerprint(serial),
                    )
                    self.assertEqual(parallel.performance["thread_count"], 4)
                    self.assertEqual(
                        parallel.performance["parallel_sessions"],
                        self.SESSION_COUNT,
                    )
                    self.assertEqual(parallel.performance["serial_sessions"], 0)
        finally:
            self.SYMBOL_COUNT = original_symbol_count

    def test_support_resistance_parallel_result_is_repeatable(self) -> None:
        original_symbol_count = self.SYMBOL_COUNT
        self.SYMBOL_COUNT = 256
        try:
            dataset, runtime = self._support_case()
            options = {
                "initial_cash": 100_000.0,
                "commission_bps": 0.0,
                "commission_min": 0.0,
                "slippage_bps": 0.0,
                "thread_count": 4,
            }
            expected = self._fingerprint(
                quant_kernel.run_backtest(dataset, runtime, options)
            )
            for repetition in range(10):
                with self.subTest(repetition=repetition):
                    result = quant_kernel.run_backtest(dataset, runtime, options)
                    self.assertEqual(self._fingerprint(result), expected)
        finally:
            self.SYMBOL_COUNT = original_symbol_count

    def test_pattern_warmup_matches_serial_with_daily_barriers(self) -> None:
        original_symbol_count = self.SYMBOL_COUNT
        self.SYMBOL_COUNT = 256
        try:
            strategy_type, bars, params = next(
                item
                for item in self.helper._staged_pattern_cases()
                if item[0] == "double_bottom"
            )
            dataset = self.helper._dataset(
                self.helper._pattern_matrix_days(
                    bars,
                    symbol_count=self.SYMBOL_COUNT,
                    session_count=self.SESSION_COUNT,
                )
            )
            runtime = self.helper._pattern_runtime(
                strategy_type,
                params,
                max_positions=self.SYMBOL_COUNT,
                position_size_pct=0.02,
            )
            start_date = date(2025, 1, 1) + timedelta(days=60)
            serial = quant_kernel.run_backtest(
                dataset,
                runtime,
                {"thread_count": 1, "start_date": start_date},
            )
            parallel = quant_kernel.run_backtest(
                dataset,
                runtime,
                {"thread_count": 4, "start_date": start_date},
            )
            self.assertEqual(self._fingerprint(parallel), self._fingerprint(serial))
            self.assertEqual(parallel.performance["thread_count"], 4)
            self.assertEqual(parallel.performance["parallel_sessions"], 60)
        finally:
            self.SYMBOL_COUNT = original_symbol_count

    def test_small_universe_uses_serial_fallback(self) -> None:
        dataset = self.helper._dataset(self._stateless_days("trend"))
        result = quant_kernel.run_backtest(
            dataset,
            self._runtime("trend"),
            {"thread_count": 4},
        )
        self.assertEqual(result.performance["thread_count"], 1)
        self.assertEqual(result.performance["parallel_sessions"], 0)
        self.assertEqual(result.performance["serial_sessions"], self.SESSION_COUNT)

    def test_thread_count_is_bounded_by_native_abi(self) -> None:
        dataset = self.helper._dataset(self._stateless_days("trend"))
        runtime = self._runtime("trend")
        for thread_count in (0, 17):
            with self.subTest(thread_count=thread_count):
                with self.assertRaisesRegex(ValueError, "between 1 and 16"):
                    quant_kernel.run_backtest(
                        dataset,
                        runtime,
                        {"thread_count": thread_count},
                    )


if __name__ == "__main__":
    unittest.main()
