from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from time import perf_counter
from typing import Any, Callable
from uuid import UUID

import quant_kernel
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.models.tables import Strategy, StrategyRun
from src.services.backtest_universe_service import (
    normalize_point_in_time_policy,
    resolve_backtest_universe,
    resolve_point_in_time_universe,
)
from src.services.backtest_worker_config import (
    resolve_backtest_intra_run_threads,
    resolve_effective_backtest_intra_run_threads,
)
from src.services.market_data_loader import MarketDataLoader
from src.services.native_result_repository import (
    NativePersistenceCancelledError,
    persist_native_result,
)
from src.services.prepared_dataset_service import (
    PREPARED_DATASET_SCHEMA_VERSION,
    PREPARED_INTEGER_INDEX,
    PreparedDatasetCache,
    PreparedDatasetDataChangedError,
    build_prepared_dataset_manifest,
    encode_prepared_snapshot,
    prepared_dataset_key,
)
from src.services.stock_basket_service import (
    DEFAULT_COMMON_STOCK_BASKET_NAME,
    load_default_common_stock_symbols,
)
from src.services.strategy_registry import (
    build_runtime_payload,
    build_strategy_catalog,
    extract_description,
    is_engine_ready,
    normalize_strategy_params,
)
from src.services.support_resistance_persistence_service import (
    SupportResistancePersistenceCancelledError,
    find_reusable_materialization,
    hydrate_state_from_materialization,
    persist_support_resistance_run,
    source_data_fingerprint,
)
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
)


UTC = timezone.utc


def _normalize_symbols(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


def _split_adjustments(
    db: Session,
    *,
    instrument_ids: list[int],
    start_date: date,
    end_date: date,
) -> list[list[Any]]:
    from src.services.backtest_engine import _load_split_adjustments_by_date

    values = _load_split_adjustments_by_date(
        db,
        [],
        start_date,
        end_date,
        instrument_ids=instrument_ids,
    )
    return [
        [trade_date.isoformat(), int(instrument_id), float(factor)]
        for trade_date, items in sorted(values.items())
        for instrument_id, factor in sorted(items.items())
    ]


def _support_hydration(
    db: Session,
    *,
    runtime: dict[str, Any],
    symbols: list[str],
    coverage_start: date,
    coverage_end: date,
    data_fingerprint: str,
) -> tuple[dict[str, Any], Any | None]:
    if runtime["strategy_type"] != "support_resistance":
        return {}, None
    materialization = find_reusable_materialization(
        db,
        runtime=runtime,
        symbols=symbols,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        expected_data_fingerprint=data_fingerprint,
    )
    if materialization is None:
        return {}, None
    state = hydrate_state_from_materialization(db, materialization)
    return {
        symbol: {
            "zone_timeline": list(symbol_state.cached_zone_timeline),
            "regime_timeline": list(symbol_state.cached_regime_timeline),
            "lifecycle_events": [list(item) for item in sorted(symbol_state.cached_lifecycle_events)],
        }
        for symbol, symbol_state in state.symbols.items()
    }, materialization


def _load_prepared_dataset(
    db: Session,
    *,
    runtime: dict[str, Any],
    symbols: list[str],
    resolved_universe: Any,
    start_date: date,
    end_date: date,
    universe_policy: dict[str, Any] | None,
    supplied: dict[str, Any] | None,
    performance: dict[str, Any],
) -> tuple[Any, dict[str, Any], str, Any | None]:
    from src.services.backtest_engine import FEATURE_RANGE_V2_SQL, _feature_snapshot_from_row
    from src.services.research_experiment_service import calculate_data_fingerprint

    if supplied is None:
        fingerprint_start = start_date
        fingerprint_end = end_date
        manifest: dict[str, Any] | None = None
    else:
        manifest = dict(supplied.get("manifest") or {})
        expected_key = str(supplied.get("key") or "")
        if prepared_dataset_key(manifest) != expected_key:
            raise ValueError("prepared dataset key does not match its manifest")
        request_range = list(manifest.get("fingerprint_request_range") or [])
        if len(request_range) != 2:
            raise ValueError("prepared dataset fingerprint request range is missing")
        fingerprint_start = date.fromisoformat(str(request_range[0]))
        fingerprint_end = date.fromisoformat(str(request_range[1]))
    fingerprint = calculate_data_fingerprint(
        db,
        symbols=symbols,
        start_date=fingerprint_start,
        end_date=fingerprint_end,
        universe_policy=universe_policy,
    )
    if manifest is None:
        manifest = build_prepared_dataset_manifest(
            data_fingerprint=fingerprint,
            strategy_type=runtime["strategy_type"],
            universe=resolved_universe.manifest(),
            requested_date_range=(start_date, end_date),
        )
    else:
        if manifest.get("data_fingerprint") != fingerprint["sha256"]:
            raise PreparedDatasetDataChangedError(
                "daily feature data changed since the prepared dataset was frozen"
            )
    manifest_instrument_ids = sorted(
        int(value) for value in manifest.get("instrument_ids") or []
    )
    if manifest_instrument_ids != sorted(
        int(value) for value in fingerprint.get("instrumentIds") or []
    ):
        raise PreparedDatasetDataChangedError("prepared dataset instrument set changed")

    cache = PreparedDatasetCache()
    dataset = cache.open(manifest)
    cache_status = "warm"
    if dataset is None:
        cache_status = "cold"
        feature_statement = text(FEATURE_RANGE_V2_SQL).bindparams(
            bindparam("instrument_ids", expanding=True)
        )
        build_start = date.fromisoformat(str(manifest["date_range"][0]))
        build_end = date.fromisoformat(str(manifest["date_range"][1]))

        def writer(array: Any) -> dict[str, Any]:
            loader = MarketDataLoader(
                db,
                statement=feature_statement,
                params={
                    "instrument_ids": manifest_instrument_ids,
                    "start_date": build_start,
                    "end_date": build_end,
                },
                row_factory=_feature_snapshot_from_row,
                performance=performance,
            )
            index = 0
            date_offsets: list[list[Any]] = []
            identity_intervals: dict[tuple[int, str], list[str]] = {}
            try:
                for trade_day, snapshots in loader.iter_days():
                    date_offsets.append([trade_day.isoformat(), index, len(snapshots)])
                    for snapshot in snapshots.values():
                        if index >= len(array):
                            raise PreparedDatasetDataChangedError(
                                "prepared dataset contains more rows than its fingerprint"
                            )
                        encode_prepared_snapshot(array, index, snapshot)
                        identity = (int(snapshot["instrument_id"]), str(snapshot["symbol"]))
                        interval = identity_intervals.setdefault(
                            identity,
                            [trade_day.isoformat(), trade_day.isoformat()],
                        )
                        interval[1] = trade_day.isoformat()
                        index += 1
            finally:
                loader.close()
            if index != len(array):
                raise PreparedDatasetDataChangedError(
                    "prepared dataset row count differs from its fingerprint"
                )
            return {
                "date_offsets": date_offsets,
                "instrument_symbol_intervals": [
                    [instrument_id, symbol, interval[0], interval[1]]
                    for (instrument_id, symbol), interval in sorted(identity_intervals.items())
                ],
                "corporate_actions": _split_adjustments(
                    db,
                    instrument_ids=manifest_instrument_ids,
                    start_date=build_start,
                    end_date=build_end,
                ),
            }

        dataset = cache.build(
            manifest,
            row_count=int(manifest["row_count"]),
            writer=writer,
        )
    coverage_start = date.fromisoformat(str(manifest["date_range"][0]))
    coverage_end = date.fromisoformat(str(manifest["date_range"][1]))
    hydration, materialization = _support_hydration(
        db,
        runtime=runtime,
        symbols=symbols,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        data_fingerprint=source_data_fingerprint(db)
        if runtime["strategy_type"] == "support_resistance"
        else fingerprint["sha256"],
    )
    if hydration:
        dataset.sidecar = {
            **dict(dataset.sidecar),
            "support_resistance_hydration": hydration,
        }
    return dataset, manifest, cache_status, materialization


def _native_support_state(result: Any, dataset: Any) -> SupportResistanceState:
    state = SupportResistanceState()
    symbols = list(result.symbols)
    support = result.support_resistance
    support = {} if support is None else support
    for key, target in (
        ("events", "events"),
        ("zone_versions", "zone_versions"),
        ("regime_versions", "regime_versions"),
    ):
        collection = support.get(key)
        if collection is None:
            continue
        # Native numeric columns are read-only numpy views, so they must never
        # be used in a boolean context (``x or []`` would raise an ambiguous
        # truth-value error). Normalize explicitly.
        instrument_ids = collection.get("instrument_id")
        symbol_ids = collection.get("symbol_id")
        payloads = collection.get("payload_json")
        instrument_ids = [] if instrument_ids is None else list(instrument_ids)
        symbol_ids = [] if symbol_ids is None else list(symbol_ids)
        payloads = [] if payloads is None else list(payloads)
        for instrument_id, symbol_id, payload in zip(
            instrument_ids,
            symbol_ids,
            payloads,
            strict=True,
        ):
            instrument_id = int(instrument_id)
            symbol = symbols[int(symbol_id)]
            symbol_state = state.symbols.setdefault(
                str(instrument_id),
                SupportResistanceSymbolState(instrument_id=instrument_id, symbol=symbol),
            )
            symbol_state.symbol = symbol
            getattr(symbol_state, target).append(json.loads(payload))
    integer_index = PREPARED_INTEGER_INDEX
    for row in dataset.integers:
        instrument_id = int(row[integer_index["instrument_id"]])
        symbol = symbols[int(row[integer_index["symbol_id"]])]
        symbol_state = state.symbols.setdefault(
            str(instrument_id),
            SupportResistanceSymbolState(instrument_id=instrument_id, symbol=symbol),
        )
        symbol_state.symbol = symbol
        symbol_state.history.append(
            {"dt_ny": date.fromordinal(int(row[integer_index["dt_ordinal"]]))}
        )
    return state


def _native_research_metrics(result: Any, initial_cash: float) -> dict[str, Any]:
    from src.services.backtest_engine import _research_metrics_from_simulation

    symbols = list(result.symbols)
    trades = result.trades
    equity = result.equity
    notional_by_symbol: dict[str, float] = {}
    net_cash_flow_by_symbol: dict[str, float] = {}
    for symbol_id, notional, cash_flow in zip(
        trades["symbol_id"],
        trades["gross_notional"],
        trades["net_cash_flow"],
        strict=True,
    ):
        symbol = symbols[int(symbol_id)]
        notional_by_symbol[symbol] = notional_by_symbol.get(symbol, 0.0) + abs(
            float(notional)
        )
        net_cash_flow_by_symbol[symbol] = net_cash_flow_by_symbol.get(symbol, 0.0) + float(
            cash_flow
        )
    positions_json = list(equity["positions_json"])
    ending_positions = json.loads(positions_json[-1]) if positions_json else {}
    return _research_metrics_from_simulation(
        equity_points=[float(value) for value in equity["equity"]],
        drawdown_points=[float(value) for value in equity["drawdown"]],
        notional_by_symbol=notional_by_symbol,
        net_cash_flow_by_symbol=net_cash_flow_by_symbol,
        ending_positions=ending_positions,
        initial_cash=initial_cash,
    )


def _native_universe_membership(result: Any, policy: dict[str, Any] | None) -> dict[str, Any] | None:
    values = result.universe_membership
    if policy is None or values is None:
        return None
    annual: dict[int, dict[str, Any]] = {}
    exclusion_columns = {
        "asset_type": "excluded_asset_type",
        "exchange": "excluded_exchange",
        "before_listing": "excluded_before_listing",
        "after_delisting": "excluded_after_delisting",
        "price": "excluded_price",
        "liquidity": "excluded_liquidity",
        "history": "excluded_history",
    }
    for index, ordinal in enumerate(values["date_ordinal"]):
        year = date.fromordinal(int(ordinal)).year
        eligible = int(values["eligible_count"][index])
        item = annual.setdefault(
            year,
            {
                "sessions": 0,
                "eligible_sum": 0,
                "eligible_min": None,
                "eligible_max": 0,
                "exclusions": {},
            },
        )
        item["sessions"] += 1
        item["eligible_sum"] += eligible
        item["eligible_min"] = (
            eligible
            if item["eligible_min"] is None
            else min(int(item["eligible_min"]), eligible)
        )
        item["eligible_max"] = max(int(item["eligible_max"]), eligible)
        for reason, column in exclusion_columns.items():
            count = int(values[column][index])
            if count:
                item["exclusions"][reason] = item["exclusions"].get(reason, 0) + count
    return {
        "policy": policy,
        "annual": {
            str(year): {
                **item,
                "eligible_average": item["eligible_sum"] / item["sessions"],
            }
            for year, item in sorted(annual.items())
        },
    }


def run_backtest_native(
    db: Session,
    strategy_id: UUID | str,
    start_date: date,
    end_date: date,
    *,
    initial_cash: float = 100_000.0,
    benchmark_symbol: str | None = None,
    commission_bps: float | None = None,
    commission_min: float | None = None,
    slippage_bps: float | None = None,
    universe_symbols: list[str] | None = None,
    universe_metadata: dict[str, Any] | None = None,
    universe_policy: dict[str, Any] | None = None,
    existing_run_id: UUID | str | None = None,
    runtime_params_override: dict[str, Any] | None = None,
    persist_level: str = "full",
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    prepared_dataset: dict[str, Any] | None = None,
) -> Any:
    from src.services.backtest_engine import (
        BacktestCancelledError,
        BacktestResult,
        _available_details,
        _normalize_persist_level,
        _peak_rss_mb,
        _resolve_backtest_cost_config,
    )

    started = perf_counter()
    performance: dict[str, Any] = {}
    configured_intra_run_threads = resolve_backtest_intra_run_threads()
    effective_intra_run_threads = resolve_effective_backtest_intra_run_threads(
        configured_intra_run_threads
    )
    level = _normalize_persist_level(persist_level)
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    runtime = build_runtime_payload(strategy)
    if runtime_params_override is not None:
        normalized = normalize_strategy_params(
            strategy.strategy_type,
            runtime_params_override,
            extract_description(runtime_params_override),
        )
        runtime["params"] = normalized
        runtime["engine_ready"] = is_engine_ready(strategy.strategy_type, normalized)
    if not runtime["engine_ready"]:
        raise ValueError("strategy is not engine-ready")

    policy = normalize_point_in_time_policy(universe_policy) if universe_policy else None
    if universe_symbols is not None and policy is not None:
        raise ValueError("provide universe_symbols or universe_policy, not both")
    if policy is not None:
        runtime["params"]["universe"] = {
            **runtime["params"]["universe"],
            "symbols": [],
            "selection_mode": "point_in_time_liquid",
            "policy": policy,
        }
        resolved = resolve_point_in_time_universe(
            db, policy, start_date=start_date - timedelta(days=400), end_date=end_date
        )
        symbols = [
            item.canonical_symbol or f"instrument-{item.instrument_id}"
            for item in resolved.instruments
        ]
    else:
        symbols = _normalize_symbols(
            universe_symbols if universe_symbols is not None else runtime["params"]["universe"].get("symbols")
        )
        if not symbols and runtime["params"]["universe"].get("selection_mode") == "all_common_stock":
            symbols = _normalize_symbols(load_default_common_stock_symbols(db))
            runtime["params"]["universe"]["default_label"] = DEFAULT_COMMON_STOCK_BASKET_NAME
        if not symbols:
            raise ValueError("backtest requires a non-empty universe")
        runtime["params"]["universe"]["symbols"] = symbols
        if universe_metadata:
            runtime["params"]["universe"]["basket"] = universe_metadata
        resolved = resolve_backtest_universe(
            db, symbols, start_date=start_date - timedelta(days=400), end_date=end_date
        )

    cost = _resolve_backtest_cost_config(
        runtime,
        commission_bps=commission_bps,
        commission_min=commission_min,
        slippage_bps=slippage_bps,
    )
    descriptor = next(
        item for item in build_strategy_catalog() if item["strategy_type"] == strategy.strategy_type
    )
    config_snapshot = {
        **dict(runtime["params"]),
        "run_options": {
            "persist_level": level,
            "intra_run_threads": effective_intra_run_threads,
            "universe_membership_semantics": resolved.membership_semantics,
            "survivorship_bias_warning": resolved.membership_semantics == "current_active_snapshot",
        },
        "universe_resolution": resolved.manifest(),
    }
    if existing_run_id is not None:
        run = db.get(StrategyRun, existing_run_id)
        if run is None:
            raise ValueError("backtest run not found")
        run.strategy_id = strategy.id
        run.strategy_version = strategy.version
        run.mode = "backtest"
        run.status = "running"
        run.started_at = datetime.now(UTC)
        run.finished_at = None
        run.window_start = start_date
        run.window_end = end_date
        run.initial_cash = initial_cash
        run.final_equity = None
        run.benchmark_symbol = benchmark_symbol
        run.config_snapshot = config_snapshot
        run.summary_metrics = {}
        run.error_message = None
    else:
        run = StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode="backtest",
            status="running",
            started_at=datetime.now(UTC),
            window_start=start_date,
            window_end=end_date,
            initial_cash=initial_cash,
            benchmark_symbol=benchmark_symbol,
            config_snapshot=config_snapshot,
        )
        db.add(run)
    db.commit()
    db.refresh(run)

    try:
        dataset, manifest, cache_status, reusable_materialization = _load_prepared_dataset(
            db,
            runtime=runtime,
            symbols=symbols,
            resolved_universe=resolved,
            start_date=start_date,
            end_date=end_date,
            universe_policy=policy,
            supplied=prepared_dataset,
            performance=performance,
        )
        session_dates = sorted(
            {
                date.fromordinal(int(value))
                for value in dataset.integers[:, PREPARED_INTEGER_INDEX["dt_ordinal"]]
                if start_date.toordinal() <= int(value) <= end_date.toordinal()
            }
        )

        def control(completed: int, total: int) -> bool:
            cancelled = bool(cancel_check and cancel_check())
            if progress_callback is not None and not cancelled:
                trade_date = session_dates[min(max(completed - 1, 0), len(session_dates) - 1)]
                progress_callback(
                    {
                        "phase": "running",
                        "trade_date": trade_date.isoformat(),
                        "completed_days": completed,
                        "total_days": total,
                        "percent": round((completed / total) * 85.0, 3),
                    }
                )
            return cancelled

        try:
            native_started = perf_counter()
            native_result = quant_kernel.run_backtest(
                dataset,
                runtime,
                {
                    "initial_cash": initial_cash,
                    "commission_bps": cost.commission_bps,
                    "commission_min": cost.commission_min,
                    "slippage_bps": cost.slippage_bps,
                    "thread_count": effective_intra_run_threads,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                control,
            )
            performance["native_kernel_ms"] = round(
                (perf_counter() - native_started) * 1000.0,
                3,
            )
        except quant_kernel.BacktestCancelledError as exc:
            raise BacktestCancelledError(str(exc)) from exc
        if cancel_check is not None and cancel_check():
            raise BacktestCancelledError("backtest cancellation requested before persistence")
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing",
                    "trade_date": end_date.isoformat(),
                    "completed_days": len(session_dates),
                    "total_days": len(session_dates),
                    "percent": 85.0,
                    "finalizing_stage": "backtest_details",
                    "completed_items": None,
                    "total_items": None,
                }
            )
        persist_stats = persist_native_result(
            db,
            run_id=run.id,
            strategy_id=strategy.id,
            result=native_result,
            persist_level=level,
            cancel_check=cancel_check,
        )
        materialization = reusable_materialization
        if runtime["strategy_type"] == "support_resistance":
            native_state = _native_support_state(native_result, dataset)
            materialization = persist_support_resistance_run(
                db,
                run=run,
                runtime=runtime,
                state=native_state,
                symbols=symbols,
                coverage_start=date.fromisoformat(str(manifest["date_range"][0])),
                coverage_end=date.fromisoformat(str(manifest["date_range"][1])),
                expected_data_fingerprint=source_data_fingerprint(db),
                persist_run_events=level == "full",
                cancel_check=cancel_check,
            )
        summary = dict(native_result.summary)
        native_performance = dict(native_result.performance)
        research_metrics = _native_research_metrics(native_result, initial_cash)
        final_equity = float(summary["final_equity"])
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        run.final_equity = final_equity
        run.summary_metrics = {
            **summary,
            "total_transaction_cost": float(summary["total_fees"]) + float(summary["total_slippage"]),
            "pending_signal_count": 0,
            "execution_lag": "next_session_open",
            "universe_size": len(symbols),
            "symbols_loaded": sorted(symbols),
            "strategy_type": runtime["strategy_type"],
            "cost_model": {
                "commission_bps": cost.commission_bps,
                "commission_min": cost.commission_min,
                "slippage_bps": cost.slippage_bps,
            },
            "benchmark_symbol": str(benchmark_symbol).upper() if benchmark_symbol else None,
            "persist_level": level,
            "available_details": _available_details(level),
            "support_resistance_materialization_id": str(materialization.id) if materialization else None,
            "support_resistance_cache_key": materialization.cache_key if materialization else None,
            "universe_membership": _native_universe_membership(native_result, policy),
            "delisting_zero_write_off": float(summary["delisting_zero_write_off"]),
            "delisting_last_close_sensitivity": 0.0,
            "kernel": {
                "version": quant_kernel.KERNEL_VERSION,
                "abi": quant_kernel.ABI_VERSION,
                "build_id": quant_kernel.BUILD_ID,
                "dataset_schema": PREPARED_DATASET_SCHEMA_VERSION,
                "strategy_revision": descriptor["algorithm_revision"],
            },
            "performance": {
                **performance,
                "configured_intra_run_threads": configured_intra_run_threads,
                "effective_intra_run_threads": effective_intra_run_threads,
                "intra_run_threads": int(native_performance["thread_count"]),
                "parallel_trading_days": int(native_performance["parallel_sessions"]),
                "serial_trading_days": int(native_performance["serial_sessions"]),
                "native_warmup_ms": round(float(native_performance["warmup_ms"]), 3),
                "native_signal_generation_ms": round(
                    float(native_performance["signal_generation_ms"]),
                    3,
                ),
                "prepared_dataset_status": cache_status,
                "prepared_dataset_key": prepared_dataset_key(manifest),
                "rows_loaded": len(dataset),
                "trading_days": int(summary["trading_days"]),
                "signals_generated": int(summary["signal_count"]),
                "trades_generated": int(summary["trade_count"]),
                "detail_rows_inserted": persist_stats.total,
                "peak_rss_mb": round(_peak_rss_mb(), 3),
                "engine_total_ms": round((perf_counter() - started) * 1000.0, 3),
            },
            **research_metrics,
        }
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "finalizing",
                    "trade_date": end_date.isoformat(),
                    "completed_days": len(session_dates),
                    "total_days": len(session_dates),
                    "percent": 99.0,
                    "finalizing_stage": "committing",
                    "completed_items": None,
                    "total_items": None,
                }
            )
        db.commit()
        db.refresh(run)
        return BacktestResult(
            run_id=str(run.id),
            strategy_id=str(strategy.id),
            status=run.status,
            initial_cash=float(initial_cash),
            final_equity=final_equity,
            total_return=float(summary["total_return"]),
            max_drawdown=float(summary["max_drawdown"]),
            signal_count=int(summary["signal_count"]),
            trade_count=int(summary["trade_count"]),
            total_fees=float(summary["total_fees"]),
            total_slippage=float(summary["total_slippage"]),
        )
    except Exception as exc:
        db.rollback()
        persistence_cancelled = isinstance(
            exc,
            (
                NativePersistenceCancelledError,
                SupportResistancePersistenceCancelledError,
            ),
        )
        failed = db.get(StrategyRun, run.id)
        if failed is not None:
            failed.status = (
                "cancelled"
                if isinstance(exc, BacktestCancelledError) or persistence_cancelled
                else "failed"
            )
            failed.finished_at = datetime.now(UTC)
            failed.error_message = str(exc)
            db.commit()
        if persistence_cancelled:
            raise BacktestCancelledError(str(exc)) from exc
        raise
