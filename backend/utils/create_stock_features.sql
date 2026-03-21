CREATE TABLE IF NOT EXISTS daily_features (
  instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
  dt_ny DATE NOT NULL,

  -- Returns computed on adjusted close (close_fa when available, else close_u)
  ret_1d DOUBLE PRECISION,
  ret_5d DOUBLE PRECISION,
  ret_20d DOUBLE PRECISION,
  ret_60d DOUBLE PRECISION,
  ret_120d DOUBLE PRECISION,
  ret_252d DOUBLE PRECISION,

  -- Trend-following moving averages / exponential averages
  sma_10 DOUBLE PRECISION,
  sma_20 DOUBLE PRECISION,
  sma_50 DOUBLE PRECISION,
  sma_100 DOUBLE PRECISION,
  sma_200 DOUBLE PRECISION,
  ema_12 DOUBLE PRECISION,
  ema_15 DOUBLE PRECISION,
  ema_20 DOUBLE PRECISION,
  ema_50 DOUBLE PRECISION,

  -- Volatility / risk
  atr_14 DOUBLE PRECISION,
  volatility_20d DOUBLE PRECISION,
  volatility_60d DOUBLE PRECISION,

  -- Mean-reversion indicators
  rsi_2 DOUBLE PRECISION,
  rsi_5 DOUBLE PRECISION,
  rsi_14 DOUBLE PRECISION,
  zscore_5 DOUBLE PRECISION,
  zscore_10 DOUBLE PRECISION,
  zscore_20 DOUBLE PRECISION,
  bb_mid_20 DOUBLE PRECISION,
  bb_upper_20 DOUBLE PRECISION,
  bb_lower_20 DOUBLE PRECISION,
  bb_width_20 DOUBLE PRECISION,

  -- Liquidity / participation
  adv_20 DOUBLE PRECISION,
  adv_60 DOUBLE PRECISION,
  dollar_volume_20 DOUBLE PRECISION,

  -- Breakout / range context
  rolling_high_20 DOUBLE PRECISION,
  rolling_high_55 DOUBLE PRECISION,
  rolling_low_20 DOUBLE PRECISION,

  asof TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (instrument_id, dt_ny)
);

ALTER TABLE daily_features
  ADD COLUMN IF NOT EXISTS ema_15 DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_daily_features_dt
  ON daily_features (dt_ny);
