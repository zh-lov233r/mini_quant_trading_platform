-- Apply explicitly after database preflight and backup. No automatic startup DDL.
CREATE TABLE IF NOT EXISTS instrument_market_caps (
    instrument_id BIGINT PRIMARY KEY REFERENCES instruments(id),
    amount NUMERIC(24,4) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    source TEXT NOT NULL,
    data_date DATE,
    retrieved_at TIMESTAMPTZ NOT NULL,
    vendor_payload JSONB NOT NULL,
    CONSTRAINT ck_market_cap_positive CHECK (amount > 0),
    CONSTRAINT ck_market_cap_currency CHECK (currency IN ('USD', 'CNY'))
);
