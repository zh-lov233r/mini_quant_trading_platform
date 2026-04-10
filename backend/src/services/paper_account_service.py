from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import (
    PaperTradingAccount,
    PortfolioSnapshot,
    Strategy,
    StrategyAllocation,
    StrategyPortfolio,
    StrategyRun,
    Transaction,
)
from src.services.alpaca_services import AlpacaAPIError, AlpacaClient, get_alpaca_client
from src.services.strategy_allocation_service import (
    DEFAULT_PORTFOLIO_NAME,
    normalize_portfolio_name,
)


DEFAULT_ACCOUNT_NAME = "default-paper-account"
ENV_FALLBACKS: dict[str, tuple[str, ...]] = {
    "ALPACA_API_KEY": ("ALPACA_KEY",),
    "ALPACA_SECRET_KEY": ("ALPACA_SECRET",),
}


@dataclass(slots=True)
class StrategyAllocationOverview:
    strategy_id: str
    strategy_name: str
    strategy_type: str
    strategy_status: str
    allocation_pct: float
    capital_base: float | None
    allow_fractional: bool
    auto_run_enabled: bool
    allocation_status: str
    notes: str | None
    latest_run_id: str | None
    latest_run_status: str | None
    latest_run_requested_at: datetime | None
    latest_run_equity: float | None


@dataclass(slots=True)
class StrategyPortfolioOverview:
    id: str
    paper_account_id: str
    name: str
    description: str | None
    status: str
    allocation_count: int
    active_allocation_count: int
    allocated_strategy_count: int
    active_allocation_pct_total: float
    latest_run_id: str | None
    latest_run_status: str | None
    latest_run_requested_at: datetime | None
    latest_run_equity: float | None
    strategies: list[StrategyAllocationOverview]


def normalize_account_name(name: str | None) -> str:
    normalized = str(name or DEFAULT_ACCOUNT_NAME).strip()
    return normalized or DEFAULT_ACCOUNT_NAME


def normalize_alpaca_base_url(base_url: str | None) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        normalized = "https://paper-api.alpaca.markets"
    if normalized.endswith("/v2"):
        normalized = normalized[:-3].rstrip("/")
    return normalized or "https://paper-api.alpaca.markets"


def ensure_default_paper_account(db: Session) -> PaperTradingAccount:
    account = db.execute(
        select(PaperTradingAccount).where(PaperTradingAccount.name == DEFAULT_ACCOUNT_NAME)
    ).scalars().first()
    if account is None:
        account = PaperTradingAccount(
            name=DEFAULT_ACCOUNT_NAME,
            broker="alpaca",
            mode="paper",
            api_key_env="ALPACA_API_KEY",
            secret_key_env="ALPACA_SECRET_KEY",
            base_url="https://paper-api.alpaca.markets",
            timeout_seconds=20,
            notes="Auto-created default paper trading account",
            status="active",
        )
        db.add(account)
        db.commit()
        db.refresh(account)
    return account


def ensure_default_strategy_portfolio(db: Session) -> StrategyPortfolio:
    default_account = ensure_default_paper_account(db)
    portfolio = db.execute(
        select(StrategyPortfolio).where(
            StrategyPortfolio.name == DEFAULT_PORTFOLIO_NAME
        )
    ).scalars().first()
    if portfolio is None:
        portfolio = StrategyPortfolio(
            paper_account_id=default_account.id,
            name=DEFAULT_PORTFOLIO_NAME,
            description="Default virtual sleeve for paper trading",
            status="active",
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
    return portfolio


def list_paper_accounts(
    db: Session,
    *,
    status: str | None = None,
) -> list[PaperTradingAccount]:
    stmt = select(PaperTradingAccount).order_by(PaperTradingAccount.created_at.asc())
    if status is not None:
        stmt = stmt.where(PaperTradingAccount.status == status)
    return db.execute(stmt).scalars().all()


def update_paper_account(
    db: Session,
    account_id: UUID | str,
    *,
    name: str,
    api_key_env: str,
    secret_key_env: str,
    base_url: str,
    timeout_seconds: float,
    notes: str | None,
    status: str,
) -> PaperTradingAccount:
    account = db.get(PaperTradingAccount, account_id)
    if account is None:
        raise ValueError("paper account not found")

    normalized_name = normalize_account_name(name)
    existing = db.execute(
        select(PaperTradingAccount)
        .where(PaperTradingAccount.id != account.id)
        .where(PaperTradingAccount.name == normalized_name)
    ).scalars().first()
    if existing is not None:
        raise ValueError("paper account name already exists")

    account.name = normalized_name
    account.api_key_env = str(api_key_env or "").strip()
    account.secret_key_env = str(secret_key_env or "").strip()
    account.base_url = normalize_alpaca_base_url(base_url)
    account.timeout_seconds = timeout_seconds
    account.notes = notes
    account.status = status

    db.commit()
    db.refresh(account)
    return account


def list_strategy_portfolios(
    db: Session,
    *,
    paper_account_id: UUID | str | None = None,
    status: str | None = None,
) -> list[StrategyPortfolio]:
    stmt = select(StrategyPortfolio).order_by(
        StrategyPortfolio.created_at.asc(),
        StrategyPortfolio.name.asc(),
    )
    if paper_account_id is not None:
        stmt = stmt.where(StrategyPortfolio.paper_account_id == paper_account_id)
    if status is not None:
        stmt = stmt.where(StrategyPortfolio.status == status)
    return db.execute(stmt).scalars().all()


def rename_strategy_portfolio(
    db: Session,
    portfolio_id: UUID | str,
    *,
    name: str | None,
) -> StrategyPortfolio:
    portfolio = db.get(StrategyPortfolio, portfolio_id)
    if portfolio is None:
        raise ValueError("strategy portfolio not found")

    normalized_name = normalize_portfolio_name(name)
    if normalized_name == portfolio.name:
        return portfolio

    existing = db.execute(
        select(StrategyPortfolio)
        .where(StrategyPortfolio.id != portfolio.id)
        .where(StrategyPortfolio.name == normalized_name)
    ).scalars().first()
    if existing is not None:
        raise ValueError(
            "strategy portfolio name already exists; first version requires globally unique portfolio names"
        )

    old_name = portfolio.name
    portfolio.name = normalized_name

    allocations = db.execute(
        select(StrategyAllocation).where(StrategyAllocation.portfolio_name == old_name)
    ).scalars().all()
    for allocation in allocations:
        allocation.portfolio_name = normalized_name

    runs = db.execute(
        select(StrategyRun).where(StrategyRun.mode == "paper")
    ).scalars().all()
    affected_run_ids: list[UUID] = []
    for run in runs:
        changed = False

        config_snapshot = dict(run.config_snapshot or {})
        paper_cfg = config_snapshot.get("paper_trading")
        if isinstance(paper_cfg, dict):
            run_portfolio_name = normalize_portfolio_name(paper_cfg.get("portfolio_name"))
            if run_portfolio_name == old_name:
                config_snapshot["paper_trading"] = {
                    **paper_cfg,
                    "portfolio_name": normalized_name,
                }
                run.config_snapshot = config_snapshot
                changed = True

        summary_metrics = dict(run.summary_metrics or {})
        summary_portfolio_name = normalize_portfolio_name(summary_metrics.get("portfolio_name"))
        if summary_portfolio_name == old_name:
            summary_metrics["portfolio_name"] = normalized_name
            run.summary_metrics = summary_metrics
            changed = True

        if changed:
            affected_run_ids.append(run.id)

    if affected_run_ids:
        snapshots = db.execute(
            select(PortfolioSnapshot).where(PortfolioSnapshot.run_id.in_(affected_run_ids))
        ).scalars().all()
        for snapshot in snapshots:
            metrics = dict(snapshot.metrics or {})
            snapshot_portfolio_name = normalize_portfolio_name(metrics.get("portfolio_name"))
            if snapshot_portfolio_name == old_name:
                metrics["portfolio_name"] = normalized_name
                snapshot.metrics = metrics

        transactions = db.execute(
            select(Transaction).where(Transaction.run_id.in_(affected_run_ids))
        ).scalars().all()
        for txn in transactions:
            meta = dict(txn.meta or {})
            txn_portfolio_name = normalize_portfolio_name(meta.get("portfolio_name"))
            if txn_portfolio_name == old_name:
                meta["portfolio_name"] = normalized_name
                txn.meta = meta

    db.commit()
    db.refresh(portfolio)
    return portfolio


def archive_strategy_portfolio(
    db: Session,
    portfolio_id: UUID | str,
) -> StrategyPortfolio:
    portfolio = db.get(StrategyPortfolio, portfolio_id)
    if portfolio is None:
        raise ValueError("strategy portfolio not found")

    if portfolio.status == "archived":
        return portfolio

    portfolio.status = "archived"

    allocations = db.execute(
        select(StrategyAllocation).where(StrategyAllocation.portfolio_name == portfolio.name)
    ).scalars().all()
    for allocation in allocations:
        allocation.status = "archived"

    db.commit()
    db.refresh(portfolio)
    return portfolio


def delete_strategy_portfolio(
    db: Session,
    portfolio_id: UUID | str,
) -> StrategyPortfolio:
    portfolio = db.get(StrategyPortfolio, portfolio_id)
    if portfolio is None:
        raise ValueError("strategy portfolio not found")

    _purge_portfolio_related_records(db, {portfolio.name})
    db.delete(portfolio)
    db.commit()
    return portfolio


def delete_paper_account(
    db: Session,
    account_id: UUID | str,
) -> PaperTradingAccount:
    account = db.get(PaperTradingAccount, account_id)
    if account is None:
        raise ValueError("paper account not found")

    portfolios = list_strategy_portfolios(db, paper_account_id=account.id)
    portfolio_names = {portfolio.name for portfolio in portfolios}
    if portfolio_names:
        _purge_portfolio_related_records(db, portfolio_names)
    db.delete(account)
    db.commit()
    return account


def get_strategy_portfolio_by_name(
    db: Session,
    portfolio_name: str | None,
) -> StrategyPortfolio | None:
    normalized = normalize_portfolio_name(portfolio_name)
    return db.execute(
        select(StrategyPortfolio).where(StrategyPortfolio.name == normalized)
    ).scalars().first()


def require_strategy_portfolio_by_name(
    db: Session,
    portfolio_name: str | None,
) -> StrategyPortfolio:
    portfolio = get_strategy_portfolio_by_name(db, portfolio_name)
    if portfolio is None:
        raise ValueError(
            f"strategy portfolio not found: {normalize_portfolio_name(portfolio_name)}"
        )
    return portfolio


def build_alpaca_client_for_portfolio(
    db: Session,
    portfolio_name: str | None,
) -> AlpacaClient:
    portfolio = require_strategy_portfolio_by_name(db, portfolio_name)
    account = db.get(PaperTradingAccount, portfolio.paper_account_id)
    if account is None:
        raise ValueError(f"paper account not found for portfolio: {portfolio.name}")
    return build_alpaca_client_for_account(account)


def build_alpaca_client_for_account(account: PaperTradingAccount) -> AlpacaClient:
    return get_alpaca_client(
        api_key=_required_env_value(account.api_key_env),
        secret_key=_required_env_value(account.secret_key_env),
        base_url=account.base_url,
        timeout_seconds=float(account.timeout_seconds or 20),
    )


def build_paper_account_overview(
    db: Session,
    account_id: UUID | str,
) -> dict[str, Any]:
    account = db.get(PaperTradingAccount, account_id)
    if account is None:
        raise ValueError("paper account not found")

    portfolios = list_strategy_portfolios(db, paper_account_id=account.id)
    portfolio_names = {portfolio.name for portfolio in portfolios}
    allocations = db.execute(
        select(StrategyAllocation, Strategy)
        .join(Strategy, Strategy.id == StrategyAllocation.strategy_id)
        .where(StrategyAllocation.portfolio_name.in_(portfolio_names or {DEFAULT_PORTFOLIO_NAME}))
        .order_by(StrategyAllocation.portfolio_name.asc(), StrategyAllocation.created_at.asc())
    ).all()
    runs = db.execute(
        select(StrategyRun)
        .where(StrategyRun.mode == "paper")
        .order_by(StrategyRun.requested_at.desc())
        .limit(200)
    ).scalars().all()

    latest_run_by_portfolio: dict[str, StrategyRun] = {}
    latest_run_by_portfolio_strategy: dict[tuple[str, str], StrategyRun] = {}
    for run in runs:
        paper_cfg = (run.config_snapshot or {}).get("paper_trading", {})
        run_portfolio_name = str(paper_cfg.get("portfolio_name") or "").strip()
        if run_portfolio_name not in portfolio_names:
            continue
        if run_portfolio_name not in latest_run_by_portfolio:
            latest_run_by_portfolio[run_portfolio_name] = run
        key = (run_portfolio_name, str(run.strategy_id))
        if key not in latest_run_by_portfolio_strategy:
            latest_run_by_portfolio_strategy[key] = run

    rows_by_portfolio: dict[str, list[tuple[StrategyAllocation, Strategy]]] = {}
    for allocation, strategy in allocations:
        rows_by_portfolio.setdefault(allocation.portfolio_name, []).append((allocation, strategy))

    portfolio_summaries: list[dict[str, Any]] = []
    active_portfolio_count = 0
    active_allocation_count = 0
    active_strategy_count = 0

    for portfolio in portfolios:
        portfolio_rows = rows_by_portfolio.get(portfolio.name, [])
        active_rows = [row for row in portfolio_rows if row[0].status == "active"]
        latest_run = latest_run_by_portfolio.get(portfolio.name)
        strategy_items: list[dict[str, Any]] = []

        for allocation, strategy in portfolio_rows:
            strategy_run = latest_run_by_portfolio_strategy.get((portfolio.name, str(strategy.id)))
            strategy_items.append(
                {
                    "strategy_id": str(strategy.id),
                    "strategy_name": strategy.name,
                    "strategy_type": strategy.strategy_type,
                    "strategy_status": strategy.status,
                    "allocation_pct": float(allocation.allocation_pct or 0),
                    "capital_base": (
                        float(allocation.capital_base)
                        if allocation.capital_base is not None
                        else None
                    ),
                    "allow_fractional": bool(allocation.allow_fractional),
                    "auto_run_enabled": bool(allocation.auto_run_enabled),
                    "allocation_status": allocation.status,
                    "notes": allocation.notes,
                    "latest_run_id": str(strategy_run.id) if strategy_run is not None else None,
                    "latest_run_status": strategy_run.status if strategy_run is not None else None,
                    "latest_run_requested_at": (
                        strategy_run.requested_at if strategy_run is not None else None
                    ),
                    "latest_run_equity": (
                        float(strategy_run.final_equity)
                        if strategy_run is not None and strategy_run.final_equity is not None
                        else _summary_metric_float(strategy_run, "virtual_equity_after")
                    ),
                }
            )

        if portfolio.status == "active":
            active_portfolio_count += 1
        active_allocation_count += len(active_rows)
        active_strategy_count += len({str(strategy.id) for _, strategy in active_rows})

        portfolio_summaries.append(
            {
                "id": str(portfolio.id),
                "paper_account_id": str(account.id),
                "name": portfolio.name,
                "description": portfolio.description,
                "status": portfolio.status,
                "allocation_count": len(portfolio_rows),
                "active_allocation_count": len(active_rows),
                "allocated_strategy_count": len({str(strategy.id) for _, strategy in active_rows}),
                "active_allocation_pct_total": sum(
                    float(allocation.allocation_pct or 0) for allocation, _ in active_rows
                ),
                "latest_run_id": str(latest_run.id) if latest_run is not None else None,
                "latest_run_status": latest_run.status if latest_run is not None else None,
                "latest_run_requested_at": (
                    latest_run.requested_at if latest_run is not None else None
                ),
                "latest_run_equity": (
                    float(latest_run.final_equity)
                    if latest_run is not None and latest_run.final_equity is not None
                    else _summary_metric_float(latest_run, "virtual_equity_after")
                ),
                "strategies": strategy_items,
            }
        )

    return {
        "account": {
            "id": str(account.id),
            "name": account.name,
            "broker": account.broker,
            "mode": account.mode,
            "api_key_env": account.api_key_env,
            "secret_key_env": account.secret_key_env,
            "base_url": account.base_url,
            "timeout_seconds": float(account.timeout_seconds or 20),
            "notes": account.notes,
            "status": account.status,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        },
        "portfolio_count": len(portfolios),
        "active_portfolio_count": active_portfolio_count,
        "active_allocation_count": active_allocation_count,
        "active_strategy_count": active_strategy_count,
        "portfolios": portfolio_summaries,
    }


def build_paper_account_workspace(
    db: Session,
    account_id: UUID | str,
    *,
    order_limit: int = 50,
    transaction_limit: int = 100,
) -> dict[str, Any]:
    account = db.get(PaperTradingAccount, account_id)
    if account is None:
        raise ValueError("paper account not found")

    overview = build_paper_account_overview(db, account_id)
    portfolios = overview["portfolios"]
    portfolio_names = {str(item["name"]) for item in portfolios}
    runs = db.execute(
        select(StrategyRun)
        .where(StrategyRun.mode == "paper")
        .order_by(StrategyRun.requested_at.desc())
        .limit(200)
    ).scalars().all()
    latest_run_by_portfolio: dict[str, StrategyRun] = {}
    for run in runs:
        portfolio_name = _run_portfolio_name(run)
        if portfolio_name not in portfolio_names or portfolio_name in latest_run_by_portfolio:
            continue
        latest_run_by_portfolio[portfolio_name] = run

    tx_rows = db.execute(
        select(Transaction, Strategy.name)
        .join(Strategy, Strategy.id == Transaction.strategy_id)
        .order_by(Transaction.ts.desc())
    ).all()

    recent_transactions: list[dict[str, Any]] = []
    tx_summary_by_portfolio: dict[str, dict[str, Any]] = {}
    for transaction, strategy_name in tx_rows:
        portfolio_name = _transaction_portfolio_name(transaction)
        if portfolio_name not in portfolio_names:
            continue

        net_cash_flow = _transaction_net_cash_flow(transaction)
        summary = tx_summary_by_portfolio.setdefault(
            portfolio_name,
            {
                "transaction_count": 0,
                "net_cash_flow": 0.0,
                "latest_transaction_at": None,
            },
        )
        summary["transaction_count"] += 1
        summary["net_cash_flow"] += net_cash_flow
        if summary["latest_transaction_at"] is None:
            summary["latest_transaction_at"] = transaction.ts

        if len(recent_transactions) < transaction_limit:
            meta = transaction.meta or {}
            recent_transactions.append(
                {
                    "id": str(transaction.id),
                    "run_id": str(transaction.run_id) if transaction.run_id else None,
                    "ts": transaction.ts,
                    "portfolio_name": portfolio_name,
                    "strategy_id": str(transaction.strategy_id),
                    "strategy_name": strategy_name,
                    "symbol": transaction.symbol,
                    "side": transaction.side,
                    "qty": float(transaction.qty),
                    "price": float(transaction.price),
                    "fee": float(transaction.fee or 0),
                    "order_id": transaction.order_id,
                    "source": str(meta.get("source") or "").strip() or None,
                    "broker_status": str(meta.get("broker_status") or "").strip() or None,
                    "net_cash_flow": net_cash_flow,
                }
            )

    enriched_portfolios: list[dict[str, Any]] = []
    for portfolio in portfolios:
        summary = tx_summary_by_portfolio.get(
            str(portfolio["name"]),
            {
                "transaction_count": 0,
                "net_cash_flow": 0.0,
                "latest_transaction_at": None,
            },
        )
        latest_run = latest_run_by_portfolio.get(str(portfolio["name"]))
        latest_run_equity = portfolio.get("latest_run_equity")
        latest_run_return_pct = None
        capital_base = _summary_metric_float(latest_run, "capital_base")
        if isinstance(latest_run_equity, (int, float)) and capital_base is not None and capital_base > 0:
            latest_run_return_pct = (float(latest_run_equity) / capital_base) - 1

        enriched_portfolios.append(
            {
                **portfolio,
                "transaction_count": summary["transaction_count"],
                "net_cash_flow": summary["net_cash_flow"],
                "latest_transaction_at": summary["latest_transaction_at"],
                "latest_run_return_pct": latest_run_return_pct,
            }
        )

    broker_sync = {
        "status": "ok",
        "fetched_at": datetime.utcnow(),
        "error": None,
    }
    broker_account = None
    broker_clock = None
    broker_portfolio_history = None
    broker_positions: list[dict[str, Any]] = []
    recent_orders: list[dict[str, Any]] = []

    try:
        client = build_alpaca_client_for_account(account)
        raw_account = client.get_account()
        raw_clock = client.get_clock()
        history_start_at, history_range = _portfolio_history_window(account.created_at)
        raw_positions = client.list_positions()
        raw_orders = client.list_orders(status="all", limit=order_limit)

        broker_account = {
            "broker_account_id": str(raw_account.get("id") or "") or None,
            "account_number": str(raw_account.get("account_number") or "") or None,
            "status": str(raw_account.get("status") or "") or None,
            "currency": str(raw_account.get("currency") or "") or None,
            "cash": _safe_float(raw_account.get("cash")),
            "equity": _safe_float(raw_account.get("equity")),
            "buying_power": _safe_float(raw_account.get("buying_power")),
            "portfolio_value": _safe_float(raw_account.get("portfolio_value") or raw_account.get("equity")),
            "long_market_value": _safe_float(raw_account.get("long_market_value")),
            "short_market_value": _safe_float(raw_account.get("short_market_value")),
            "last_equity": _safe_float(raw_account.get("last_equity")),
            "daytrade_count": _safe_int(raw_account.get("daytrade_count")),
            "pattern_day_trader": _safe_bool(raw_account.get("pattern_day_trader")),
            "trading_blocked": _safe_bool(raw_account.get("trading_blocked")),
            "transfers_blocked": _safe_bool(raw_account.get("transfers_blocked")),
            "account_blocked": _safe_bool(raw_account.get("account_blocked")),
        }
        broker_clock = {
            "timestamp": raw_clock.get("timestamp"),
            "is_open": _safe_bool(raw_clock.get("is_open")),
            "next_open": raw_clock.get("next_open"),
            "next_close": raw_clock.get("next_close"),
        }
        try:
            raw_portfolio_history = client.get_portfolio_history(
                timeframe="1D",
                date_start=history_start_at.date(),
                date_end=datetime.now(UTC).date(),
            )
        except AlpacaAPIError as exc:
            if _is_ignorable_portfolio_history_error(exc):
                raw_portfolio_history = {}
            else:
                raise

        broker_portfolio_history = _build_broker_portfolio_history(
            raw_portfolio_history,
            range_label=history_range,
            start_at=history_start_at,
        )
        broker_positions = [
            {
                "symbol": str(item.get("symbol") or ""),
                "side": str(item.get("side") or "") or None,
                "qty": _safe_float(item.get("qty")),
                "market_value": _safe_float(item.get("market_value")),
                "cost_basis": _safe_float(item.get("cost_basis")),
                "avg_entry_price": _safe_float(item.get("avg_entry_price")),
                "unrealized_pl": _safe_float(item.get("unrealized_pl")),
                "unrealized_plpc": _safe_float(item.get("unrealized_plpc")),
                "current_price": _safe_float(item.get("current_price")),
                "change_today": _safe_float(item.get("change_today")),
            }
            for item in raw_positions
        ]
        recent_orders = [
            {
                "id": str(item.get("id") or "") or None,
                "client_order_id": str(item.get("client_order_id") or "") or None,
                "symbol": str(item.get("symbol") or "") or None,
                "side": str(item.get("side") or "") or None,
                "type": str(item.get("type") or "") or None,
                "time_in_force": str(item.get("time_in_force") or "") or None,
                "status": str(item.get("status") or "") or None,
                "qty": _safe_float(item.get("qty")),
                "filled_qty": _safe_float(item.get("filled_qty")),
                "filled_avg_price": _safe_float(item.get("filled_avg_price")),
                "limit_price": _safe_float(item.get("limit_price")),
                "stop_price": _safe_float(item.get("stop_price")),
                "submitted_at": item.get("submitted_at"),
                "filled_at": item.get("filled_at"),
                "canceled_at": item.get("canceled_at"),
            }
            for item in raw_orders
        ]
    except Exception as exc:
        broker_sync = {
            "status": "error",
            "fetched_at": datetime.utcnow(),
            "error": str(exc),
        }

    return {
        "account": overview["account"],
        "broker_sync": broker_sync,
        "broker_account": broker_account,
        "broker_clock": broker_clock,
        "portfolio_history": broker_portfolio_history,
        "positions": broker_positions,
        "recent_orders": recent_orders,
        "recent_transactions": recent_transactions,
        "portfolios": enriched_portfolios,
        "stats": {
            "portfolio_count": overview["portfolio_count"],
            "active_portfolio_count": overview["active_portfolio_count"],
            "active_allocation_count": overview["active_allocation_count"],
            "active_strategy_count": overview["active_strategy_count"],
            "position_count": len(broker_positions),
            "order_count": len(recent_orders),
            "transaction_count": len(recent_transactions),
        },
    }


def _required_env_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        for fallback_name in ENV_FALLBACKS.get(name, ()):
            fallback_value = os.getenv(fallback_name, "").strip()
            if fallback_value:
                return fallback_value
        raise ValueError(f"missing environment variable for paper account credential: {name}")
    return value


def _summary_metric_float(run: StrategyRun | None, key: str) -> float | None:
    if run is None:
        return None
    value = (run.summary_metrics or {}).get(key)
    if value is None:
        return None
    return float(value)


def _portfolio_history_window(created_at: datetime | None) -> tuple[datetime, str]:
    now = datetime.now(UTC)
    one_year_ago = now - timedelta(days=365)
    if created_at is None:
        return one_year_ago, "1Y"
    normalized_created_at = (
        created_at.replace(tzinfo=UTC)
        if created_at.tzinfo is None
        else created_at.astimezone(UTC)
    )
    if normalized_created_at < one_year_ago:
        return one_year_ago, "1Y"
    return normalized_created_at, "SINCE_INCEPTION"


def _build_broker_portfolio_history(
    raw_history: dict[str, Any],
    *,
    range_label: str,
    start_at: datetime,
) -> dict[str, Any] | None:
    timestamps = raw_history.get("timestamp")
    equity = raw_history.get("equity")
    if not isinstance(timestamps, list) or not isinstance(equity, list):
        return None

    point_count = min(len(timestamps), len(equity))
    if point_count == 0:
        return None

    profit_loss = raw_history.get("profit_loss")
    profit_loss_pct = raw_history.get("profit_loss_pct")
    base_value = _safe_float(raw_history.get("base_value"))

    points: list[dict[str, Any]] = []
    for index in range(point_count):
        ts_value = _history_timestamp_to_datetime(timestamps[index])
        equity_value = _safe_float(equity[index])
        if ts_value is None or equity_value is None:
            continue
        points.append(
            {
                "ts": ts_value,
                "equity": equity_value,
                "profit_loss": (
                    _safe_float(profit_loss[index])
                    if isinstance(profit_loss, list) and index < len(profit_loss)
                    else None
                ),
                "profit_loss_pct": (
                    _safe_float(profit_loss_pct[index])
                    if isinstance(profit_loss_pct, list) and index < len(profit_loss_pct)
                    else None
                ),
            }
        )

    if not points:
        return None

    start_value = points[0]["equity"]
    end_value = points[-1]["equity"]

    return {
        "range_label": range_label,
        "start_at": start_at,
        "end_at": points[-1]["ts"],
        "base_value": base_value,
        "start_value": start_value,
        "end_value": end_value,
        "absolute_change": end_value - start_value,
        "percent_change": ((end_value / start_value) - 1) if start_value else None,
        "points": points,
    }


def _is_ignorable_portfolio_history_error(exc: AlpacaAPIError) -> bool:
    if exc.status_code != 422:
        return False
    message = str(exc).lower()
    return "date_start cannot be after date_end" in message


def _purge_portfolio_related_records(db: Session, portfolio_names: set[str]) -> None:
    if not portfolio_names:
        return

    allocations = db.execute(
        select(StrategyAllocation).where(StrategyAllocation.portfolio_name.in_(portfolio_names))
    ).scalars().all()
    for allocation in allocations:
        db.delete(allocation)

    runs = db.execute(
        select(StrategyRun).where(StrategyRun.mode == "paper")
    ).scalars().all()
    matched_run_ids: set[UUID] = set()
    for run in runs:
        portfolio_name = _run_portfolio_name(run)
        if portfolio_name in portfolio_names:
            matched_run_ids.add(run.id)

    transactions = db.execute(select(Transaction).order_by(Transaction.ts.desc())).scalars().all()
    for transaction in transactions:
        tx_portfolio_name = _transaction_portfolio_name(transaction)
        if tx_portfolio_name in portfolio_names or transaction.run_id in matched_run_ids:
            db.delete(transaction)

    for run in runs:
        if run.id in matched_run_ids:
            db.delete(run)


def _run_portfolio_name(run: StrategyRun) -> str | None:
    paper_cfg = (run.config_snapshot or {}).get("paper_trading", {})
    if isinstance(paper_cfg, dict):
        value = str(paper_cfg.get("portfolio_name") or "").strip()
        if value:
            return value

    summary_value = str((run.summary_metrics or {}).get("portfolio_name") or "").strip()
    return summary_value or None


def _transaction_portfolio_name(transaction: Transaction) -> str | None:
    value = str((transaction.meta or {}).get("portfolio_name") or "").strip()
    return value or None


def _transaction_net_cash_flow(transaction: Transaction) -> float:
    qty = float(transaction.qty)
    price = float(transaction.price)
    fee = float(transaction.fee or 0)
    gross = qty * price
    if str(transaction.side).upper() == "SELL":
        return gross - fee
    return -gross - fee


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)


def _history_timestamp_to_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None

    if timestamp > 1_000_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, tz=UTC)
