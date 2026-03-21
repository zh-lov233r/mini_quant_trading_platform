-- Security master + point-in-time symbol mapping.
-- This file is intended for fresh schema creation. Existing databases that
-- already have these tables should be migrated instead of relying on
-- CREATE TABLE IF NOT EXISTS to reshape them.

CREATE EXTENSION IF NOT EXISTS btree_gist;


-- Stable security identity. Downstream fact tables should reference
-- instruments.id instead of raw ticker symbols.
CREATE TABLE IF NOT EXISTS instruments (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

  -- Massive reference IDs
  share_class_figi TEXT NOT NULL UNIQUE,
  composite_figi   TEXT,
  cik              TEXT,

  -- Current snapshot attributes
  ticker_canonical TEXT,
  exchange         TEXT NOT NULL,
  mic              TEXT,
  asset_type       TEXT NOT NULL DEFAULT 'CS',
  share_class      TEXT,
  name             TEXT,
  currency         TEXT NOT NULL DEFAULT 'USD',
  country          TEXT,
  locale           TEXT DEFAULT 'us',
  market           TEXT NOT NULL DEFAULT 'stocks',

  listed_at        DATE,
  delisted_at      DATE,
  is_active        BOOLEAN NOT NULL DEFAULT TRUE,

  vendor_source    TEXT NOT NULL DEFAULT 'massive',
  vendor_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  CHECK (ticker_canonical IS NULL OR ticker_canonical = UPPER(ticker_canonical)),
  CHECK (currency = UPPER(currency)),
  CHECK (delisted_at IS NULL OR listed_at IS NULL OR delisted_at >= listed_at)
);


-- Map exchange+symbol to a stable instrument across time.
CREATE TABLE IF NOT EXISTS symbol_history (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,

  exchange      TEXT NOT NULL,
  mic           TEXT,
  symbol        TEXT NOT NULL,
  currency      TEXT NOT NULL DEFAULT 'USD',

  valid_from    DATE NOT NULL,
  valid_to      DATE,

  -- One instrument can have aliases, but only one primary symbol interval at
  -- a time should be open for trading workflows.
  is_primary    BOOLEAN NOT NULL DEFAULT TRUE,

  -- When Massive does not provide an exact start date, keep the uncertainty
  -- explicit instead of backfilling fake dates like 1900-01-01.
  valid_from_precision TEXT NOT NULL DEFAULT 'exact',

  source        TEXT NOT NULL DEFAULT 'massive',
  vendor_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  asof          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  CHECK (symbol = UPPER(symbol)),
  CHECK (currency = UPPER(currency)),
  CHECK (valid_to IS NULL OR valid_to >= valid_from),
  CHECK (valid_from_precision IN ('exact', 'inferred', 'unknown'))
);


-- Shared updated_at trigger function.
CREATE OR REPLACE FUNCTION _touch_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_instruments_touch ON instruments;
CREATE TRIGGER trg_instruments_touch
BEFORE UPDATE ON instruments
FOR EACH ROW EXECUTE FUNCTION _touch_updated_at();

DROP TRIGGER IF EXISTS trg_symbol_history_touch ON symbol_history;
CREATE TRIGGER trg_symbol_history_touch
BEFORE UPDATE ON symbol_history
FOR EACH ROW EXECUTE FUNCTION _touch_updated_at();


-- Instrument lookup indexes.
CREATE INDEX IF NOT EXISTS idx_instr_exchange        ON instruments(exchange);
CREATE INDEX IF NOT EXISTS idx_instr_ticker          ON instruments(ticker_canonical);
CREATE INDEX IF NOT EXISTS idx_instr_active          ON instruments(is_active);
CREATE INDEX IF NOT EXISTS idx_instr_figi_composite  ON instruments(composite_figi);
CREATE INDEX IF NOT EXISTS idx_instr_cik             ON instruments(cik);


-- Snapshot symbol classification from Massive reference data. This lets the
-- day-agg importer separate obvious non-common rows from true common-stock
-- mapping gaps without polluting the security master.
CREATE TABLE IF NOT EXISTS symbol_reference (
  symbol         TEXT NOT NULL,
  exchange       TEXT NOT NULL,
  asset_type     TEXT NOT NULL,
  market         TEXT NOT NULL DEFAULT 'stocks',
  locale         TEXT NOT NULL DEFAULT 'us',
  is_common_stock BOOLEAN NOT NULL DEFAULT FALSE,
  name           TEXT,
  source         TEXT NOT NULL DEFAULT 'massive',
  asof           TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (symbol, exchange, asset_type)
);

CREATE INDEX IF NOT EXISTS idx_symbol_reference_symbol
  ON symbol_reference (symbol);

CREATE INDEX IF NOT EXISTS idx_symbol_reference_common
  ON symbol_reference (is_common_stock, symbol);


-- Corporate actions drive price adjustment and total-return calculations.
-- Keep them on the stable instrument identity, not on raw tickers.
CREATE TABLE IF NOT EXISTS corporate_actions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

  instrument_id   BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,

  action_type     TEXT NOT NULL,
  ex_date         DATE NOT NULL,
  announcement_date DATE,
  record_date     DATE,
  payable_date    DATE,

  -- Split-style events: 2-for-1 => split_from=1, split_to=2
  split_from      NUMERIC(20, 8),
  split_to        NUMERIC(20, 8),

  -- Cash dividend per share in the declared currency
  cash_amount     NUMERIC(20, 8),
  currency        TEXT NOT NULL DEFAULT 'USD',

  vendor_event_id TEXT,
  vendor_source   TEXT NOT NULL DEFAULT 'massive',
  vendor_payload  JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CHECK (
    action_type IN (
      'split',
      'reverse_split',
      'cash_dividend',
      'stock_dividend',
      'spin_off',
      'merger',
      'delisting'
    )
  ),
  CHECK (currency = UPPER(currency)),
  CHECK (split_from IS NULL OR split_from > 0),
  CHECK (split_to IS NULL OR split_to > 0),
  CHECK (cash_amount IS NULL OR cash_amount >= 0)
);

DROP TRIGGER IF EXISTS trg_corporate_actions_touch ON corporate_actions;
CREATE TRIGGER trg_corporate_actions_touch
BEFORE UPDATE ON corporate_actions
FOR EACH ROW EXECUTE FUNCTION _touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_ca_instrument_ex_date
  ON corporate_actions (instrument_id, ex_date);

CREATE INDEX IF NOT EXISTS idx_ca_ex_date
  ON corporate_actions (ex_date);

CREATE INDEX IF NOT EXISTS idx_ca_action_type
  ON corporate_actions (action_type);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_vendor_event
  ON corporate_actions (vendor_source, vendor_event_id)
  WHERE vendor_event_id IS NOT NULL;


-- Symbol history lookup indexes.
CREATE INDEX IF NOT EXISTS idx_sh_ex_sym_from
  ON symbol_history (exchange, symbol, valid_from DESC);

CREATE INDEX IF NOT EXISTS idx_sh_iid_from
  ON symbol_history (instrument_id, valid_from DESC);

CREATE INDEX IF NOT EXISTS idx_sh_open_symbol
  ON symbol_history (exchange, symbol)
  WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_sh_open_primary_by_instrument
  ON symbol_history (instrument_id)
  WHERE valid_to IS NULL AND is_primary;


-- A given exchange+symbol can only point to one instrument at a time.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'sh_symbol_exchange_no_overlap'
  ) THEN
    ALTER TABLE symbol_history
      ADD CONSTRAINT sh_symbol_exchange_no_overlap
      EXCLUDE USING gist (
        exchange WITH =,
        symbol WITH =,
        daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[]') WITH &&
      );
  END IF;
END $$;


-- A single instrument can only have one primary symbol interval at a time.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'sh_primary_symbol_no_overlap'
  ) THEN
    ALTER TABLE symbol_history
      ADD CONSTRAINT sh_primary_symbol_no_overlap
      EXCLUDE USING gist (
        instrument_id WITH =,
        daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[]') WITH &&
      )
      WHERE (is_primary);
  END IF;
END $$;
