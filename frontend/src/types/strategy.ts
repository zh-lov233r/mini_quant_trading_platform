export type StrategyType =
  | "trend"
  | "mean_reversion"
  | "momentum_breakout"
  | "island_reversal"
  | "double_bottom"
  | "head_shoulders_bottom"
  | "rounded_bottom"
  | "v_reversal"
  | "support_resistance"
  | "custom";
export type StrategyStatus = "draft" | "active" | "archived";

export interface IndicatorSpec {
  kind: "ema" | "sma";
  window: number;
}

export interface MeanReversionStrategyParams {
  signal: {
    min_strength_score: number;
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
    max_holding_days: number;
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
    min_strength_score: number;
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

export interface MomentumBreakoutStrategyParams {
  signal: {
    min_strength_score: number;
    minimum_return_20d: number;
    breakout_buffer_pct: number;
    volume_multiplier: number;
    exit_return_20d: number;
    price_field: "close";
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
    timeframe: "1d";
    rebalance: string;
    run_at: "close";
    backtest?: {
      commission_bps?: number;
      commission_min?: number;
      slippage_bps?: number;
    };
  };
  metadata: {
    description: string;
    schema_version: number;
  };
}

export interface IslandReversalStrategyParams {
  signal: {
    previous_body_atr_min: number;
    breakout_body_atr_min: number;
    exhaustion_body_atr_max: number;
    island_body_atr_max: number;
    min_strength_score: number;
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
    stage_1_target_pct: number;
    stage_2_target_pct: number;
    stage_3_target_pct: number;
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

export interface DoubleBottomStrategyParams {
  signal: {
    rebound_volume_ratio_min: number;
    rebound_volume_ratio_max: number;
    min_strength_score: number;
    downtrend_lookback: number;
    downtrend_min_drop_pct: number;
    downtrend_max_up_day_ratio: number;
    downtrend_min_r_squared: number;
    min_bottom_spacing: number;
    max_bottom_spacing: number;
    left_bottom_before_bars: number;
    left_bottom_after_bars: number;
    bottom_tolerance_pct: number;
    neckline_min_rebound_pct: number;
    rebound_up_day_ratio_min: number;
    second_bottom_volume_ratio_max: number;
    breakout_volume_ratio_min: number;
    max_breakout_bars_after_right_bottom: number;
    breakout_buffer_pct: number;
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
    stage_1_target_pct: number;
    stage_2_target_pct: number;
    stage_3_target_pct: number;
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

interface StagedBottomStrategyParamsBase {
  universe: { symbols: string[]; selection_mode: string };
  risk: {
    max_positions: number;
    position_size_pct: number;
    stage_1_target_pct: number;
    stage_2_target_pct: number;
    stage_3_target_pct: number;
    stop_loss_atr: number;
    max_loss_pct: number;
    take_profit_atr: number;
  };
  execution: { timeframe: "1d"; rebalance: "daily"; run_at: "close" };
  metadata: { description: string; schema_version: number; algorithm_version?: string };
}

export interface HeadShouldersBottomStrategyParams extends StagedBottomStrategyParamsBase {
  signal: {
    platform_bars: number;
    platform_range_atr_max: number;
    platform_drift_atr_max: number;
    rebound_volume_ratio_min: number;
    rebound_volume_ratio_max: number;
    min_strength_score: number;
    downtrend_lookback: number;
    downtrend_min_drop_pct: number;
    pivot_left_bars: number;
    pivot_right_bars: number;
    min_segment_bars: number;
    max_segment_bars: number;
    shoulder_tolerance_pct: number;
    head_depth_min_pct: number;
    head_volume_ratio_max: number;
    right_shoulder_volume_ratio_max: number;
    breakout_volume_ratio_min: number;
    breakout_buffer_pct: number;
  };
  metadata: StagedBottomStrategyParamsBase["metadata"] & { algorithm_version: "confirmed-pivots-v2" };
}

export interface RoundedBottomStrategyParams extends StagedBottomStrategyParamsBase {
  signal: {
    weakening_buffer_pct: number;
    min_strength_score: number;
    min_lookback: number;
    max_lookback: number;
    min_depth_pct: number;
    min_r_squared: number;
    vertex_position_min: number;
    vertex_position_max: number;
    pivot_left_bars: number;
    pivot_right_bars: number;
    min_pullback_spacing: number;
    right_volume_ratio_min: number;
    pullback_volume_ratio_max: number;
    breakout_volume_ratio_min: number;
    breakout_buffer_pct: number;
  };
  metadata: StagedBottomStrategyParamsBase["metadata"] & { algorithm_version: "log-quadratic-v2" };
}

export interface VReversalStrategyParams extends StagedBottomStrategyParamsBase {
  signal: {
    consolidation_range_atr_max: number;
    consolidation_drift_atr_max: number;
    breakout_buffer_pct: number;
    bearish_body_atr_min: number;
    min_strength_score: number;
    downtrend_lookback: number;
    downtrend_min_drop_pct: number;
    pivot_max_bars: number;
    reversal_min_return_pct: number;
    reversal_min_atr: number;
    pivot_volume_ratio_min: number;
    continuation_window: number;
    continuation_volume_ratio_min: number;
    consolidation_min_bars: number;
    consolidation_max_bars: number;
    breakout_volume_ratio_min: number;
    retest_window: number;
    retest_volume_ratio_max: number;
    support_tolerance_pct: number;
    bearish_reversal_volume_ratio_min: number;
  };
  metadata: StagedBottomStrategyParamsBase["metadata"] & { algorithm_version: "volume-v-reversal-v2" };
}

export interface SupportResistanceStrategyParams {
  signal: {
    min_strength_score: number;
    support_bounce_enabled: boolean;
    pivot_left_bars: number;
    pivot_right_bars: number;
    detection_window: number;
    min_line_pivots: number;
    min_line_span_sessions: number;
    max_zones_per_kind: number;
    pivot_tolerance_atr: number;
    line_inlier_tolerance_atr: number;
    max_abs_slope_atr_per_session: number;
    zone_half_width_atr: number;
    decay_half_life: number;
    bounce_confirmation_atr: number;
  };
  universe: { symbols: string[]; selection_mode: string };
  risk: {
    max_positions: number;
    position_size_pct: number;
    stop_loss_atr: number;
    max_loss_pct: number;
    take_profit_atr: number;
    min_reward_risk: number;
    max_holding_days: number;
    risk_per_trade_pct: number;
    stop_cooldown_sessions: number;
    break_even_at_r: number;
    market_filter_enabled: boolean;
    market_filter_symbol: string;
  };
  execution: { timeframe: "1d"; rebalance: "daily"; run_at: "close" };
  metadata: {
    description: string;
    schema_version: number;
    algorithm_version: "pivot-slope-regime-v3" | "pivot-slope-atr-v2";
    price_semantics: "forward_adjusted_preferred_unadjusted_fallback";
  };
}

export type CustomStrategyParams = Record<string, unknown>;

export interface StrategyParamsByType {
  trend: TrendStrategyParams;
  mean_reversion: MeanReversionStrategyParams;
  momentum_breakout: MomentumBreakoutStrategyParams;
  island_reversal: IslandReversalStrategyParams;
  double_bottom: DoubleBottomStrategyParams;
  head_shoulders_bottom: HeadShouldersBottomStrategyParams;
  rounded_bottom: RoundedBottomStrategyParams;
  v_reversal: VReversalStrategyParams;
  support_resistance: SupportResistanceStrategyParams;
  custom: CustomStrategyParams;
}

export type StrategyParams = StrategyParamsByType[StrategyType];

export interface StrategyCreate {
  name: string;
  description?: string | null;
  strategy_type: StrategyType;
  status?: StrategyStatus;
  params: Record<string, unknown>;
}

export interface StrategyCloneCreate {
  name: string;
  description?: string | null;
  params: Record<string, unknown>;
}

export interface StrategyValidation {
  valid: boolean;
  engine_ready: boolean;
  strategy_type: StrategyType;
  normalized_params: StrategyParams;
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
  strategy_type: StrategyType;
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
  strategy_type: StrategyType;
  engine_ready: boolean;
  params: StrategyParams;
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
  deleted_support_resistance_run_events: number;
  deleted_support_resistance_run_links: number;
  retained_support_resistance_materializations: number;
}

export interface StrategyCatalogItem {
  strategy_type: StrategyType;
  label: string;
  description: string;
  engine_ready: boolean;
  defaults: Record<string, unknown>;
  parameter_schema: Record<string, unknown>;
  required_features: string[];
  algorithm_revision: number | null;
  history_length: number;
}

export interface TrendIndicatorSupport {
  ema_windows: number[];
  sma_windows: number[];
}

export interface StrategyFeatureSupport {
  trend: TrendIndicatorSupport;
}
