-- Add append-only market-regime transitions for pivot-slope-regime-v3.
-- Apply inside a controlled transaction after the documented read-only
-- preflight and schema backup. This repository has no Alembic workflow.

CREATE TABLE IF NOT EXISTS support_resistance_regime_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  materialization_id UUID NOT NULL REFERENCES support_resistance_materializations(id) ON DELETE CASCADE,
  instrument_id BIGINT REFERENCES instruments(id) ON DELETE SET NULL,
  symbol TEXT NOT NULL,
  version INTEGER NOT NULL,
  effective_from DATE NOT NULL,
  regime VARCHAR(16) NOT NULL,
  lower_zone_key VARCHAR(64),
  upper_zone_key VARCHAR(64),
  reason_code VARCHAR(64) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_support_resistance_regime_versions_identity
    UNIQUE (materialization_id, symbol, version),
  CONSTRAINT uq_support_resistance_regime_versions_effective_from
    UNIQUE (materialization_id, symbol, effective_from),
  CONSTRAINT ck_support_resistance_regime
    CHECK (regime IN ('uptrend', 'downtrend', 'range', 'transition')),
  CONSTRAINT ck_support_resistance_regime_version
    CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS idx_support_resistance_regime_versions_timeline
  ON support_resistance_regime_versions
  (materialization_id, symbol, effective_from);

-- The first local v3 draft used nullable instrument_id in these unique
-- constraints. Rebuild them idempotently so missing instrument mappings cannot
-- weaken the append-only timeline invariant.
ALTER TABLE support_resistance_regime_versions
  DROP CONSTRAINT IF EXISTS uq_support_resistance_regime_versions_identity;
ALTER TABLE support_resistance_regime_versions
  ADD CONSTRAINT uq_support_resistance_regime_versions_identity
  UNIQUE (materialization_id, symbol, version);
ALTER TABLE support_resistance_regime_versions
  DROP CONSTRAINT IF EXISTS uq_support_resistance_regime_versions_effective_from;
ALTER TABLE support_resistance_regime_versions
  ADD CONSTRAINT uq_support_resistance_regime_versions_effective_from
  UNIQUE (materialization_id, symbol, effective_from);
