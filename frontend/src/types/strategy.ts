export type StrategyType = "trend";

export interface TrendParams {
  ema_short: string;           // 必须形如 "EMA15"
  sma_long: string;            // 必须形如 "SMA200"
  volume_multiplier: number;   // >0
  atr_multiplier: number;      // >0
}

export interface StrategyCreate {
  name: string;
  strategy_type: StrategyType; // 固定 "trend"
  status?: "draft" | "active";
  params: TrendParams;
}

export interface StrategyOut {
  id: string;
  name: string;
  strategy_type: StrategyType | string;
  status: string;
  version: number;
  params: TrendParams;
}
