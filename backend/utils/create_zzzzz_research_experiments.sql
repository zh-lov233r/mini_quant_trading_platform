ALTER TABLE strategies
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128);

ALTER TABLE strategies
    ALTER COLUMN idempotency_key TYPE VARCHAR(128);

CREATE UNIQUE INDEX IF NOT EXISTS uq_strategies_idempotency_key
    ON strategies (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS research_experiments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_run_id VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued', 'running', 'completed', 'partially_failed', 'failed',
            'cancel_requested', 'cancelled', 'data_changed'
        )),
    spec JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code VARCHAR(64),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_research_experiments_idempotency_key UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_research_experiments_workflow_run
    ON research_experiments (workflow_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_experiments_status
    ON research_experiments (status, created_at);

CREATE TABLE IF NOT EXISTS experiment_trials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES research_experiments(id) ON DELETE CASCADE,
    backtest_run_id UUID REFERENCES strategy_runs(id) ON DELETE SET NULL,
    trial_key VARCHAR(64) NOT NULL,
    ordinal INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    sample_kind VARCHAR(16) NOT NULL
        CHECK (sample_kind IN ('in_sample', 'out_of_sample')),
    cost_scenario VARCHAR(64) NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    params_hash VARCHAR(64) NOT NULL,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    cost_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_fingerprint VARCHAR(64),
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt INTEGER NOT NULL DEFAULT 0,
    error_code VARCHAR(64),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_experiment_trials_key UNIQUE (experiment_id, trial_key),
    CONSTRAINT uq_experiment_trials_ordinal UNIQUE (experiment_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_experiment_trials_claim
    ON experiment_trials (status, experiment_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_experiment_trials_backtest
    ON experiment_trials (backtest_run_id);
