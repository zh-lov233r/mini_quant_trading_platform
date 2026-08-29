-- Additive market-reference enrichment schema.
-- Safe for both fresh schemas and existing databases; this project does not
-- currently use Alembic, so operators apply this file explicitly during rollout.

ALTER TABLE instruments
  ADD COLUMN IF NOT EXISTS sic_code TEXT,
  ADD COLUMN IF NOT EXISTS sic_description TEXT,
  ADD COLUMN IF NOT EXISTS sic_source TEXT,
  ADD COLUMN IF NOT EXISTS sic_asof TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_instruments_sic_code
  ON instruments (sic_code)
  WHERE sic_code IS NOT NULL;


CREATE TABLE IF NOT EXISTS stock_short_interest (
  instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  settlement_date DATE NOT NULL,
  short_interest BIGINT,
  avg_daily_volume BIGINT,
  days_to_cover DOUBLE PRECISION,
  vendor_source TEXT NOT NULL DEFAULT 'massive',
  vendor_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  asof TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (instrument_id, settlement_date, vendor_source),
  CHECK (short_interest IS NULL OR short_interest >= 0),
  CHECK (avg_daily_volume IS NULL OR avg_daily_volume >= 0),
  CHECK (days_to_cover IS NULL OR days_to_cover >= 0)
);
CREATE INDEX IF NOT EXISTS idx_short_interest_settlement_date
  ON stock_short_interest (settlement_date DESC);


CREATE TABLE IF NOT EXISTS security_ticker_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  event_date DATE NOT NULL,
  event_type TEXT NOT NULL,
  ticker TEXT NOT NULL,
  exchange TEXT,
  composite_figi TEXT,
  share_class_figi TEXT,
  resolution_status TEXT NOT NULL DEFAULT 'pending',
  resolution_reason TEXT,
  before_intervals JSONB,
  after_intervals JSONB,
  applied_at TIMESTAMPTZ,
  vendor_source TEXT NOT NULL DEFAULT 'massive',
  vendor_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  asof TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (instrument_id, event_date, event_type, ticker, vendor_source),
  CHECK (ticker = UPPER(ticker)),
  CHECK (event_type IN ('ticker_change')),
  CHECK (resolution_status IN ('pending', 'applied', 'unresolved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_ticker_events_instrument_date
  ON security_ticker_events (instrument_id, event_date);

CREATE INDEX IF NOT EXISTS idx_ticker_events_resolution
  ON security_ticker_events (resolution_status, event_date);


CREATE TABLE IF NOT EXISTS instrument_vendor_sync_state (
  instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  dataset TEXT NOT NULL,
  vendor_source TEXT NOT NULL DEFAULT 'massive',
  last_attempt_at TIMESTAMPTZ,
  last_success_at TIMESTAMPTZ,
  last_error TEXT,

  PRIMARY KEY (instrument_id, dataset, vendor_source)
);
