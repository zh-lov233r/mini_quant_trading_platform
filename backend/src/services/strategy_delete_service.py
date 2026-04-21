from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic, sleep
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import PaperTradingAccount, StrategyAllocation, StrategyPortfolio, Transaction
from src.services.alpaca_services import AlpacaClient, AlpacaClientError
from src.services.paper_account_service import build_alpaca_client_for_account
from src.services.strategy_allocation_service import normalize_portfolio_name


log = logging.getLogger("paper_trading")

STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX = "strategy_delete_requires_position_close:"
STRATEGY_DELETE_MANUAL_RECONCILE_PREFIX = "strategy_delete_requires_manual_reconcile:"
STRATEGY_DELETE_BROKER_QTY_TOLERANCE = 1e-6
STRATEGY_DELETE_CLEANUP_CLIENT_ORDER_ID_PREFIX = "paper-cleanup"
STRATEGY_DELETE_ORDER_TERMINAL_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "filled",
    "rejected",
}


class StrategyDeleteCloseError(RuntimeError):
    """Raised when broker positions could not be flattened before deletion."""


@dataclass(slots=True)
class StrategyDeleteBrokerPosition:
    paper_account_id: UUID
    paper_account_name: str
    symbol: str
    portfolio_names: tuple[str, ...]
    strategy_qty: float
    broker_qty: float
    close_qty: float | None
    close_side: str | None
    reason: str | None = None

    @property
    def can_close(self) -> bool:
        return self.close_qty is not None and self.close_side is not None and not self.reason


def inspect_strategy_delete_broker_positions(
    db: Session,
    strategy_id: UUID | str,
) -> list[StrategyDeleteBrokerPosition]:
    portfolio_account_index = _strategy_portfolio_account_index(db, strategy_id)
    if not portfolio_account_index:
        return []

    local_qty_by_account_symbol: dict[tuple[UUID, str], float] = defaultdict(float)
    portfolio_names_by_account_symbol: dict[tuple[UUID, str], set[str]] = defaultdict(set)

    transactions = db.execute(
        select(Transaction)
        .where(Transaction.strategy_id == strategy_id)
        .order_by(Transaction.ts.asc(), Transaction.id.asc())
    ).scalars().all()

    for transaction in transactions:
        if not _transaction_fill_applied(transaction):
            continue

        portfolio_name = _transaction_portfolio_name(transaction)
        if not portfolio_name:
            continue

        account = portfolio_account_index.get(portfolio_name)
        if account is None:
            continue

        symbol = str(transaction.symbol or "").strip().upper()
        qty = _safe_float(transaction.qty) or 0.0
        if not symbol or _qty_is_zero(qty):
            continue

        signed_qty = -qty if str(transaction.side or "").strip().upper() == "SELL" else qty
        key = (account.id, symbol)
        local_qty_by_account_symbol[key] += signed_qty
        portfolio_names_by_account_symbol[key].add(portfolio_name)

    if not local_qty_by_account_symbol:
        return []

    accounts_by_id: dict[UUID, PaperTradingAccount] = {}
    for account in portfolio_account_index.values():
        accounts_by_id[account.id] = account
    broker_qty_by_account_symbol = _load_broker_qty_by_account_symbol(accounts_by_id)

    exposures: list[StrategyDeleteBrokerPosition] = []
    for (account_id, symbol), strategy_qty in sorted(local_qty_by_account_symbol.items()):
        if _qty_is_zero(strategy_qty):
            continue

        broker_qty = broker_qty_by_account_symbol.get((account_id, symbol), 0.0)
        if _qty_is_zero(broker_qty):
            continue

        close_qty: float | None = None
        close_side: str | None = None
        reason: str | None = None

        if strategy_qty * broker_qty <= 0:
            reason = "broker position direction does not match the strategy ledger"
        elif abs(strategy_qty) > abs(broker_qty) + STRATEGY_DELETE_BROKER_QTY_TOLERANCE:
            reason = (
                f"broker qty {broker_qty:.4f} is smaller than local strategy qty {strategy_qty:.4f}"
            )
        else:
            close_qty = abs(strategy_qty)
            close_side = "sell" if broker_qty > 0 else "buy"

        account = accounts_by_id[account_id]
        exposures.append(
            StrategyDeleteBrokerPosition(
                paper_account_id=account_id,
                paper_account_name=account.name,
                symbol=symbol,
                portfolio_names=tuple(sorted(portfolio_names_by_account_symbol[(account_id, symbol)])),
                strategy_qty=strategy_qty,
                broker_qty=broker_qty,
                close_qty=close_qty,
                close_side=close_side,
                reason=reason,
            )
        )

    return exposures


def close_strategy_delete_broker_positions(
    db: Session,
    strategy_id: UUID | str,
    *,
    broker_positions: list[StrategyDeleteBrokerPosition] | None = None,
) -> list[dict[str, Any]]:
    exposures = broker_positions if broker_positions is not None else inspect_strategy_delete_broker_positions(
        db,
        strategy_id,
    )
    if not exposures:
        return []

    unsafe = [item for item in exposures if not item.can_close]
    if unsafe:
        raise StrategyDeleteCloseError(
            build_strategy_delete_manual_reconcile_message(
                strategy_name=None,
                broker_positions=unsafe,
            )
        )

    accounts: dict[UUID, PaperTradingAccount] = {}
    for exposure in exposures:
        account = db.get(PaperTradingAccount, exposure.paper_account_id)
        if account is None:
            raise StrategyDeleteCloseError(
                f"paper account not found while closing strategy positions: {exposure.paper_account_id}"
            )
        accounts[account.id] = account

    closed_orders: list[dict[str, Any]] = []
    for exposure in exposures:
        account = accounts[exposure.paper_account_id]
        client = build_alpaca_client_for_account(account)
        client_order_id = _strategy_delete_cleanup_client_order_id(
            strategy_id=strategy_id,
            symbol=exposure.symbol,
            side=exposure.close_side or "sell",
        )

        try:
            submitted_order = client.submit_order(
                symbol=exposure.symbol,
                side=exposure.close_side or "sell",
                qty=exposure.close_qty,
                order_type="market",
                time_in_force="day",
                client_order_id=client_order_id,
            )
            terminal_order = _wait_for_terminal_order(client, submitted_order)
        except AlpacaClientError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise StrategyDeleteCloseError(
                f"failed to close Alpaca position for {exposure.symbol} in account '{account.name}': {exc}"
            ) from exc

        terminal_status = str(terminal_order.get("status") or "").strip().lower()
        if terminal_status != "filled":
            raise StrategyDeleteCloseError(
                f"close order for {exposure.symbol} in account '{account.name}' finished with status '{terminal_status or 'unknown'}'; strategy was not deleted"
            )

        log.info(
            "Strategy delete cleanup submitted strategy_id=%s account=%s symbol=%s side=%s qty=%.6f order_id=%s client_order_id=%s",
            str(strategy_id),
            account.name,
            exposure.symbol,
            exposure.close_side or "-",
            float(exposure.close_qty or 0.0),
            str(terminal_order.get("id") or submitted_order.get("id") or "") or "-",
            client_order_id,
        )
        closed_orders.append(terminal_order)

    return closed_orders


def build_strategy_delete_position_close_message(
    *,
    strategy_name: str | None,
    broker_positions: list[StrategyDeleteBrokerPosition],
) -> str:
    strategy_label = strategy_name or "this strategy"
    summary = "; ".join(_format_broker_position(item) for item in broker_positions)
    return (
        f"{STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX} "
        f'Strategy "{strategy_label}" still has live Alpaca positions that match its local paper ledger: '
        f"{summary}. Confirm deletion again to flatten these positions first, then delete the strategy."
    )


def build_strategy_delete_manual_reconcile_message(
    *,
    strategy_name: str | None,
    broker_positions: list[StrategyDeleteBrokerPosition],
) -> str:
    strategy_label = strategy_name or "this strategy"
    summary = "; ".join(_format_broker_position(item, include_reason=True) for item in broker_positions)
    return (
        f"{STRATEGY_DELETE_MANUAL_RECONCILE_PREFIX} "
        f'Strategy "{strategy_label}" has Alpaca positions that cannot be safely auto-closed: '
        f"{summary}. Reconcile the broker account manually before deleting this strategy."
    )


def _strategy_portfolio_account_index(
    db: Session,
    strategy_id: UUID | str,
) -> dict[str, PaperTradingAccount]:
    portfolio_names = {
        normalize_portfolio_name(value)
        for value in db.execute(
            select(StrategyAllocation.portfolio_name).where(StrategyAllocation.strategy_id == strategy_id)
        ).scalars().all()
        if str(value or "").strip()
    }

    transactions = db.execute(select(Transaction).where(Transaction.strategy_id == strategy_id)).scalars().all()
    for transaction in transactions:
        portfolio_name = _transaction_portfolio_name(transaction)
        if portfolio_name:
            portfolio_names.add(portfolio_name)

    if not portfolio_names:
        return {}

    portfolios = db.execute(
        select(StrategyPortfolio).where(StrategyPortfolio.name.in_(portfolio_names))
    ).scalars().all()
    if not portfolios:
        return {}

    accounts_by_id: dict[UUID, PaperTradingAccount] = {}
    for portfolio in portfolios:
        account = accounts_by_id.get(portfolio.paper_account_id)
        if account is None:
            account = db.get(PaperTradingAccount, portfolio.paper_account_id)
            if account is not None:
                accounts_by_id[portfolio.paper_account_id] = account

    return {
        portfolio.name: accounts_by_id[portfolio.paper_account_id]
        for portfolio in portfolios
        if portfolio.paper_account_id in accounts_by_id
    }


def _load_broker_qty_by_account_symbol(
    accounts_by_id: dict[UUID, PaperTradingAccount],
) -> dict[tuple[UUID, str], float]:
    broker_qty_by_account_symbol: dict[tuple[UUID, str], float] = {}
    for account_id, account in accounts_by_id.items():
        client = build_alpaca_client_for_account(account)
        for position in client.list_positions():
            symbol = str(position.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            broker_qty_by_account_symbol[(account_id, symbol)] = _signed_position_qty(position)
    return broker_qty_by_account_symbol


def _wait_for_terminal_order(
    client: AlpacaClient,
    order: dict[str, Any],
    *,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    latest_order = dict(order)
    order_id = str(latest_order.get("id") or "").strip()
    if not order_id:
        raise StrategyDeleteCloseError("cleanup close order did not return an order id")

    deadline = monotonic() + timeout_seconds
    while True:
        status = str(latest_order.get("status") or "").strip().lower()
        if status in STRATEGY_DELETE_ORDER_TERMINAL_STATUSES:
            return latest_order
        if monotonic() >= deadline:
            raise StrategyDeleteCloseError(
                f"close order '{order_id}' did not reach a terminal status within {timeout_seconds:.0f}s"
            )
        sleep(poll_interval_seconds)
        latest_order = client.get_order(order_id)


def _strategy_delete_cleanup_client_order_id(
    *,
    strategy_id: UUID | str,
    symbol: str,
    side: str,
) -> str:
    strategy_token = str(strategy_id).replace("-", "")[:8].lower()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return (
        f"{STRATEGY_DELETE_CLEANUP_CLIENT_ORDER_ID_PREFIX}-"
        f"{strategy_token}-{symbol.upper()}-{side.lower()}-{timestamp}"
    )


def _format_broker_position(
    position: StrategyDeleteBrokerPosition,
    *,
    include_reason: bool = False,
) -> str:
    portfolio_label = ", ".join(position.portfolio_names) if position.portfolio_names else "unassigned"
    summary = (
        f'account "{position.paper_account_name}" symbol {position.symbol} '
        f"strategy_qty={position.strategy_qty:.4f} broker_qty={position.broker_qty:.4f} "
        f"(portfolios: {portfolio_label})"
    )
    if include_reason and position.reason:
        return f"{summary} reason={position.reason}"
    return summary


def _transaction_fill_applied(transaction: Transaction) -> bool:
    meta = transaction.meta or {}
    explicit = meta.get("paper_fill_applied")
    if isinstance(explicit, bool):
        return explicit

    source = str(meta.get("source") or "").strip().lower()
    if source in {"alpaca_live", "manual_virtual"}:
        return True
    if source != "alpaca_paper":
        return source in {"alpaca_paper", "alpaca_live", "manual_virtual"} and source != "backtest"

    filled_qty = _safe_float(meta.get("filled_qty")) or 0.0
    if filled_qty > 0:
        return True

    broker_status = str(meta.get("broker_status") or "").strip().lower()
    if broker_status in {"accepted", "new", "pending_new", "accepted_for_bidding"}:
        return False
    if broker_status in {"canceled", "cancelled", "expired", "rejected"}:
        return False
    if broker_status == "filled":
        return True

    return (_safe_float(transaction.price) or 0.0) > 0


def _transaction_portfolio_name(transaction: Transaction) -> str | None:
    value = str((transaction.meta or {}).get("portfolio_name") or "").strip()
    if not value:
        return None
    return normalize_portfolio_name(value)


def _signed_position_qty(position: dict[str, Any]) -> float:
    qty = _safe_float(position.get("qty")) or 0.0
    side = str(position.get("side") or "").strip().lower()
    return -qty if side == "short" else qty


def _qty_is_zero(value: float) -> bool:
    return abs(value) <= STRATEGY_DELETE_BROKER_QTY_TOLERANCE


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
