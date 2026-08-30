-- Add sloped-zone geometry after pivot-atr-v1 materializations have been removed.
-- Run inside the controlled deployment transaction after taking a full backup.
-- This repository has no Alembic workflow.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM support_resistance_materializations
    WHERE algorithm_version = 'pivot-atr-v1'
  ) THEN
    RAISE EXCEPTION 'pivot-atr-v1 materializations must be deleted before the v2 schema migration';
  END IF;
END
$$;

ALTER TABLE support_resistance_zone_versions
  ADD COLUMN IF NOT EXISTS anchor_session_index INTEGER,
  ADD COLUMN IF NOT EXISTS slope_per_session NUMERIC(24, 10),
  ADD COLUMN IF NOT EXISTS fit_residual_atr NUMERIC(20, 10),
  ADD COLUMN IF NOT EXISTS projection_end DATE,
  ADD COLUMN IF NOT EXISTS end_center_price NUMERIC(24, 10),
  ADD COLUMN IF NOT EXISTS end_lower_price NUMERIC(24, 10),
  ADD COLUMN IF NOT EXISTS end_upper_price NUMERIC(24, 10);

ALTER TABLE support_resistance_zone_versions
  ALTER COLUMN anchor_session_index SET NOT NULL,
  ALTER COLUMN slope_per_session SET NOT NULL,
  ALTER COLUMN fit_residual_atr SET NOT NULL,
  ALTER COLUMN projection_end SET NOT NULL,
  ALTER COLUMN end_center_price SET NOT NULL,
  ALTER COLUMN end_lower_price SET NOT NULL,
  ALTER COLUMN end_upper_price SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_support_resistance_zone_projection_window'
  ) THEN
    ALTER TABLE support_resistance_zone_versions
      ADD CONSTRAINT ck_support_resistance_zone_projection_window
      CHECK (projection_end >= effective_from);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_support_resistance_zone_end_prices'
  ) THEN
    ALTER TABLE support_resistance_zone_versions
      ADD CONSTRAINT ck_support_resistance_zone_end_prices
      CHECK (end_lower_price <= end_center_price AND end_center_price <= end_upper_price);
  END IF;
END
$$;
