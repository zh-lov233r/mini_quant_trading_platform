-- Versioned sparse support/resistance cache and per-run audit events.
-- Apply only after taking a database backup. This repository has no Alembic workflow.

CREATE TABLE IF NOT EXISTS support_resistance_materializations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cache_key VARCHAR(64) NOT NULL,
  algorithm_version VARCHAR(64) NOT NULL,
  detector_params JSONB NOT NULL,
  universe_hash VARCHAR(64) NOT NULL,
  symbols JSONB NOT NULL DEFAULT '[]'::jsonb,
  coverage_start DATE NOT NULL,
  coverage_end DATE NOT NULL,
  price_semantics VARCHAR(96) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'building',
  statistics JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  invalidated_at TIMESTAMPTZ,
  CONSTRAINT ck_support_resistance_materializations_status
    CHECK (status IN ('building', 'completed', 'failed')),
  CONSTRAINT ck_support_resistance_materializations_window
    CHECK (coverage_end >= coverage_start)
);

CREATE INDEX IF NOT EXISTS idx_support_resistance_materializations_lookup
  ON support_resistance_materializations
  (algorithm_version, universe_hash, coverage_start, coverage_end);

CREATE UNIQUE INDEX IF NOT EXISTS uq_support_resistance_materializations_current_cache_key
  ON support_resistance_materializations (cache_key)
  WHERE invalidated_at IS NULL;

CREATE TABLE IF NOT EXISTS support_resistance_zone_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  materialization_id UUID NOT NULL REFERENCES support_resistance_materializations(id) ON DELETE CASCADE,
  instrument_id BIGINT REFERENCES instruments(id) ON DELETE SET NULL,
  symbol TEXT NOT NULL,
  zone_key VARCHAR(64) NOT NULL,
  version INTEGER NOT NULL,
  effective_from DATE NOT NULL,
  effective_to DATE,
  role VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL,
  center_price NUMERIC(24, 10) NOT NULL,
  lower_price NUMERIC(24, 10) NOT NULL,
  upper_price NUMERIC(24, 10) NOT NULL,
  atr_width NUMERIC(24, 10) NOT NULL,
  anchor_session_index INTEGER NOT NULL,
  slope_per_session NUMERIC(24, 10) NOT NULL,
  fit_residual_atr NUMERIC(20, 10) NOT NULL,
  projection_end DATE NOT NULL,
  end_center_price NUMERIC(24, 10) NOT NULL,
  end_lower_price NUMERIC(24, 10) NOT NULL,
  end_upper_price NUMERIC(24, 10) NOT NULL,
  pivot_count INTEGER NOT NULL,
  touch_count INTEGER NOT NULL,
  source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_support_resistance_zone_versions_identity
    UNIQUE (materialization_id, instrument_id, zone_key, version),
  CONSTRAINT uq_support_resistance_zone_versions_effective_from
    UNIQUE (materialization_id, instrument_id, zone_key, effective_from),
  CONSTRAINT ck_support_resistance_zone_role
    CHECK (role IN ('support', 'resistance')),
  CONSTRAINT ck_support_resistance_zone_status
    CHECK (status IN ('active', 'expired', 'broken', 'transformed')),
  CONSTRAINT ck_support_resistance_zone_window
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
  CONSTRAINT ck_support_resistance_zone_projection_window
    CHECK (projection_end >= effective_from),
  CONSTRAINT ck_support_resistance_zone_end_prices
    CHECK (end_lower_price <= end_center_price AND end_center_price <= end_upper_price)
);

CREATE INDEX IF NOT EXISTS idx_support_resistance_zone_versions_timeline
  ON support_resistance_zone_versions
  (materialization_id, symbol, zone_key, effective_from);

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
    UNIQUE (materialization_id, instrument_id, version),
  CONSTRAINT uq_support_resistance_regime_versions_effective_from
    UNIQUE (materialization_id, instrument_id, effective_from),
  CONSTRAINT ck_support_resistance_regime
    CHECK (regime IN ('uptrend', 'downtrend', 'range', 'transition')),
  CONSTRAINT ck_support_resistance_regime_version
    CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS idx_support_resistance_regime_versions_timeline
  ON support_resistance_regime_versions
  (materialization_id, symbol, effective_from);

CREATE TABLE IF NOT EXISTS support_resistance_run_materializations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
  materialization_id UUID NOT NULL REFERENCES support_resistance_materializations(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_support_resistance_run_materializations_run
    UNIQUE (run_id)
);

CREATE TABLE IF NOT EXISTS support_resistance_run_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
  materialization_id UUID NOT NULL REFERENCES support_resistance_materializations(id) ON DELETE RESTRICT,
  instrument_id BIGINT REFERENCES instruments(id) ON DELETE SET NULL,
  symbol TEXT NOT NULL,
  event_date DATE NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  zone_key VARCHAR(64),
  setup VARCHAR(32),
  selected BOOLEAN NOT NULL DEFAULT FALSE,
  score NUMERIC(20, 10),
  posterior_sample_count INTEGER,
  lower_price NUMERIC(24, 10),
  upper_price NUMERIC(24, 10),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_support_resistance_run_events_filter
  ON support_resistance_run_events (run_id, symbol, zone_key, event_date);
