import type { StrategyType } from "@/types/strategy";

export type GuidedFieldKind = "number" | "percent" | "boolean" | "select" | "text";

export interface GuidedFieldDefinition {
  key: string;
  path: string;
  kind: GuidedFieldKind;
  advanced?: boolean;
  integer?: boolean;
  min?: number;
  max?: number;
  step?: number;
  options?: Array<{ label: string; value: string | number }>;
  dynamicOptions?: "fast_window" | "slow_window";
}

export interface StrategyGuidanceDefinition {
  signal: GuidedFieldDefinition[];
  risk: GuidedFieldDefinition[];
}

const commonRisk: GuidedFieldDefinition[] = [
  { key: "maxPositions", path: "risk.max_positions", kind: "number", integer: true, min: 1, step: 1 },
  { key: "positionSizePct", path: "risk.position_size_pct", kind: "percent", min: 0.01, max: 1, step: 1 },
];

const commonSignal: GuidedFieldDefinition[] = [
  { key: "minStrengthScore", path: "signal.min_strength_score", kind: "number", min: 0, max: 100, step: 1 },
];

const stagedRisk: GuidedFieldDefinition[] = [
  { key: "stage1TargetPct", path: "risk.stage_1_target_pct", kind: "percent", min: 0.01, max: 0.98, step: 1 },
  { key: "stage2TargetPct", path: "risk.stage_2_target_pct", kind: "percent", min: 0.02, max: 0.99, step: 1 },
  { key: "stage3TargetPct", path: "risk.stage_3_target_pct", kind: "percent", min: 1, max: 1, step: 1 },
];

export const ENGINE_READY_TYPES: StrategyType[] = [
  "trend",
  "mean_reversion",
  "momentum_breakout",
  "island_reversal",
  "double_bottom",
  "head_shoulders_bottom",
  "rounded_bottom",
  "v_reversal",
  "support_resistance",
];

export const STRATEGY_GUIDANCE: Record<Exclude<StrategyType, "custom">, StrategyGuidanceDefinition> = {
  trend: {
    signal: [
      ...commonSignal,
      { key: "fastKind", path: "signal.fast_indicator.kind", kind: "select", options: [{ label: "EMA", value: "ema" }, { label: "SMA", value: "sma" }] },
      { key: "fastWindow", path: "signal.fast_indicator.window", kind: "select", dynamicOptions: "fast_window" },
      { key: "slowKind", path: "signal.slow_indicator.kind", kind: "select", options: [{ label: "EMA", value: "ema" }, { label: "SMA", value: "sma" }] },
      { key: "slowWindow", path: "signal.slow_indicator.window", kind: "select", dynamicOptions: "slow_window" },
      { key: "volumeMultiplier", path: "signal.volume_multiplier", kind: "number", min: 0.01, step: 0.1 },
      { key: "atrMultiplier", path: "signal.atr_multiplier", kind: "number", min: 0.01, step: 0.1, advanced: true },
    ],
    risk: [
      ...commonRisk,
      { key: "stopLossPct", path: "risk.stop_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 },
    ],
  },
  mean_reversion: {
    signal: [
      ...commonSignal,
      { key: "lookbackWindow", path: "signal.lookback_window", kind: "select", options: [5, 10, 20].map((value) => ({ label: String(value), value })) },
      { key: "zscoreEntry", path: "signal.zscore_entry", kind: "number", min: 0.01, step: 0.1 },
      { key: "zscoreExit", path: "signal.zscore_exit", kind: "number", min: 0.01, step: 0.1 },
    ],
    risk: [
      ...commonRisk,
      { key: "stopLossPct", path: "risk.stop_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitPct", path: "risk.take_profit_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "maxHoldingDays", path: "risk.max_holding_days", kind: "number", integer: true, min: 0, step: 1, advanced: true },
    ],
  },
  momentum_breakout: {
    signal: [
      ...commonSignal,
      { key: "minimumReturn20d", path: "signal.minimum_return_20d", kind: "percent", min: 0, max: 1, step: 0.5 },
      { key: "breakoutBufferPct", path: "signal.breakout_buffer_pct", kind: "percent", min: 0, max: 1, step: 0.1 },
      { key: "volumeMultiplier", path: "signal.volume_multiplier", kind: "number", min: 0.01, step: 0.1 },
      { key: "exitReturn20d", path: "signal.exit_return_20d", kind: "percent", min: -1, max: 1, step: 0.5, advanced: true },
    ],
    risk: [
      ...commonRisk,
      { key: "stopLossPct", path: "risk.stop_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitPct", path: "risk.take_profit_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
    ],
  },
  island_reversal: {
    signal: [
      ...commonSignal,
      { key: "previousBodyAtrMin", path: "signal.previous_body_atr_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "breakoutBodyAtrMin", path: "signal.breakout_body_atr_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "exhaustionBodyAtrMax", path: "signal.exhaustion_body_atr_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "islandBodyAtrMax", path: "signal.island_body_atr_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "downtrendLookback", path: "signal.downtrend_lookback", kind: "number", integer: true, min: 1, step: 1 },
      { key: "downtrendMinDropPct", path: "signal.downtrend_min_drop_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "leftGapMinPct", path: "signal.left_gap_min_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1 },
      { key: "rightGapMinPct", path: "signal.right_gap_min_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1 },
      { key: "minIslandBars", path: "signal.min_island_bars", kind: "number", integer: true, min: 1, step: 1 },
      { key: "maxIslandBars", path: "signal.max_island_bars", kind: "number", integer: true, min: 1, step: 1 },
      { key: "leftVolumeRatioMax", path: "signal.left_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "rightVolumeRatioMin", path: "signal.right_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "retestWindow", path: "signal.retest_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "retestVolumeRatioMax", path: "signal.retest_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "supportTolerancePct", path: "signal.support_tolerance_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1, advanced: true },
    ],
    risk: [
      ...commonRisk,
      ...stagedRisk,
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 },
    ],
  },
  double_bottom: {
    signal: [
      ...commonSignal,
      { key: "reboundVolumeRatioMin", path: "signal.rebound_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "reboundVolumeRatioMax", path: "signal.rebound_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "downtrendLookback", path: "signal.downtrend_lookback", kind: "number", integer: true, min: 1, step: 1 },
      { key: "downtrendMinDropPct", path: "signal.downtrend_min_drop_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "downtrendMaxUpDayRatio", path: "signal.downtrend_max_up_day_ratio", kind: "percent", min: 0.0001, max: 1, step: 1, advanced: true },
      { key: "downtrendMinRSquared", path: "signal.downtrend_min_r_squared", kind: "percent", min: 0.0001, max: 1, step: 1, advanced: true },
      { key: "minBottomSpacing", path: "signal.min_bottom_spacing", kind: "number", integer: true, min: 1, step: 1 },
      { key: "maxBottomSpacing", path: "signal.max_bottom_spacing", kind: "number", integer: true, min: 1, step: 1 },
      { key: "leftBottomBeforeBars", path: "signal.left_bottom_before_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "leftBottomAfterBars", path: "signal.left_bottom_after_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "bottomTolerancePct", path: "signal.bottom_tolerance_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1 },
      { key: "necklineMinReboundPct", path: "signal.neckline_min_rebound_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1 },
      { key: "reboundUpDayRatioMin", path: "signal.rebound_up_day_ratio_min", kind: "percent", min: 0.0001, max: 1, step: 1, advanced: true },
      { key: "secondBottomVolumeRatioMax", path: "signal.second_bottom_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "breakoutVolumeRatioMin", path: "signal.breakout_volume_ratio_min", kind: "number", min: 0.01, step: 0.1 },
      { key: "maxBreakoutBars", path: "signal.max_breakout_bars_after_right_bottom", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "breakoutBufferPct", path: "signal.breakout_buffer_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1, advanced: true },
      { key: "retestWindow", path: "signal.retest_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "retestVolumeRatioMax", path: "signal.retest_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "supportTolerancePct", path: "signal.support_tolerance_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1, advanced: true },
    ],
    risk: [
      ...commonRisk,
      ...stagedRisk,
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 },
    ],
  },
  head_shoulders_bottom: {
    signal: [
      ...commonSignal,
      { key: "platformBars", path: "signal.platform_bars", kind: "number", min: 3, step: 1, integer: true, advanced: true },
      { key: "platformRangeAtrMax", path: "signal.platform_range_atr_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "platformDriftAtrMax", path: "signal.platform_drift_atr_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "reboundVolumeRatioMin", path: "signal.rebound_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "reboundVolumeRatioMax", path: "signal.rebound_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "downtrendMinDropPct", path: "signal.downtrend_min_drop_pct", kind: "percent", min: 0.0001, max: 0.9999, step: 0.1, advanced: true },
      { key: "headVolumeRatioMax", path: "signal.head_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "rightShoulderVolumeRatioMax", path: "signal.right_shoulder_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "breakoutBufferPct", path: "signal.breakout_buffer_pct", kind: "percent", min: 0, max: 0.9999, step: 0.1, advanced: true },
      { key: "downtrendLookback", path: "signal.downtrend_lookback", kind: "number", integer: true, min: 1, step: 1 },
      { key: "headDepthMinPct", path: "signal.head_depth_min_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "shoulderTolerancePct", path: "signal.shoulder_tolerance_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "breakoutVolumeRatioMin", path: "signal.breakout_volume_ratio_min", kind: "number", min: 0.01, step: 0.1 },
      { key: "pivotLeftBars", path: "signal.pivot_left_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "pivotRightBars", path: "signal.pivot_right_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "minSegmentBars", path: "signal.min_segment_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "maxSegmentBars", path: "signal.max_segment_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
    ],
    risk: [...commonRisk, ...stagedRisk, { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 }, { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 }, { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 }],
  },
  rounded_bottom: {
    signal: [
      ...commonSignal,
      { key: "weakeningBufferPct", path: "signal.weakening_buffer_pct", kind: "percent", min: 0.0001, max: 0.9999, step: 0.1, advanced: true },
      { key: "vertexPositionMin", path: "signal.vertex_position_min", kind: "percent", min: 0.0001, max: 0.9999, step: 0.1, advanced: true },
      { key: "vertexPositionMax", path: "signal.vertex_position_max", kind: "percent", min: 0.0001, max: 0.9999, step: 0.1, advanced: true },
      { key: "rightVolumeRatioMin", path: "signal.right_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "pullbackVolumeRatioMax", path: "signal.pullback_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "breakoutBufferPct", path: "signal.breakout_buffer_pct", kind: "percent", min: 0, max: 0.9999, step: 0.1, advanced: true },
      { key: "minLookback", path: "signal.min_lookback", kind: "number", integer: true, min: 3, step: 1 },
      { key: "maxLookback", path: "signal.max_lookback", kind: "number", integer: true, min: 3, step: 1 },
      { key: "minDepthPct", path: "signal.min_depth_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "minRSquared", path: "signal.min_r_squared", kind: "percent", min: 0.0001, max: 1, step: 1 },
      { key: "breakoutVolumeRatioMin", path: "signal.breakout_volume_ratio_min", kind: "number", min: 0.01, step: 0.1 },
      { key: "minPullbackSpacing", path: "signal.min_pullback_spacing", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "pivotLeftBars", path: "signal.pivot_left_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "pivotRightBars", path: "signal.pivot_right_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
    ],
    risk: [...commonRisk, ...stagedRisk, { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 }, { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 }, { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 }],
  },
  v_reversal: {
    signal: [
      ...commonSignal,
      { key: "consolidationRangeAtrMax", path: "signal.consolidation_range_atr_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "consolidationDriftAtrMax", path: "signal.consolidation_drift_atr_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "breakoutBufferPct", path: "signal.breakout_buffer_pct", kind: "percent", min: 0, max: 0.9999, step: 0.1, advanced: true },
      { key: "bearishBodyAtrMin", path: "signal.bearish_body_atr_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "pivotMaxBars", path: "signal.pivot_max_bars", kind: "number", min: 1, step: 1, integer: true, advanced: true },
      { key: "reversalMinAtr", path: "signal.reversal_min_atr", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "continuationVolumeRatioMin", path: "signal.continuation_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "breakoutVolumeRatioMin", path: "signal.breakout_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "retestVolumeRatioMax", path: "signal.retest_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "supportTolerancePct", path: "signal.support_tolerance_pct", kind: "percent", min: 0.0001, max: 0.9999, step: 0.1, advanced: true },
      { key: "bearishReversalVolumeRatioMin", path: "signal.bearish_reversal_volume_ratio_min", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "downtrendLookback", path: "signal.downtrend_lookback", kind: "number", integer: true, min: 1, step: 1 },
      { key: "downtrendMinDropPct", path: "signal.downtrend_min_drop_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "reversalMinReturnPct", path: "signal.reversal_min_return_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "pivotVolumeRatioMin", path: "signal.pivot_volume_ratio_min", kind: "number", min: 0.01, step: 0.1 },
      { key: "continuationWindow", path: "signal.continuation_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "consolidationMinBars", path: "signal.consolidation_min_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "consolidationMaxBars", path: "signal.consolidation_max_bars", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "retestWindow", path: "signal.retest_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
    ],
    risk: [...commonRisk, ...stagedRisk, { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 }, { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 }, { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 }],
  },
  support_resistance: {
    signal: [
      ...commonSignal,
      { key: "maxZonesPerKind", path: "signal.max_zones_per_kind", kind: "number", integer: true, min: 1, max: 5, step: 1 },
      { key: "pivotToleranceAtr", path: "signal.pivot_tolerance_atr", kind: "number", min: 0, max: 0.1, step: 0.01, advanced: true },
      { key: "supportBounceEnabled", path: "signal.support_bounce_enabled", kind: "boolean" },
      { key: "resistanceBreakoutEnabled", path: "signal.resistance_breakout_enabled", kind: "boolean" },
      { key: "breakoutRetestEnabled", path: "signal.breakout_retest_enabled", kind: "boolean" },
      { key: "pivotLeftBars", path: "signal.pivot_left_bars", kind: "number", integer: true, min: 1, step: 1 },
      { key: "pivotRightBars", path: "signal.pivot_right_bars", kind: "number", integer: true, min: 1, step: 1 },
      { key: "detectionWindow", path: "signal.detection_window", kind: "number", integer: true, min: 3, step: 1 },
      { key: "minLinePivots", path: "signal.min_line_pivots", kind: "number", integer: true, min: 3, step: 1, advanced: true },
      { key: "minLineSpanSessions", path: "signal.min_line_span_sessions", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "lineInlierToleranceAtr", path: "signal.line_inlier_tolerance_atr", kind: "number", min: 0.01, step: 0.05, advanced: true },
      { key: "maxAbsSlopeAtrPerSession", path: "signal.max_abs_slope_atr_per_session", kind: "number", min: 0.01, step: 0.05, advanced: true },
      { key: "zoneHalfWidthAtr", path: "signal.zone_half_width_atr", kind: "number", min: 0.01, step: 0.05, advanced: true },
      { key: "decayHalfLife", path: "signal.decay_half_life", kind: "number", min: 0.01, step: 1, advanced: true },
      { key: "bounceConfirmationAtr", path: "signal.bounce_confirmation_atr", kind: "number", min: 0.01, step: 0.05 },
      { key: "breakoutConfirmationAtr", path: "signal.breakout_confirmation_atr", kind: "number", min: 0.01, step: 0.05 },
      { key: "breakoutVolumeRatioMin", path: "signal.breakout_volume_ratio_min", kind: "number", min: 0.01, step: 0.1 },
      { key: "retestWindow", path: "signal.retest_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "retestVolumeRatioMax", path: "signal.retest_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
    ],
    risk: [
      { key: "maxPositions", path: "risk.max_positions", kind: "number", integer: true, min: 1, step: 1 },
      { key: "positionNotionalCap", path: "risk.position_size_pct", kind: "percent", min: 0.0001, max: 1, step: 1 },
      { key: "riskPerTradePct", path: "risk.risk_per_trade_pct", kind: "percent", min: 0.0001, max: 1, step: 0.1 },
      { key: "stopCooldownSessions", path: "risk.stop_cooldown_sessions", kind: "number", integer: true, min: 0, step: 1 },
      { key: "breakEvenAtR", path: "risk.break_even_at_r", kind: "number", min: 0.01, step: 0.1 },
      { key: "marketFilterEnabled", path: "risk.market_filter_enabled", kind: "boolean" },
      { key: "marketFilterSymbol", path: "risk.market_filter_symbol", kind: "text" },
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "stopReferencePct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "minRewardRisk", path: "risk.min_reward_risk", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "maxHoldingDays", path: "risk.max_holding_days", kind: "number", integer: true, min: 1, step: 1, advanced: true },
    ],
  },
};

export function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

export function getPathValue(source: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, segment) => {
    if (!current || typeof current !== "object" || Array.isArray(current)) return undefined;
    return (current as Record<string, unknown>)[segment];
  }, source);
}

export function setPathValue(
  source: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const next = cloneRecord(source);
  const parts = path.split(".");
  let current = next;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) {
      current[part] = value;
      return;
    }
    const existing = current[part];
    if (!existing || typeof existing !== "object" || Array.isArray(existing)) current[part] = {};
    current = current[part] as Record<string, unknown>;
  });
  return next;
}

export function normalizeSymbols(value: string): string[] {
  return Array.from(new Set(value.split(/[\s,;]+/).map((item) => item.trim().toUpperCase()).filter(Boolean))).sort();
}
