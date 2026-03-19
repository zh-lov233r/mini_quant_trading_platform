-- Create strategy table
-- Not NULL fields：name; strategy_type; params
CREATE TABLE IF NOT EXISTS strategies (
    id UUID NOT NULL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    strategy_type VARCHAR(32) NOT NULL,
    params JSONB NOT NULL,
    cur_position JSONB DEFAULT '{}'::jsonb,   -- current position, key-value pair
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    idempotency_key VARCHAR(64) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Avoid repeating the same-name strategy under the same version number
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'uq_strategy_name_version'
  ) THEN
    ALTER TABLE strategies
    ADD CONSTRAINT uq_strategy_name_version UNIQUE (name, version);
  END IF;
END $$;


-- Create trasactional table, for every transaction executed
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,   -- transaction time
    symbol TEXT NOT NULL,      -- symbol
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty NUMERIC(20,8) NOT NULL CHECK (qty > 0),   -- quantity
    price NUMERIC(20,8) NOT NULL CHECK (price >= 0),   -- transaction price
    fee NUMERIC(20,8) DEFAULT 0,                   -- service charge
    order_id TEXT,                                 -- broker order ID
    meta JSONB NOT NULL DEFAULT '{}'::jsonb        -- other fileds, like slippage
);

-- create index
CREATE INDEX IF NOT EXISTS idx_tx_strategy_ts ON transactions (strategy_id, ts);
CREATE INDEX IF NOT EXISTS idx_tx_symbol_ts   ON transactions (symbol, ts);
