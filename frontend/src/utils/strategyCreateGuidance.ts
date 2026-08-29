import type { StrategyType } from "@/types/strategy";

export type GuidedFieldKind = "number" | "percent" | "boolean" | "select";

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

export const ENGINE_READY_TYPES: StrategyType[] = [
  "trend",
  "mean_reversion",
  "momentum_breakout",
  "island_reversal",
  "double_bottom",
  "support_resistance",
];

export const STRATEGY_GUIDANCE: Record<Exclude<StrategyType, "custom">, StrategyGuidanceDefinition> = {
  trend: {
    signal: [
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
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 },
    ],
  },
  double_bottom: {
    signal: [
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
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
      { key: "takeProfitAtr", path: "risk.take_profit_atr", kind: "number", min: 0.01, step: 0.1 },
    ],
  },
  support_resistance: {
    signal: [
      { key: "supportBounceEnabled", path: "signal.support_bounce_enabled", kind: "boolean" },
      { key: "resistanceBreakoutEnabled", path: "signal.resistance_breakout_enabled", kind: "boolean" },
      { key: "breakoutRetestEnabled", path: "signal.breakout_retest_enabled", kind: "boolean" },
      { key: "pivotLeftBars", path: "signal.pivot_left_bars", kind: "number", integer: true, min: 1, step: 1 },
      { key: "pivotRightBars", path: "signal.pivot_right_bars", kind: "number", integer: true, min: 1, step: 1 },
      { key: "detectionWindow", path: "signal.detection_window", kind: "number", integer: true, min: 3, step: 1 },
      { key: "clusterRadiusAtr", path: "signal.cluster_radius_atr", kind: "number", min: 0.01, step: 0.05, advanced: true },
      { key: "zoneHalfWidthAtr", path: "signal.zone_half_width_atr", kind: "number", min: 0.01, step: 0.05, advanced: true },
      { key: "minTouches", path: "signal.min_touches", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "decayHalfLife", path: "signal.decay_half_life", kind: "number", min: 0.01, step: 1, advanced: true },
      { key: "maxZonesPerSide", path: "signal.max_zones_per_side", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "bounceConfirmationAtr", path: "signal.bounce_confirmation_atr", kind: "number", min: 0.01, step: 0.05 },
      { key: "breakoutConfirmationAtr", path: "signal.breakout_confirmation_atr", kind: "number", min: 0.01, step: 0.05 },
      { key: "breakoutVolumeRatioMin", path: "signal.breakout_volume_ratio_min", kind: "number", min: 0.01, step: 0.1 },
      { key: "retestWindow", path: "signal.retest_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "retestVolumeRatioMax", path: "signal.retest_volume_ratio_max", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "scoreOutcomeWindow", path: "signal.score_outcome_window", kind: "number", integer: true, min: 1, step: 1, advanced: true },
      { key: "scoreTargetAtr", path: "signal.score_target_atr", kind: "number", min: 0.01, step: 0.1, advanced: true },
      { key: "scoreStopAtr", path: "signal.score_stop_atr", kind: "number", min: 0.01, step: 0.1, advanced: true },
    ],
    risk: [
      ...commonRisk,
      { key: "stopLossAtr", path: "risk.stop_loss_atr", kind: "number", min: 0.01, step: 0.1 },
      { key: "maxLossPct", path: "risk.max_loss_pct", kind: "percent", min: 0.0001, max: 1, step: 0.5 },
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
