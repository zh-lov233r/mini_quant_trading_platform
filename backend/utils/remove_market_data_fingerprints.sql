-- Replace row-level market-data fingerprints with an exclusive maintenance window.
-- This repository has no Alembic workflow. Before applying, identify the exact
-- target database, take a backup, and review the affected row counts.

BEGIN;

CREATE TABLE IF NOT EXISTS market_data_maintenance_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'ready',
    owner_token UUID,
    requested_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_market_data_maintenance_state_singleton CHECK (id = 1),
    CONSTRAINT ck_market_data_maintenance_state_status
        CHECK (status IN ('ready', 'draining', 'updating', 'failed'))
);

INSERT INTO market_data_maintenance_state (id, status)
VALUES (1, 'ready')
ON CONFLICT (id) DO NOTHING;

UPDATE research_experiments
SET status = 'failed',
    error_code = COALESCE(error_code, 'retired_data_changed_status'),
    error_message = COALESCE(error_message, 'The retired data_changed status was converted during schema upgrade.'),
    finished_at = COALESCE(finished_at, NOW()),
    updated_at = NOW()
WHERE status = 'data_changed';

ALTER TABLE research_experiments
    DROP CONSTRAINT IF EXISTS ck_research_experiments_status;
ALTER TABLE research_experiments
    DROP CONSTRAINT IF EXISTS research_experiments_status_check;
ALTER TABLE research_experiments
    ADD CONSTRAINT ck_research_experiments_status CHECK (
        status IN (
            'queued', 'running', 'waiting_agent', 'completed', 'partially_failed', 'failed',
            'cancel_requested', 'cancelled'
        )
    );

ALTER TABLE experiment_trials
    DROP COLUMN IF EXISTS data_fingerprint;

ALTER TABLE support_resistance_materializations
    ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ;

-- Existing materializations remain linked to historical runs but cannot be
-- reused after this cache-identity change.
UPDATE support_resistance_materializations
SET invalidated_at = COALESCE(invalidated_at, NOW());

ALTER TABLE support_resistance_materializations
    DROP CONSTRAINT IF EXISTS uq_support_resistance_materializations_cache_key;
ALTER TABLE support_resistance_materializations
    DROP COLUMN IF EXISTS source_data_fingerprint;

DROP INDEX IF EXISTS idx_support_resistance_materializations_lookup;
CREATE INDEX idx_support_resistance_materializations_lookup
    ON support_resistance_materializations
    (algorithm_version, universe_hash, coverage_start, coverage_end);

CREATE UNIQUE INDEX IF NOT EXISTS uq_support_resistance_materializations_current_cache_key
    ON support_resistance_materializations (cache_key)
    WHERE invalidated_at IS NULL;

COMMIT;
