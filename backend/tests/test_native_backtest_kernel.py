from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gc
import hashlib
import json
import math
from pathlib import Path
import threading
import unittest
from unittest.mock import MagicMock

import numpy as np
import quant_kernel

from src.services.backtest_engine import (
    BacktestCostConfig,
    _apply_buy_signals,
    _apply_sell_signals,
    _inject_backtest_positions,
    _portfolio_equity,
    _update_last_marks,
)
from src.services.prepared_dataset_service import (
    PREPARED_FLOAT_FIELDS,
    PREPARED_INTEGER_FIELDS,
    PreparedDataset,
)
from src.services.signal_strength_service import annotate_and_rank_signals
from src.services.strategy_engine import STRATEGY_HANDLERS, required_recent_bar_count_for_runtime
from src.services.strategy_registry import normalize_strategy_params
from src.services.backtest_universe_service import point_in_time_entry_eligible


@dataclass
class _TransactionCollector:
    rows: list[dict[str, object]]

    def add_transaction(self, values: dict[str, object]) -> None:
        self.rows.append(values)


class NativeBacktestKernelTests(unittest.TestCase):
    maxDiff = None

    GOLDEN_PATH = Path(__file__).with_name("fixtures") / "native_pattern_backtest_golden.json"

    def _assert_nested_close(self, actual: object, expected: object, path: str = "$") -> None:
        if isinstance(expected, float):
            self.assertIsInstance(actual, (int, float), msg=path)
            if math.isnan(expected):
                self.assertTrue(math.isnan(float(actual)))
            else:
                self.assertAlmostEqual(float(actual), expected, delta=1e-10, msg=path)
            return
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict)
            self.assertEqual(set(actual), set(expected))
            for key in expected:
                self._assert_nested_close(actual[key], expected[key], f"{path}.{key}")
            return
        if isinstance(expected, (list, tuple)):
            self.assertIsInstance(actual, (list, tuple))
            self.assertEqual(len(actual), len(expected))
            for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=True)):
                self._assert_nested_close(actual_item, expected_item, f"{path}[{index}]")
            return
        self.assertEqual(actual, expected)

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

    def _python_oracle(
        self,
        days: list[tuple[date, dict[str, dict[str, object]]]],
        runtime: dict[str, object],
        *,
        costs: BacktestCostConfig | None = None,
    ) -> dict[str, object]:
        cash = 1_000.0
        holdings: dict[int, float] = {}
        averages: dict[int, float] = {}
        entry_dates: dict[int, date] = {}
        entry_indices: dict[int, int] = {}
        entry_features: dict[int, dict[str, object]] = {}
        last_prices: dict[int, float] = {}
        pending = []
        costs = costs or BacktestCostConfig(0.0, 0.0, 0.0)
        transactions: list[dict[str, object]] = []
        collector = _TransactionCollector(transactions)
        signals: list[tuple[object, ...]] = []
        signal_metadata: list[dict[str, object]] = []
        equities: list[float] = []
        peak = cash
        max_drawdown = 0.0
        total_fees = 0.0
        total_slippage = 0.0
        trade_count = 0
        recent_bar_count = required_recent_bar_count_for_runtime(runtime)

        for day_index, (trade_day, snapshots) in enumerate(days):
            if recent_bar_count > 0:
                for snapshot in snapshots.values():
                    snapshot["recent_bars"] = list(snapshot["recent_bars"][-recent_bar_count:])
            identity = {
                int(snapshot["instrument_id"]): snapshot
                for snapshot in snapshots.values()
            }
            prices = {
                int(event.instrument_id): float(identity[int(event.instrument_id)]["open"])
                for event in pending
                if event.instrument_id in identity
            }
            marks = {
                instrument_id: float(
                    (identity.get(instrument_id) or {}).get("open")
                    or last_prices.get(instrument_id, 0.0)
                )
                for instrument_id in holdings
            }
            cash_ref = {"cash": cash}
            sell = _apply_sell_signals(
                db=MagicMock(),
                strategy=MagicMock(id="strategy"),
                run=MagicMock(id="run"),
                signals=pending,
                holdings=holdings,
                avg_entry_prices=averages,
                entry_trade_dates=entry_dates,
                entry_day_indices=entry_indices,
                entry_signal_features=entry_features,
                execution_prices=prices,
                execution_snapshots=identity,
                cash_ref=cash_ref,
                cost_config=costs,
                repository=collector,
                stable_instrument_identity=True,
            )
            equity_before = _portfolio_equity(float(cash_ref["cash"]), holdings, marks)
            risk = runtime["params"]["risk"]  # type: ignore[index]
            buy = _apply_buy_signals(
                db=MagicMock(),
                strategy=MagicMock(id="strategy"),
                run=MagicMock(id="run"),
                signals=pending,
                holdings=holdings,
                avg_entry_prices=averages,
                entry_trade_dates=entry_dates,
                entry_day_indices=entry_indices,
                entry_signal_features=entry_features,
                execution_prices=prices,
                execution_snapshots=identity,
                cash_ref=cash_ref,
                equity_before=equity_before,
                max_positions=int(risk["max_positions"]),
                position_size_pct=float(risk["position_size_pct"]),
                cost_config=costs,
                trade_day=trade_day,
                trade_day_index=day_index,
                repository=collector,
                stable_instrument_identity=True,
            )
            trade_count += sell.trade_count + buy.trade_count
            total_fees += sell.total_fees + buy.total_fees
            total_slippage += sell.total_slippage + buy.total_slippage
            cash = float(cash_ref["cash"])
            _inject_backtest_positions(
                identity,
                holdings,
                averages,
                entry_dates,
                entry_indices,
                entry_features,
                trade_day,
                day_index,
            )
            current = STRATEGY_HANDLERS[str(runtime["strategy_type"])](runtime, snapshots)
            for event in current:
                event.instrument_id = int(snapshots[event.symbol]["instrument_id"])
            annotate_and_rank_signals(runtime, current)
            for event in current:
                strength = event.metadata.get("strength") or {}
                signals.append(
                    (
                        day_index,
                        event.instrument_id,
                        1 if event.action == "BUY" else -1,
                        event.score,
                        strength.get("score"),
                        strength.get("rank"),
                        strength.get("passes_threshold", True),
                        event.reason,
                    )
                )
                signal_metadata.append(copy.deepcopy(event.metadata))
            pending = current
            _update_last_marks(holdings, last_prices, identity, prices)
            equity = _portfolio_equity(cash, holdings, last_prices)
            equities.append(equity)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

        return {
            "final_equity": equities[-1],
            "max_drawdown": max_drawdown,
            "signal_count": len(signals),
            "trade_count": trade_count,
            "total_fees": total_fees,
            "total_slippage": total_slippage,
            "signals": signals,
            "signal_metadata": signal_metadata,
            "transactions": transactions,
            "equity": equities,
            "holdings": copy.deepcopy(holdings),
            "averages": copy.deepcopy(averages),
            "entry_dates": copy.deepcopy(entry_dates),
            "entry_features": copy.deepcopy(entry_features),
            "last_prices": copy.deepcopy(last_prices),
        }

    def _assert_trades_match_oracle(
        self,
        result: quant_kernel.KernelResult,
        oracle: dict[str, object],
    ) -> None:
        transactions = oracle["transactions"]
        self.assertEqual(len(result.trades["side"]), len(transactions))
        for index, transaction in enumerate(transactions):
            meta = transaction["meta"]
            is_buy = transaction["side"] == "BUY"
            self.assertEqual(int(result.trades["side"][index]), 1 if is_buy else -1)
            self.assertEqual(
                int(result.trades["instrument_id"][index]),
                int(transaction["instrument_id"]),
            )
            for column, key in (("quantity", "qty"), ("price", "price"), ("fee", "fee")):
                self.assertAlmostEqual(
                    float(result.trades[column][index]),
                    float(transaction[key]),
                    delta=1e-10,
                )
            self.assertEqual(result.trades["reason"][index], meta["reason"])
            self.assertEqual(
                int(result.trades["execution_timestamp_us"][index]),
                int(transaction["ts"].timestamp() * 1_000_000),
            )
            self.assertEqual(
                int(result.trades["signal_timestamp_us"][index]),
                int(datetime.fromisoformat(meta["signal_ts"]).timestamp() * 1_000_000),
            )
            self.assertEqual(
                date.fromordinal(int(result.trades["execution_date_ordinal"][index])).isoformat(),
                meta["execution_trade_date"],
            )
            for column, key in (
                ("reference_price", "reference_price"),
                ("slippage_cost", "slippage_cost"),
                ("slippage_bps", "slippage_bps"),
                ("gross_notional", "gross_notional"),
                ("net_cash_flow", "net_cash_flow"),
            ):
                self.assertAlmostEqual(
                    float(result.trades[column][index]),
                    float(meta[key]),
                    delta=1e-10,
                )
            if is_buy:
                self._assert_nested_close(
                    json.loads(result.trades["entry_signal_features_json"][index]),
                    meta["entry_signal_features"],
                )
                setup_id = meta.get("setup_id")
                self.assertEqual(result.trades["setup_id"][index], setup_id or "")
                self.assertEqual(
                    int(result.trades["stage_index"][index]),
                    int(meta["stage_index"] or 0),
                )
                self.assertEqual(result.trades["stage_key"][index], meta.get("stage_key") or "")
                stage_target = float(result.trades["stage_target_pct"][index])
                if setup_id is None:
                    self.assertTrue(math.isnan(stage_target))
                else:
                    self.assertAlmostEqual(stage_target, float(meta["stage_target_pct"]), delta=1e-10)
                for column, key in (
                    ("position_quantity_before", "position_qty_before"),
                    ("position_quantity_after", "position_qty_after"),
                    ("position_average_entry_price_after", "position_avg_entry_price_after"),
                ):
                    self.assertAlmostEqual(
                        float(result.trades[column][index]),
                        float(meta[key]),
                        delta=1e-10,
                    )
            else:
                self.assertEqual(result.trades["entry_signal_features_json"][index], "")
                self.assertEqual(result.trades["setup_id"][index], "")
                self.assertEqual(int(result.trades["stage_index"][index]), 0)
                self.assertEqual(result.trades["stage_key"][index], "")
                self.assertTrue(math.isnan(float(result.trades["stage_target_pct"][index])))

    def _assert_final_positions_match_oracle(
        self,
        result: quant_kernel.KernelResult,
        oracle: dict[str, object],
    ) -> None:
        final_session = int(result.equity["session_index"][-1])
        native: dict[int, int] = {
            int(instrument_id): index
            for index, (session_index, instrument_id) in enumerate(
                zip(
                    result.positions["session_index"],
                    result.positions["instrument_id"],
                    strict=True,
                )
            )
            if int(session_index) == final_session
        }
        holdings = oracle["holdings"]
        self.assertEqual(set(native), set(holdings))
        for instrument_id, quantity in holdings.items():
            index = native[int(instrument_id)]
            self.assertAlmostEqual(
                float(result.positions["quantity"][index]),
                float(quantity),
                delta=1e-10,
            )
            self.assertAlmostEqual(
                float(result.positions["average_entry_price"][index]),
                float(oracle["averages"][instrument_id]),
                delta=1e-10,
            )
            self.assertAlmostEqual(
                float(result.positions["close"][index]),
                float(oracle["last_prices"][instrument_id]),
                delta=1e-10,
            )
            self.assertEqual(
                date.fromordinal(int(result.positions["entry_date_ordinal"][index])),
                oracle["entry_dates"][instrument_id],
            )
            self._assert_nested_close(
                json.loads(result.positions["entry_signal_features_json"][index]),
                oracle["entry_features"][instrument_id],
            )

    @staticmethod
    def _oracle_golden_record(oracle: dict[str, object]) -> dict[str, object]:
        payload = {
            "summary": {
                key: oracle[key]
                for key in (
                    "final_equity",
                    "max_drawdown",
                    "signal_count",
                    "trade_count",
                    "total_fees",
                    "total_slippage",
                )
            },
            "signals": oracle["signals"],
            "signal_metadata": oracle["signal_metadata"],
            "transactions": oracle["transactions"],
            "equity": oracle["equity"],
            "holdings": oracle["holdings"],
            "averages": oracle["averages"],
            "entry_dates": oracle["entry_dates"],
            "entry_features": oracle["entry_features"],
            "last_prices": oracle["last_prices"],
        }

        def encode(value: object) -> str:
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            raise TypeError(f"unsupported golden value: {type(value).__name__}")

        canonical = json.dumps(
            payload,
            default=encode,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        metadata = oracle["signal_metadata"]
        transactions = oracle["transactions"]
        return {
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "summary": payload["summary"],
            "signal_stages": [
                [
                    item.get("setup", {}).get("stage_index"),
                    item.get("setup", {}).get("stage_key"),
                ]
                for item in metadata
            ],
            "trade_stages": [
                [
                    item["side"],
                    item["meta"].get("stage_index"),
                    item["meta"].get("stage_key"),
                ]
                for item in transactions
            ],
        }

    def _assert_summary_matches_oracle(
        self,
        days: list[tuple[date, dict[str, dict[str, object]]]],
        runtime: dict[str, object],
        *,
        costs: BacktestCostConfig | None = None,
        oracle: dict[str, object] | None = None,
    ) -> quant_kernel.KernelResult:
        costs = costs or BacktestCostConfig(0.0, 0.0, 0.0)
        oracle = oracle or self._python_oracle(days, runtime, costs=costs)
        result = quant_kernel.run_backtest(
            self._dataset(days),
            runtime,
            {
                "initial_cash": 1_000.0,
                "commission_bps": costs.commission_bps,
                "commission_min": costs.commission_min,
                "slippage_bps": costs.slippage_bps,
            },
        )
        for key in (
            "final_equity",
            "max_drawdown",
            "signal_count",
            "trade_count",
            "total_fees",
            "total_slippage",
        ):
            self.assertAlmostEqual(float(result.summary[key]), float(oracle[key]), delta=1e-10)
        np.testing.assert_allclose(result.equity["equity"], oracle["equity"], atol=1e-10)
        native_signals = []
        for index, reason in enumerate(result.signals["reason"]):
            raw_score = float(result.signals["score"][index])
            strength = float(result.signals["strength_score"][index])
            native_signals.append(
                (
                    int(result.signals["session_index"][index]),
                    int(result.signals["instrument_id"][index]),
                    int(result.signals["action"][index]),
                    None if math.isnan(raw_score) else raw_score,
                    None if math.isnan(strength) else strength,
                    int(result.signals["strength_rank"][index]) or None,
                    bool(result.signals["passes_threshold"][index]),
                    reason,
                )
            )
        self._assert_nested_close(native_signals, oracle["signals"])
        self._assert_nested_close(
            [json.loads(value) for value in result.signals["metadata_json"]],
            oracle["signal_metadata"],
        )
        self._assert_trades_match_oracle(result, oracle)
        self._assert_final_positions_match_oracle(result, oracle)
        return result

    def test_momentum_t_plus_one_sell_first_and_shared_cash_match_python(self) -> None:
        days = self._market_days()
        runtime = self._runtime()
        oracle = self._python_oracle(days, runtime)
        result = self._assert_summary_matches_oracle(days, runtime)

        native_signals = []
        columns = result.signals
        for index, reason in enumerate(columns["reason"]):
            strength = float(columns["strength_score"][index])
            native_signals.append(
                (
                    int(columns["session_index"][index]),
                    int(columns["instrument_id"][index]),
                    int(columns["action"][index]),
                    float(columns["score"][index]),
                    None if math.isnan(strength) else strength,
                    int(columns["strength_rank"][index]) or None,
                    bool(columns["passes_threshold"][index]),
                    reason,
                )
            )
        self.assertEqual(native_signals, oracle["signals"])

        native_trades = result.trades
        self.assertEqual(native_trades["side"].tolist(), [1, -1, 1])
        self.assertEqual(native_trades["instrument_id"].tolist(), [1, 1, 2])
        self.assertEqual(native_trades["session_index"].tolist(), [1, 2, 2])
        self.assertEqual(native_trades["signal_session_index"].tolist(), [0, 1, 1])
        np.testing.assert_allclose(native_trades["quantity"], [50.0, 50.0, 22.5], atol=1e-10)
        np.testing.assert_allclose(native_trades["price"], [10.0, 8.0, 20.0], atol=1e-10)

    def test_trend_and_mean_reversion_ledgers_match_python(self) -> None:
        for strategy_type in ("trend", "mean_reversion"):
            with self.subTest(strategy_type=strategy_type):
                result = self._assert_summary_matches_oracle(
                    self._single_symbol_days(strategy_type),
                    self._single_symbol_runtime(strategy_type),
                )
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

    def test_all_staged_pattern_ledgers_match_python(self) -> None:
        cases = self._staged_pattern_cases()
        golden = json.loads(self.GOLDEN_PATH.read_text(encoding="utf-8"))
        for strategy_type, bars, params in cases:
            with self.subTest(strategy_type=strategy_type):
                days = self._pattern_days(bars)
                runtime = self._pattern_runtime(strategy_type, params)
                oracle = self._python_oracle(days, runtime)
                self.assertEqual(self._oracle_golden_record(oracle), golden[strategy_type])
                result = self._assert_summary_matches_oracle(days, runtime, oracle=oracle)
                self.assertGreater(result.summary["signal_count"], 0)
                self.assertGreater(result.summary["trade_count"], 0)
                self._assert_nested_close(
                    [json.loads(value) for value in result.signals["metadata_json"]],
                    oracle["signal_metadata"],
                )

    def test_staged_pattern_20_symbol_120_session_ledgers_match_python(self) -> None:
        for strategy_type, bars, params in self._staged_pattern_cases():
            with self.subTest(strategy_type=strategy_type):
                days = self._pattern_matrix_days(bars)
                runtime = self._pattern_runtime(
                    strategy_type,
                    params,
                    max_positions=20,
                    position_size_pct=0.02,
                )
                result = self._assert_summary_matches_oracle(days, runtime)
                self.assertEqual(result.summary["trading_days"], 120)
                self.assertEqual(len(result.equity["session_index"]), 120)

    def test_commission_minimum_and_slippage_match_python(self) -> None:
        days = self._market_days()
        runtime = self._runtime()
        costs = BacktestCostConfig(1.0, 1.0, 10.0)
        oracle = self._python_oracle(days, runtime, costs=costs)
        result = self._assert_summary_matches_oracle(days, runtime, costs=costs)
        transactions = oracle["transactions"]
        self.assertEqual(len(transactions), len(result.trades["side"]))
        for index, transaction in enumerate(transactions):
            self.assertAlmostEqual(result.trades["quantity"][index], transaction["qty"], delta=1e-10)
            self.assertAlmostEqual(result.trades["price"][index], transaction["price"], delta=1e-10)
            self.assertAlmostEqual(result.trades["fee"][index], transaction["fee"], delta=1e-10)
            self.assertAlmostEqual(
                result.trades["reference_price"][index],
                transaction["meta"]["reference_price"],
                delta=1e-10,
            )
            self.assertAlmostEqual(
                result.trades["slippage_cost"][index],
                transaction["meta"]["slippage_cost"],
                delta=1e-10,
            )
            self.assertEqual(result.trades["reason"][index], transaction["meta"]["reason"])
            self.assertEqual(
                int(result.trades["execution_timestamp_us"][index]),
                int(transaction["ts"].timestamp() * 1_000_000),
            )
            self.assertEqual(
                int(result.trades["signal_timestamp_us"][index]),
                int(datetime.fromisoformat(transaction["meta"]["signal_ts"]).timestamp() * 1_000_000),
            )
            self.assertEqual(
                date.fromordinal(int(result.trades["execution_date_ordinal"][index])).isoformat(),
                transaction["meta"]["execution_trade_date"],
            )
            self.assertAlmostEqual(
                result.trades["gross_notional"][index],
                transaction["meta"]["gross_notional"],
                delta=1e-10,
            )
            self.assertAlmostEqual(
                result.trades["net_cash_flow"][index],
                transaction["meta"]["net_cash_flow"],
                delta=1e-10,
            )
            self.assertAlmostEqual(
                result.trades["slippage_bps"][index],
                transaction["meta"]["slippage_bps"],
                delta=1e-10,
            )
            entry_features = result.trades["entry_signal_features_json"][index]
            if int(result.trades["side"][index]) == 1:
                self.assertEqual(
                    json.loads(entry_features),
                    transaction["meta"]["entry_signal_features"],
                )
                self.assertTrue(math.isnan(float(result.trades["stage_target_pct"][index])))
                self.assertEqual(float(result.trades["position_quantity_before"][index]), 0.0)
                self.assertAlmostEqual(
                    result.trades["position_quantity_after"][index],
                    transaction["qty"],
                    delta=1e-10,
                )
                self.assertAlmostEqual(
                    result.trades["position_average_entry_price_after"][index],
                    transaction["meta"]["position_avg_entry_price_after"],
                    delta=1e-10,
                )
            else:
                self.assertEqual(entry_features, "")
                self.assertTrue(math.isnan(result.trades["stage_target_pct"][index]))
                self.assertAlmostEqual(
                    result.trades["position_quantity_before"][index],
                    transaction["qty"],
                    delta=1e-10,
                )
                self.assertEqual(float(result.trades["position_quantity_after"][index]), 0.0)
                self.assertTrue(
                    math.isnan(result.trades["position_average_entry_price_after"][index])
                )

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
        oracle = self._python_oracle(days[start_index:], runtime)
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
        for key in (
            "final_equity",
            "max_drawdown",
            "signal_count",
            "trade_count",
            "total_fees",
            "total_slippage",
        ):
            self.assertAlmostEqual(float(result.summary[key]), float(oracle[key]), delta=1e-10)
        np.testing.assert_allclose(result.equity["equity"], oracle["equity"], atol=1e-10)
        self._assert_nested_close(
            [json.loads(value) for value in result.signals["metadata_json"]],
            oracle["signal_metadata"],
        )
        self._assert_trades_match_oracle(result, oracle)

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
        result = self._assert_summary_matches_oracle(days, runtime)

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


if __name__ == "__main__":
    unittest.main()
