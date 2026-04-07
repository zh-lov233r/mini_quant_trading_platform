export type StrategyType = "trend" | "mean_reversion" | "island_reversal" | "custom";
export type StrategyStatus = "draft" | "active" | "archived";

export interface IndicatorSpec {
  kind: "ema" | "sma";
  window: number;
}

export interface MeanReversionStrategyParams {
  signal: {
    lookback_window: number;
    zscore_entry: number;
    zscore_exit: number;
    price_field: string;
  };
  universe: {
    symbols: string[];
    selection_mode: string;
  };
  risk: {
    max_positions: number;
    position_size_pct: number;
    stop_loss_pct: number;
    take_profit_pct: number;
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
    stop_loss_pct: number;
    stop_loss_atr: number;
    take_profit_atr: number;
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

export interface IslandReversalStrategyParams {
  signal: {
    downtrend_lookback: number;
    downtrend_min_drop_pct: number;
    left_gap_min_pct: number;
    right_gap_min_pct: number;
    min_island_bars: number;
    max_island_bars: number;
    left_volume_ratio_max: number;
    right_volume_ratio_min: number;
    retest_window: number;
    retest_volume_ratio_max: number;
    support_tolerance_pct: number;
  };
  universe: {
    symbols: string[];
    selection_mode: string;
  };
  risk: {
    max_positions: number;
    position_size_pct: number;
    stop_loss_atr: number;
    max_loss_pct: number;
    take_profit_atr: number;
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

export type StrategyParams =
  | TrendStrategyParams
  | MeanReversionStrategyParams
  | IslandReversalStrategyParams
  | Record<string, unknown>;

export interface StrategyCreate {
  name: string;
  description?: string | null;
  strategy_type: StrategyType;
  status?: StrategyStatus;
  params: Record<string, unknown>;
}

export interface StrategyRename {
  name: string;
}

export interface StrategyConfigUpdate {
  description?: string | null;
  status?: StrategyStatus;
  params: Record<string, unknown>;
}

export interface StrategyOut {
  id: string;
  strategy_key: string;
  display_name?: string | null;
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
  strategy_key?: string;
  display_name?: string | null;
  name: string;
  version: number;
  status: string;
  strategy_type: StrategyType | string;
  engine_ready: boolean;
  params: Record<string, unknown>;
}

export interface StrategyDeleteOut {
  strategy_id: string;
  strategy_name: string;
  deleted_backtest_runs: number;
  deleted_paper_runs: number;
  deleted_live_runs: number;
  deleted_backtest_snapshots: number;
  deleted_signals: number;
  deleted_transactions: number;
  deleted_allocations: number;
}

export interface StrategyCatalogItem {
  strategy_type: StrategyType;
  label: string;
  description: string;
  engine_ready: boolean;
  defaults: Record<string, unknown>;
}

export interface TrendIndicatorSupport {
  ema_windows: number[];
  sma_windows: number[];
}

export interface StrategyFeatureSupport {
  trend: TrendIndicatorSupport;
}
