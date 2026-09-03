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
            'queued', 'running', 'waiting_agent', 'completed', 'partially_failed', 'failed',
            'cancel_requested', 'cancelled'
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

ALTER TABLE research_experiments
    ADD COLUMN IF NOT EXISTS parent_experiment_id UUID
        REFERENCES research_experiments(id) ON DELETE CASCADE;

ALTER TABLE research_experiments
    ADD COLUMN IF NOT EXISTS study_kind VARCHAR(64) NOT NULL DEFAULT 'adaptive_category';

CREATE INDEX IF NOT EXISTS idx_research_experiments_parent
    ON research_experiments (parent_experiment_id, created_at);

CREATE INDEX IF NOT EXISTS idx_research_experiments_study_kind
    ON research_experiments (study_kind, status, created_at);

CREATE INDEX IF NOT EXISTS idx_research_experiments_workflow_run
    ON research_experiments (workflow_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_experiments_status
    ON research_experiments (status, created_at);

-- Existing installations need the additive status change before adaptive experiments can pause
-- between rounds. PostgreSQL does not support adding a value to an anonymous CHECK in place.
ALTER TABLE research_experiments DROP CONSTRAINT IF EXISTS ck_research_experiments_status;
ALTER TABLE research_experiments DROP CONSTRAINT IF EXISTS research_experiments_status_check;
ALTER TABLE research_experiments ADD CONSTRAINT ck_research_experiments_status CHECK (
    status IN (
        'queued', 'running', 'waiting_agent', 'completed', 'partially_failed', 'failed',
        'cancel_requested', 'cancelled'
    )
);

CREATE TABLE IF NOT EXISTS experiment_rounds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES research_experiments(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    proposal JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_experiment_rounds_ordinal UNIQUE (experiment_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_experiment_rounds_experiment
    ON experiment_rounds (experiment_id, ordinal);

CREATE TABLE IF NOT EXISTS experiment_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES research_experiments(id) ON DELETE CASCADE,
    round_id UUID NOT NULL REFERENCES experiment_rounds(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    parameter_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    params_hash VARCHAR(64) NOT NULL,
    rationale TEXT,
    aggregate_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    pareto_rank INTEGER,
    promoted_strategy_id UUID REFERENCES strategies(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_experiment_candidates_params_hash UNIQUE (experiment_id, params_hash),
    CONSTRAINT uq_experiment_candidates_round_ordinal UNIQUE (round_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_experiment_candidates_experiment
    ON experiment_candidates (experiment_id, params_hash);
CREATE INDEX IF NOT EXISTS idx_experiment_candidates_promoted_strategy
    ON experiment_candidates (promoted_strategy_id);

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
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt INTEGER NOT NULL DEFAULT 0,
    error_code VARCHAR(64),
    error_message TEXT,
    cancel_requested_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_experiment_trials_key UNIQUE (experiment_id, trial_key),
    CONSTRAINT uq_experiment_trials_ordinal UNIQUE (experiment_id, ordinal)
);

ALTER TABLE experiment_trials
    ADD COLUMN IF NOT EXISTS candidate_id UUID REFERENCES experiment_candidates(id) ON DELETE CASCADE;

ALTER TABLE experiment_trials
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_experiment_trials_candidate
    ON experiment_trials (candidate_id, sample_kind, cost_scenario);

CREATE UNIQUE INDEX IF NOT EXISTS uq_experiment_trials_candidate_sample_cost
    ON experiment_trials (experiment_id, candidate_id, sample_kind, cost_scenario)
    WHERE candidate_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_experiment_trials_claim
    ON experiment_trials (status, experiment_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_experiment_trials_backtest
    ON experiment_trials (backtest_run_id);
