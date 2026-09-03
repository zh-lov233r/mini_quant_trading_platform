from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import (
    PaperTradingAccount,
    PortfolioSnapshot,
    Signal,
    Strategy,
    StrategyAllocation,
    StrategyRun,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    Transaction,
)
from src.services.alpaca_services import AlpacaAPIError, AlpacaClient, AlpacaClientError
from src.services.paper_account_service import (
    build_broker_account_isolation_report,
    build_alpaca_client_for_portfolio,
    require_strategy_portfolio_by_name,
)
from src.services.stock_basket_service import (
    DEFAULT_COMMON_STOCK_BASKET_NAME,
    load_default_common_stock_symbols,
)
from src.services.signal_strength_service import (
    get_signal_strength,
    ordered_entry_buy_signals,
    passes_strength_threshold,
)
from src.services.staged_entry_service import (
    can_apply_staged_entry,
    merge_entry_features,
    pattern_setup_from_metadata,
    select_highest_stage_signals,
)
from src.services.strategy_allocation_service import (
    DEFAULT_PORTFOLIO_NAME,
    get_strategy_allocation,
    list_allocated_strategies,
    normalize_portfolio_name,
    validate_portfolio_allocations,
)
from src.services.strategy_engine import (
    SignalEvent,
    evaluate_native_day,
    evaluate_native_signals,
    load_feature_market_data,
    required_recent_bar_count_for_runtime,
    required_recent_bar_lookback_days,
    support_resistance_hydration_payload,
    support_resistance_state_from_native_day,
)
from src.services.strategy_registry import build_runtime_payload
from src.services.support_resistance_persistence_service import (
    SupportResistanceMaterializationBuildError,
    find_reusable_materialization,
    hydrate_state_from_materialization,
    persist_support_resistance_run,
    record_failed_materialization_after_rollback,
    source_data_fingerprint,
)
from src.services.support_resistance_service import (
    SupportResistanceState,
    entry_price_is_inside_channel,
    project_entry_channel,
)


log = logging.getLogger("paper_trading")
PAPER_BROKER_ORDER_SOURCE = "alpaca_paper"
PAPER_BROKER_TERMINAL_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "filled",
    "rejected",
}
PAPER_TRANSACTION_SOURCES = {PAPER_BROKER_ORDER_SOURCE, "alpaca_live", "manual_virtual"}
PAPER_TRADING_TRIGGER_MANUAL = "manual"
PAPER_TRADING_TRIGGER_SCHEDULER = "scheduler"
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(slots=True)
class VirtualSubportfolioConfig:
    portfolio_name: str
    allocation_pct: float
    capital_base: float
    allow_fractional: bool
    source: str
    allocation_id: str | None = None


@dataclass(slots=True)
class VirtualPosition:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float = 0.0
    entry_trade_date: date | None = None
    entry_signal_features: dict[str, Any] | None = None

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass(slots=True)
class VirtualSubportfolioState:
    cash: float
    equity: float
    gross_exposure: float
    net_exposure: float
    positions_by_symbol: dict[str, VirtualPosition] = field(default_factory=dict)

    @property
    def long_position_count(self) -> int:
        return sum(1 for position in self.positions_by_symbol.values() if position.qty > 0)


@dataclass(slots=True)
class PaperTradingOrderOutcome:
    symbol: str
    action: str
    status: str
    reason: str
    client_order_id: str | None = None
    order_id: str | None = None
    qty: float | None = None
    filled_qty: float | None = None
    reference_price: float | None = None
    execution_price: float | None = None
    broker_status: str | None = None
    signal_strength: dict[str, Any] | None = None


@dataclass(slots=True)
class PaperTradingResult:
    run_id: str
    strategy_id: str
    status: str
    trade_date: date
    portfolio_name: str
    allocation_pct: float
    capital_base: float
    signal_count: int
    order_count: int
    submitted_order_count: int
    skipped_order_count: int
    failed_order_count: int
    final_cash: float
    final_equity: float
    pending_order_count: int = 0


@dataclass(slots=True)
class MultiStrategyPaperTradingResult:
    portfolio_name: str
    trade_date: date
    total_runs: int
    completed_runs: int
    failed_runs: int
    results: list[PaperTradingResult]


def run_paper_trading(
    db: Session,
    strategy_id: UUID | str,
    trade_date: date,
    *,
    alpaca_client: AlpacaClient | None = None,
    submit_orders: bool = True,
    universe_symbols: list[str] | None = None,
    universe_metadata: dict[str, Any] | None = None,
    portfolio_name: str | None = None,
    trigger: str = PAPER_TRADING_TRIGGER_MANUAL,
) -> PaperTradingResult:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")

    normalized_portfolio = normalize_portfolio_name(portfolio_name)
    runtime = build_runtime_payload(strategy)
    runtime = _resolve_runtime_universe(
        db,
        runtime,
        universe_symbols=universe_symbols,
        universe_metadata=universe_metadata,
    )
    if not runtime["engine_ready"]:
        raise ValueError("strategy is not engine-ready")

    symbols = runtime["params"]["universe"]["symbols"]
    if not symbols:
        raise ValueError("paper trading requires a non-empty symbol universe")

    started_at = datetime.now(timezone.utc)
    run = StrategyRun(
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        mode="paper",
        status="running",
        started_at=started_at,
        window_start=trade_date,
        window_end=trade_date,
        config_snapshot={
            **runtime["params"],
            "paper_trading": {
                "submit_orders": submit_orders,
                "portfolio_name": normalized_portfolio,
                "trigger": trigger,
            },
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        client = alpaca_client or build_alpaca_client_for_portfolio(db, normalized_portfolio)
        account_before = client.get_account()
        allocation_cfg = _resolve_virtual_subportfolio_config(
            db,
            strategy.id,
            normalized_portfolio,
            account_before,
        )
        run.config_snapshot = {
            **runtime["params"],
            "paper_trading": {
                "submit_orders": submit_orders,
                "portfolio_name": allocation_cfg.portfolio_name,
                "trigger": trigger,
                "allocation": {
                    "allocation_id": allocation_cfg.allocation_id,
                    "allocation_pct": allocation_cfg.allocation_pct,
                    "capital_base": allocation_cfg.capital_base,
                    "allow_fractional": allocation_cfg.allow_fractional,
                    "source": allocation_cfg.source,
                },
            },
        }
        db.flush()

        if submit_orders:
            _sync_strategy_pending_orders(
                db,
                strategy_id=strategy.id,
                portfolio_name=allocation_cfg.portfolio_name,
                client=client,
            )

        broker_positions_before = client.list_positions()
        open_orders = client.list_orders(status="open") if submit_orders else []
        broker_isolation_summary = None
        if submit_orders:
            portfolio = require_strategy_portfolio_by_name(db, allocation_cfg.portfolio_name)
            account = db.get(PaperTradingAccount, portfolio.paper_account_id)
            if account is None:
                raise ValueError(
                    f"paper account not found for portfolio: {allocation_cfg.portfolio_name}"
                )
            broker_isolation = build_broker_account_isolation_report(
                db,
                account,
                raw_positions=broker_positions_before,
                raw_orders=open_orders,
            )
            broker_isolation_summary = {
                "status": broker_isolation["status"],
                "active_external_order_count": broker_isolation["active_external_order_count"],
                "active_system_untracked_order_count": broker_isolation["active_system_untracked_order_count"],
                "active_external_position_count": broker_isolation["active_external_position_count"],
                "position_mismatch_count": broker_isolation["position_mismatch_count"],
                "warnings": list(broker_isolation["warnings"]),
            }
            if broker_isolation["status"] == "blocked":
                reason = "; ".join(broker_isolation["warnings"][:3]) or "active broker state does not match the local paper ledger"
                raise ValueError(
                    f"paper account isolation blocked live trading for portfolio '{allocation_cfg.portfolio_name}': {reason}"
                )

        recent_bar_count = required_recent_bar_count_for_runtime(runtime)
        recent_bar_lookback_days = required_recent_bar_lookback_days(recent_bar_count)
        support_resistance_source_fingerprint = (
            source_data_fingerprint(db)
            if runtime["strategy_type"] == "support_resistance"
            else None
        )
        snapshots = load_feature_market_data(
            db,
            trade_date,
            symbols,
            recent_bar_count=recent_bar_count,
            recent_bar_lookback_days=recent_bar_lookback_days,
        )
        if not snapshots:
            raise ValueError("no feature snapshots found for the requested universe and trade date")

        price_lookup_before = _build_price_lookup(snapshots, broker_positions_before)
        sleeve_before = _rebuild_virtual_subportfolio_state(
            db,
            strategy.id,
            allocation_cfg,
            price_lookup_before,
        )
        _inject_virtual_positions(
            snapshots,
            sleeve_before.positions_by_symbol,
            trade_date,
            use_trading_days=runtime["strategy_type"] == "support_resistance",
        )
        support_resistance_materialization = None
        if runtime["strategy_type"] == "support_resistance":
            replay_dates = [
                replay_date
                for symbol in symbols
                for bar in (snapshots.get(symbol) or {}).get("recent_bars") or []
                if (replay_date := _support_resistance_bar_date(bar)) is not None
            ]
            coverage_start = min(replay_dates) if replay_dates else trade_date
            reusable = find_reusable_materialization(
                db,
                runtime=runtime,
                symbols=symbols,
                coverage_start=coverage_start,
                coverage_end=trade_date,
                expected_data_fingerprint=support_resistance_source_fingerprint,
            )
            replay_state = (
                hydrate_state_from_materialization(db, reusable)
                if reusable is not None
                else SupportResistanceState()
            )
            for symbol, payload in support_resistance_hydration_payload(
                replay_state,
                snapshots,
            ).items():
                if symbol in snapshots:
                    snapshots[symbol]["support_resistance_hydration"] = payload
            signals, native_audit = evaluate_native_day(runtime, snapshots)
            replay_state = support_resistance_state_from_native_day(native_audit, snapshots)
            # Materialization and run-event audit must succeed before the first
            # possible broker order is submitted.
            support_resistance_materialization = persist_support_resistance_run(
                db,
                run=run,
                runtime=runtime,
                state=replay_state,
                symbols=symbols,
                coverage_start=coverage_start,
                coverage_end=trade_date,
                expected_data_fingerprint=support_resistance_source_fingerprint,
            )
        else:
            signals = evaluate_native_signals(runtime, snapshots)
        _prepare_support_resistance_paper_entries(
            signals,
            strategy=strategy,
            portfolio_name=allocation_cfg.portfolio_name,
            trade_date=trade_date,
            client=client,
            submit_orders=submit_orders,
        )
        _persist_signals(db, strategy, run, signals)

        order_outcomes, sleeve_after, broker_cash_after = _execute_paper_orders(
            db=db,
            strategy=strategy,
            run=run,
            runtime=runtime,
            trade_date=trade_date,
            client=client,
            broker_cash=_account_cash(account_before),
            sleeve_state=sleeve_before,
            allocation_cfg=allocation_cfg,
            open_orders=open_orders,
            signals=signals,
            snapshots=snapshots,
            submit_orders=submit_orders,
        )

        account_after = client.get_account() if submit_orders else account_before
        broker_positions_after = client.list_positions() if submit_orders else broker_positions_before
        if not submit_orders:
            account_after = {
                **account_before,
                "cash": broker_cash_after,
                "equity": _to_float(account_before.get("equity")),
            }

        price_lookup_after = _build_price_lookup(snapshots, broker_positions_after)
        sleeve_after = _mark_virtual_subportfolio_to_market(sleeve_after, price_lookup_after)

        signal_by_symbol = {event.symbol: event for event in signals}
        snapshot_ts = _snapshot_ts(signals, snapshots)
        db.add(
            PortfolioSnapshot(
                run_id=run.id,
                ts=snapshot_ts,
                cash=sleeve_after.cash,
                equity=sleeve_after.equity,
                gross_exposure=sleeve_after.gross_exposure,
                net_exposure=sleeve_after.net_exposure,
                drawdown=None,
                positions=_serialize_virtual_positions(sleeve_after.positions_by_symbol, signal_by_symbol),
                metrics={
                    "portfolio_name": allocation_cfg.portfolio_name,
                    "allocation_pct": allocation_cfg.allocation_pct,
                    "capital_base": allocation_cfg.capital_base,
                    "account_id": account_after.get("id"),
                    "account_status": account_after.get("status"),
                    "broker_cash": _account_cash(account_after),
                    "broker_equity": _account_equity(account_after),
                    "buying_power": _to_float(account_after.get("buying_power")),
                    "open_order_count": len(open_orders),
                    "signal_count": len(signals),
                    "order_count": len(order_outcomes),
                    "virtual_long_position_count": sleeve_after.long_position_count,
                },
            )
        )

        submitted_count = sum(1 for item in order_outcomes if item.status == "submitted")
        skipped_count = sum(1 for item in order_outcomes if item.status == "skipped")
        failed_count = sum(1 for item in order_outcomes if item.status == "failed")
        pending_count = sum(1 for item in order_outcomes if item.status == "pending")

        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.initial_cash = sleeve_before.cash
        run.final_equity = sleeve_after.equity
        run.summary_metrics = {
            "trade_date": str(trade_date),
            "portfolio_name": allocation_cfg.portfolio_name,
            "allocation_pct": allocation_cfg.allocation_pct,
            "capital_base": allocation_cfg.capital_base,
            "signal_count": len(signals),
            "order_count": len(order_outcomes),
            "submitted_order_count": submitted_count,
            "skipped_order_count": skipped_count,
            "failed_order_count": failed_count,
            "pending_order_count": pending_count,
            "submit_orders": submit_orders,
            "trigger": trigger,
            "universe_size": len(symbols),
            "symbols_loaded": sorted(snapshots.keys()),
            "symbols_signaled": sorted({event.symbol for event in signals}),
            "strategy_type": runtime["strategy_type"],
            "support_resistance_materialization_id": (
                str(support_resistance_materialization.id)
                if support_resistance_materialization is not None
                else None
            ),
            "support_resistance_cache_key": (
                support_resistance_materialization.cache_key
                if support_resistance_materialization is not None
                else None
            ),
            "account_id": account_after.get("id"),
            "account_status": account_after.get("status"),
            "broker_cash": _account_cash(account_after),
            "broker_equity": _account_equity(account_after),
            "broker_isolation": broker_isolation_summary,
            "virtual_cash_before": sleeve_before.cash,
            "virtual_equity_before": sleeve_before.equity,
            "virtual_cash_after": sleeve_after.cash,
            "virtual_equity_after": sleeve_after.equity,
            "virtual_gross_exposure_after": sleeve_after.gross_exposure,
            "virtual_long_position_count_after": sleeve_after.long_position_count,
            "orders": [
                {
                    "symbol": item.symbol,
                    "action": item.action,
                    "status": item.status,
                    "reason": item.reason,
                    "client_order_id": item.client_order_id,
                    "order_id": item.order_id,
                    "qty": item.qty,
                    "filled_qty": item.filled_qty,
                    "reference_price": item.reference_price,
                    "execution_price": item.execution_price,
                    "broker_status": item.broker_status,
                    "signal_strength": item.signal_strength,
                }
                for item in order_outcomes
            ],
        }
        db.commit()
        db.refresh(run)
        _log_paper_trading_run_summary(
            strategy_id=str(strategy.id),
            strategy_name=strategy.name,
            run_id=str(run.id),
            portfolio_name=allocation_cfg.portfolio_name,
            trade_date=trade_date,
            trigger=trigger,
            submit_orders=submit_orders,
            signal_count=len(signals),
            order_count=len(order_outcomes),
            submitted_order_count=submitted_count,
            skipped_order_count=skipped_count,
            failed_order_count=failed_count,
            final_cash=sleeve_after.cash,
            final_equity=sleeve_after.equity,
            pending_order_count=pending_count,
        )

        return PaperTradingResult(
            run_id=str(run.id),
            strategy_id=str(strategy.id),
            status=run.status,
            trade_date=trade_date,
            portfolio_name=allocation_cfg.portfolio_name,
            allocation_pct=allocation_cfg.allocation_pct,
            capital_base=allocation_cfg.capital_base,
            signal_count=len(signals),
            order_count=len(order_outcomes),
            submitted_order_count=submitted_count,
            skipped_order_count=skipped_count,
            failed_order_count=failed_count,
            final_cash=sleeve_after.cash,
            final_equity=sleeve_after.equity,
        )
    except Exception as exc:
        db.rollback()
        if isinstance(exc, SupportResistanceMaterializationBuildError):
            try:
                record_failed_materialization_after_rollback(db, exc)
            except Exception:
                db.rollback()
        failed_run = db.get(StrategyRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.finished_at = datetime.now(timezone.utc)
            failed_run.error_message = str(exc)
            db.commit()
        log.exception(
            "Paper trading run failed strategy_id=%s strategy_name=%s run_id=%s portfolio=%s trade_date=%s trigger=%s submit_orders=%s",
            str(strategy.id),
            strategy.name,
            str(run.id),
            normalized_portfolio,
            trade_date.isoformat(),
            trigger,
            submit_orders,
        )
        raise


def _support_resistance_bar_date(bar: dict[str, Any]) -> date | None:
    value = bar.get("dt_ny")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def run_multi_strategy_paper_trading(
    db: Session,
    trade_date: date,
    *,
    alpaca_client: AlpacaClient | None = None,
    portfolio_name: str | None = None,
    submit_orders: bool = True,
    continue_on_error: bool = False,
    auto_run_only: bool = False,
    trigger: str = PAPER_TRADING_TRIGGER_MANUAL,
) -> MultiStrategyPaperTradingResult:
    normalized_portfolio = normalize_portfolio_name(portfolio_name)
    allocated = list_allocated_strategies(
        db,
        portfolio_name=normalized_portfolio,
        auto_run_enabled=True if auto_run_only else None,
    )
    if not allocated:
        if auto_run_only:
            raise ValueError(
                f"no active auto-run strategy allocations found for portfolio '{normalized_portfolio}'"
            )
        raise ValueError(f"no active strategy allocations found for portfolio '{normalized_portfolio}'")

    validate_portfolio_allocations([allocation for _, allocation in allocated])
    client = alpaca_client or build_alpaca_client_for_portfolio(db, normalized_portfolio)
    results: list[PaperTradingResult] = []
    failed_runs = 0

    for strategy, allocation in allocated:
        try:
            result = run_paper_trading(
                db,
                strategy.id,
                trade_date,
                alpaca_client=client,
                submit_orders=submit_orders,
                portfolio_name=allocation.portfolio_name,
                trigger=trigger,
            )
            results.append(result)
        except Exception:
            failed_runs += 1
            if not continue_on_error:
                raise

    return MultiStrategyPaperTradingResult(
        portfolio_name=normalized_portfolio,
        trade_date=trade_date,
        total_runs=len(allocated),
        completed_runs=len(results),
        failed_runs=failed_runs,
        results=results,
    )


def process_pending_support_resistance_entries(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Validate and reconcile durable support/resistance BUY intents at the open."""
    now_ny = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    cutoff = _paper_entry_cutoff()
    signals = db.execute(
        select(Signal)
        .join(Strategy, Strategy.id == Signal.strategy_id)
        .join(StrategyRun, StrategyRun.id == Signal.run_id)
        .where(Strategy.strategy_type == "support_resistance")
        .where(StrategyRun.mode == "paper")
        .where(Signal.signal == "BUY")
        .order_by(Signal.ts.asc(), Signal.id.asc())
    ).scalars().all()
    actionable = [
        signal
        for signal in signals
        if isinstance((signal.features or {}).get("paper_execution"), dict)
        and str((signal.features or {})["paper_execution"].get("status"))
        in {"pending", "evaluating", "submitted"}
    ]
    if not actionable:
        return 0

    clients: dict[str, AlpacaClient] = {}
    touched_runs: set[UUID] = set()
    run_contexts: dict[UUID, tuple[AlpacaClient, str, Strategy]] = {}
    processed = 0
    for signal in actionable:
        execution = dict((signal.features or {})["paper_execution"])
        try:
            eligible_date = date.fromisoformat(str(execution["eligible_trade_date"]))
        except (KeyError, ValueError):
            _update_signal_paper_execution(signal, status="failed", reason_code="invalid_eligible_trade_date")
            touched_runs.add(signal.run_id)
            processed += 1
            continue
        if eligible_date > now_ny.date():
            continue

        run = db.get(StrategyRun, signal.run_id)
        strategy = db.get(Strategy, signal.strategy_id)
        if run is None or strategy is None:
            continue
        paper_cfg = (run.config_snapshot or {}).get("paper_trading") or {}
        portfolio_name = normalize_portfolio_name(paper_cfg.get("portfolio_name"))
        client = clients.get(portfolio_name)
        if client is None:
            client = build_alpaca_client_for_portfolio(db, portfolio_name)
            clients[portfolio_name] = client
        run_contexts[run.id] = (client, portfolio_name, strategy)
        status = str(execution.get("status"))
        projected_channel = project_entry_channel(execution.get("entry_channel"), sessions=1)
        cutoff_reached = eligible_date < now_ny.date() or now_ny.time() >= cutoff
        if status != "submitted" and cutoff_reached:
            _update_signal_paper_execution(
                signal,
                status="expired",
                reason_code="fresh_open_quote_unavailable_before_cutoff",
                projected_channel=projected_channel,
            )
            _append_support_resistance_execution_event(
                db,
                signal,
                event_date=eligible_date,
                event_type="entry_channel_rejection",
                reason_code="fresh_open_quote_unavailable_before_cutoff",
                projected_channel=projected_channel,
            )
            touched_runs.add(run.id)
            processed += 1
            continue
        if status == "submitted" and cutoff_reached:
            _reconcile_submitted_channel_entry(
                db,
                signal=signal,
                strategy=strategy,
                run=run,
                client=client,
                portfolio_name=portfolio_name,
                projected_channel=projected_channel,
                quote_inside_channel=False,
                cutoff_reached=True,
            )
            touched_runs.add(run.id)
            processed += 1
            continue
        clock = client.get_clock()
        if not bool(clock.get("is_open")):
            continue

        snapshots = client.get_stock_snapshots([signal.symbol])
        ask_price, quote_ts = _fresh_snapshot_ask(
            snapshots.get(signal.symbol.upper()),
            expected_date=eligible_date,
            now=now_ny,
        )
        inside_channel = False
        channel_reason = "missing_fresh_open_quote"
        if ask_price is not None:
            inside_channel, channel_reason = entry_price_is_inside_channel(
                projected_channel,
                ask_price,
            )

        if status == "submitted":
            _reconcile_submitted_channel_entry(
                db,
                signal=signal,
                strategy=strategy,
                run=run,
                client=client,
                portfolio_name=portfolio_name,
                projected_channel=projected_channel,
                quote_inside_channel=inside_channel,
                cutoff_reached=False,
            )
            touched_runs.add(run.id)
            processed += 1
            continue

        if ask_price is None:
            _append_signal_execution_attempt(signal, now_ny, channel_reason)
            continue
        if not inside_channel:
            _update_signal_paper_execution(
                signal,
                status="skipped",
                reason_code=channel_reason,
                quote_price=ask_price,
                quote_ts=quote_ts,
                projected_channel=projected_channel,
            )
            _append_support_resistance_execution_event(
                db,
                signal,
                event_date=eligible_date,
                event_type="entry_channel_rejection",
                reason_code=channel_reason,
                projected_channel=projected_channel,
                execution_price=ask_price,
            )
            touched_runs.add(run.id)
            processed += 1
            continue

        open_sells = client.list_orders(status="open", side="sell")
        if any(str(order.get("client_order_id") or "").startswith("paper-") for order in open_sells):
            _append_signal_execution_attempt(signal, now_ny, "waiting_for_system_sell_orders")
            continue

        account = client.get_account()
        allocation_cfg = _resolve_virtual_subportfolio_config(
            db,
            strategy.id,
            portfolio_name,
            account,
        )
        existing_order = next(
            (
                order
                for order in client.list_orders(
                    status="all",
                    limit=500,
                    symbols=[signal.symbol],
                    side="buy",
                )
                if str(order.get("client_order_id") or "") == str(execution["client_order_id"])
            ),
            None,
        )
        if existing_order is not None:
            requested_qty = _to_float(existing_order.get("qty"))
            existing_transaction = next(
                (
                    item
                    for item in db.execute(
                        select(Transaction)
                        .where(Transaction.strategy_id == strategy.id)
                        .where(Transaction.run_id == run.id)
                    ).scalars().all()
                    if str((item.meta or {}).get("client_order_id") or "")
                    == str(execution["client_order_id"])
                ),
                None,
            )
            transaction = _upsert_paper_broker_order_transaction(
                db,
                strategy_id=strategy.id,
                run_id=run.id,
                symbol=signal.symbol,
                side="BUY",
                trade_date=eligible_date,
                reason=str(signal.reason or "support/resistance valid-channel entry"),
                signal_ts=signal.ts,
                entry_signal_features=dict(signal.features or {}),
                order=existing_order,
                requested_qty=requested_qty,
                reference_price=ask_price,
                client_order_id=str(execution["client_order_id"]),
                portfolio_name=portfolio_name,
                allocation_pct=allocation_cfg.allocation_pct,
                existing_txn=existing_transaction,
            )
            broker_status = str((transaction.meta or {}).get("broker_status") or "").lower()
            _update_signal_paper_execution(
                signal,
                status="submitted",
                reason_code="existing_client_order_reconciled",
                order_id=transaction.order_id,
                broker_status=broker_status,
                terminal=broker_status in PAPER_BROKER_TERMINAL_STATUSES,
                projected_channel=projected_channel,
            )
            if _transaction_represents_virtual_fill(transaction):
                _record_channel_fill_violation_if_needed(
                    db,
                    signal=signal,
                    execution_price=float(transaction.price),
                    projected_channel=projected_channel,
                    transaction=transaction,
                )
            touched_runs.add(run.id)
            processed += 1
            continue

        runtime = build_runtime_payload(strategy)
        sleeve = _rebuild_virtual_subportfolio_state(
            db,
            strategy.id,
            allocation_cfg,
            {signal.symbol.upper(): ask_price},
        )
        current_position = sleeve.positions_by_symbol.get(signal.symbol.upper())
        if current_position is not None and current_position.qty > 0:
            reason = (
                "channel_fill_violation_blocks_add"
                if _position_has_channel_fill_violation(current_position)
                else "virtual_long_position_already_exists"
            )
            _update_signal_paper_execution(signal, status="skipped", reason_code=reason)
            touched_runs.add(run.id)
            processed += 1
            continue
        risk_cfg = runtime["params"]["risk"]
        if sleeve.long_position_count >= int(risk_cfg["max_positions"]):
            _update_signal_paper_execution(signal, status="skipped", reason_code="max_positions_reached")
            touched_runs.add(run.id)
            processed += 1
            continue
        event = SignalEvent(
            strategy_id=str(strategy.id),
            ts=signal.ts,
            symbol=signal.symbol,
            action="BUY",
            reason=str(signal.reason or "support/resistance valid-channel entry"),
            score=float(signal.score) if signal.score is not None else None,
            metadata=dict(signal.features or {}),
            instrument_id=signal.instrument_id,
        )
        if not passes_strength_threshold(event):
            _update_signal_paper_execution(signal, status="skipped", reason_code="strength_below_threshold")
            touched_runs.add(run.id)
            processed += 1
            continue
        target_value = min(
            sleeve.cash,
            _account_cash(account),
            sleeve.equity * float(risk_cfg["position_size_pct"]),
        )
        qty = _estimate_paper_buy_qty(
            target_value,
            ask_price,
            allow_fractional=allocation_cfg.allow_fractional,
        )
        if qty <= 0:
            _update_signal_paper_execution(signal, status="skipped", reason_code="insufficient_cash")
            touched_runs.add(run.id)
            processed += 1
            continue
        limit_price = _round_limit_price_down(float(projected_channel["upper"]))
        outcome = _submit_paper_order(
            db=db,
            strategy=strategy,
            run=run,
            trade_date=eligible_date,
            client=client,
            event=event,
            submit_orders=True,
            qty=qty,
            reference_price=ask_price,
            client_order_id=str(execution["client_order_id"]),
            portfolio_name=portfolio_name,
            allocation_pct=allocation_cfg.allocation_pct,
            order_type="limit",
            limit_price=limit_price,
        )
        _update_signal_paper_execution(
            signal,
            status="submitted" if outcome.status == "submitted" else "failed",
            reason_code=(
                "strict_channel_limit_order_submitted"
                if outcome.status == "submitted"
                else "limit_order_submission_failed"
            ),
            quote_price=ask_price,
            quote_ts=quote_ts,
            projected_channel=projected_channel,
            limit_price=limit_price,
            order_id=outcome.order_id,
            broker_status=outcome.broker_status,
            terminal=str(outcome.broker_status or "").lower() in PAPER_BROKER_TERMINAL_STATUSES,
        )
        if outcome.execution_price is not None:
            violation = _record_channel_fill_violation_if_needed(
                db,
                signal=signal,
                execution_price=outcome.execution_price,
                projected_channel=projected_channel,
            )
            if violation and outcome.order_id and str(outcome.broker_status or "").lower() not in PAPER_BROKER_TERMINAL_STATUSES:
                client.cancel_order(outcome.order_id)
        touched_runs.add(run.id)
        processed += 1

    for run_id in touched_runs:
        _refresh_pending_run_summary(db, run_id, context=run_contexts.get(run_id))
    db.commit()
    return processed


def _paper_entry_cutoff() -> time:
    raw = os.getenv("PAPER_TRADING_OPEN_ENTRY_CUTOFF_NY", "09:35").strip()
    try:
        return time.fromisoformat(raw)
    except (TypeError, ValueError):
        log.warning("Invalid PAPER_TRADING_OPEN_ENTRY_CUTOFF_NY=%r; using 09:35", raw)
        return time(hour=9, minute=35)


def _fresh_snapshot_ask(
    snapshot: dict[str, Any] | None,
    *,
    expected_date: date,
    now: datetime | None = None,
) -> tuple[float | None, str | None]:
    quote = (snapshot or {}).get("latestQuote")
    if not isinstance(quote, dict):
        return None, None
    ask = _to_float(quote.get("ap"))
    quote_ts = str(quote.get("t") or "") or None
    parsed = _parse_iso_datetime(quote_ts)
    parsed_ny = parsed.astimezone(NEW_YORK) if parsed is not None else None
    if (
        ask <= 0
        or parsed_ny is None
        or parsed_ny.date() != expected_date
        or parsed_ny.time() < time(hour=9, minute=30)
    ):
        return None, quote_ts
    if now is not None:
        max_age = _paper_quote_max_age_seconds()
        age = (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > max_age:
            return None, quote_ts
    return ask, quote_ts


def _paper_quote_max_age_seconds() -> float:
    raw = os.getenv("PAPER_TRADING_OPEN_QUOTE_MAX_AGE_SECONDS", "15").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 15.0
    return value if value > 0 else 15.0


def _round_limit_price_down(price: float) -> float:
    quantum = Decimal("0.0001") if price < 1 else Decimal("0.01")
    return float(Decimal(str(price)).quantize(quantum, rounding=ROUND_DOWN))


def _update_signal_paper_execution(signal: Signal, **updates: Any) -> None:
    features = dict(signal.features or {})
    execution = dict(features.get("paper_execution") or {})
    execution.update(updates)
    execution["updated_at"] = datetime.now(timezone.utc).isoformat()
    features["paper_execution"] = execution
    signal.features = features


def _append_signal_execution_attempt(signal: Signal, now_ny: datetime, reason_code: str) -> None:
    execution = dict((signal.features or {}).get("paper_execution") or {})
    attempts = list(execution.get("attempts") or [])
    attempts.append({"ts": now_ny.isoformat(), "reason_code": reason_code})
    _update_signal_paper_execution(signal, status="evaluating", attempts=attempts[-50:])


def _position_has_channel_fill_violation(position: VirtualPosition) -> bool:
    execution = (position.entry_signal_features or {}).get("paper_execution") or {}
    return bool(execution.get("channel_fill_violation"))


def _reconcile_submitted_channel_entry(
    db: Session,
    *,
    signal: Signal,
    strategy: Strategy,
    run: StrategyRun,
    client: AlpacaClient,
    portfolio_name: str,
    projected_channel: dict[str, Any],
    quote_inside_channel: bool,
    cutoff_reached: bool,
) -> None:
    execution = dict((signal.features or {}).get("paper_execution") or {})
    order_id = str(execution.get("order_id") or "")
    if not order_id:
        _update_signal_paper_execution(signal, status="failed", reason_code="submitted_order_missing_id")
        return
    _sync_strategy_pending_orders(
        db,
        strategy_id=strategy.id,
        portfolio_name=portfolio_name,
        client=client,
    )
    transaction = next(
        (
            item
            for item in db.execute(
                select(Transaction)
                .where(Transaction.strategy_id == strategy.id)
                .where(Transaction.run_id == run.id)
                .order_by(Transaction.ts.desc())
            ).scalars().all()
            if str(item.order_id or "") == order_id
        ),
        None,
    )
    if transaction is None:
        _update_signal_paper_execution(signal, status="failed", reason_code="submitted_order_not_in_ledger")
        return
    broker_status = str((transaction.meta or {}).get("broker_status") or "").lower()
    violation = False
    if _transaction_represents_virtual_fill(transaction):
        violation = _record_channel_fill_violation_if_needed(
            db,
            signal=signal,
            execution_price=float(transaction.price),
            projected_channel=projected_channel,
            transaction=transaction,
        )
    if broker_status in PAPER_BROKER_TERMINAL_STATUSES:
        _update_signal_paper_execution(signal, broker_status=broker_status, terminal=True)
        return
    if violation or cutoff_reached or not quote_inside_channel:
        client.cancel_order(order_id)
        _update_signal_paper_execution(
            signal,
            broker_status="cancel_requested",
            terminal=True,
            reason_code=(
                "channel_fill_violation"
                if violation
                else "entry_cutoff_reached"
                if cutoff_reached
                else "quote_left_valid_channel"
            ),
        )


def _record_channel_fill_violation_if_needed(
    db: Session,
    *,
    signal: Signal,
    execution_price: float,
    projected_channel: dict[str, Any],
    transaction: Transaction | None = None,
) -> bool:
    lower = _to_float(projected_channel.get("lower"))
    if lower <= 0 or execution_price >= lower:
        return False
    violation = {
        "reason_code": "channel_fill_below_support_inner_edge",
        "execution_price": execution_price,
        "projected_lower": lower,
    }
    _update_signal_paper_execution(signal, channel_fill_violation=violation)
    execution = (signal.features or {}).get("paper_execution") or {}
    try:
        event_date = date.fromisoformat(str(execution.get("eligible_trade_date")))
    except ValueError:
        event_date = signal.ts.date()
    _append_support_resistance_execution_event(
        db,
        signal,
        event_date=event_date,
        event_type="channel_fill_violation",
        reason_code=violation["reason_code"],
        projected_channel=projected_channel,
        execution_price=execution_price,
    )
    if transaction is None:
        transaction = next(
            (
                item
                for item in db.execute(
                    select(Transaction)
                    .where(Transaction.run_id == signal.run_id)
                    .where(Transaction.symbol == signal.symbol)
                    .order_by(Transaction.ts.desc())
                ).scalars().all()
                if item.side == "BUY"
            ),
            None,
        )
    if transaction is not None:
        meta = dict(transaction.meta or {})
        features = dict(meta.get("entry_signal_features") or {})
        paper_execution = dict(features.get("paper_execution") or {})
        paper_execution["channel_fill_violation"] = violation
        features["paper_execution"] = paper_execution
        meta["entry_signal_features"] = features
        meta["channel_fill_violation"] = violation
        transaction.meta = meta
    return True


def _append_support_resistance_execution_event(
    db: Session,
    signal: Signal,
    *,
    event_date: date,
    event_type: str,
    reason_code: str,
    projected_channel: dict[str, Any],
    execution_price: float | None = None,
) -> None:
    link = db.execute(
        select(SupportResistanceRunMaterialization).where(
            SupportResistanceRunMaterialization.run_id == signal.run_id
        )
    ).scalar_one_or_none()
    if link is None:
        return
    duplicate = db.execute(
        select(SupportResistanceRunEvent)
        .where(SupportResistanceRunEvent.run_id == signal.run_id)
        .where(SupportResistanceRunEvent.symbol == signal.symbol)
        .where(SupportResistanceRunEvent.event_date == event_date)
        .where(SupportResistanceRunEvent.event_type == event_type)
    ).scalars().first()
    if duplicate is not None:
        return
    payload = {
        "event_date": event_date.isoformat(),
        "event_type": event_type,
        "reason_code": reason_code,
        "execution_price": execution_price,
        "lower": projected_channel.get("lower"),
        "upper": projected_channel.get("upper"),
        "entry_channel": projected_channel,
    }
    db.add(
        SupportResistanceRunEvent(
            run_id=signal.run_id,
            materialization_id=link.materialization_id,
            instrument_id=signal.instrument_id,
            symbol=signal.symbol,
            event_date=event_date,
            event_type=event_type,
            zone_key=projected_channel.get("support_zone_key"),
            setup=((signal.features or {}).get("support_resistance") or {}).get("selected_setup"),
            selected=False,
            lower_price=projected_channel.get("lower"),
            upper_price=projected_channel.get("upper"),
            payload=payload,
        )
    )


def _refresh_pending_run_summary(
    db: Session,
    run_id: UUID,
    *,
    context: tuple[AlpacaClient, str, Strategy] | None = None,
) -> None:
    run = db.get(StrategyRun, run_id)
    if run is None:
        return
    signals = db.execute(select(Signal).where(Signal.run_id == run_id)).scalars().all()
    executions = [
        (signal.features or {}).get("paper_execution")
        for signal in signals
        if isinstance((signal.features or {}).get("paper_execution"), dict)
    ]
    pending_count = sum(
        1
        for execution in executions
        if execution.get("status") in {"pending", "evaluating", "submitted"}
        and not execution.get("terminal")
    )
    summary = dict(run.summary_metrics or {})
    base_counts = dict(summary.get("pre_open_order_counts") or {})
    if not base_counts:
        base_counts = {
            "submitted": int(summary.get("submitted_order_count") or 0),
            "skipped": int(summary.get("skipped_order_count") or 0),
            "failed": int(summary.get("failed_order_count") or 0),
        }
    summary["pre_open_order_counts"] = base_counts
    summary["submitted_order_count"] = base_counts["submitted"] + sum(
        1 for execution in executions if execution.get("status") == "submitted"
    )
    summary["skipped_order_count"] = base_counts["skipped"] + sum(
        1 for execution in executions if execution.get("status") in {"skipped", "expired"}
    )
    summary["failed_order_count"] = base_counts["failed"] + sum(
        1 for execution in executions if execution.get("status") == "failed"
    )
    summary["pending_order_count"] = pending_count
    summary["paper_entry_executions"] = executions
    if pending_count == 0 and context is not None:
        client, portfolio_name, strategy = context
        try:
            account = client.get_account()
            allocation_cfg = _resolve_virtual_subportfolio_config(
                db,
                strategy.id,
                portfolio_name,
                account,
            )
            broker_positions = client.list_positions()
            price_lookup = {
                str(position.get("symbol") or "").upper(): _to_float(position.get("current_price"))
                for position in broker_positions
                if position.get("symbol") and _to_float(position.get("current_price")) > 0
            }
            sleeve = _rebuild_virtual_subportfolio_state(
                db,
                strategy.id,
                allocation_cfg,
                price_lookup,
            )
            run.final_equity = sleeve.equity
            summary.update(
                {
                    "virtual_cash_after": sleeve.cash,
                    "virtual_equity_after": sleeve.equity,
                    "virtual_gross_exposure_after": sleeve.gross_exposure,
                    "virtual_long_position_count_after": sleeve.long_position_count,
                }
            )
            db.add(
                PortfolioSnapshot(
                    run_id=run.id,
                    ts=datetime.now(timezone.utc),
                    cash=sleeve.cash,
                    equity=sleeve.equity,
                    gross_exposure=sleeve.gross_exposure,
                    net_exposure=sleeve.net_exposure,
                    drawdown=None,
                    positions=_serialize_virtual_positions(sleeve.positions_by_symbol, {}),
                    metrics={
                        "portfolio_name": portfolio_name,
                        "paper_open_entry_reconciled": True,
                    },
                )
            )
        except AlpacaClientError as exc:
            log.warning("Could not refresh reconciled paper snapshot for run %s: %s", run_id, exc)
    run.summary_metrics = summary


def _resolve_runtime_universe(
    db: Session,
    runtime: dict[str, Any],
    *,
    universe_symbols: list[str] | None,
    universe_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if universe_symbols is not None:
        normalized_symbols = _normalize_symbol_universe(universe_symbols)
        runtime["params"]["universe"]["symbols"] = normalized_symbols
        runtime["params"]["universe"]["selection_mode"] = "stock_basket"
        if universe_metadata:
            runtime["params"]["universe"]["basket"] = universe_metadata
        return runtime

    universe_cfg = runtime["params"]["universe"]
    if (
        not universe_cfg.get("symbols")
        and universe_cfg.get("selection_mode") == "all_common_stock"
    ):
        normalized_symbols = _normalize_symbol_universe(load_default_common_stock_symbols(db))
        universe_cfg["symbols"] = normalized_symbols
        universe_cfg["default_label"] = DEFAULT_COMMON_STOCK_BASKET_NAME
    return runtime


def _resolve_virtual_subportfolio_config(
    db: Session,
    strategy_id: UUID,
    portfolio_name: str,
    account: dict[str, Any],
) -> VirtualSubportfolioConfig:
    allocation = get_strategy_allocation(
        db,
        strategy_id,
        portfolio_name=portfolio_name,
        status="active",
    )
    account_equity = _account_equity(account)
    if allocation is None:
        return VirtualSubportfolioConfig(
            portfolio_name=portfolio_name,
            allocation_pct=1.0,
            capital_base=account_equity,
            allow_fractional=True,
            source="implicit_full_account",
            allocation_id=None,
        )

    capital_base = (
        float(allocation.capital_base)
        if allocation.capital_base is not None
        else account_equity * float(allocation.allocation_pct or 0)
    )
    return _to_virtual_config(allocation, capital_base=capital_base)


def _to_virtual_config(
    allocation: StrategyAllocation,
    *,
    capital_base: float,
) -> VirtualSubportfolioConfig:
    return VirtualSubportfolioConfig(
        portfolio_name=allocation.portfolio_name,
        allocation_pct=float(allocation.allocation_pct or 0),
        capital_base=max(capital_base, 0.0),
        allow_fractional=bool(allocation.allow_fractional),
        source="strategy_allocation",
        allocation_id=str(allocation.id),
    )


def _persist_signals(
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    signals: list[SignalEvent],
) -> None:
    for event in signals:
        db.add(
            Signal(
                run_id=run.id,
                strategy_id=strategy.id,
                ts=event.ts,
                symbol=event.symbol,
                signal=event.action,
                score=event.score,
                reason=event.reason,
                features=event.metadata,
            )
        )


def _prepare_support_resistance_paper_entries(
    signals: list[SignalEvent],
    *,
    strategy: Strategy,
    portfolio_name: str,
    trade_date: date,
    client: AlpacaClient,
    submit_orders: bool,
) -> None:
    eligible_trade_date = (
        _next_broker_open_date(client, trade_date)
        if submit_orders
        else _next_weekday(trade_date)
    )
    for event in signals:
        support_resistance = event.metadata.get("support_resistance")
        if event.action != "BUY" or not isinstance(support_resistance, dict):
            continue
        event.metadata = dict(event.metadata)
        event.metadata["paper_execution"] = {
            "status": "pending" if submit_orders else "dry_run",
            "eligible_trade_date": eligible_trade_date.isoformat(),
            "signal_trade_date": trade_date.isoformat(),
            "client_order_id": _client_order_id(
                strategy.id,
                portfolio_name,
                trade_date,
                event,
            ),
            "entry_channel": support_resistance.get("entry_channel"),
            "attempts": [],
        }


def _next_broker_open_date(client: AlpacaClient, trade_date: date) -> date:
    clock = client.get_clock()
    raw_next_open = clock.get("next_open")
    if raw_next_open:
        try:
            parsed = datetime.fromisoformat(str(raw_next_open).replace("Z", "+00:00"))
            return parsed.date()
        except ValueError as exc:
            raise ValueError("Alpaca clock returned an invalid next_open timestamp") from exc
    raise ValueError("Alpaca clock did not return next_open for strict entry scheduling")


def _next_weekday(trade_date: date) -> date:
    candidate = trade_date + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _rebuild_virtual_subportfolio_state(
    db: Session,
    strategy_id: UUID,
    allocation_cfg: VirtualSubportfolioConfig,
    price_lookup: dict[str, float],
) -> VirtualSubportfolioState:
    state = VirtualSubportfolioState(
        cash=allocation_cfg.capital_base,
        equity=allocation_cfg.capital_base,
        gross_exposure=0.0,
        net_exposure=0.0,
        positions_by_symbol={},
    )
    transactions = db.execute(
        select(Transaction)
        .where(Transaction.strategy_id == strategy_id)
        .order_by(Transaction.ts.asc(), Transaction.id.asc())
    ).scalars().all()

    for txn in transactions:
        source = str((txn.meta or {}).get("source") or "").strip().lower()
        if source == "backtest":
            continue
        if source and source not in PAPER_TRANSACTION_SOURCES:
            continue
        txn_portfolio_raw = (txn.meta or {}).get("portfolio_name")
        txn_portfolio = (
            normalize_portfolio_name(str(txn_portfolio_raw))
            if txn_portfolio_raw is not None
            else None
        )
        if txn_portfolio is not None and txn_portfolio != allocation_cfg.portfolio_name:
            continue
        if txn_portfolio is None and allocation_cfg.portfolio_name != DEFAULT_PORTFOLIO_NAME:
            continue
        if not _transaction_represents_virtual_fill(txn):
            continue
        _apply_virtual_fill(
            state,
            symbol=txn.symbol,
            side=str(txn.side),
            qty=float(txn.qty),
            price=float(txn.price),
            fee=float(txn.fee or 0),
            trade_date=_transaction_trade_date(txn),
            entry_signal_features=(
                (txn.meta or {}).get("entry_signal_features")
                if isinstance((txn.meta or {}).get("entry_signal_features"), dict)
                else None
            ),
        )

    return _mark_virtual_subportfolio_to_market(state, price_lookup)


def _execute_paper_orders(
    *,
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    runtime: dict[str, Any],
    trade_date: date,
    client: AlpacaClient,
    broker_cash: float,
    sleeve_state: VirtualSubportfolioState,
    allocation_cfg: VirtualSubportfolioConfig,
    open_orders: list[dict[str, Any]],
    signals: list[SignalEvent],
    snapshots: dict[str, dict[str, Any]],
    submit_orders: bool,
) -> tuple[list[PaperTradingOrderOutcome], VirtualSubportfolioState, float]:
    outcomes: list[PaperTradingOrderOutcome] = []
    risk_cfg = runtime["params"]["risk"]
    max_positions = int(risk_cfg["max_positions"])
    position_size_pct = float(risk_cfg["position_size_pct"])

    projected_broker_cash = float(broker_cash)
    projected_sleeve = _clone_virtual_state(sleeve_state)
    open_order_keys = _open_order_keys(open_orders)

    ordered_signals = [event for event in signals if event.action == "SELL"]
    staged_candidates = select_highest_stage_signals(signals)
    ordered_signals.extend(ordered_entry_buy_signals(staged_candidates))
    for event in ordered_signals:
        if event.action not in {"BUY", "SELL"}:
            continue

        symbol = event.symbol.upper()
        reference_price = _reference_price(snapshots.get(symbol))
        client_order_id = _client_order_id(
            strategy.id,
            allocation_cfg.portfolio_name,
            trade_date,
            event,
        )
        open_order_key = (symbol, event.action)

        if reference_price is None or reference_price <= 0:
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="missing reference price",
                    client_order_id=client_order_id,
                )
            )
            continue
        if open_order_key in open_order_keys:
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="open Alpaca order already exists",
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                )
            )
            continue

        paper_execution = event.metadata.get("paper_execution")
        if event.action == "BUY" and isinstance(paper_execution, dict):
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="pending" if submit_orders else "skipped",
                    reason=(
                        "awaiting strict next-session channel validation"
                        if submit_orders
                        else "dry run only"
                    ),
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                    signal_strength=get_signal_strength(event),
                )
            )
            continue

        current_position = projected_sleeve.positions_by_symbol.get(symbol)
        current_qty = current_position.qty if current_position else 0.0

        if event.action == "SELL":
            if current_qty <= 0:
                outcomes.append(
                    PaperTradingOrderOutcome(
                        symbol=symbol,
                        action=event.action,
                        status="skipped",
                        reason="no virtual long position to sell",
                        client_order_id=client_order_id,
                        reference_price=reference_price,
                    )
                )
                continue

            outcome = _submit_paper_order(
                db=db,
                strategy=strategy,
                run=run,
                trade_date=trade_date,
                client=client,
                event=event,
                submit_orders=submit_orders,
                qty=current_qty,
                reference_price=reference_price,
                client_order_id=client_order_id,
                portfolio_name=allocation_cfg.portfolio_name,
                allocation_pct=allocation_cfg.allocation_pct,
            )
            outcomes.append(outcome)
            if outcome.status == "submitted":
                fill_qty = outcome.filled_qty or 0.0
                fill_price = outcome.execution_price or reference_price
                if fill_qty > 0 and fill_price > 0:
                    projected_broker_cash += fill_qty * fill_price
                    _apply_virtual_fill(
                        projected_sleeve,
                        symbol=symbol,
                        side="SELL",
                        qty=fill_qty,
                        price=fill_price,
                        fee=0.0,
                        trade_date=trade_date,
                    )
                    projected_sleeve = _mark_virtual_subportfolio_to_market(
                        projected_sleeve,
                        {symbol: fill_price},
                    )
                open_order_keys.add(open_order_key)
            continue

        staged_setup = pattern_setup_from_metadata(event.metadata)
        current_features = current_position.entry_signal_features if current_position else None
        if current_qty > 0 and (
            staged_setup is None
            or not can_apply_staged_entry(event.metadata, current_features)
        ):
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="virtual long position already exists or staged entry is not newer",
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                )
            )
            continue
        if not passes_strength_threshold(event):
            strength = get_signal_strength(event) or {}
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="signal strength below minimum threshold",
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                    signal_strength=strength,
                )
            )
            continue
        if current_qty <= 0 and projected_sleeve.long_position_count >= max_positions:
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="max_positions reached",
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                )
            )
            continue

        target_fraction = float(staged_setup["stage_target_pct"]) if staged_setup else 1.0
        desired_position_value = projected_sleeve.equity * position_size_pct * target_fraction
        current_position_value = current_qty * reference_price
        incremental_target = max(desired_position_value - current_position_value, 0.0)
        target_value = min(
            projected_sleeve.cash,
            incremental_target,
            projected_broker_cash,
        )
        qty = _estimate_paper_buy_qty(
            target_value,
            reference_price,
            allow_fractional=allocation_cfg.allow_fractional,
        )
        if qty <= 0:
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="insufficient virtual or broker cash for target position",
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                )
            )
            continue

        merged_features = merge_entry_features(current_features, event.metadata)
        projected_qty = current_qty + qty
        current_average = current_position.avg_entry_price if current_position is not None else 0.0
        projected_average = (
            ((current_qty * current_average) + (qty * reference_price)) / projected_qty
            if projected_qty > 0
            else 0.0
        )
        merged_features["staged_entry_audit"] = {
            "target_position_value": desired_position_value,
            "incremental_target_value": incremental_target,
            "added_notional": qty * reference_price,
            "position_qty_before": current_qty,
            "position_qty_after": projected_qty,
            "weighted_avg_cost": projected_average,
        }
        event.metadata = merged_features
        outcome = _submit_paper_order(
            db=db,
            strategy=strategy,
            run=run,
            trade_date=trade_date,
            client=client,
            event=event,
            submit_orders=submit_orders,
            qty=qty,
            reference_price=reference_price,
            client_order_id=client_order_id,
            portfolio_name=allocation_cfg.portfolio_name,
            allocation_pct=allocation_cfg.allocation_pct,
        )
        outcomes.append(outcome)
        if outcome.status == "submitted":
            fill_qty = outcome.filled_qty or 0.0
            fill_price = outcome.execution_price or reference_price
            if fill_qty > 0 and fill_price > 0:
                projected_broker_cash = max(projected_broker_cash - (fill_qty * fill_price), 0.0)
                _apply_virtual_fill(
                    projected_sleeve,
                    symbol=symbol,
                    side="BUY",
                    qty=fill_qty,
                    price=fill_price,
                    fee=0.0,
                    trade_date=trade_date,
                    entry_signal_features=merged_features,
                )
                projected_sleeve = _mark_virtual_subportfolio_to_market(
                    projected_sleeve,
                    {symbol: fill_price},
                )
            open_order_keys.add(open_order_key)

    return outcomes, projected_sleeve, projected_broker_cash


def _log_paper_trading_run_summary(
    *,
    strategy_id: str,
    strategy_name: str,
    run_id: str,
    portfolio_name: str,
    trade_date: date,
    trigger: str,
    submit_orders: bool,
    signal_count: int,
    order_count: int,
    submitted_order_count: int,
    skipped_order_count: int,
    failed_order_count: int,
    final_cash: float,
    final_equity: float,
) -> None:
    log.info(
        "Paper trading run completed strategy_id=%s strategy_name=%s run_id=%s portfolio=%s trade_date=%s trigger=%s submit_orders=%s signals=%s orders=%s submitted=%s skipped=%s failed=%s final_cash=%.4f final_equity=%.4f",
        strategy_id,
        strategy_name,
        run_id,
        portfolio_name,
        trade_date.isoformat(),
        trigger,
        submit_orders,
        signal_count,
        order_count,
        submitted_order_count,
        skipped_order_count,
        failed_order_count,
        final_cash,
        final_equity,
    )


def _log_paper_trading_transaction_event(
    *,
    event_name: str,
    strategy_id: str,
    strategy_name: str | None,
    run_id: str | None,
    portfolio_name: str | None,
    trade_date: date | None,
    transaction: Transaction,
    requested_qty: float | None = None,
    reference_price: float | None = None,
    reason: str | None = None,
) -> None:
    meta = transaction.meta or {}
    fill_applied = _transaction_represents_virtual_fill(transaction)
    log.info(
        "Paper trading transaction event=%s strategy_id=%s strategy_name=%s run_id=%s portfolio=%s trade_date=%s symbol=%s side=%s ts=%s order_id=%s client_order_id=%s broker_status=%s requested_qty=%s filled_qty=%s qty=%s execution_price=%s reference_price=%s fill_applied=%s reason=%s",
        event_name,
        strategy_id,
        strategy_name or "-",
        run_id or "-",
        portfolio_name or "-",
        trade_date.isoformat() if trade_date is not None else "-",
        transaction.symbol,
        transaction.side,
        transaction.ts.isoformat() if transaction.ts is not None else "-",
        transaction.order_id or "-",
        str(meta.get("client_order_id") or "") or "-",
        str(meta.get("broker_status") or "") or "-",
        requested_qty,
        _to_float(meta.get("filled_qty")),
        float(transaction.qty or 0),
        float(transaction.price or 0),
        reference_price,
        fill_applied,
        reason or str(meta.get("reason") or "") or "-",
    )


def _submit_paper_order(
    *,
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    trade_date: date,
    client: AlpacaClient,
    event: SignalEvent,
    submit_orders: bool,
    qty: float,
    reference_price: float,
    client_order_id: str,
    portfolio_name: str,
    allocation_pct: float,
    order_type: str = "market",
    limit_price: float | None = None,
) -> PaperTradingOrderOutcome:
    if not submit_orders:
        return PaperTradingOrderOutcome(
            symbol=event.symbol.upper(),
            action=event.action,
            status="skipped",
            reason="dry run only",
            client_order_id=client_order_id,
            qty=qty,
            reference_price=reference_price,
            signal_strength=get_signal_strength(event),
        )

    try:
        order = client.submit_order(
            symbol=event.symbol,
            qty=qty,
            side=event.action.lower(),
            order_type=order_type,
            time_in_force="day",
            client_order_id=client_order_id,
            limit_price=limit_price,
            extended_hours=False,
        )
    except AlpacaAPIError as exc:
        log.warning(
            "Paper trading order submission failed strategy_id=%s strategy_name=%s run_id=%s portfolio=%s trade_date=%s symbol=%s side=%s client_order_id=%s qty=%s reference_price=%s error=%s",
            str(strategy.id),
            strategy.name,
            str(run.id),
            portfolio_name,
            trade_date.isoformat(),
            event.symbol.upper(),
            event.action,
            client_order_id,
            qty,
            reference_price,
            str(exc),
        )
        return PaperTradingOrderOutcome(
            symbol=event.symbol.upper(),
            action=event.action,
            status="failed",
            reason=str(exc),
            client_order_id=client_order_id,
            qty=qty,
            reference_price=reference_price,
        )

    order_id = str(order.get("id")) if order.get("id") else None
    broker_status = str(order.get("status")) if order.get("status") else None
    transaction = _upsert_paper_broker_order_transaction(
        db,
        strategy_id=strategy.id,
        run_id=run.id,
        symbol=event.symbol.upper(),
        side=event.action,
        trade_date=trade_date,
        reason=event.reason,
        signal_ts=event.ts,
        entry_signal_features=(
            event.metadata if event.action == "BUY" and isinstance(event.metadata, dict) else None
        ),
        order=order,
        requested_qty=qty,
        reference_price=reference_price,
        client_order_id=client_order_id,
        portfolio_name=portfolio_name,
        allocation_pct=allocation_pct,
    )
    db.commit()
    _log_paper_trading_transaction_event(
        event_name="submitted",
        strategy_id=str(strategy.id),
        strategy_name=strategy.name,
        run_id=str(run.id),
        portfolio_name=portfolio_name,
        trade_date=trade_date,
        transaction=transaction,
        requested_qty=qty,
        reference_price=reference_price,
        reason=event.reason,
    )

    filled_qty = _to_float((transaction.meta or {}).get("filled_qty"))
    execution_price = float(transaction.price) if _transaction_represents_virtual_fill(transaction) else None
    return PaperTradingOrderOutcome(
        symbol=event.symbol.upper(),
        action=event.action,
        status="submitted",
        reason=event.reason,
        client_order_id=client_order_id,
        order_id=order_id,
        qty=qty,
        filled_qty=filled_qty,
        reference_price=reference_price,
        execution_price=execution_price,
        broker_status=broker_status,
        signal_strength=get_signal_strength(event),
    )


def _sync_strategy_pending_orders(
    db: Session,
    *,
    strategy_id: UUID,
    portfolio_name: str,
    client: AlpacaClient,
) -> None:
    pending_transactions = [
        txn
        for txn in db.execute(
            select(Transaction)
            .where(Transaction.strategy_id == strategy_id)
            .order_by(Transaction.ts.asc(), Transaction.id.asc())
        ).scalars().all()
        if _transaction_portfolio_name(txn) == portfolio_name and _transaction_needs_broker_sync(txn)
    ]

    if not pending_transactions:
        return

    reconciliation_events: list[tuple[Transaction, float, float | None, str | None]] = []
    for txn in pending_transactions:
        if not txn.order_id:
            continue
        order = client.get_order(str(txn.order_id))
        meta = txn.meta or {}
        previous_broker_status = str(meta.get("broker_status") or "").strip().lower() or None
        previous_filled_qty = _to_float(meta.get("filled_qty"))
        previous_execution_price = float(txn.price or 0)
        signal_ts = _parse_iso_datetime(meta.get("signal_ts"))
        requested_qty = _requested_transaction_qty(txn)
        reference_price = _to_float(meta.get("reference_price"))
        updated_txn = _upsert_paper_broker_order_transaction(
            db,
            strategy_id=txn.strategy_id,
            run_id=txn.run_id,
            symbol=txn.symbol,
            side=txn.side,
            trade_date=_transaction_trade_date(txn),
            reason=str(meta.get("reason") or ""),
            signal_ts=signal_ts,
            entry_signal_features=(
                meta.get("entry_signal_features")
                if isinstance(meta.get("entry_signal_features"), dict)
                else None
            ),
            order=order,
            requested_qty=requested_qty,
            reference_price=reference_price,
            client_order_id=str(meta.get("client_order_id") or "") or None,
            portfolio_name=portfolio_name,
            allocation_pct=_to_float(meta.get("allocation_pct")),
            existing_txn=txn,
        )
        updated_meta = updated_txn.meta or {}
        updated_broker_status = str(updated_meta.get("broker_status") or "").strip().lower() or None
        updated_filled_qty = _to_float(updated_meta.get("filled_qty"))
        updated_execution_price = float(updated_txn.price or 0)
        if (
            previous_broker_status != updated_broker_status
            or abs(updated_filled_qty - previous_filled_qty) > 1e-9
            or abs(updated_execution_price - previous_execution_price) > 1e-9
        ):
            reconciliation_events.append(
                (
                    updated_txn,
                    requested_qty,
                    reference_price,
                    str(updated_meta.get("reason") or "") or None,
                )
            )

    db.commit()
    for transaction, requested_qty, reference_price, reason in reconciliation_events:
        _log_paper_trading_transaction_event(
            event_name="reconciled",
            strategy_id=str(transaction.strategy_id),
            strategy_name=None,
            run_id=str(transaction.run_id) if transaction.run_id else None,
            portfolio_name=portfolio_name,
            trade_date=_transaction_trade_date(transaction),
            transaction=transaction,
            requested_qty=requested_qty,
            reference_price=reference_price,
            reason=reason,
        )


def _upsert_paper_broker_order_transaction(
    db: Session,
    *,
    strategy_id: UUID,
    run_id: UUID | None,
    symbol: str,
    side: str,
    trade_date: date,
    reason: str,
    signal_ts: datetime | None,
    entry_signal_features: dict[str, Any] | None,
    order: dict[str, Any],
    requested_qty: float,
    reference_price: float,
    client_order_id: str | None,
    portfolio_name: str,
    allocation_pct: float,
    existing_txn: Transaction | None = None,
) -> Transaction:
    raw_order_id = order.get("id")
    if raw_order_id:
        order_id = str(raw_order_id)
    elif existing_txn is not None and existing_txn.order_id:
        order_id = str(existing_txn.order_id)
    else:
        order_id = None
    broker_status = str(order.get("status") or "").strip().lower() or None
    filled_qty = _to_float(order.get("filled_qty"))
    if filled_qty <= 0 and broker_status == "filled":
        filled_qty = _to_float(order.get("qty")) or requested_qty
    filled_avg_price = _to_float(order.get("filled_avg_price"))
    fill_applied = filled_qty > 0
    executed_price = (
        filled_avg_price
        or _to_float(order.get("limit_price"))
        or reference_price
    ) if fill_applied else 0.0

    order_meta = dict(existing_txn.meta or {}) if existing_txn is not None else {}
    if not client_order_id:
        client_order_id = str(order.get("client_order_id") or order_meta.get("client_order_id") or "") or None
    if requested_qty <= 0:
        requested_qty = (
            _to_float(order_meta.get("requested_qty"))
            or _to_float(order.get("qty"))
            or _to_float(existing_txn.qty if existing_txn is not None else 0)
        )
    if reference_price <= 0:
        reference_price = _to_float(order_meta.get("reference_price"))
    if not reason:
        reason = str(order_meta.get("reason") or "")
    if signal_ts is None:
        signal_ts = _parse_iso_datetime(order_meta.get("signal_ts"))
    if entry_signal_features is None and isinstance(order_meta.get("entry_signal_features"), dict):
        entry_signal_features = order_meta.get("entry_signal_features")
    if isinstance(entry_signal_features, dict) and fill_applied:
        entry_signal_features = dict(entry_signal_features)
        audit = dict(entry_signal_features.get("staged_entry_audit") or {})
        audit["added_notional"] = filled_qty * executed_price
        entry_signal_features["staged_entry_audit"] = audit
    if allocation_pct <= 0:
        allocation_pct = _to_float(order_meta.get("allocation_pct"))

    meta = {
        **order_meta,
        "source": PAPER_BROKER_ORDER_SOURCE,
        "reason": reason,
        "signal_ts": signal_ts.isoformat() if signal_ts is not None else order_meta.get("signal_ts"),
        "entry_signal_features": entry_signal_features,
        "execution_trade_date": trade_date.isoformat(),
        "client_order_id": client_order_id,
        "broker_status": broker_status,
        "reference_price": reference_price,
        "requested_qty": requested_qty,
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price or None,
        "portfolio_name": portfolio_name,
        "allocation_pct": allocation_pct,
        "virtual_subportfolio": True,
        "paper_fill_applied": fill_applied,
        "paper_reconciled_at": datetime.now(timezone.utc).isoformat(),
        "submitted_order": order,
    }

    transaction_ts = _broker_order_timestamp(order)
    if existing_txn is None:
        existing_txn = Transaction(
            strategy_id=strategy_id,
            run_id=run_id,
            ts=transaction_ts,
            symbol=symbol.upper(),
            side=side.upper(),
            qty=filled_qty if fill_applied else requested_qty,
            price=executed_price,
            fee=0,
            order_id=order_id,
            meta=meta,
        )
        db.add(existing_txn)
        return existing_txn

    existing_txn.ts = transaction_ts
    existing_txn.qty = filled_qty if fill_applied else requested_qty
    existing_txn.price = executed_price
    existing_txn.fee = 0
    existing_txn.order_id = order_id or existing_txn.order_id
    existing_txn.meta = meta
    return existing_txn


def _transaction_represents_virtual_fill(txn: Transaction) -> bool:
    meta = txn.meta or {}
    explicit = meta.get("paper_fill_applied")
    if isinstance(explicit, bool):
        return explicit

    source = str(meta.get("source") or "").strip().lower()
    if source in {"alpaca_live", "manual_virtual"}:
        return True
    if source != PAPER_BROKER_ORDER_SOURCE:
        return source in PAPER_TRANSACTION_SOURCES and source != "backtest"

    filled_qty = _to_float(meta.get("filled_qty"))
    if filled_qty > 0:
        return True

    broker_status = str(meta.get("broker_status") or "").strip().lower()
    if broker_status in {"accepted", "new", "pending_new", "accepted_for_bidding"}:
        return False
    if broker_status in {"canceled", "cancelled", "expired", "rejected"}:
        return False
    if broker_status == "filled":
        return True

    return float(txn.price or 0) > 0


def _transaction_needs_broker_sync(txn: Transaction) -> bool:
    meta = txn.meta or {}
    source = str(meta.get("source") or "").strip().lower()
    if source != PAPER_BROKER_ORDER_SOURCE or not txn.order_id:
        return False

    broker_status = str(meta.get("broker_status") or "").strip().lower()
    if not broker_status:
        return True
    return broker_status not in PAPER_BROKER_TERMINAL_STATUSES


def _requested_transaction_qty(txn: Transaction) -> float:
    requested_qty = _to_float((txn.meta or {}).get("requested_qty"))
    if requested_qty > 0:
        return requested_qty
    return _to_float(txn.qty)


def _transaction_portfolio_name(txn: Transaction) -> str | None:
    value = str((txn.meta or {}).get("portfolio_name") or "").strip()
    if not value:
        return None
    return normalize_portfolio_name(value)


def _broker_order_timestamp(order: dict[str, Any]) -> datetime:
    for field in ("filled_at", "updated_at", "submitted_at", "created_at"):
        parsed = _parse_iso_datetime(order.get(field))
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _apply_virtual_fill(
    state: VirtualSubportfolioState,
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    fee: float,
    trade_date: date | None = None,
    entry_signal_features: dict[str, Any] | None = None,
) -> None:
    normalized_symbol = symbol.upper()
    position = state.positions_by_symbol.get(normalized_symbol)
    current_qty = position.qty if position is not None else 0.0
    current_avg = position.avg_entry_price if position is not None else 0.0
    current_entry_trade_date = position.entry_trade_date if position is not None else None
    current_entry_signal_features = position.entry_signal_features if position is not None else None

    if side.upper() == "BUY":
        total_cost = (qty * price) + fee
        new_qty = current_qty + qty
        if new_qty <= 0:
            return
        weighted_cost = (current_qty * current_avg) + (qty * price)
        avg_entry = weighted_cost / new_qty if new_qty else 0.0
        state.cash -= total_cost
        state.positions_by_symbol[normalized_symbol] = VirtualPosition(
            symbol=normalized_symbol,
            qty=new_qty,
            avg_entry_price=avg_entry,
            current_price=price,
            entry_trade_date=current_entry_trade_date or trade_date,
            entry_signal_features=entry_signal_features or current_entry_signal_features,
        )
        return

    proceeds = (qty * price) - fee
    state.cash += proceeds
    remaining_qty = max(current_qty - qty, 0.0)
    if remaining_qty <= 1e-9:
        state.positions_by_symbol.pop(normalized_symbol, None)
        return
    state.positions_by_symbol[normalized_symbol] = VirtualPosition(
        symbol=normalized_symbol,
        qty=remaining_qty,
        avg_entry_price=current_avg,
        current_price=price,
        entry_trade_date=current_entry_trade_date,
        entry_signal_features=current_entry_signal_features,
    )


def _mark_virtual_subportfolio_to_market(
    state: VirtualSubportfolioState,
    price_lookup: dict[str, float],
) -> VirtualSubportfolioState:
    gross_exposure = 0.0
    net_exposure = 0.0
    for symbol, position in list(state.positions_by_symbol.items()):
        current_price = float(price_lookup.get(symbol, position.current_price or 0.0))
        state.positions_by_symbol[symbol] = VirtualPosition(
            symbol=symbol,
            qty=position.qty,
            avg_entry_price=position.avg_entry_price,
            current_price=current_price,
            entry_trade_date=position.entry_trade_date,
            entry_signal_features=position.entry_signal_features,
        )
        market_value = state.positions_by_symbol[symbol].market_value
        gross_exposure += abs(market_value)
        net_exposure += market_value
    state.gross_exposure = gross_exposure
    state.net_exposure = net_exposure
    state.equity = state.cash + net_exposure
    return state


def _clone_virtual_state(state: VirtualSubportfolioState) -> VirtualSubportfolioState:
    return VirtualSubportfolioState(
        cash=state.cash,
        equity=state.equity,
        gross_exposure=state.gross_exposure,
        net_exposure=state.net_exposure,
        positions_by_symbol={
            symbol: VirtualPosition(
                symbol=position.symbol,
                qty=position.qty,
                avg_entry_price=position.avg_entry_price,
                current_price=position.current_price,
                entry_trade_date=position.entry_trade_date,
                entry_signal_features=position.entry_signal_features,
            )
            for symbol, position in state.positions_by_symbol.items()
        },
    )


def _inject_virtual_positions(
    snapshots: dict[str, dict[str, Any]],
    positions_by_symbol: dict[str, VirtualPosition],
    trade_date: date,
    *,
    use_trading_days: bool = False,
) -> None:
    for symbol, snapshot in snapshots.items():
        position = positions_by_symbol.get(symbol)
        snapshot["position"] = position.qty if position is not None else 0.0
        snapshot["avg_entry_price"] = position.avg_entry_price if position is not None else None
        snapshot["entry_trade_date"] = position.entry_trade_date if position is not None else None
        snapshot["entry_signal_features"] = position.entry_signal_features if position is not None else None
        if position is None or position.entry_trade_date is None:
            snapshot["position_holding_days"] = None
        elif use_trading_days:
            observed_dates = {
                observed_date
                for bar in snapshot.get("recent_bars") or []
                if (observed_date := _support_resistance_bar_date(bar)) is not None
                and position.entry_trade_date < observed_date <= trade_date
            }
            snapshot["position_holding_days"] = len(observed_dates)
        else:
            snapshot["position_holding_days"] = max(
                (trade_date - position.entry_trade_date).days,
                0,
            )


def _build_price_lookup(
    snapshots: dict[str, dict[str, Any]],
    broker_positions: list[dict[str, Any]],
) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for symbol, snapshot in snapshots.items():
        price = _reference_price(snapshot)
        if price is not None:
            lookup[symbol] = price
    for position in broker_positions:
        symbol = str(position.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        current_price = _to_float(position.get("current_price"))
        if current_price > 0:
            lookup[symbol] = current_price
    return lookup


def _open_order_keys(open_orders: list[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for order in open_orders:
        symbol = str(order.get("symbol") or "").strip().upper()
        side = str(order.get("side") or "").strip().upper()
        if not symbol or side not in {"BUY", "SELL"}:
            continue
        keys.add((symbol, side))
    return keys


def _serialize_virtual_positions(
    positions_by_symbol: dict[str, VirtualPosition],
    signal_by_symbol: dict[str, SignalEvent],
) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for symbol, position in positions_by_symbol.items():
        signal = signal_by_symbol.get(symbol)
        payload[symbol] = {
            "qty": position.qty,
            "avg_entry_price": position.avg_entry_price,
            "entry_trade_date": (
                position.entry_trade_date.isoformat() if position.entry_trade_date is not None else None
            ),
            "entry_signal_features": position.entry_signal_features,
            "current_price": position.current_price,
            "market_value": position.market_value,
            "latest_signal": getattr(signal, "action", None),
        }
    return payload


def _transaction_trade_date(txn: Transaction) -> date:
    meta = txn.meta or {}
    execution_trade_date = meta.get("execution_trade_date")
    if isinstance(execution_trade_date, str):
        try:
            return date.fromisoformat(execution_trade_date)
        except ValueError:
            pass
    return txn.ts.date() if txn.ts is not None else datetime.now(timezone.utc).date()


def _snapshot_ts(
    signals: list[SignalEvent],
    snapshots: dict[str, dict[str, Any]],
) -> datetime:
    if signals:
        return max(event.ts for event in signals)
    first_snapshot = next(iter(snapshots.values()))
    return first_snapshot.get("ts") or datetime.now(timezone.utc)


def _account_cash(account: dict[str, Any]) -> float:
    return _to_float(account.get("cash"))


def _account_equity(account: dict[str, Any]) -> float:
    return (
        _to_float(account.get("equity"))
        or _to_float(account.get("portfolio_value"))
        or _to_float(account.get("cash"))
    )


def _estimate_paper_buy_qty(
    target_value: float,
    reference_price: float,
    *,
    allow_fractional: bool,
) -> float:
    if target_value <= 0 or reference_price <= 0:
        return 0.0
    raw_qty = target_value / reference_price
    if allow_fractional:
        return round(raw_qty, 6)
    return float(int(raw_qty))


def _reference_price(snapshot: dict[str, Any] | None) -> float | None:
    if snapshot is None:
        return None
    price = snapshot.get("close")
    if price is None:
        return None
    value = float(price)
    return value if value > 0 else None


def _client_order_id(
    strategy_id: UUID,
    portfolio_name: str,
    trade_date: date,
    event: SignalEvent,
) -> str:
    symbol = event.symbol.upper()
    action = event.action.lower()
    portfolio_token = normalize_portfolio_name(portfolio_name).replace(" ", "-").lower()[:20]
    setup = pattern_setup_from_metadata(event.metadata)
    if setup is not None:
        identity = "|".join(
            (
                normalize_portfolio_name(portfolio_name),
                str(strategy_id),
                trade_date.isoformat(),
                symbol,
                action,
                str(setup["setup_id"]),
                str(setup["stage_index"]),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        return f"paper-{trade_date:%Y%m%d}-{symbol[:8]}-{action[0]}-{digest}-s{int(setup['stage_index'])}"
    return f"paper-{portfolio_token}-{str(strategy_id)[:8]}-{trade_date:%Y%m%d}-{symbol}-{action}"


def _normalize_symbol_universe(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    if not normalized:
        raise ValueError("universe_symbols must contain at least one non-empty ticker")
    return normalized


def _to_float(value: Any) -> float:
    try:
        if value in {None, ""}:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
