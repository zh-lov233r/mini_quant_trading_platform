# 数据模型; Data Model
from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()
JSON_VARIANT = JSONB().with_variant(JSON(), "sqlite")


class StockBasket(Base):
    __tablename__ = "stock_baskets"
    __table_args__ = (
        UniqueConstraint("name", name="uq_stock_baskets_name"),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_stock_baskets_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False)
    description = Column(Text)
    symbols = Column(JSON_VARIANT, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="draft")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("strategy_key", "version", name="uq_strategy_key_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_key = Column(String(128), nullable=False)
    name = Column(String(128), nullable=False)
    strategy_type = Column(String(32), nullable=False)
    params = Column(JSON_VARIANT, nullable=False)
    cur_position = Column(JSON_VARIANT, default=dict)
    status = Column(String(16), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String(64), unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    runs = relationship(
        "StrategyRun",
        back_populates="strategy",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    signals = relationship(
        "Signal",
        back_populates="strategy",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    allocations = relationship(
        "StrategyAllocation",
        back_populates="strategy",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    transactions = relationship(
        "Transaction",
        back_populates="strategy",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PaperTradingAccount(Base):
    __tablename__ = "paper_trading_accounts"
    __table_args__ = (
        UniqueConstraint("name", name="uq_paper_trading_accounts_name"),
        CheckConstraint(
            "broker IN ('alpaca')",
            name="ck_paper_trading_accounts_broker",
        ),
        CheckConstraint(
            "mode IN ('paper', 'live')",
            name="ck_paper_trading_accounts_mode",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_paper_trading_accounts_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False)
    broker = Column(String(32), nullable=False, default="alpaca")
    mode = Column(String(16), nullable=False, default="paper")
    api_key_env = Column(String(128), nullable=False, default="ALPACA_API_KEY")
    secret_key_env = Column(String(128), nullable=False, default="ALPACA_SECRET_KEY")
    base_url = Column(String(255), nullable=False, default="https://paper-api.alpaca.markets")
    timeout_seconds = Column(Numeric(10, 4), nullable=False, default=20)
    notes = Column(Text)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    portfolios = relationship(
        "StrategyPortfolio",
        back_populates="paper_account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StrategyPortfolio(Base):
    __tablename__ = "strategy_portfolios"
    __table_args__ = (
        UniqueConstraint("name", name="uq_strategy_portfolios_name"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_strategy_portfolios_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("paper_trading_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(64), nullable=False)
    description = Column(Text)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    paper_account = relationship("PaperTradingAccount", back_populates="portfolios")


class StrategyAllocation(Base):
    __tablename__ = "strategy_allocations"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id",
            "portfolio_name",
            name="uq_strategy_allocations_strategy_portfolio",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_strategy_allocations_status",
        ),
        CheckConstraint(
            "allocation_pct >= 0 AND allocation_pct <= 1",
            name="ck_strategy_allocations_pct",
        ),
        CheckConstraint(
            "capital_base IS NULL OR capital_base >= 0",
            name="ck_strategy_allocations_capital_base",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    portfolio_name = Column(String(64), nullable=False, default="default")
    allocation_pct = Column(Numeric(12, 8), nullable=False, default=0)
    capital_base = Column(Numeric(20, 8))
    allow_fractional = Column(Integer, nullable=False, default=1)
    notes = Column(Text)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    strategy = relationship("Strategy", back_populates="allocations")


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('backtest', 'paper', 'live')",
            name="ck_strategy_runs_mode",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_strategy_runs_status",
        ),
        CheckConstraint(
            "window_end IS NULL OR window_start IS NULL OR window_end >= window_start",
            name="ck_strategy_runs_window",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_strategy_runs_times",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_version = Column(Integer, nullable=False)
    mode = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="queued")
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    window_start = Column(Date)
    window_end = Column(Date)
    initial_cash = Column(Numeric(20, 8))
    final_equity = Column(Numeric(20, 8))
    benchmark_symbol = Column(Text)
    config_snapshot = Column(JSON_VARIANT, nullable=False, default=dict)
    summary_metrics = Column(JSON_VARIANT, nullable=False, default=dict)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    strategy = relationship("Strategy", back_populates="runs")
    signals = relationship(
        "Signal",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    transactions = relationship(
        "Transaction",
        back_populates="run",
        passive_deletes=True,
    )
    portfolio_snapshots = relationship(
        "PortfolioSnapshot",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PortfolioSnapshot.ts",
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("signal IN ('BUY', 'SELL', 'HOLD')", name="ck_signals_signal"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(Text, nullable=False)
    signal = Column(Text, nullable=False)
    score = Column(Numeric(20, 8))
    reason = Column(Text)
    features = Column(JSON_VARIANT, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run = relationship("StrategyRun", back_populates="signals")
    strategy = relationship("Strategy", back_populates="signals")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_transactions_side"),
        CheckConstraint("qty > 0", name="ck_transactions_qty"),
        CheckConstraint("price >= 0", name="ck_transactions_price"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="SET NULL"),
    )
    ts = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(Text, nullable=False)
    side = Column(Text, nullable=False)
    qty = Column(Numeric(20, 8), nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    fee = Column(Numeric(20, 8), default=0)
    order_id = Column(Text)
    meta = Column(JSON_VARIANT, nullable=False, default=dict)

    strategy = relationship("Strategy", back_populates="transactions")
    run = relationship("StrategyRun", back_populates="transactions")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "ts", name="uq_portfolio_snapshots_run_ts"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts = Column(DateTime(timezone=True), nullable=False)
    cash = Column(Numeric(20, 8), nullable=False)
    equity = Column(Numeric(20, 8), nullable=False)
    gross_exposure = Column(Numeric(20, 8), default=0)
    net_exposure = Column(Numeric(20, 8), default=0)
    drawdown = Column(Numeric(20, 8))
    positions = Column(JSON_VARIANT, nullable=False, default=dict)
    metrics = Column(JSON_VARIANT, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run = relationship("StrategyRun", back_populates="portfolio_snapshots")
