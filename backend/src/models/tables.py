# 数据模型; Data Model
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    JSON,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()
JSON_VARIANT = JSONB().with_variant(JSON(), "sqlite")


class Instrument(Base):
    """ORM mapping for the SQL-managed security-master identity table."""

    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint(
            "ticker_canonical IS NULL OR ticker_canonical = UPPER(ticker_canonical)",
            name="ck_instruments_ticker_canonical_upper",
        ),
        CheckConstraint(
            "currency = UPPER(currency)",
            name="ck_instruments_currency_upper",
        ),
        CheckConstraint(
            "delisted_at IS NULL OR listed_at IS NULL OR delisted_at >= listed_at",
            name="ck_instruments_listing_window",
        ),
        Index("idx_instr_exchange", "exchange"),
        Index("idx_instr_ticker", "ticker_canonical"),
        Index("idx_instr_active", "is_active"),
        Index("idx_instr_figi_composite", "composite_figi"),
        Index("idx_instr_cik", "cik"),
        Index(
            "idx_instruments_sic_code",
            "sic_code",
            postgresql_where=text("sic_code IS NOT NULL"),
        ),
    )

    id = Column(BigInteger, Identity(always=True), primary_key=True)
    share_class_figi = Column(Text, nullable=False, unique=True)
    composite_figi = Column(Text)
    cik = Column(Text)
    ticker_canonical = Column(Text)
    exchange = Column(Text, nullable=False)
    mic = Column(Text)
    asset_type = Column(Text, nullable=False, default="CS")
    share_class = Column(Text)
    name = Column(Text)
    currency = Column(Text, nullable=False, default="USD")
    country = Column(Text)
    locale = Column(Text, default="us")
    market = Column(Text, nullable=False, default="stocks")
    sic_code = Column(Text)
    sic_description = Column(Text)
    sic_source = Column(Text)
    sic_asof = Column(DateTime(timezone=True))
    listed_at = Column(Date)
    delisted_at = Column(Date)
    is_active = Column(Boolean, nullable=False, default=True)
    vendor_source = Column(Text, nullable=False, default="massive")
    vendor_payload = Column(JSON_VARIANT, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


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
    idempotency_key = Column(String(128), unique=True)
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
    auto_run_enabled = Column(Boolean, nullable=False, default=True)
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
    support_resistance_materializations = relationship(
        "SupportResistanceRunMaterialization",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    support_resistance_events = relationship(
        "SupportResistanceRunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    backtest_job = relationship(
        "BacktestJob",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class BacktestJob(Base):
    """Durable PostgreSQL-backed execution lease for one StrategyRun."""

    __tablename__ = "backtest_jobs"
    __table_args__ = (
        CheckConstraint(
            "source IN ('manual', 'research', 'verification')",
            name="ck_backtest_jobs_source",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_backtest_jobs_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_backtest_jobs_attempt"),
        CheckConstraint("max_attempts >= 1", name="ck_backtest_jobs_max_attempts"),
        Index(
            "idx_backtest_jobs_claim",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
        Index("idx_backtest_jobs_lease", "status", "lease_expires_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    experiment_trial_id = Column(
        UUID(as_uuid=True),
        ForeignKey("experiment_trials.id", ondelete="SET NULL"),
        nullable=True,
    )
    source = Column(String(16), nullable=False, default="manual")
    status = Column(String(16), nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=0)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=2)
    available_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    claimed_by = Column(Text)
    claimed_at = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))
    lease_expires_at = Column(DateTime(timezone=True))
    cancel_requested_at = Column(DateTime(timezone=True))
    payload = Column(JSON_VARIANT, nullable=False, default=dict)
    progress = Column(JSON_VARIANT, nullable=False, default=dict)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    run = relationship("StrategyRun", back_populates="backtest_job")
    experiment_trial = relationship("ExperimentTrial")


class BacktestWorkerManager(Base):
    """Heartbeat and child-worker state for an on-demand manager instance."""

    __tablename__ = "backtest_worker_managers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('idle', 'starting', 'running', 'backoff', 'standby', 'stopping')",
            name="ck_backtest_worker_managers_status",
        ),
        Index("idx_backtest_worker_managers_heartbeat", "heartbeat_at"),
    )

    manager_id = Column(Text, primary_key=True)
    hostname = Column(Text, nullable=False)
    pid = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="standby")
    is_leader = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    heartbeat_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    worker_pid = Column(Integer)
    worker_started_at = Column(DateTime(timezone=True))
    last_worker_exit_at = Column(DateTime(timezone=True))
    last_worker_exit_code = Column(Integer)
    next_worker_start_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class SupportResistanceMaterialization(Base):
    __tablename__ = "support_resistance_materializations"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_support_resistance_materializations_cache_key"),
        CheckConstraint(
            "status IN ('building', 'completed', 'failed')",
            name="ck_support_resistance_materializations_status",
        ),
        CheckConstraint(
            "coverage_end >= coverage_start",
            name="ck_support_resistance_materializations_window",
        ),
        Index(
            "idx_support_resistance_materializations_lookup",
            "algorithm_version",
            "universe_hash",
            "source_data_fingerprint",
            "coverage_start",
            "coverage_end",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cache_key = Column(String(64), nullable=False)
    algorithm_version = Column(String(64), nullable=False)
    detector_params = Column(JSON_VARIANT, nullable=False)
    universe_hash = Column(String(64), nullable=False)
    symbols = Column(JSON_VARIANT, nullable=False, default=list)
    coverage_start = Column(Date, nullable=False)
    coverage_end = Column(Date, nullable=False)
    source_data_fingerprint = Column(String(64), nullable=False)
    price_semantics = Column(String(96), nullable=False)
    status = Column(String(16), nullable=False, default="building")
    statistics = Column(JSON_VARIANT, nullable=False, default=dict)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    zone_versions = relationship(
        "SupportResistanceZoneVersion",
        back_populates="materialization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    regime_versions = relationship(
        "SupportResistanceRegimeVersion",
        back_populates="materialization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    run_links = relationship(
        "SupportResistanceRunMaterialization",
        back_populates="materialization",
        passive_deletes=True,
    )


class SupportResistanceZoneVersion(Base):
    __tablename__ = "support_resistance_zone_versions"
    __table_args__ = (
        UniqueConstraint(
            "materialization_id",
            "instrument_id",
            "zone_key",
            "version",
            name="uq_support_resistance_zone_versions_identity",
        ),
        UniqueConstraint(
            "materialization_id",
            "instrument_id",
            "zone_key",
            "effective_from",
            name="uq_support_resistance_zone_versions_effective_from",
        ),
        CheckConstraint("role IN ('support', 'resistance')", name="ck_support_resistance_zone_role"),
        CheckConstraint(
            "status IN ('active', 'expired', 'broken', 'transformed')",
            name="ck_support_resistance_zone_status",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_support_resistance_zone_window",
        ),
        CheckConstraint(
            "projection_end >= effective_from",
            name="ck_support_resistance_zone_projection_window",
        ),
        CheckConstraint(
            "end_lower_price <= end_center_price AND end_center_price <= end_upper_price",
            name="ck_support_resistance_zone_end_prices",
        ),
        Index(
            "idx_support_resistance_zone_versions_timeline",
            "materialization_id",
            "symbol",
            "zone_key",
            "effective_from",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    materialization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_resistance_materializations.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id = Column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="SET NULL"),
    )
    symbol = Column(Text, nullable=False)
    zone_key = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date)
    role = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    center_price = Column(Numeric(24, 10), nullable=False)
    lower_price = Column(Numeric(24, 10), nullable=False)
    upper_price = Column(Numeric(24, 10), nullable=False)
    atr_width = Column(Numeric(24, 10), nullable=False)
    anchor_session_index = Column(Integer, nullable=False)
    slope_per_session = Column(Numeric(24, 10), nullable=False)
    fit_residual_atr = Column(Numeric(20, 10), nullable=False)
    projection_end = Column(Date, nullable=False)
    end_center_price = Column(Numeric(24, 10), nullable=False)
    end_lower_price = Column(Numeric(24, 10), nullable=False)
    end_upper_price = Column(Numeric(24, 10), nullable=False)
    pivot_count = Column(Integer, nullable=False)
    touch_count = Column(Integer, nullable=False)
    source_metadata = Column(JSON_VARIANT, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    materialization = relationship("SupportResistanceMaterialization", back_populates="zone_versions")


class SupportResistanceRegimeVersion(Base):
    __tablename__ = "support_resistance_regime_versions"
    __table_args__ = (
        UniqueConstraint(
            "materialization_id",
            "symbol",
            "version",
            name="uq_support_resistance_regime_versions_identity",
        ),
        UniqueConstraint(
            "materialization_id",
            "symbol",
            "effective_from",
            name="uq_support_resistance_regime_versions_effective_from",
        ),
        CheckConstraint(
            "regime IN ('uptrend', 'downtrend', 'range', 'transition')",
            name="ck_support_resistance_regime",
        ),
        CheckConstraint("version > 0", name="ck_support_resistance_regime_version"),
        Index(
            "idx_support_resistance_regime_versions_timeline",
            "materialization_id",
            "symbol",
            "effective_from",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    materialization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_resistance_materializations.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id = Column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="SET NULL"),
    )
    symbol = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    effective_from = Column(Date, nullable=False)
    regime = Column(String(16), nullable=False)
    lower_zone_key = Column(String(64))
    upper_zone_key = Column(String(64))
    reason_code = Column(String(64), nullable=False)
    evidence = Column(JSON_VARIANT, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    materialization = relationship(
        "SupportResistanceMaterialization",
        back_populates="regime_versions",
    )


class SupportResistanceRunMaterialization(Base):
    __tablename__ = "support_resistance_run_materializations"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_support_resistance_run_materializations_run"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    materialization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_resistance_materializations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run = relationship("StrategyRun", back_populates="support_resistance_materializations")
    materialization = relationship("SupportResistanceMaterialization", back_populates="run_links")


class SupportResistanceRunEvent(Base):
    __tablename__ = "support_resistance_run_events"
    __table_args__ = (
        Index(
            "idx_support_resistance_run_events_filter",
            "run_id",
            "symbol",
            "zone_key",
            "event_date",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    materialization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_resistance_materializations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    instrument_id = Column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="SET NULL"),
    )
    symbol = Column(Text, nullable=False)
    event_date = Column(Date, nullable=False)
    event_type = Column(String(32), nullable=False)
    zone_key = Column(String(64))
    setup = Column(String(32))
    selected = Column(Boolean, nullable=False, default=False)
    score = Column(Numeric(20, 10))
    posterior_sample_count = Column(Integer)
    lower_price = Column(Numeric(24, 10))
    upper_price = Column(Numeric(24, 10))
    payload = Column(JSON_VARIANT, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run = relationship("StrategyRun", back_populates="support_resistance_events")


class ResearchExperiment(Base):
    __tablename__ = "research_experiments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_research_experiments_idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_agent', 'completed', 'partially_failed', "
            "'failed', 'cancel_requested', 'cancelled', 'data_changed')",
            name="ck_research_experiments_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_experiment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_experiments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    study_kind = Column(String(64), nullable=False, default="adaptive_category", index=True)
    workflow_run_id = Column(String(64), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="queued", index=True)
    spec = Column(JSON_VARIANT, nullable=False, default=dict)
    run_manifest = Column(JSON_VARIANT, nullable=False, default=dict)
    progress = Column(JSON_VARIANT, nullable=False, default=dict)
    report = Column(JSON_VARIANT, nullable=False, default=dict)
    error_code = Column(String(64))
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    trials = relationship(
        "ExperimentTrial",
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExperimentTrial.ordinal",
    )
    rounds = relationship(
        "ExperimentRound",
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExperimentRound.ordinal",
    )
    candidates = relationship(
        "ExperimentCandidate",
        back_populates="experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExperimentCandidate.params_hash",
    )
    parent_experiment = relationship(
        "ResearchExperiment",
        remote_side=[id],
        back_populates="child_experiments",
    )
    child_experiments = relationship(
        "ResearchExperiment",
        back_populates="parent_experiment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ResearchExperiment.created_at",
    )


class ExperimentRound(Base):
    __tablename__ = "experiment_rounds"
    __table_args__ = (
        UniqueConstraint("experiment_id", "ordinal", name="uq_experiment_rounds_ordinal"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_experiment_rounds_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="queued")
    proposal = Column(JSON_VARIANT, nullable=False, default=dict)
    validation_issues = Column(JSON_VARIANT, nullable=False, default=list)
    result_summary = Column(JSON_VARIANT, nullable=False, default=dict)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    experiment = relationship("ResearchExperiment", back_populates="rounds")
    candidates = relationship(
        "ExperimentCandidate",
        back_populates="round",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExperimentCandidate.ordinal",
    )


class ExperimentCandidate(Base):
    __tablename__ = "experiment_candidates"
    __table_args__ = (
        UniqueConstraint("experiment_id", "params_hash", name="uq_experiment_candidates_params_hash"),
        UniqueConstraint("round_id", "ordinal", name="uq_experiment_candidates_round_ordinal"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_id = Column(
        UUID(as_uuid=True),
        ForeignKey("experiment_rounds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    parameter_overrides = Column(JSON_VARIANT, nullable=False, default=dict)
    params = Column(JSON_VARIANT, nullable=False, default=dict)
    params_hash = Column(String(64), nullable=False)
    rationale = Column(Text)
    aggregate_metrics = Column(JSON_VARIANT, nullable=False, default=dict)
    pareto_rank = Column(Integer)
    promoted_strategy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    experiment = relationship("ResearchExperiment", back_populates="candidates")
    round = relationship("ExperimentRound", back_populates="candidates")
    trials = relationship("ExperimentTrial", back_populates="candidate")
    promoted_strategy = relationship("Strategy", foreign_keys=[promoted_strategy_id])


class ExperimentTrial(Base):
    __tablename__ = "experiment_trials"
    __table_args__ = (
        UniqueConstraint("experiment_id", "trial_key", name="uq_experiment_trials_key"),
        UniqueConstraint("experiment_id", "ordinal", name="uq_experiment_trials_ordinal"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_experiment_trials_status",
        ),
        CheckConstraint(
            "sample_kind IN ('in_sample', 'out_of_sample')",
            name="ck_experiment_trials_sample_kind",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    backtest_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id = Column(
        UUID(as_uuid=True),
        ForeignKey("experiment_candidates.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    trial_key = Column(String(64), nullable=False)
    ordinal = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="queued", index=True)
    sample_kind = Column(String(16), nullable=False)
    cost_scenario = Column(String(64), nullable=False)
    params = Column(JSON_VARIANT, nullable=False, default=dict)
    params_hash = Column(String(64), nullable=False)
    window_start = Column(Date, nullable=False)
    window_end = Column(Date, nullable=False)
    cost_config = Column(JSON_VARIANT, nullable=False, default=dict)
    data_fingerprint = Column(String(64))
    metrics = Column(JSON_VARIANT, nullable=False, default=dict)
    attempt = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64))
    error_message = Column(Text)
    cancel_requested_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    experiment = relationship("ResearchExperiment", back_populates="trials")
    candidate = relationship("ExperimentCandidate", back_populates="trials")
    backtest_run = relationship("StrategyRun")


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
    instrument_id = Column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="SET NULL"),
        nullable=True,
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
    instrument_id = Column(
        BigInteger,
        ForeignKey("instruments.id", ondelete="SET NULL"),
        nullable=True,
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
