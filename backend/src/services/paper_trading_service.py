from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import (
    PortfolioSnapshot,
    Signal,
    Strategy,
    StrategyAllocation,
    StrategyRun,
    Transaction,
)
from src.services.alpaca_services import AlpacaAPIError, AlpacaClient
from src.services.paper_account_service import (
    build_alpaca_client_for_portfolio,
    ensure_default_strategy_portfolio,
)
from src.services.stock_basket_service import (
    DEFAULT_COMMON_STOCK_BASKET_NAME,
    load_default_common_stock_symbols,
)
from src.services.strategy_allocation_service import (
    DEFAULT_PORTFOLIO_NAME,
    get_strategy_allocation,
    list_allocated_strategies,
    normalize_portfolio_name,
    validate_portfolio_allocations,
)
from src.services.strategy_engine import STRATEGY_HANDLERS, SignalEvent, load_feature_market_data
from src.services.strategy_registry import build_runtime_payload


PAPER_TRANSACTION_SOURCES = {"alpaca_paper", "alpaca_live", "manual_virtual"}


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
    reference_price: float | None = None
    execution_price: float | None = None
    broker_status: str | None = None


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
) -> PaperTradingResult:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")

    ensure_default_strategy_portfolio(db)
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

    handler = STRATEGY_HANDLERS.get(runtime["strategy_type"])
    if handler is None:
        raise ValueError(f"unsupported strategy_type for paper trading: {runtime['strategy_type']}")

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

        broker_positions_before = client.list_positions()
        open_orders = client.list_orders(status="open") if submit_orders else []

        snapshots = load_feature_market_data(db, trade_date, symbols)
        if not snapshots:
            raise ValueError("no feature snapshots found for the requested universe and trade date")

        price_lookup_before = _build_price_lookup(snapshots, broker_positions_before)
        sleeve_before = _rebuild_virtual_subportfolio_state(
            db,
            strategy.id,
            allocation_cfg,
            price_lookup_before,
        )
        _inject_virtual_positions(snapshots, sleeve_before.positions_by_symbol)
        signals = handler(runtime, snapshots)
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
            "submit_orders": submit_orders,
            "universe_size": len(symbols),
            "symbols_loaded": sorted(snapshots.keys()),
            "symbols_signaled": sorted({event.symbol for event in signals}),
            "strategy_type": runtime["strategy_type"],
            "account_id": account_after.get("id"),
            "account_status": account_after.get("status"),
            "broker_cash": _account_cash(account_after),
            "broker_equity": _account_equity(account_after),
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
                    "reference_price": item.reference_price,
                    "execution_price": item.execution_price,
                    "broker_status": item.broker_status,
                }
                for item in order_outcomes
            ],
        }
        db.commit()
        db.refresh(run)

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
        failed_run = db.get(StrategyRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.finished_at = datetime.now(timezone.utc)
            failed_run.error_message = str(exc)
            db.commit()
        raise


def run_multi_strategy_paper_trading(
    db: Session,
    trade_date: date,
    *,
    alpaca_client: AlpacaClient | None = None,
    portfolio_name: str | None = None,
    submit_orders: bool = True,
    continue_on_error: bool = False,
) -> MultiStrategyPaperTradingResult:
    ensure_default_strategy_portfolio(db)
    normalized_portfolio = normalize_portfolio_name(portfolio_name)
    allocated = list_allocated_strategies(db, portfolio_name=normalized_portfolio)
    if not allocated:
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
        _apply_virtual_fill(
            state,
            symbol=txn.symbol,
            side=str(txn.side),
            qty=float(txn.qty),
            price=float(txn.price),
            fee=float(txn.fee or 0),
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

    ordered_signals = sorted(signals, key=lambda item: 0 if item.action == "SELL" else 1)
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
                fill_qty = outcome.qty or current_qty
                fill_price = outcome.execution_price or reference_price
                projected_broker_cash += fill_qty * fill_price
                _apply_virtual_fill(
                    projected_sleeve,
                    symbol=symbol,
                    side="SELL",
                    qty=fill_qty,
                    price=fill_price,
                    fee=0.0,
                )
                projected_sleeve = _mark_virtual_subportfolio_to_market(
                    projected_sleeve,
                    {symbol: fill_price},
                )
                open_order_keys.add(open_order_key)
            continue

        if current_qty > 0:
            outcomes.append(
                PaperTradingOrderOutcome(
                    symbol=symbol,
                    action=event.action,
                    status="skipped",
                    reason="virtual long position already exists",
                    client_order_id=client_order_id,
                    reference_price=reference_price,
                )
            )
            continue
        if projected_sleeve.long_position_count >= max_positions:
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

        target_value = min(
            projected_sleeve.cash,
            projected_sleeve.equity * position_size_pct,
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

        outcome = _submit_paper_order(
            db=db,
            strategy=strategy,
            run=run,
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
            fill_qty = outcome.qty or qty
            fill_price = outcome.execution_price or reference_price
            projected_broker_cash = max(projected_broker_cash - (fill_qty * fill_price), 0.0)
            _apply_virtual_fill(
                projected_sleeve,
                symbol=symbol,
                side="BUY",
                qty=fill_qty,
                price=fill_price,
                fee=0.0,
            )
            projected_sleeve = _mark_virtual_subportfolio_to_market(
                projected_sleeve,
                {symbol: fill_price},
            )
            open_order_keys.add(open_order_key)

    return outcomes, projected_sleeve, projected_broker_cash


def _submit_paper_order(
    *,
    db: Session,
    strategy: Strategy,
    run: StrategyRun,
    client: AlpacaClient,
    event: SignalEvent,
    submit_orders: bool,
    qty: float,
    reference_price: float,
    client_order_id: str,
    portfolio_name: str,
    allocation_pct: float,
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
        )

    try:
        order = client.submit_order(
            symbol=event.symbol,
            qty=qty,
            side=event.action.lower(),
            order_type="market",
            time_in_force="day",
            client_order_id=client_order_id,
        )
    except AlpacaAPIError as exc:
        return PaperTradingOrderOutcome(
            symbol=event.symbol.upper(),
            action=event.action,
            status="failed",
            reason=str(exc),
            client_order_id=client_order_id,
            qty=qty,
            reference_price=reference_price,
        )

    broker_qty = _to_float(order.get("filled_qty")) or _to_float(order.get("qty")) or qty
    broker_price = (
        _to_float(order.get("filled_avg_price"))
        or _to_float(order.get("limit_price"))
        or reference_price
    )
    order_id = str(order.get("id")) if order.get("id") else None
    broker_status = str(order.get("status")) if order.get("status") else None

    db.add(
        Transaction(
            strategy_id=strategy.id,
            run_id=run.id,
            ts=datetime.now(timezone.utc),
            symbol=event.symbol.upper(),
            side=event.action,
            qty=broker_qty,
            price=broker_price,
            fee=0,
            order_id=order_id,
            meta={
                "source": "alpaca_paper",
                "reason": event.reason,
                "signal_ts": event.ts.isoformat(),
                "client_order_id": client_order_id,
                "broker_status": broker_status,
                "reference_price": reference_price,
                "requested_qty": qty,
                "filled_qty": _to_float(order.get("filled_qty")),
                "filled_avg_price": _to_float(order.get("filled_avg_price")),
                "portfolio_name": portfolio_name,
                "allocation_pct": allocation_pct,
                "virtual_subportfolio": True,
                "submitted_order": order,
            },
        )
    )
    return PaperTradingOrderOutcome(
        symbol=event.symbol.upper(),
        action=event.action,
        status="submitted",
        reason=event.reason,
        client_order_id=client_order_id,
        order_id=order_id,
        qty=broker_qty,
        reference_price=reference_price,
        execution_price=broker_price,
        broker_status=broker_status,
    )


def _apply_virtual_fill(
    state: VirtualSubportfolioState,
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    fee: float,
) -> None:
    normalized_symbol = symbol.upper()
    position = state.positions_by_symbol.get(normalized_symbol)
    current_qty = position.qty if position is not None else 0.0
    current_avg = position.avg_entry_price if position is not None else 0.0

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
            )
            for symbol, position in state.positions_by_symbol.items()
        },
    )


def _inject_virtual_positions(
    snapshots: dict[str, dict[str, Any]],
    positions_by_symbol: dict[str, VirtualPosition],
) -> None:
    for symbol, snapshot in snapshots.items():
        position = positions_by_symbol.get(symbol)
        snapshot["position"] = position.qty if position is not None else 0.0


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
            "current_price": position.current_price,
            "market_value": position.market_value,
            "latest_signal": getattr(signal, "action", None),
        }
    return payload


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
