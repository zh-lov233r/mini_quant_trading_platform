CREATE TABLE IF NOT EXISTS eod_bars (
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),

  -- 原始时间与“美东交易日”
  ts_utc  TIMESTAMPTZ NOT NULL,                                  -- 由 flat file 的 window_start(ns) 转换
  dt_ny   DATE GENERATED ALWAYS AS
          ((ts_utc AT TIME ZONE 'America/New_York')::date) STORED,

  -- 未复权（unadjusted, 来自 flat files 或 REST adjusted=false）
  open_u  DOUBLE PRECISION,
  high_u  DOUBLE PRECISION,
  low_u   DOUBLE PRECISION,
  close_u DOUBLE PRECISION,
  volume  BIGINT,
  vwap    DOUBLE PRECISION,
  trades  INT,

  -- 换手率（volume / 流通股 或 总股本），导入后回填
  turnover DOUBLE PRECISION,

  -- 前复权（以“最近股本口径”为锚；过去价格会变小）
  fwd_factor DOUBLE PRECISION,   -- = cum(dt)/cum_last
  open_fa  DOUBLE PRECISION,
  high_fa  DOUBLE PRECISION,
  low_fa   DOUBLE PRECISION,
  close_fa DOUBLE PRECISION,

  -- 后复权（以“最早股本口径”为锚；未来价格会变大）
  bwd_factor DOUBLE PRECISION,   -- = cum(dt)
  open_ba  DOUBLE PRECISION,
  high_ba  DOUBLE PRECISION,
  low_ba   DOUBLE PRECISION,
  close_ba DOUBLE PRECISION,

  vendor  TEXT DEFAULT 'massive',
  asof    TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (instrument_id, dt_ny)
);

-- 常用索引
CREATE INDEX IF NOT EXISTS idx_eod_ts ON eod_bars (ts_utc);
CREATE INDEX IF NOT EXISTS idx_eod_dt ON eod_bars (dt_ny);


