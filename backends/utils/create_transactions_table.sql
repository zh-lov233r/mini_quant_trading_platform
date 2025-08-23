CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,   -- 成交时间
    symbol TEXT NOT NULL,      -- 股票代码
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty NUMERIC(20,8) NOT NULL CHECK (qty > 0),   -- 成交数量
    price NUMERIC(20,8) NOT NULL CHECK (price >= 0),   -- 成交价格
    fee NUMERIC(20,8) DEFAULT 0,                   -- 手续费
    order_id TEXT,                                 -- 可选：券商订单号
    meta JSONB NOT NULL DEFAULT '{}'::jsonb        -- 其它字段（滑点、场景等）
);

-- 常用索引
CREATE INDEX IF NOT EXISTS idx_tx_strategy_ts ON transactions (strategy_id, ts);
CREATE INDEX IF NOT EXISTS idx_tx_symbol_ts   ON transactions (symbol, ts);