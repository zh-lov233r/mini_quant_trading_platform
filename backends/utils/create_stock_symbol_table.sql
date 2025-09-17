CREATE TABLE public.stocks_min (
  symbol   TEXT PRIMARY KEY,   -- 主键：每个股票代码唯一
  name     TEXT NOT NULL,      -- 公司名
  ipo_year INTEGER,            -- 上市年份
  sector   TEXT,
  industry TEXT
);

-- （可选）约束：统一大写并限制长度，避免大小写导致的重复
ALTER TABLE public.stocks_min
  ADD CONSTRAINT symbol_format CHECK (symbol = UPPER(symbol) AND length(symbol) <= 10);

-- （可选）常用查询索引
CREATE INDEX idx_stocks_min_sector   ON public.stocks_min(sector);
CREATE INDEX idx_stocks_min_industry ON public.stocks_min(industry);





