from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gc
import json
import math
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
from src.services.strategy_engine import STRATEGY_HANDLERS
from src.services.strategy_registry import normalize_strategy_params
from src.services.backtest_universe_service import point_in_time_entry_eligible


@dataclass
class _TransactionCollector:
    rows: list[dict[str, object]]

    def add_transaction(self, values: dict[str, object]) -> None:
        self.rows.append(values)


class NativeBacktestKernelTests(unittest.TestCase):
    maxDiff = None

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

        for day_index, (trade_day, snapshots) in enumerate(days):
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
        }

    def _assert_summary_matches_oracle(
        self,
        days: list[tuple[date, dict[str, dict[str, object]]]],
        runtime: dict[str, object],
        *,
        costs: BacktestCostConfig | None = None,
    ) -> quant_kernel.KernelResult:
        costs = costs or BacktestCostConfig(0.0, 0.0, 0.0)
        oracle = self._python_oracle(days, runtime, costs=costs)
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
        self.assertEqual(
            [json.loads(value) for value in result.signals["metadata_json"]],
            oracle["signal_metadata"],
        )
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
                self.assertEqual(float(result.trades["stage_target_pct"][index]), 1.0)
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
        self.assertEqual(window_only.universe_membership["eligible_count"].tolist(), [0])
        self.assertEqual(window_only.universe_membership["excluded_history"].tolist(), [2])
        self.assertEqual(window_only.signals["instrument_id"].tolist(), [])

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
