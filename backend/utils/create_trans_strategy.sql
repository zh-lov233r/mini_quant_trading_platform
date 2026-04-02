-- Create strategy table
-- Not NULL fields：name; strategy_type; params
CREATE TABLE IF NOT EXISTS strategies (
    id UUID NOT NULL PRIMARY KEY,
    strategy_key VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    strategy_type VARCHAR(32) NOT NULL,
    params JSONB NOT NULL,
    cur_position JSONB DEFAULT '{}'::jsonb,   -- legacy runtime field; prefer portfolio snapshots for new code
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    idempotency_key VARCHAR(64) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE strategies
    ADD COLUMN IF NOT EXISTS strategy_key VARCHAR(128);

UPDATE strategies
SET strategy_key = name
WHERE strategy_key IS NULL OR BTRIM(strategy_key) = '';

ALTER TABLE strategies
    ALTER COLUMN strategy_key SET NOT NULL;

-- Avoid repeating the same strategy family under the same version number
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'uq_strategy_key_version'
  ) THEN
    ALTER TABLE strategies
    ADD CONSTRAINT uq_strategy_key_version UNIQUE (strategy_key, version);
  END IF;
END $$;


-- One logical execution of a strategy.
-- mode:
--   backtest = historical replay
--   paper    = simulated live trading
--   live     = broker-connected live trading
CREATE TABLE IF NOT EXISTS strategy_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    strategy_version INTEGER NOT NULL,
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('backtest', 'paper', 'live')),
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    window_start DATE,
    window_end DATE,
    initial_cash NUMERIC(20,8),
    final_equity NUMERIC(20,8),
    benchmark_symbol TEXT,
    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (window_end IS NULL OR window_start IS NULL OR window_end >= window_start),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy_requested
    ON strategy_runs (strategy_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_mode_status
    ON strategy_runs (mode, status, requested_at DESC);


-- Generated signals per strategy run.
-- features stores the indicator snapshot / reason context used by the engine.
CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD')),
    score NUMERIC(20,8),
    reason TEXT,
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_run_ts
    ON signals (run_id, ts);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts
    ON signals (symbol, ts);


-- Time-series snapshots for PnL curve / risk curve / position snapshots.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    cash NUMERIC(20,8) NOT NULL,
    equity NUMERIC(20,8) NOT NULL,
    gross_exposure NUMERIC(20,8) DEFAULT 0,
    net_exposure NUMERIC(20,8) DEFAULT 0,
    drawdown NUMERIC(20,8),
    positions JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, ts)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_run_ts
    ON portfolio_snapshots (run_id, ts);


-- Create transactional table, for every transaction executed
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    run_id UUID REFERENCES strategy_runs(id) ON DELETE SET NULL,
    ts TIMESTAMPTZ NOT NULL,   -- transaction time
    symbol TEXT NOT NULL,      -- symbol
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty NUMERIC(20,8) NOT NULL CHECK (qty > 0),   -- quantity
    price NUMERIC(20,8) NOT NULL CHECK (price >= 0),   -- transaction price
    fee NUMERIC(20,8) DEFAULT 0,                   -- service charge
    order_id TEXT,                                 -- broker order ID
    meta JSONB NOT NULL DEFAULT '{}'::jsonb        -- other fileds, like slippage
);

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS run_id UUID;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'fk_transactions_run_id'
  ) THEN
    ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_run_id
    FOREIGN KEY (run_id) REFERENCES strategy_runs(id) ON DELETE SET NULL;
  END IF;
END $$;

-- create index
CREATE INDEX IF NOT EXISTS idx_tx_strategy_ts ON transactions (strategy_id, ts);
CREATE INDEX IF NOT EXISTS idx_tx_run_ts      ON transactions (run_id, ts);
CREATE INDEX IF NOT EXISTS idx_tx_symbol_ts   ON transactions (symbol, ts);
