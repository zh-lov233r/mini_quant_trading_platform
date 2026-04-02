CREATE TABLE IF NOT EXISTS paper_trading_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(128) NOT NULL,
    broker varchar(32) NOT NULL DEFAULT 'alpaca',
    mode varchar(16) NOT NULL DEFAULT 'paper',
    api_key_env varchar(128) NOT NULL DEFAULT 'ALPACA_API_KEY',
    secret_key_env varchar(128) NOT NULL DEFAULT 'ALPACA_SECRET_KEY',
    base_url varchar(255) NOT NULL DEFAULT 'https://paper-api.alpaca.markets',
    timeout_seconds numeric(10, 4) NOT NULL DEFAULT 20,
    notes text,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_paper_trading_accounts_name UNIQUE (name),
    CONSTRAINT ck_paper_trading_accounts_broker CHECK (broker IN ('alpaca')),
    CONSTRAINT ck_paper_trading_accounts_mode CHECK (mode IN ('paper', 'live')),
    CONSTRAINT ck_paper_trading_accounts_status CHECK (status IN ('active', 'archived'))
);

CREATE TABLE IF NOT EXISTS strategy_portfolios (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_account_id uuid NOT NULL REFERENCES paper_trading_accounts(id) ON DELETE CASCADE,
    name varchar(64) NOT NULL,
    description text,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_strategy_portfolios_name UNIQUE (name),
    CONSTRAINT ck_strategy_portfolios_status CHECK (status IN ('active', 'archived'))
);

INSERT INTO paper_trading_accounts (
    name,
    broker,
    mode,
    api_key_env,
    secret_key_env,
    base_url,
    timeout_seconds,
    notes,
    status
)
VALUES (
    'default-paper-account',
    'alpaca',
    'paper',
    'ALPACA_API_KEY',
    'ALPACA_SECRET_KEY',
    'https://paper-api.alpaca.markets',
    20,
    'Auto-created default paper trading account',
    'active'
)
ON CONFLICT (name) DO NOTHING;

INSERT INTO strategy_portfolios (
    paper_account_id,
    name,
    description,
    status
)
SELECT
    account.id,
    'default',
    'Default virtual sleeve for paper trading',
    'active'
FROM paper_trading_accounts account
WHERE account.name = 'default-paper-account'
ON CONFLICT (name) DO NOTHING;
