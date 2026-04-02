ALTER TABLE strategies
    ADD COLUMN IF NOT EXISTS strategy_key VARCHAR(128);

UPDATE strategies
SET strategy_key = name
WHERE strategy_key IS NULL OR BTRIM(strategy_key) = '';

ALTER TABLE strategies
    ALTER COLUMN strategy_key SET NOT NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'uq_strategy_name_version'
  ) THEN
    ALTER TABLE strategies
    DROP CONSTRAINT uq_strategy_name_version;
  END IF;
END $$;

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

CREATE INDEX IF NOT EXISTS idx_strategies_strategy_key
    ON strategies (strategy_key, version DESC);
