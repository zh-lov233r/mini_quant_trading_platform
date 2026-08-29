-- Additive backtest performance and durable-worker schema.
-- This repository has no Alembic workflow; apply only after a backup and a
-- read-only preflight against the exact target database.

ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS instrument_id BIGINT
    REFERENCES instruments(id) ON DELETE SET NULL;

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS instrument_id BIGINT
    REFERENCES instruments(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS backtest_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL UNIQUE
        REFERENCES strategy_runs(id) ON DELETE CASCADE,
    experiment_trial_id UUID
        REFERENCES experiment_trials(id) ON DELETE SET NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'research', 'verification')),
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_attempts >= 1),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_by TEXT,
    claimed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_jobs_claim
    ON backtest_jobs (status, available_at, priority DESC, created_at);

CREATE INDEX IF NOT EXISTS idx_backtest_jobs_lease
    ON backtest_jobs (status, lease_expires_at);

-- Deliberately no signals/transactions cursor index is created here. Add one
-- only after production-scale EXPLAIN ANALYZE proves at least 20% improvement.
