CREATE TABLE IF NOT EXISTS strategy_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    portfolio_name VARCHAR(64) NOT NULL DEFAULT 'default',
    allocation_pct NUMERIC(12,8) NOT NULL DEFAULT 0,
    capital_base NUMERIC(20,8),
    allow_fractional INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_strategy_allocations_strategy_portfolio
        UNIQUE (strategy_id, portfolio_name),
    CONSTRAINT ck_strategy_allocations_status
        CHECK (status IN ('draft', 'active', 'archived')),
    CONSTRAINT ck_strategy_allocations_pct
        CHECK (allocation_pct >= 0 AND allocation_pct <= 1),
    CONSTRAINT ck_strategy_allocations_capital_base
        CHECK (capital_base IS NULL OR capital_base >= 0)
);

CREATE INDEX IF NOT EXISTS idx_strategy_allocations_portfolio_status
    ON strategy_allocations (portfolio_name, status);

CREATE INDEX IF NOT EXISTS idx_strategy_allocations_strategy_status
    ON strategy_allocations (strategy_id, status);
