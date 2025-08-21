-- 必填字段：name; strategy_type; params
CREATE TABLE strategies (
    id UUID NOT NULL,
    name VARCHAR(128) NOT NULL,
    strategy_type VARCHAR(32) NOT NULL,
    params JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    idempotency_key VARCHAR(64) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 避免同名策略在同一版本号下重复
ALTER TABLE strategies
ADD CONSTRAINT uq_strategy_name_version UNIQUE (name, version);

