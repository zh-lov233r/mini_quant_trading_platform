export type StrategyType = "trend" | "mean_reversion" | "custom";
export type StrategyStatus = "draft" | "active" | "archived";

export interface IndicatorSpec {
  kind: "ema" | "sma";
  window: number;
}

export interface TrendStrategyParams {
  signal: {
    fast_indicator: IndicatorSpec;
    slow_indicator: IndicatorSpec;
    volume_multiplier: number;
    atr_multiplier: number;
    price_field: string;
    trigger: string;
  };
  universe: {
    symbols: string[];
    selection_mode: string;
  };
  risk: {
    max_positions: number;
    position_size_pct: number;
    stop_loss_atr: number;
  };
  execution: {
    timeframe: string;
    rebalance: string;
    run_at: string;
  };
  metadata: {
    description: string;
    schema_version: number;
  };
}

export type StrategyParams = TrendStrategyParams | Record<string, unknown>;

export interface StrategyCreate {
  name: string;
  description?: string | null;
  strategy_type: StrategyType;
  status?: StrategyStatus;
  params: Record<string, unknown>;
}

export interface StrategyOut {
  id: string;
  name: string;
  description?: string | null;
  strategy_type: StrategyType | string;
  status: string;
  version: number;
  params: StrategyParams;
  engine_ready: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface StrategyRuntimeOut {
  strategy_id: string;
  name: string;
  version: number;
  status: string;
  strategy_type: StrategyType | string;
  engine_ready: boolean;
  params: Record<string, unknown>;
}

export interface StrategyCatalogItem {
  strategy_type: StrategyType;
  label: string;
  description: string;
  engine_ready: boolean;
  defaults: Record<string, unknown>;
}
