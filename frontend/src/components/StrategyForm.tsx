import React, { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";

import {
  createStrategy,
  getStrategyCatalog,
  getStrategyFeatureSupport,
  updateStrategyConfig,
} from "@/api/strategies";
import { SelectControl } from "@/components/workspace/SelectControl";
import { useI18n } from "@/i18n/provider";
import type {
  StrategyCatalogItem,
  StrategyCreate,
  StrategyFeatureSupport,
  StrategyOut,
  StrategyStatus,
  StrategyType,
} from "@/types/strategy";
import { getStrategyCategoryPresentation } from "@/utils/strategy";

const MEAN_REVERSION_LOOKBACK_OPTIONS = [5, 10, 20];
const STATUS_OPTIONS = ["draft", "active", "archived"].map((value) => ({ value, label: value }));
const LINE_KIND_OPTIONS = [
  { value: "ema", label: "EMA" },
  { value: "sma", label: "SMA" },
];
const REBALANCE_OPTIONS = ["daily", "weekly", "monthly"].map((value) => ({ value, label: value }));
const RUN_AT_OPTIONS = ["close", "open"].map((value) => ({ value, label: value }));

function toRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

function toFiniteNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function toStringValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function toBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function toSymbolText(value: unknown): string {
  if (!Array.isArray(value)) {
    return "";
  }
  return value
    .map((item) => String(item).trim().toUpperCase())
    .filter(Boolean)
    .join(",");
}

function withMinStrengthScore(value: unknown, score: number): Record<string, unknown> {
  const params = { ...toRecord(value) };
  params.signal = { ...toRecord(params.signal), min_strength_score: score };
  return params;
}

function isStrategyType(value: unknown): value is StrategyType {
  return (
    value === "trend"
    || value === "mean_reversion"
    || value === "momentum_breakout"
    || value === "island_reversal"
    || value === "double_bottom"
    || value === "head_shoulders_bottom"
    || value === "rounded_bottom"
    || value === "v_reversal"
    || value === "support_resistance"
    || value === "custom"
  );
}

function isStrategyStatus(value: unknown): value is StrategyStatus {
  return value === "draft" || value === "active" || value === "archived";
}

interface StrategyFormProps {
  mode?: "create" | "edit";
  initialStrategy?: StrategyOut | null;
}

export default function StrategyForm({
  mode = "create",
  initialStrategy = null,
}: StrategyFormProps) {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const isEditMode = mode === "edit";
  const initialParams = toRecord(initialStrategy?.params);
  const initialSignal = toRecord(initialParams.signal);
  const initialUniverse = toRecord(initialParams.universe);
  const initialRisk = toRecord(initialParams.risk);
  const initialExecution = toRecord(initialParams.execution);
  const initialFastIndicator = toRecord(initialSignal.fast_indicator);
  const initialSlowIndicator = toRecord(initialSignal.slow_indicator);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [featureSupport, setFeatureSupport] = useState<StrategyFeatureSupport | null>(null);
  const [featureSupportError, setFeatureSupportError] = useState<string | null>(null);
  const [name, setName] = useState(
    initialStrategy?.name ?? "Trend_EMA15_SMA200"
  );
  const [description, setDescription] = useState(
    initialStrategy
      ? initialStrategy.description
        ?? toStringValue(toRecord(initialParams.metadata).description, "")
      : isZh
        ? "双均线趋势策略"
        : "Dual moving average trend strategy"
  );
  const [strategyType, setStrategyType] = useState<StrategyType>(
    isStrategyType(initialStrategy?.strategy_type) ? initialStrategy.strategy_type : "trend"
  );
  const [status, setStatus] = useState<StrategyStatus>(
    isStrategyStatus(initialStrategy?.status) ? initialStrategy.status : "draft"
  );
  const [fastKind, setFastKind] = useState<"ema" | "sma">(
    initialFastIndicator.kind === "sma" ? "sma" : "ema"
  );
  const [fastWindow, setFastWindow] = useState(
    toFiniteNumber(initialFastIndicator.window, 15)
  );
  const [slowKind, setSlowKind] = useState<"ema" | "sma">(
    initialSlowIndicator.kind === "ema" ? "ema" : "sma"
  );
  const [slowWindow, setSlowWindow] = useState(
    toFiniteNumber(initialSlowIndicator.window, 200)
  );
  const [volMul, setVolMul] = useState(
    toFiniteNumber(initialSignal.volume_multiplier, 1.5)
  );
  const [atrMul, setAtrMul] = useState(
    toFiniteNumber(initialSignal.atr_multiplier, 2.0)
  );
  const [minStrengthScore, setMinStrengthScore] = useState(
    toFiniteNumber(initialSignal.min_strength_score, 50)
  );
  const [trendStopLossPct, setTrendStopLossPct] = useState(
    toFiniteNumber(initialRisk.stop_loss_pct, 0.1)
  );
  const [trendTakeProfitAtr, setTrendTakeProfitAtr] = useState(
    toFiniteNumber(initialRisk.take_profit_atr, 4.0)
  );
  const [symbols, setSymbols] = useState(toSymbolText(initialUniverse.symbols));
  const [maxPositions, setMaxPositions] = useState(
    toFiniteNumber(initialRisk.max_positions, 10)
  );
  const [positionSizePct, setPositionSizePct] = useState(
    toFiniteNumber(initialRisk.position_size_pct, 0.1)
  );
  const [rebalance, setRebalance] = useState(
    toStringValue(initialExecution.rebalance, "daily")
  );
  const [runAt, setRunAt] = useState(
    toStringValue(initialExecution.run_at, "close")
  );
  const [meanReversionLookback, setMeanReversionLookback] = useState(
    toFiniteNumber(initialSignal.lookback_window, 20)
  );
  const [meanReversionZscoreEntry, setMeanReversionZscoreEntry] = useState(
    toFiniteNumber(initialSignal.zscore_entry, 2.0)
  );
  const [meanReversionZscoreExit, setMeanReversionZscoreExit] = useState(
    toFiniteNumber(initialSignal.zscore_exit, 0.5)
  );
  const [meanReversionStopLossPct, setMeanReversionStopLossPct] = useState(
    toFiniteNumber(initialRisk.stop_loss_pct, 0.1)
  );
  const [meanReversionTakeProfitPct, setMeanReversionTakeProfitPct] = useState(
    toFiniteNumber(initialRisk.take_profit_pct, 0.1)
  );
  const [meanReversionMaxHoldingDays, setMeanReversionMaxHoldingDays] = useState(
    toFiniteNumber(initialRisk.max_holding_days, 0)
  );
  const [islandDowntrendLookback, setIslandDowntrendLookback] = useState(
    toFiniteNumber(initialSignal.downtrend_lookback, 60)
  );
  const [islandDowntrendMinDropPct, setIslandDowntrendMinDropPct] = useState(
    toFiniteNumber(initialSignal.downtrend_min_drop_pct, 0.15)
  );
  const [leftGapMinPct, setLeftGapMinPct] = useState(
    toFiniteNumber(initialSignal.left_gap_min_pct, 0.02)
  );
  const [rightGapMinPct, setRightGapMinPct] = useState(
    toFiniteNumber(initialSignal.right_gap_min_pct, 0.02)
  );
  const [minIslandBars, setMinIslandBars] = useState(
    toFiniteNumber(initialSignal.min_island_bars, 1)
  );
  const [maxIslandBars, setMaxIslandBars] = useState(
    toFiniteNumber(initialSignal.max_island_bars, 8)
  );
  const [leftVolumeRatioMax, setLeftVolumeRatioMax] = useState(
    toFiniteNumber(initialSignal.left_volume_ratio_max, 0.8)
  );
  const [rightVolumeRatioMin, setRightVolumeRatioMin] = useState(
    toFiniteNumber(initialSignal.right_volume_ratio_min, 1.5)
  );
  const [retestWindow, setRetestWindow] = useState(
    toFiniteNumber(initialSignal.retest_window, 10)
  );
  const [retestVolumeRatioMax, setRetestVolumeRatioMax] = useState(
    toFiniteNumber(initialSignal.retest_volume_ratio_max, 0.7)
  );
  const [supportTolerancePct, setSupportTolerancePct] = useState(
    toFiniteNumber(initialSignal.support_tolerance_pct, 0.01)
  );
  const [islandStopLossAtr, setIslandStopLossAtr] = useState(
    toFiniteNumber(initialRisk.stop_loss_atr, 1.5)
  );
  const [islandMaxLossPct, setIslandMaxLossPct] = useState(
    toFiniteNumber(initialRisk.max_loss_pct, 0.1)
  );
  const [islandTakeProfitAtr, setIslandTakeProfitAtr] = useState(
    toFiniteNumber(initialRisk.take_profit_atr, 3.0)
  );
  const [doubleBottomDowntrendLookback, setDoubleBottomDowntrendLookback] = useState(
    toFiniteNumber(initialSignal.downtrend_lookback, 60)
  );
  const [doubleBottomDowntrendMinDropPct, setDoubleBottomDowntrendMinDropPct] = useState(
    toFiniteNumber(initialSignal.downtrend_min_drop_pct, 0.2)
  );
  const [doubleBottomDowntrendMaxUpDayRatio, setDoubleBottomDowntrendMaxUpDayRatio] = useState(
    toFiniteNumber(initialSignal.downtrend_max_up_day_ratio, 0.35)
  );
  const [doubleBottomDowntrendMinRSquared, setDoubleBottomDowntrendMinRSquared] = useState(
    toFiniteNumber(initialSignal.downtrend_min_r_squared, 0.65)
  );
  const [minBottomSpacing, setMinBottomSpacing] = useState(
    toFiniteNumber(initialSignal.min_bottom_spacing, 5)
  );
  const [maxBottomSpacing, setMaxBottomSpacing] = useState(
    toFiniteNumber(initialSignal.max_bottom_spacing, 30)
  );
  const [leftBottomBeforeBars, setLeftBottomBeforeBars] = useState(
    toFiniteNumber(initialSignal.left_bottom_before_bars, 1)
  );
  const [leftBottomAfterBars, setLeftBottomAfterBars] = useState(
    toFiniteNumber(initialSignal.left_bottom_after_bars, 1)
  );
  const [bottomTolerancePct, setBottomTolerancePct] = useState(
    toFiniteNumber(initialSignal.bottom_tolerance_pct, 0.03)
  );
  const [necklineMinReboundPct, setNecklineMinReboundPct] = useState(
    toFiniteNumber(initialSignal.neckline_min_rebound_pct, 0.06)
  );
  const [reboundUpDayRatioMin, setReboundUpDayRatioMin] = useState(
    toFiniteNumber(initialSignal.rebound_up_day_ratio_min, 0.6)
  );
  const [secondBottomVolumeRatioMax, setSecondBottomVolumeRatioMax] = useState(
    toFiniteNumber(initialSignal.second_bottom_volume_ratio_max, 0.9)
  );
  const [breakoutVolumeRatioMin, setBreakoutVolumeRatioMin] = useState(
    toFiniteNumber(initialSignal.breakout_volume_ratio_min, 1.5)
  );
  const [maxBreakoutBarsAfterRightBottom, setMaxBreakoutBarsAfterRightBottom] = useState(
    toFiniteNumber(initialSignal.max_breakout_bars_after_right_bottom, 40)
  );
  const [breakoutBufferPct, setBreakoutBufferPct] = useState(
    toFiniteNumber(initialSignal.breakout_buffer_pct, 0.005)
  );
  const [doubleBottomRetestWindow, setDoubleBottomRetestWindow] = useState(
    toFiniteNumber(initialSignal.retest_window, 10)
  );
  const [doubleBottomRetestVolumeRatioMax, setDoubleBottomRetestVolumeRatioMax] = useState(
    toFiniteNumber(initialSignal.retest_volume_ratio_max, 0.8)
  );
  const [doubleBottomSupportTolerancePct, setDoubleBottomSupportTolerancePct] = useState(
    toFiniteNumber(initialSignal.support_tolerance_pct, 0.02)
  );
  const [doubleBottomStopLossAtr, setDoubleBottomStopLossAtr] = useState(
    toFiniteNumber(initialRisk.stop_loss_atr, 1.5)
  );
  const [doubleBottomMaxLossPct, setDoubleBottomMaxLossPct] = useState(
    toFiniteNumber(initialRisk.max_loss_pct, 0.08)
  );
  const [doubleBottomTakeProfitAtr, setDoubleBottomTakeProfitAtr] = useState(
    toFiniteNumber(initialRisk.take_profit_atr, 3.0)
  );
  const [supportBounceEnabled, setSupportBounceEnabled] = useState(
    toBoolean(initialSignal.support_bounce_enabled, true)
  );
  const [resistanceBreakoutEnabled, setResistanceBreakoutEnabled] = useState(
    toBoolean(initialSignal.resistance_breakout_enabled, true)
  );
  const [breakoutRetestEnabled, setBreakoutRetestEnabled] = useState(
    toBoolean(initialSignal.breakout_retest_enabled, true)
  );
  const [srPivotLeftBars, setSrPivotLeftBars] = useState(toFiniteNumber(initialSignal.pivot_left_bars, 3));
  const [srPivotRightBars, setSrPivotRightBars] = useState(toFiniteNumber(initialSignal.pivot_right_bars, 3));
  const [srDetectionWindow, setSrDetectionWindow] = useState(toFiniteNumber(initialSignal.detection_window, 120));
  const [srMinLinePivots, setSrMinLinePivots] = useState(toFiniteNumber(initialSignal.min_line_pivots, 3));
  const [srMinLineSpanSessions, setSrMinLineSpanSessions] = useState(toFiniteNumber(initialSignal.min_line_span_sessions, 10));
  const [srLineInlierToleranceAtr, setSrLineInlierToleranceAtr] = useState(toFiniteNumber(initialSignal.line_inlier_tolerance_atr, 0.75));
  const [srMaxAbsSlopeAtrPerSession, setSrMaxAbsSlopeAtrPerSession] = useState(toFiniteNumber(initialSignal.max_abs_slope_atr_per_session, 0.25));
  const [srZoneHalfWidthAtr, setSrZoneHalfWidthAtr] = useState(toFiniteNumber(initialSignal.zone_half_width_atr, 0.5));
  const [srDecayHalfLife, setSrDecayHalfLife] = useState(toFiniteNumber(initialSignal.decay_half_life, 60));
  const [srBounceConfirmationAtr, setSrBounceConfirmationAtr] = useState(toFiniteNumber(initialSignal.bounce_confirmation_atr, 0.25));
  const [srBreakoutConfirmationAtr, setSrBreakoutConfirmationAtr] = useState(toFiniteNumber(initialSignal.breakout_confirmation_atr, 0.5));
  const [srBreakoutVolumeRatioMin, setSrBreakoutVolumeRatioMin] = useState(toFiniteNumber(initialSignal.breakout_volume_ratio_min, 1.5));
  const [srRetestWindow, setSrRetestWindow] = useState(toFiniteNumber(initialSignal.retest_window, 10));
  const [srRetestVolumeRatioMax, setSrRetestVolumeRatioMax] = useState(toFiniteNumber(initialSignal.retest_volume_ratio_max, 0.8));
  const [srScoreOutcomeWindow, setSrScoreOutcomeWindow] = useState(toFiniteNumber(initialSignal.score_outcome_window, 20));
  const [srScoreTargetAtr, setSrScoreTargetAtr] = useState(toFiniteNumber(initialSignal.score_target_atr, 3));
  const [srScoreStopAtr, setSrScoreStopAtr] = useState(toFiniteNumber(initialSignal.score_stop_atr, 1.5));
  const [srStopLossAtr, setSrStopLossAtr] = useState(toFiniteNumber(initialRisk.stop_loss_atr, 1.5));
  const [srMaxLossPct, setSrMaxLossPct] = useState(toFiniteNumber(initialRisk.max_loss_pct, 0.08));
  const [srTakeProfitAtr, setSrTakeProfitAtr] = useState(toFiniteNumber(initialRisk.take_profit_atr, 3));
  const [srMinRewardRisk, setSrMinRewardRisk] = useState(toFiniteNumber(initialRisk.min_reward_risk, 1.5));
  const [srMaxHoldingDays, setSrMaxHoldingDays] = useState(toFiniteNumber(initialRisk.max_holding_days, 40));
  const [rawJson, setRawJson] = useState(
    initialStrategy
      ? JSON.stringify(initialStrategy.params, null, 2)
      : "{\n  \"rules\": []\n}"
  );
  const [resp, setResp] = useState<StrategyOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const selectedTemplate = useMemo(
    () => catalog.find((item) => item.strategy_type === strategyType) || null,
    [catalog, strategyType]
  );

  const applyTemplateDefaults = (template: StrategyCatalogItem) => {
    const defaults = toRecord(template.defaults);
    const signal = toRecord(defaults.signal);
    const universe = toRecord(defaults.universe);
    const risk = toRecord(defaults.risk);
    const execution = toRecord(defaults.execution);

    setSymbols(toSymbolText(universe.symbols));
    setMaxPositions(toFiniteNumber(risk.max_positions, 10));
    setPositionSizePct(toFiniteNumber(risk.position_size_pct, 0.1));
    setMinStrengthScore(toFiniteNumber(signal.min_strength_score, 50));
    setRebalance(toStringValue(execution.rebalance, "daily"));
    setRunAt(toStringValue(execution.run_at, "close"));

    if (template.strategy_type === "trend") {
      const fastIndicator = toRecord(signal.fast_indicator);
      const slowIndicator = toRecord(signal.slow_indicator);

      setFastKind(fastIndicator.kind === "sma" ? "sma" : "ema");
      setFastWindow(toFiniteNumber(fastIndicator.window, 15));
      setSlowKind(slowIndicator.kind === "ema" ? "ema" : "sma");
      setSlowWindow(toFiniteNumber(slowIndicator.window, 200));
      setVolMul(toFiniteNumber(signal.volume_multiplier, 1.5));
      setAtrMul(toFiniteNumber(signal.atr_multiplier, 2.0));
      setTrendStopLossPct(toFiniteNumber(risk.stop_loss_pct, 0.1));
      setTrendTakeProfitAtr(toFiniteNumber(risk.take_profit_atr, 4.0));
      return;
    }

    if (template.strategy_type === "mean_reversion") {
      setMeanReversionLookback(toFiniteNumber(signal.lookback_window, 20));
      setMeanReversionZscoreEntry(toFiniteNumber(signal.zscore_entry, 2.0));
      setMeanReversionZscoreExit(toFiniteNumber(signal.zscore_exit, 0.5));
      setMeanReversionStopLossPct(toFiniteNumber(risk.stop_loss_pct, 0.1));
      setMeanReversionTakeProfitPct(toFiniteNumber(risk.take_profit_pct, 0.1));
      setMeanReversionMaxHoldingDays(toFiniteNumber(risk.max_holding_days, 0));
      return;
    }

    if (template.strategy_type === "island_reversal") {
      setIslandDowntrendLookback(toFiniteNumber(signal.downtrend_lookback, 60));
      setIslandDowntrendMinDropPct(toFiniteNumber(signal.downtrend_min_drop_pct, 0.15));
      setLeftGapMinPct(toFiniteNumber(signal.left_gap_min_pct, 0.02));
      setRightGapMinPct(toFiniteNumber(signal.right_gap_min_pct, 0.02));
      setMinIslandBars(toFiniteNumber(signal.min_island_bars, 1));
      setMaxIslandBars(toFiniteNumber(signal.max_island_bars, 8));
      setLeftVolumeRatioMax(toFiniteNumber(signal.left_volume_ratio_max, 0.8));
      setRightVolumeRatioMin(toFiniteNumber(signal.right_volume_ratio_min, 1.5));
      setRetestWindow(toFiniteNumber(signal.retest_window, 10));
      setRetestVolumeRatioMax(toFiniteNumber(signal.retest_volume_ratio_max, 0.7));
      setSupportTolerancePct(toFiniteNumber(signal.support_tolerance_pct, 0.01));
      setIslandStopLossAtr(toFiniteNumber(risk.stop_loss_atr, 1.5));
      setIslandMaxLossPct(toFiniteNumber(risk.max_loss_pct, 0.1));
      setIslandTakeProfitAtr(toFiniteNumber(risk.take_profit_atr, 3.0));
      return;
    }

    if (template.strategy_type === "double_bottom") {
      setDoubleBottomDowntrendLookback(toFiniteNumber(signal.downtrend_lookback, 60));
      setDoubleBottomDowntrendMinDropPct(toFiniteNumber(signal.downtrend_min_drop_pct, 0.2));
      setDoubleBottomDowntrendMaxUpDayRatio(toFiniteNumber(signal.downtrend_max_up_day_ratio, 0.35));
      setDoubleBottomDowntrendMinRSquared(toFiniteNumber(signal.downtrend_min_r_squared, 0.65));
      setMinBottomSpacing(toFiniteNumber(signal.min_bottom_spacing, 5));
      setMaxBottomSpacing(toFiniteNumber(signal.max_bottom_spacing, 30));
      setLeftBottomBeforeBars(toFiniteNumber(signal.left_bottom_before_bars, 1));
      setLeftBottomAfterBars(toFiniteNumber(signal.left_bottom_after_bars, 1));
      setBottomTolerancePct(toFiniteNumber(signal.bottom_tolerance_pct, 0.03));
      setNecklineMinReboundPct(toFiniteNumber(signal.neckline_min_rebound_pct, 0.06));
      setReboundUpDayRatioMin(toFiniteNumber(signal.rebound_up_day_ratio_min, 0.6));
      setSecondBottomVolumeRatioMax(toFiniteNumber(signal.second_bottom_volume_ratio_max, 0.9));
      setBreakoutVolumeRatioMin(toFiniteNumber(signal.breakout_volume_ratio_min, 1.5));
      setMaxBreakoutBarsAfterRightBottom(toFiniteNumber(signal.max_breakout_bars_after_right_bottom, 40));
      setBreakoutBufferPct(toFiniteNumber(signal.breakout_buffer_pct, 0.005));
      setDoubleBottomRetestWindow(toFiniteNumber(signal.retest_window, 10));
      setDoubleBottomRetestVolumeRatioMax(toFiniteNumber(signal.retest_volume_ratio_max, 0.8));
      setDoubleBottomSupportTolerancePct(toFiniteNumber(signal.support_tolerance_pct, 0.02));
      setDoubleBottomStopLossAtr(toFiniteNumber(risk.stop_loss_atr, 1.5));
      setDoubleBottomMaxLossPct(toFiniteNumber(risk.max_loss_pct, 0.08));
      setDoubleBottomTakeProfitAtr(toFiniteNumber(risk.take_profit_atr, 3.0));
      return;
    }

    if (template.strategy_type === "support_resistance") {
      setSupportBounceEnabled(toBoolean(signal.support_bounce_enabled, true));
      setResistanceBreakoutEnabled(toBoolean(signal.resistance_breakout_enabled, true));
      setBreakoutRetestEnabled(toBoolean(signal.breakout_retest_enabled, true));
      setSrPivotLeftBars(toFiniteNumber(signal.pivot_left_bars, 3));
      setSrPivotRightBars(toFiniteNumber(signal.pivot_right_bars, 3));
      setSrDetectionWindow(toFiniteNumber(signal.detection_window, 120));
      setSrMinLinePivots(toFiniteNumber(signal.min_line_pivots, 3));
      setSrMinLineSpanSessions(toFiniteNumber(signal.min_line_span_sessions, 10));
      setSrLineInlierToleranceAtr(toFiniteNumber(signal.line_inlier_tolerance_atr, 0.75));
      setSrMaxAbsSlopeAtrPerSession(toFiniteNumber(signal.max_abs_slope_atr_per_session, 0.25));
      setSrZoneHalfWidthAtr(toFiniteNumber(signal.zone_half_width_atr, 0.5));
      setSrDecayHalfLife(toFiniteNumber(signal.decay_half_life, 60));
      setSrBounceConfirmationAtr(toFiniteNumber(signal.bounce_confirmation_atr, 0.25));
      setSrBreakoutConfirmationAtr(toFiniteNumber(signal.breakout_confirmation_atr, 0.5));
      setSrBreakoutVolumeRatioMin(toFiniteNumber(signal.breakout_volume_ratio_min, 1.5));
      setSrRetestWindow(toFiniteNumber(signal.retest_window, 10));
      setSrRetestVolumeRatioMax(toFiniteNumber(signal.retest_volume_ratio_max, 0.8));
      setSrScoreOutcomeWindow(toFiniteNumber(signal.score_outcome_window, 20));
      setSrScoreTargetAtr(toFiniteNumber(signal.score_target_atr, 3));
      setSrScoreStopAtr(toFiniteNumber(signal.score_stop_atr, 1.5));
      setSrStopLossAtr(toFiniteNumber(risk.stop_loss_atr, 1.5));
      setSrMaxLossPct(toFiniteNumber(risk.max_loss_pct, 0.08));
      setSrTakeProfitAtr(toFiniteNumber(risk.take_profit_atr, 3));
      setSrMinRewardRisk(toFiniteNumber(risk.min_reward_risk, 1.5));
      setSrMaxHoldingDays(toFiniteNumber(risk.max_holding_days, 40));
      return;
    }

    setRawJson(JSON.stringify(template.defaults, null, 2));
  };

  const resetToTemplateDefaults = () => {
    if (!selectedTemplate) {
      return;
    }

    const confirmed = typeof window === "undefined"
      ? true
      : window.confirm(
        isZh
          ? "确认将当前策略参数重置为该策略类型的默认值吗？这不会修改策略名称、说明或状态。"
          : "Reset the current strategy parameters to this template's defaults? This keeps the strategy name, description, and status unchanged."
      );
    if (!confirmed) {
      return;
    }

    applyTemplateDefaults(selectedTemplate);
    setErr(null);
    setResp(null);
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([getStrategyCatalog(), getStrategyFeatureSupport()])
      .then(([items, support]) => {
        if (!cancelled) {
          setCatalog(items);
          setFeatureSupport(support);
        }
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setCatalogError(error.message || (isZh ? "无法加载策略模板" : "Unable to load strategy templates"));
          setFeatureSupportError(
            error.message || (isZh ? "无法加载数据库支持的指标配置" : "Unable to load supported indicator settings from the database")
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isZh]);

  useEffect(() => {
    if (isEditMode) {
      return;
    }
    if (
      strategyType === "trend"
      || strategyType === "island_reversal"
      || strategyType === "double_bottom"
      || strategyType === "support_resistance"
      || catalog.length === 0
    ) {
      return;
    }
    const item = catalog.find((entry) => entry.strategy_type === strategyType);
    if (item) {
      setRawJson(JSON.stringify(item.defaults, null, 2));
    }
  }, [catalog, isEditMode, strategyType]);

  useEffect(() => {
    if (isEditMode) {
      return;
    }
    if (strategyType !== "mean_reversion" || !selectedTemplate) {
      return;
    }

    const defaults = toRecord(selectedTemplate.defaults);
    const signal = toRecord(defaults.signal);
    const universe = toRecord(defaults.universe);
    const risk = toRecord(defaults.risk);
    const execution = toRecord(defaults.execution);

    setMeanReversionLookback(toFiniteNumber(signal.lookback_window, 20));
    setMeanReversionZscoreEntry(toFiniteNumber(signal.zscore_entry, 2.0));
    setMeanReversionZscoreExit(toFiniteNumber(signal.zscore_exit, 0.5));
    setSymbols(toSymbolText(universe.symbols));
    setMaxPositions(toFiniteNumber(risk.max_positions, 10));
    setPositionSizePct(toFiniteNumber(risk.position_size_pct, 0.1));
    setMeanReversionStopLossPct(toFiniteNumber(risk.stop_loss_pct, 0.1));
    setMeanReversionTakeProfitPct(toFiniteNumber(risk.take_profit_pct, 0.1));
    setMeanReversionMaxHoldingDays(toFiniteNumber(risk.max_holding_days, 0));
    setRebalance(toStringValue(execution.rebalance, "daily"));
    setRunAt(toStringValue(execution.run_at, "close"));
  }, [isEditMode, selectedTemplate, strategyType]);

  useEffect(() => {
    if (isEditMode) {
      return;
    }
    if (strategyType !== "island_reversal" || !selectedTemplate) {
      return;
    }

    const defaults = toRecord(selectedTemplate.defaults);
    const signal = toRecord(defaults.signal);
    const universe = toRecord(defaults.universe);
    const risk = toRecord(defaults.risk);
    const execution = toRecord(defaults.execution);

    setIslandDowntrendLookback(toFiniteNumber(signal.downtrend_lookback, 60));
    setIslandDowntrendMinDropPct(toFiniteNumber(signal.downtrend_min_drop_pct, 0.15));
    setLeftGapMinPct(toFiniteNumber(signal.left_gap_min_pct, 0.02));
    setRightGapMinPct(toFiniteNumber(signal.right_gap_min_pct, 0.02));
    setMinIslandBars(toFiniteNumber(signal.min_island_bars, 1));
    setMaxIslandBars(toFiniteNumber(signal.max_island_bars, 8));
    setLeftVolumeRatioMax(toFiniteNumber(signal.left_volume_ratio_max, 0.8));
    setRightVolumeRatioMin(toFiniteNumber(signal.right_volume_ratio_min, 1.5));
    setRetestWindow(toFiniteNumber(signal.retest_window, 10));
    setRetestVolumeRatioMax(toFiniteNumber(signal.retest_volume_ratio_max, 0.7));
    setSupportTolerancePct(toFiniteNumber(signal.support_tolerance_pct, 0.01));
    setSymbols(toSymbolText(universe.symbols));
    setMaxPositions(toFiniteNumber(risk.max_positions, 6));
    setPositionSizePct(toFiniteNumber(risk.position_size_pct, 0.15));
    setIslandStopLossAtr(toFiniteNumber(risk.stop_loss_atr, 1.5));
    setIslandMaxLossPct(toFiniteNumber(risk.max_loss_pct, 0.1));
    setIslandTakeProfitAtr(toFiniteNumber(risk.take_profit_atr, 3.0));
    setRebalance(toStringValue(execution.rebalance, "daily"));
    setRunAt(toStringValue(execution.run_at, "close"));
  }, [isEditMode, selectedTemplate, strategyType]);

  useEffect(() => {
    if (isEditMode) {
      return;
    }
    if (strategyType !== "double_bottom" || !selectedTemplate) {
      return;
    }

    const defaults = toRecord(selectedTemplate.defaults);
    const signal = toRecord(defaults.signal);
    const universe = toRecord(defaults.universe);
    const risk = toRecord(defaults.risk);
    const execution = toRecord(defaults.execution);

    setDoubleBottomDowntrendLookback(toFiniteNumber(signal.downtrend_lookback, 60));
    setDoubleBottomDowntrendMinDropPct(toFiniteNumber(signal.downtrend_min_drop_pct, 0.2));
    setDoubleBottomDowntrendMaxUpDayRatio(toFiniteNumber(signal.downtrend_max_up_day_ratio, 0.35));
    setDoubleBottomDowntrendMinRSquared(toFiniteNumber(signal.downtrend_min_r_squared, 0.65));
    setMinBottomSpacing(toFiniteNumber(signal.min_bottom_spacing, 5));
    setMaxBottomSpacing(toFiniteNumber(signal.max_bottom_spacing, 30));
    setLeftBottomBeforeBars(toFiniteNumber(signal.left_bottom_before_bars, 1));
    setLeftBottomAfterBars(toFiniteNumber(signal.left_bottom_after_bars, 1));
    setBottomTolerancePct(toFiniteNumber(signal.bottom_tolerance_pct, 0.03));
    setNecklineMinReboundPct(toFiniteNumber(signal.neckline_min_rebound_pct, 0.06));
    setReboundUpDayRatioMin(toFiniteNumber(signal.rebound_up_day_ratio_min, 0.6));
    setSecondBottomVolumeRatioMax(toFiniteNumber(signal.second_bottom_volume_ratio_max, 0.9));
    setBreakoutVolumeRatioMin(toFiniteNumber(signal.breakout_volume_ratio_min, 1.5));
    setMaxBreakoutBarsAfterRightBottom(toFiniteNumber(signal.max_breakout_bars_after_right_bottom, 40));
    setBreakoutBufferPct(toFiniteNumber(signal.breakout_buffer_pct, 0.005));
    setDoubleBottomRetestWindow(toFiniteNumber(signal.retest_window, 10));
    setDoubleBottomRetestVolumeRatioMax(toFiniteNumber(signal.retest_volume_ratio_max, 0.8));
    setDoubleBottomSupportTolerancePct(toFiniteNumber(signal.support_tolerance_pct, 0.02));
    setSymbols(toSymbolText(universe.symbols));
    setMaxPositions(toFiniteNumber(risk.max_positions, 6));
    setPositionSizePct(toFiniteNumber(risk.position_size_pct, 0.15));
    setDoubleBottomStopLossAtr(toFiniteNumber(risk.stop_loss_atr, 1.5));
    setDoubleBottomMaxLossPct(toFiniteNumber(risk.max_loss_pct, 0.08));
    setDoubleBottomTakeProfitAtr(toFiniteNumber(risk.take_profit_atr, 3.0));
    setRebalance(toStringValue(execution.rebalance, "daily"));
    setRunAt(toStringValue(execution.run_at, "close"));
  }, [isEditMode, selectedTemplate, strategyType]);

  const fastWindowOptions = useMemo(
    () =>
      fastKind === "ema"
        ? featureSupport?.trend.ema_windows || []
        : featureSupport?.trend.sma_windows || [],
    [fastKind, featureSupport]
  );

  const slowWindowOptions = useMemo(
    () =>
      slowKind === "ema"
        ? featureSupport?.trend.ema_windows || []
        : featureSupport?.trend.sma_windows || [],
    [slowKind, featureSupport]
  );

  useEffect(() => {
    if (fastWindowOptions.length === 0) {
      return;
    }
    if (!fastWindowOptions.includes(Number(fastWindow))) {
      setFastWindow(fastWindowOptions[0]);
    }
  }, [fastWindow, fastWindowOptions]);

  useEffect(() => {
    if (slowWindowOptions.length === 0) {
      return;
    }
    if (!slowWindowOptions.includes(Number(slowWindow))) {
      setSlowWindow(slowWindowOptions[0]);
    }
  }, [slowWindow, slowWindowOptions]);

  const trendParams = useMemo(
    () => {
      const parsedSymbols = symbols
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);

      return {
      signal: {
        min_strength_score: Number(minStrengthScore),
        fast_indicator: {
          kind: fastKind,
          window: Number(fastWindow),
        },
        slow_indicator: {
          kind: slowKind,
          window: Number(slowWindow),
        },
        volume_multiplier: Number(volMul),
        atr_multiplier: Number(atrMul),
        price_field: "close",
        trigger: "cross_over",
      },
      universe: {
        symbols: parsedSymbols,
        selection_mode: parsedSymbols.length > 0 ? "manual" : "all_common_stock",
      },
      risk: {
        max_positions: Number(maxPositions),
        position_size_pct: Number(positionSizePct),
        stop_loss_pct: Number(trendStopLossPct),
        stop_loss_atr: Number(atrMul),
        take_profit_atr: Number(trendTakeProfitAtr),
      },
      execution: {
        timeframe: "1d",
        rebalance,
        run_at: runAt,
      },
      metadata: {
        description,
        schema_version: 1,
      },
      };
    },
    [
      atrMul,
      description,
      fastKind,
      fastWindow,
      maxPositions,
      minStrengthScore,
      positionSizePct,
      rebalance,
      runAt,
      slowKind,
      slowWindow,
      symbols,
      trendStopLossPct,
      trendTakeProfitAtr,
      volMul,
    ]
  );

  const meanReversionParams = useMemo(
    () => {
      const parsedSymbols = symbols
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);

      return {
        signal: {
          min_strength_score: Number(minStrengthScore),
          lookback_window: Number(meanReversionLookback),
          zscore_entry: Number(meanReversionZscoreEntry),
          zscore_exit: Number(meanReversionZscoreExit),
          price_field: "close",
        },
        universe: {
          symbols: parsedSymbols,
          selection_mode: parsedSymbols.length > 0 ? "manual" : "all_common_stock",
        },
        risk: {
          max_positions: Number(maxPositions),
          position_size_pct: Number(positionSizePct),
          stop_loss_pct: Number(meanReversionStopLossPct),
          take_profit_pct: Number(meanReversionTakeProfitPct),
          max_holding_days: Number(meanReversionMaxHoldingDays),
        },
        execution: {
          timeframe: "1d",
          rebalance,
          run_at: runAt,
        },
        metadata: {
          description,
          schema_version: 1,
        },
      };
    },
    [
      description,
      maxPositions,
      meanReversionLookback,
      meanReversionStopLossPct,
      meanReversionTakeProfitPct,
      meanReversionMaxHoldingDays,
      meanReversionZscoreEntry,
      meanReversionZscoreExit,
      minStrengthScore,
      positionSizePct,
      rebalance,
      runAt,
      symbols,
    ]
  );

  const islandReversalParams = useMemo(
    () => {
      const parsedSymbols = symbols
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);

      return {
        signal: {
          min_strength_score: Number(minStrengthScore),
          downtrend_lookback: Number(islandDowntrendLookback),
          downtrend_min_drop_pct: Number(islandDowntrendMinDropPct),
          left_gap_min_pct: Number(leftGapMinPct),
          right_gap_min_pct: Number(rightGapMinPct),
          min_island_bars: Number(minIslandBars),
          max_island_bars: Number(maxIslandBars),
          left_volume_ratio_max: Number(leftVolumeRatioMax),
          right_volume_ratio_min: Number(rightVolumeRatioMin),
          retest_window: Number(retestWindow),
          retest_volume_ratio_max: Number(retestVolumeRatioMax),
          support_tolerance_pct: Number(supportTolerancePct),
        },
        universe: {
          symbols: parsedSymbols,
          selection_mode: parsedSymbols.length > 0 ? "manual" : "all_common_stock",
        },
        risk: {
          max_positions: Number(maxPositions),
          position_size_pct: Number(positionSizePct),
          stop_loss_atr: Number(islandStopLossAtr),
          max_loss_pct: Number(islandMaxLossPct),
          take_profit_atr: Number(islandTakeProfitAtr),
        },
        execution: {
          timeframe: "1d",
          rebalance,
          run_at: runAt,
        },
        metadata: {
          description,
          schema_version: 1,
        },
      };
    },
    [
      description,
      islandDowntrendLookback,
      islandDowntrendMinDropPct,
      islandMaxLossPct,
      islandStopLossAtr,
      islandTakeProfitAtr,
      leftGapMinPct,
      leftVolumeRatioMax,
      maxIslandBars,
      maxPositions,
      minIslandBars,
      minStrengthScore,
      positionSizePct,
      rebalance,
      retestVolumeRatioMax,
      retestWindow,
      rightGapMinPct,
      rightVolumeRatioMin,
      runAt,
      supportTolerancePct,
      symbols,
    ]
  );

  const doubleBottomParams = useMemo(
    () => {
      const parsedSymbols = symbols
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);

      return {
        signal: {
          min_strength_score: Number(minStrengthScore),
          downtrend_lookback: Number(doubleBottomDowntrendLookback),
          downtrend_min_drop_pct: Number(doubleBottomDowntrendMinDropPct),
          downtrend_max_up_day_ratio: Number(doubleBottomDowntrendMaxUpDayRatio),
          downtrend_min_r_squared: Number(doubleBottomDowntrendMinRSquared),
          min_bottom_spacing: Number(minBottomSpacing),
          max_bottom_spacing: Number(maxBottomSpacing),
          left_bottom_before_bars: Number(leftBottomBeforeBars),
          left_bottom_after_bars: Number(leftBottomAfterBars),
          bottom_tolerance_pct: Number(bottomTolerancePct),
          neckline_min_rebound_pct: Number(necklineMinReboundPct),
          rebound_up_day_ratio_min: Number(reboundUpDayRatioMin),
          second_bottom_volume_ratio_max: Number(secondBottomVolumeRatioMax),
          breakout_volume_ratio_min: Number(breakoutVolumeRatioMin),
          max_breakout_bars_after_right_bottom: Number(maxBreakoutBarsAfterRightBottom),
          breakout_buffer_pct: Number(breakoutBufferPct),
          retest_window: Number(doubleBottomRetestWindow),
          retest_volume_ratio_max: Number(doubleBottomRetestVolumeRatioMax),
          support_tolerance_pct: Number(doubleBottomSupportTolerancePct),
        },
        universe: {
          symbols: parsedSymbols,
          selection_mode: parsedSymbols.length > 0 ? "manual" : "all_common_stock",
        },
        risk: {
          max_positions: Number(maxPositions),
          position_size_pct: Number(positionSizePct),
          stop_loss_atr: Number(doubleBottomStopLossAtr),
          max_loss_pct: Number(doubleBottomMaxLossPct),
          take_profit_atr: Number(doubleBottomTakeProfitAtr),
        },
        execution: {
          timeframe: "1d",
          rebalance,
          run_at: runAt,
        },
        metadata: {
          description,
          schema_version: 1,
        },
      };
    },
    [
      bottomTolerancePct,
      breakoutBufferPct,
      breakoutVolumeRatioMin,
      description,
      doubleBottomDowntrendLookback,
      doubleBottomDowntrendMaxUpDayRatio,
      doubleBottomDowntrendMinDropPct,
      doubleBottomDowntrendMinRSquared,
      doubleBottomMaxLossPct,
      doubleBottomRetestVolumeRatioMax,
      doubleBottomRetestWindow,
      doubleBottomStopLossAtr,
      doubleBottomSupportTolerancePct,
      doubleBottomTakeProfitAtr,
      leftBottomAfterBars,
      leftBottomBeforeBars,
      maxBottomSpacing,
      maxBreakoutBarsAfterRightBottom,
      maxPositions,
      minBottomSpacing,
      minStrengthScore,
      necklineMinReboundPct,
      positionSizePct,
      reboundUpDayRatioMin,
      rebalance,
      runAt,
      secondBottomVolumeRatioMax,
      symbols,
    ]
  );

  const supportResistanceParams = useMemo(() => {
    const parsedSymbols = symbols
      .split(",")
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean);
    return {
      signal: {
        min_strength_score: Number(minStrengthScore),
        support_bounce_enabled: supportBounceEnabled,
        resistance_breakout_enabled: resistanceBreakoutEnabled,
        breakout_retest_enabled: breakoutRetestEnabled,
        pivot_left_bars: Number(srPivotLeftBars),
        pivot_right_bars: Number(srPivotRightBars),
        detection_window: Number(srDetectionWindow),
        min_line_pivots: Number(srMinLinePivots),
        min_line_span_sessions: Number(srMinLineSpanSessions),
        line_inlier_tolerance_atr: Number(srLineInlierToleranceAtr),
        max_abs_slope_atr_per_session: Number(srMaxAbsSlopeAtrPerSession),
        zone_half_width_atr: Number(srZoneHalfWidthAtr),
        decay_half_life: Number(srDecayHalfLife),
        bounce_confirmation_atr: Number(srBounceConfirmationAtr),
        breakout_confirmation_atr: Number(srBreakoutConfirmationAtr),
        breakout_volume_ratio_min: Number(srBreakoutVolumeRatioMin),
        retest_window: Number(srRetestWindow),
        retest_volume_ratio_max: Number(srRetestVolumeRatioMax),
        score_outcome_window: Number(srScoreOutcomeWindow),
        score_target_atr: Number(srScoreTargetAtr),
        score_stop_atr: Number(srScoreStopAtr),
      },
      universe: {
        symbols: parsedSymbols,
        selection_mode: parsedSymbols.length ? "manual" : "all_common_stock",
      },
      risk: {
        max_positions: Number(maxPositions),
        position_size_pct: Number(positionSizePct),
        stop_loss_atr: Number(srStopLossAtr),
        max_loss_pct: Number(srMaxLossPct),
        take_profit_atr: Number(srTakeProfitAtr),
        min_reward_risk: Number(srMinRewardRisk),
        max_holding_days: Number(srMaxHoldingDays),
      },
      execution: { timeframe: "1d", rebalance, run_at: runAt },
      metadata: {
        description,
        schema_version: 1,
        algorithm_version: "pivot-slope-regime-v3",
        price_semantics: "forward_adjusted_preferred_unadjusted_fallback",
      },
    };
  }, [
    breakoutRetestEnabled, description, maxPositions, positionSizePct, rebalance,
    resistanceBreakoutEnabled, runAt, srBounceConfirmationAtr, srBreakoutConfirmationAtr,
    srBreakoutVolumeRatioMin, srDecayHalfLife, srDetectionWindow,
    srLineInlierToleranceAtr, srMaxAbsSlopeAtrPerSession, srMaxHoldingDays, srMaxLossPct,
    srMinLinePivots, srMinLineSpanSessions, srMinRewardRisk, minStrengthScore,
    srPivotLeftBars, srPivotRightBars, srRetestVolumeRatioMax, srRetestWindow,
    srScoreOutcomeWindow, srScoreStopAtr, srScoreTargetAtr, srStopLossAtr,
    srTakeProfitAtr, srZoneHalfWidthAtr, supportBounceEnabled, symbols,
  ]);

  const previewPayload = useMemo<StrategyCreate>(() => {
    if (strategyType === "trend") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: trendParams,
      };
    }
    if (strategyType === "mean_reversion") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: meanReversionParams,
      };
    }
    if (strategyType === "island_reversal") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: islandReversalParams,
      };
    }
    if (strategyType === "double_bottom") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: doubleBottomParams,
      };
    }
    if (strategyType === "support_resistance") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: supportResistanceParams,
      };
    }

    try {
      const parsed = JSON.parse(rawJson);
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: strategyType === "momentum_breakout"
          ? withMinStrengthScore(parsed, Number(minStrengthScore))
          : parsed,
      };
    } catch {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: {},
      };
    }
  }, [description, doubleBottomParams, islandReversalParams, meanReversionParams, minStrengthScore, name, rawJson, status, strategyType, supportResistanceParams, trendParams]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setResp(null);
    setLoading(true);

    try {
      if (!name.trim()) {
        throw new Error(isZh ? "策略名不能为空" : "Strategy name cannot be empty");
      }
      if (!(Number(minStrengthScore) >= 0 && Number(minStrengthScore) <= 100)) {
        throw new Error(isZh ? "最低信号强度必须在 0–100 之间" : "Minimum signal strength must be within 0–100");
      }

      let payload: StrategyCreate;
      if (strategyType === "trend") {
        if (!(Number(fastWindow) > 0)) throw new Error(isZh ? "短周期必须 > 0" : "Fast window must be > 0");
        if (!(Number(slowWindow) > 0)) throw new Error(isZh ? "长周期必须 > 0" : "Slow window must be > 0");
        if (fastWindowOptions.length > 0 && !fastWindowOptions.includes(Number(fastWindow))) {
          throw new Error(
            isZh
              ? `当前数据库不支持快线 ${fastKind.toUpperCase()}${fastWindow}，可用周期: ${fastWindowOptions.join(", ")}`
              : `The database does not support fast line ${fastKind.toUpperCase()}${fastWindow}. Supported windows: ${fastWindowOptions.join(", ")}`
          );
        }
        if (slowWindowOptions.length > 0 && !slowWindowOptions.includes(Number(slowWindow))) {
          throw new Error(
            isZh
              ? `当前数据库不支持慢线 ${slowKind.toUpperCase()}${slowWindow}，可用周期: ${slowWindowOptions.join(", ")}`
              : `The database does not support slow line ${slowKind.toUpperCase()}${slowWindow}. Supported windows: ${slowWindowOptions.join(", ")}`
          );
        }
        if (!(Number(volMul) > 0)) throw new Error(isZh ? "成交量过滤倍数必须 > 0" : "Volume multiplier must be > 0");
        if (!(Number(atrMul) > 0)) throw new Error(isZh ? "ATR 乘数必须 > 0" : "ATR multiplier must be > 0");
        if (!(Number(trendStopLossPct) > 0 && Number(trendStopLossPct) <= 1)) {
          throw new Error(isZh ? "固定止损比例必须在 (0, 1] 之间" : "Fixed stop loss pct must be within (0, 1]");
        }
        if (!(Number(trendTakeProfitAtr) > 0)) {
          throw new Error(isZh ? "ATR 止盈倍数必须 > 0" : "ATR take profit must be > 0");
        }
        if (!(Number(maxPositions) > 0)) throw new Error(isZh ? "最大持仓数必须 > 0" : "Max positions must be > 0");
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error(isZh ? "单票仓位比例必须在 (0, 1] 之间" : "Position size percentage must be within (0, 1]");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: trendParams,
        };
      } else if (strategyType === "mean_reversion") {
        if (!MEAN_REVERSION_LOOKBACK_OPTIONS.includes(Number(meanReversionLookback))) {
          throw new Error(
            isZh
              ? `均值回归回看窗口必须是 ${MEAN_REVERSION_LOOKBACK_OPTIONS.join(", ")} 中的一个`
              : `Mean-reversion lookback must be one of ${MEAN_REVERSION_LOOKBACK_OPTIONS.join(", ")}`
          );
        }
        if (!(Number(meanReversionZscoreEntry) > 0)) {
          throw new Error(isZh ? "Z-score 入场阈值必须 > 0" : "Z-score entry threshold must be > 0");
        }
        if (!(Number(meanReversionZscoreExit) > 0)) {
          throw new Error(isZh ? "Z-score 出场阈值必须 > 0" : "Z-score exit threshold must be > 0");
        }
        if (!(Number(maxPositions) > 0)) {
          throw new Error(isZh ? "最大持仓数必须 > 0" : "Max positions must be > 0");
        }
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error(isZh ? "单票仓位比例必须在 (0, 1] 之间" : "Position size percentage must be within (0, 1]");
        }
        if (!(Number(meanReversionStopLossPct) > 0 && Number(meanReversionStopLossPct) <= 1)) {
          throw new Error(isZh ? "止损比例必须在 (0, 1] 之间" : "Stop loss pct must be within (0, 1]");
        }
        if (!(Number(meanReversionTakeProfitPct) > 0 && Number(meanReversionTakeProfitPct) <= 1)) {
          throw new Error(isZh ? "止盈比例必须在 (0, 1] 之间" : "Take profit pct must be within (0, 1]");
        }
        if (!(Number(meanReversionMaxHoldingDays) >= 0 && Number.isInteger(Number(meanReversionMaxHoldingDays)))) {
          throw new Error(isZh ? "最大持仓天数必须是大于等于 0 的整数" : "Max holding days must be a non-negative integer");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: meanReversionParams,
        };
      } else if (strategyType === "island_reversal") {
        if (!(Number(islandDowntrendLookback) > 0)) {
          throw new Error(isZh ? "下跌回看窗口必须 > 0" : "Downtrend lookback must be > 0");
        }
        if (!(Number(islandDowntrendMinDropPct) > 0 && Number(islandDowntrendMinDropPct) <= 1)) {
          throw new Error(isZh ? "最低下跌幅度必须在 (0, 1] 之间" : "Min downtrend drop must be within (0, 1]");
        }
        if (!(Number(leftGapMinPct) > 0 && Number(leftGapMinPct) <= 1)) {
          throw new Error(isZh ? "左侧缺口最小幅度必须在 (0, 1] 之间" : "Left gap min pct must be within (0, 1]");
        }
        if (!(Number(rightGapMinPct) > 0 && Number(rightGapMinPct) <= 1)) {
          throw new Error(isZh ? "右侧缺口最小幅度必须在 (0, 1] 之间" : "Right gap min pct must be within (0, 1]");
        }
        if (!(Number(minIslandBars) > 0)) {
          throw new Error(isZh ? "最少岛区 K 线数必须 > 0" : "Min island bars must be > 0");
        }
        if (!(Number(maxIslandBars) >= Number(minIslandBars))) {
          throw new Error(isZh ? "最多岛区 K 线数不能小于最少岛区 K 线数" : "Max island bars cannot be less than min island bars");
        }
        if (!(Number(leftVolumeRatioMax) > 0)) {
          throw new Error(isZh ? "左侧缩量上限必须 > 0" : "Left volume ratio max must be > 0");
        }
        if (!(Number(rightVolumeRatioMin) > 0)) {
          throw new Error(isZh ? "右侧放量下限必须 > 0" : "Right volume ratio min must be > 0");
        }
        if (!(Number(retestWindow) > 0)) {
          throw new Error(isZh ? "回踩观察窗口必须 > 0" : "Retest window must be > 0");
        }
        if (!(Number(retestVolumeRatioMax) > 0)) {
          throw new Error(isZh ? "回踩缩量上限必须 > 0" : "Retest volume ratio max must be > 0");
        }
        if (!(Number(supportTolerancePct) > 0 && Number(supportTolerancePct) <= 1)) {
          throw new Error(isZh ? "缺口支撑容差必须在 (0, 1] 之间" : "Support tolerance pct must be within (0, 1]");
        }
        if (!(Number(maxPositions) > 0)) {
          throw new Error(isZh ? "最大持仓数必须 > 0" : "Max positions must be > 0");
        }
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error(isZh ? "单票仓位比例必须在 (0, 1] 之间" : "Position size percentage must be within (0, 1]");
        }
        if (!(Number(islandStopLossAtr) > 0)) {
          throw new Error(isZh ? "ATR 止损倍数必须 > 0" : "ATR stop loss must be > 0");
        }
        if (!(Number(islandMaxLossPct) > 0 && Number(islandMaxLossPct) <= 1)) {
          throw new Error(isZh ? "最大亏损强平比例必须在 (0, 1] 之间" : "Max loss pct must be within (0, 1]");
        }
        if (!(Number(islandTakeProfitAtr) > 0)) {
          throw new Error(isZh ? "ATR 止盈倍数必须 > 0" : "ATR take profit must be > 0");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: islandReversalParams,
        };
      } else if (strategyType === "double_bottom") {
        if (!(Number(doubleBottomDowntrendLookback) > 0)) {
          throw new Error(isZh ? "下跌回看窗口必须 > 0" : "Downtrend lookback must be > 0");
        }
        if (!(Number(doubleBottomDowntrendMinDropPct) > 0 && Number(doubleBottomDowntrendMinDropPct) <= 1)) {
          throw new Error(isZh ? "最低下跌幅度必须在 (0, 1] 之间" : "Min downtrend drop must be within (0, 1]");
        }
        if (!(Number(doubleBottomDowntrendMaxUpDayRatio) > 0 && Number(doubleBottomDowntrendMaxUpDayRatio) <= 1)) {
          throw new Error(
            isZh ? "下跌上涨天数占比上限必须在 (0, 1] 之间" : "Downtrend max up-day ratio must be within (0, 1]"
          );
        }
        if (!(Number(doubleBottomDowntrendMinRSquared) > 0 && Number(doubleBottomDowntrendMinRSquared) <= 1)) {
          throw new Error(
            isZh ? "下跌最小线性拟合度必须在 (0, 1] 之间" : "Downtrend min R-squared must be within (0, 1]"
          );
        }
        if (!(Number(minBottomSpacing) > 0)) {
          throw new Error(isZh ? "双底最小间距必须 > 0" : "Min bottom spacing must be > 0");
        }
        if (!(Number(maxBottomSpacing) >= Number(minBottomSpacing))) {
          throw new Error(isZh ? "双底最大间距不能小于最小间距" : "Max bottom spacing cannot be less than min bottom spacing");
        }
        if (!(Number(leftBottomBeforeBars) > 0)) {
          throw new Error(isZh ? "左底前置 K 线数必须 > 0" : "Left-bottom bars before must be > 0");
        }
        if (!(Number(leftBottomAfterBars) > 0)) {
          throw new Error(isZh ? "左底后置 K 线数必须 > 0" : "Left-bottom bars after must be > 0");
        }
        if (!(Number(bottomTolerancePct) > 0 && Number(bottomTolerancePct) <= 1)) {
          throw new Error(isZh ? "双底价差容忍度必须在 (0, 1] 之间" : "Bottom tolerance pct must be within (0, 1]");
        }
        if (!(Number(necklineMinReboundPct) > 0 && Number(necklineMinReboundPct) <= 1)) {
          throw new Error(isZh ? "颈线最小反弹幅度必须在 (0, 1] 之间" : "Neckline min rebound pct must be within (0, 1]");
        }
        if (!(Number(reboundUpDayRatioMin) > 0 && Number(reboundUpDayRatioMin) <= 1)) {
          throw new Error(
            isZh
              ? "左底到右底上涨天数占比必须在 (0, 1] 之间"
              : "Left-to-right-bottom up-day ratio must be within (0, 1]"
          );
        }
        if (!(Number(secondBottomVolumeRatioMax) > 0)) {
          throw new Error(isZh ? "底部缩量上限必须 > 0" : "Bottom volume ratio max must be > 0");
        }
        if (!(Number(breakoutVolumeRatioMin) > 0)) {
          throw new Error(isZh ? "突破放量下限必须 > 0" : "Breakout volume ratio min must be > 0");
        }
        if (!(Number(maxBreakoutBarsAfterRightBottom) > 0)) {
          throw new Error(
            isZh ? "右底后最大等待突破 K 线数必须 > 0" : "Max breakout wait after the right bottom must be > 0"
          );
        }
        if (!(Number(breakoutBufferPct) > 0 && Number(breakoutBufferPct) <= 1)) {
          throw new Error(isZh ? "突破缓冲必须在 (0, 1] 之间" : "Breakout buffer pct must be within (0, 1]");
        }
        if (!(Number(doubleBottomRetestWindow) > 0)) {
          throw new Error(isZh ? "回踩观察窗口必须 > 0" : "Retest window must be > 0");
        }
        if (!(Number(doubleBottomRetestVolumeRatioMax) > 0)) {
          throw new Error(isZh ? "回踩缩量上限必须 > 0" : "Retest volume ratio max must be > 0");
        }
        if (!(Number(doubleBottomSupportTolerancePct) > 0 && Number(doubleBottomSupportTolerancePct) <= 1)) {
          throw new Error(isZh ? "颈线支撑容差必须在 (0, 1] 之间" : "Support tolerance pct must be within (0, 1]");
        }
        if (!(Number(maxPositions) > 0)) {
          throw new Error(isZh ? "最大持仓数必须 > 0" : "Max positions must be > 0");
        }
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error(isZh ? "单票仓位比例必须在 (0, 1] 之间" : "Position size percentage must be within (0, 1]");
        }
        if (!(Number(doubleBottomStopLossAtr) > 0)) {
          throw new Error(isZh ? "ATR 止损倍数必须 > 0" : "ATR stop loss must be > 0");
        }
        if (!(Number(doubleBottomMaxLossPct) > 0 && Number(doubleBottomMaxLossPct) <= 1)) {
          throw new Error(isZh ? "最大亏损强平比例必须在 (0, 1] 之间" : "Max loss pct must be within (0, 1]");
        }
        if (!(Number(doubleBottomTakeProfitAtr) > 0)) {
          throw new Error(isZh ? "ATR 止盈倍数必须 > 0" : "ATR take profit must be > 0");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: doubleBottomParams,
        };
      } else if (strategyType === "support_resistance") {
        if (!supportBounceEnabled && !resistanceBreakoutEnabled && !breakoutRetestEnabled) {
          throw new Error(isZh ? "至少启用一种入场模式" : "Enable at least one entry mode");
        }
        const positiveValues = [
          srPivotLeftBars, srPivotRightBars, srDetectionWindow, srMinLinePivots,
          srMinLineSpanSessions, srLineInlierToleranceAtr, srMaxAbsSlopeAtrPerSession,
          srZoneHalfWidthAtr, srDecayHalfLife,
          srBounceConfirmationAtr, srBreakoutConfirmationAtr, srBreakoutVolumeRatioMin,
          srRetestWindow, srRetestVolumeRatioMax, srScoreOutcomeWindow, srScoreTargetAtr,
          srScoreStopAtr, srStopLossAtr, srTakeProfitAtr, srMinRewardRisk,
          srMaxHoldingDays, maxPositions, positionSizePct,
        ];
        if (positiveValues.some((value) => !(Number(value) > 0))) {
          throw new Error(isZh ? "支撑压力参数必须全部大于 0" : "Support/resistance numeric parameters must be positive");
        }
        if (Number(srDetectionWindow) < Number(srPivotLeftBars) + Number(srPivotRightBars) + 1) {
          throw new Error(isZh ? "检测窗口必须覆盖 Pivot 左右确认区间" : "Detection window must cover the full Pivot confirmation interval");
        }
        if (!(Number(srMaxLossPct) > 0 && Number(srMaxLossPct) <= 1) || Number(positionSizePct) > 1) {
          throw new Error(isZh ? "比例参数必须在 (0, 1] 之间" : "Percentage parameters must be within (0, 1]");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: supportResistanceParams,
        };
      } else {
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: strategyType === "momentum_breakout"
            ? withMinStrengthScore(JSON.parse(rawJson), Number(minStrengthScore))
            : JSON.parse(rawJson),
        };
      }

      const data = isEditMode && initialStrategy
        ? await updateStrategyConfig(initialStrategy.id, {
            description: payload.description?.trim() ?? "",
            status,
            params: payload.params,
          })
        : await createStrategy(
            payload,
            (crypto as any)?.randomUUID?.() || String(Date.now())
          );
      setResp(data);
      if (isEditMode) {
        await router.push(`/strategies/${encodeURIComponent(data.id)}`);
      } else if (typeof window !== "undefined" && window.history.length > 1) {
        router.back();
      } else {
        await router.push("/strategies");
      }
    } catch (error: any) {
      setErr(error?.message || (isZh ? "提交失败" : "Submit failed"));
    } finally {
      setLoading(false);
    }
  };

  const boxStyle: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    margin: "0 0 12px",
  };
  const groupedBoxStyle: React.CSSProperties = {
    ...boxStyle,
    margin: 0,
  };
  const inputStyle: React.CSSProperties = {
    padding: 12,
    border: "1px solid rgba(71, 85, 105, 0.34)",
    borderRadius: 14,
    fontSize: 14,
    background: "rgba(8, 15, 24, 0.82)",
    color: "#e2e8f0",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
  const cardStyle: React.CSSProperties = {
    padding: 22,
    border: "1px solid rgba(71, 85, 105, 0.3)",
    borderRadius: 24,
    background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
    color: "#e2e8f0",
    boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
  };
  const groupedPanelStyle: React.CSSProperties = {
    padding: 18,
    borderRadius: 20,
    border: "1px solid rgba(71, 85, 105, 0.24)",
    background: "linear-gradient(180deg, rgba(15,23,42,0.64), rgba(8,15,24,0.52))",
  };
  const groupedPanelTitleStyle: React.CSSProperties = {
    margin: "0 0 6px",
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "rgba(125, 211, 252, 0.92)",
  };
  const groupedPanelHintStyle: React.CSSProperties = {
    margin: "0 0 14px",
    color: "rgba(148, 163, 184, 0.82)",
    fontSize: 13,
    lineHeight: 1.55,
  };
  const groupedGridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: 12,
  };
  const groupedCompactGridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    gap: 12,
  };
  const srDetectionFields: Array<{ labelZh: string; labelEn: string; value: number; setValue: (value: number) => void }> = [
    { labelZh: "Pivot 左侧 K 线", labelEn: "Pivot Left Bars", value: srPivotLeftBars, setValue: setSrPivotLeftBars },
    { labelZh: "Pivot 右侧确认 K 线", labelEn: "Pivot Right Bars", value: srPivotRightBars, setValue: setSrPivotRightBars },
    { labelZh: "检测窗口", labelEn: "Detection Window", value: srDetectionWindow, setValue: setSrDetectionWindow },
    { labelZh: "最少拟合 Pivot", labelEn: "Minimum Line Pivots", value: srMinLinePivots, setValue: setSrMinLinePivots },
    { labelZh: "最小跨度（交易日）", labelEn: "Minimum Span (Sessions)", value: srMinLineSpanSessions, setValue: setSrMinLineSpanSessions },
    { labelZh: "内点容差（ATR）", labelEn: "Inlier Tolerance (ATR)", value: srLineInlierToleranceAtr, setValue: setSrLineInlierToleranceAtr },
    { labelZh: "最大斜率（ATR/日）", labelEn: "Maximum Slope (ATR/Session)", value: srMaxAbsSlopeAtrPerSession, setValue: setSrMaxAbsSlopeAtrPerSession },
    { labelZh: "区域半宽（ATR）", labelEn: "Zone Half Width (ATR)", value: srZoneHalfWidthAtr, setValue: setSrZoneHalfWidthAtr },
    { labelZh: "衰减半衰期", labelEn: "Decay Half-Life", value: srDecayHalfLife, setValue: setSrDecayHalfLife },
  ];
  const srSignalFields: Array<{ labelZh: string; labelEn: string; value: number; setValue: (value: number) => void }> = [
    { labelZh: "支撑反弹确认（ATR）", labelEn: "Bounce Confirmation (ATR)", value: srBounceConfirmationAtr, setValue: setSrBounceConfirmationAtr },
    { labelZh: "压力突破确认（ATR）", labelEn: "Breakout Confirmation (ATR)", value: srBreakoutConfirmationAtr, setValue: setSrBreakoutConfirmationAtr },
    { labelZh: "突破成交量 / ADV20", labelEn: "Breakout Volume / ADV20", value: srBreakoutVolumeRatioMin, setValue: setSrBreakoutVolumeRatioMin },
    { labelZh: "回踩窗口", labelEn: "Retest Window", value: srRetestWindow, setValue: setSrRetestWindow },
    { labelZh: "回踩量 / 突破量上限", labelEn: "Retest / Breakout Volume Max", value: srRetestVolumeRatioMax, setValue: setSrRetestVolumeRatioMax },
    { labelZh: "评分结果窗口", labelEn: "Scoring Outcome Window", value: srScoreOutcomeWindow, setValue: setSrScoreOutcomeWindow },
    { labelZh: "评分目标（ATR）", labelEn: "Scoring Target (ATR)", value: srScoreTargetAtr, setValue: setSrScoreTargetAtr },
    { labelZh: "评分止损（ATR）", labelEn: "Scoring Stop (ATR)", value: srScoreStopAtr, setValue: setSrScoreStopAtr },
  ];
  const srRiskFields: Array<{ labelZh: string; labelEn: string; value: number; setValue: (value: number) => void }> = [
    { labelZh: "最大持仓数", labelEn: "Max Positions", value: maxPositions, setValue: setMaxPositions },
    { labelZh: "单票仓位比例", labelEn: "Position Size Pct", value: positionSizePct, setValue: setPositionSizePct },
    { labelZh: "ATR 止损", labelEn: "ATR Stop", value: srStopLossAtr, setValue: setSrStopLossAtr },
    { labelZh: "最大亏损比例", labelEn: "Max Loss Pct", value: srMaxLossPct, setValue: setSrMaxLossPct },
    { labelZh: "无压力区目标（ATR）", labelEn: "Fallback Target (ATR)", value: srTakeProfitAtr, setValue: setSrTakeProfitAtr },
    { labelZh: "最低盈亏比", labelEn: "Minimum Reward / Risk", value: srMinRewardRisk, setValue: setSrMinRewardRisk },
    { labelZh: "最长持有交易日", labelEn: "Maximum Holding Days", value: srMaxHoldingDays, setValue: setSrMaxHoldingDays },
  ];

  return (
    <form
      id="strategy-create-form"
      onSubmit={submit}
      style={{
        margin: 0,
        padding: "0 0 48px",
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        color: "#e2e8f0",
      }}
    >
      <div
        style={{
          marginBottom: 16,
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <button
          type="button"
          onClick={() => {
            if (typeof window !== "undefined" && window.history.length > 1) {
              router.back();
            } else {
              router.push("/strategies");
            }
          }}
          style={{
            padding: "10px 14px",
            borderRadius: 14,
            border: "1px solid rgba(71, 85, 105, 0.34)",
            background: "rgba(15, 23, 42, 0.76)",
            color: "#e2e8f0",
            fontWeight: 700,
            cursor: "pointer",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {isZh ? "返回上一页" : "Back"}
        </button>

        {isEditMode ? (
          <button
            type="button"
            onClick={resetToTemplateDefaults}
            disabled={loading || !selectedTemplate}
            style={{
              padding: "10px 14px",
              borderRadius: 14,
              border: "1px solid rgba(125, 211, 252, 0.3)",
              background: "rgba(8, 47, 73, 0.72)",
              color: "#e0f2fe",
              fontWeight: 700,
              cursor: loading || !selectedTemplate ? "not-allowed" : "pointer",
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              opacity: loading || !selectedTemplate ? 0.56 : 1,
            }}
          >
            {isZh ? "重置参数为默认值" : "Reset Params To Defaults"}
          </button>
        ) : null}

        <button
          type="submit"
          disabled={loading}
          style={{
            padding: "10px 14px",
            borderRadius: 14,
            border: "none",
            background: "#0891b2",
            color: "#f8fafc",
            fontWeight: 700,
            cursor: loading ? "progress" : "pointer",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            opacity: loading ? 0.72 : 1,
          }}
        >
          {loading
            ? (isZh ? "提交中…" : "Submitting...")
            : isEditMode
              ? (isZh ? "保存参数修改" : "Save Parameter Changes")
              : isZh
                ? "保存策略"
                : "Save Strategy"}
        </button>
      </div>

      {catalogError && (
        <div style={{ color: "#fda4af", marginBottom: 16 }}>{catalogError}</div>
      )}
      {featureSupportError && (
        <div style={{ color: "#fdba74", marginBottom: 16 }}>
          {featureSupportError}
        </div>
      )}
      {isEditMode ? (
        <div
          style={{
            marginBottom: 16,
            padding: "12px 14px",
            borderRadius: 16,
            border: "1px solid rgba(56, 189, 248, 0.22)",
            background: "rgba(8, 47, 73, 0.32)",
            color: "#bae6fd",
            lineHeight: 1.6,
          }}
        >
          {isZh
            ? "当前页面用于编辑策略参数。策略名称仍可在详情页的改名区域单独维护，策略类型在这里保持锁定。"
            : "This page edits persisted strategy parameters. Rename stays on the detail page, and the strategy type remains locked here."}
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: isEditMode
            ? "minmax(0, 1fr)"
            : "minmax(0, 1.3fr) minmax(320px, 1fr)",
          gap: 20,
          alignItems: "start",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section style={cardStyle}>
            <div style={boxStyle}>
              <label>{isZh ? "策略名" : "Strategy Name"}</label>
              <input
                style={inputStyle}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={isEditMode}
                required
              />
            </div>

            <div style={boxStyle}>
              <label>{isZh ? "策略说明" : "Description"}</label>
              <textarea
                style={{ ...inputStyle, minHeight: 90, resize: "vertical" }}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={
                  isZh
                    ? "例如：收盘后扫描趋势股，第二天开盘前生成调仓建议"
                    : "For example: scan trend candidates after close and generate rebalance suggestions before next open"
                }
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div style={boxStyle}>
                <label>{isZh ? "策略类型" : "Strategy Type"}</label>
                <SelectControl
                  value={strategyType}
                  onValueChange={(value) => {
                    const nextType = value as StrategyType;
                    setStrategyType(nextType);
                    const nextTemplate = catalog.find((item) => item.strategy_type === nextType);
                    if (nextTemplate) applyTemplateDefaults(nextTemplate);
                  }}
                  disabled={isEditMode}
                  options={catalog.map((item) => ({
                    value: item.strategy_type,
                    label: item.label,
                    accent: getStrategyCategoryPresentation(item.strategy_type, locale).accent,
                  }))}
                />
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "状态" : "Status"}</label>
                <SelectControl
                  value={status}
                  onValueChange={(value) => setStatus(value as StrategyStatus)}
                  options={STATUS_OPTIONS}
                />
              </div>
            </div>

            {strategyType !== "custom" ? (
              <div style={{ ...boxStyle, marginTop: 12 }}>
                <label>{isZh ? "最低 BUY 信号强度（0–100）" : "Minimum BUY Signal Strength (0–100)"}</label>
                <input
                  style={inputStyle}
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={minStrengthScore}
                  onChange={(event) => setMinStrengthScore(Number(event.target.value))}
                />
                <small style={{ color: "#94a3b8", lineHeight: 1.5 }}>
                  {isZh
                    ? "低于阈值的 BUY 信号仍会保存用于审计，但不会占用持仓名额。"
                    : "BUY signals below this threshold remain auditable but cannot consume a position slot."}
                </small>
              </div>
            ) : null}
          </section>

          {strategyType === "trend" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "趋势参数" : "Trend Parameters"}</h3>
              <div style={{ marginBottom: 14, color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh ? "当前数据库支持的趋势均线周期:" : "Trend moving-average windows supported by the database:"}
                {" "}
                EMA {featureSupport?.trend.ema_windows.join(", ") || (isZh ? "加载中" : "Loading")}
                {" "}
                | SMA {featureSupport?.trend.sma_windows.join(", ") || (isZh ? "加载中" : "Loading")}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "快线类型" : "Fast Line Type"}</label>
                  <SelectControl
                    value={fastKind}
                    onValueChange={(value) => setFastKind(value as "ema" | "sma")}
                    options={LINE_KIND_OPTIONS}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "快线周期" : "Fast Window"}</label>
                  <SelectControl
                    value={fastWindow}
                    onValueChange={(value) => setFastWindow(Number(value))}
                    options={(fastWindowOptions.length === 0 ? [fastWindow] : fastWindowOptions).map((window) => ({
                      value: window,
                      label: window,
                    }))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "慢线类型" : "Slow Line Type"}</label>
                  <SelectControl
                    value={slowKind}
                    onValueChange={(value) => setSlowKind(value as "ema" | "sma")}
                    options={LINE_KIND_OPTIONS}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "慢线周期" : "Slow Window"}</label>
                  <SelectControl
                    value={slowWindow}
                    onValueChange={(value) => setSlowWindow(Number(value))}
                    options={(slowWindowOptions.length === 0 ? [slowWindow] : slowWindowOptions).map((window) => ({
                      value: window,
                      label: window,
                    }))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "成交量过滤倍数" : "Volume Multiplier"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={volMul}
                    onChange={(e) => setVolMul(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "ATR 止损倍数" : "ATR Stop Loss"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={atrMul}
                    onChange={(e) => setAtrMul(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "固定止损比例" : "Fixed Stop Loss Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={trendStopLossPct}
                    onChange={(e) => setTrendStopLossPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "ATR 止盈倍数" : "ATR Take Profit"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={trendTakeProfitAtr}
                    onChange={(e) => setTrendTakeProfitAtr(Number(e.target.value))}
                  />
                </div>
              </div>

                <div style={boxStyle}>
                  <label>{isZh ? "股票池" : "Universe"}</label>
                  <input
                    style={inputStyle}
                    value={symbols}
                    onChange={(e) => setSymbols(e.target.value)}
                    placeholder={
                      isZh
                        ? "留空则默认绑定全部 common stock；也可以手动输入 AAPL,MSFT,NVDA"
                        : "Leave empty to use all common stocks by default, or enter symbols like AAPL,MSFT,NVDA"
                    }
                  />
                </div>
                <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                  {isZh
                    ? "当前默认行为：如果股票池留空，策略会把 universe 解释为全部 active US common stock。"
                    : "Current default behavior: if universe is left empty, the strategy interprets it as all active US common stocks."}
                </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "最大持仓数" : "Max Positions"}</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={maxPositions}
                    onChange={(e) => setMaxPositions(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "单票仓位比例" : "Position Size Pct"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="0.01"
                    style={inputStyle}
                    value={positionSizePct}
                    onChange={(e) => setPositionSizePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "调仓频率" : "Rebalance Frequency"}</label>
                  <SelectControl
                    value={rebalance}
                    onValueChange={setRebalance}
                    options={REBALANCE_OPTIONS}
                  />
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "运行时机" : "Run Timing"}</label>
                <SelectControl
                  value={runAt}
                  onValueChange={setRunAt}
                  options={RUN_AT_OPTIONS}
                />
              </div>
            </section>
          ) : strategyType === "mean_reversion" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "均值回归参数" : "Mean Reversion Parameters"}</h3>
              <div style={{ marginBottom: 14, color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "止盈止损字段都使用小数表示百分比，例如 0.1 = 10%。当前 z-score 窗口只支持 5 / 10 / 20。"
                  : "Stop-loss and take-profit fields use decimal percentages, for example 0.1 = 10%. Supported z-score windows are 5 / 10 / 20."}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "回看窗口" : "Lookback Window"}</label>
                  <SelectControl
                    value={meanReversionLookback}
                    onValueChange={(value) => setMeanReversionLookback(Number(value))}
                    options={MEAN_REVERSION_LOOKBACK_OPTIONS.map((window) => ({ value: window, label: window }))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "Z-score 入场阈值" : "Z-score Entry"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={meanReversionZscoreEntry}
                    onChange={(e) => setMeanReversionZscoreEntry(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "Z-score 出场阈值" : "Z-score Exit"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={meanReversionZscoreExit}
                    onChange={(e) => setMeanReversionZscoreExit(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "止损比例" : "Stop Loss Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={meanReversionStopLossPct}
                    onChange={(e) => setMeanReversionStopLossPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "止盈比例" : "Take Profit Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={meanReversionTakeProfitPct}
                    onChange={(e) => setMeanReversionTakeProfitPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最大持仓天数" : "Max Holding Days"}</label>
                  <input
                    type="number"
                    min={0}
                    step={1}
                    style={inputStyle}
                    value={meanReversionMaxHoldingDays}
                    onChange={(e) => setMeanReversionMaxHoldingDays(Number(e.target.value))}
                  />
                  <div style={{ color: "rgba(148, 163, 184, 0.82)", fontSize: 12, marginTop: 6 }}>
                    {isZh ? "填 0 表示禁用这条时间止盈/止损规则。" : "Use 0 to disable the time-based exit rule."}
                  </div>
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "股票池" : "Universe"}</label>
                <input
                  style={inputStyle}
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                  placeholder={
                    isZh
                      ? "留空则默认绑定全部 common stock；也可以手动输入 AAPL,MSFT,NVDA"
                      : "Leave empty to use all common stocks by default, or enter symbols like AAPL,MSFT,NVDA"
                  }
                />
              </div>
              <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "股票池留空时，会在全部 common stock 中扫描均值回归机会。"
                  : "If the universe is empty, the strategy scans for mean-reversion setups across all common stocks."}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "最大持仓数" : "Max Positions"}</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={maxPositions}
                    onChange={(e) => setMaxPositions(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "单票仓位比例" : "Position Size Pct"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="0.01"
                    style={inputStyle}
                    value={positionSizePct}
                    onChange={(e) => setPositionSizePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "调仓频率" : "Rebalance Frequency"}</label>
                  <SelectControl
                    value={rebalance}
                    onValueChange={setRebalance}
                    options={REBALANCE_OPTIONS}
                  />
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "运行时机" : "Run Timing"}</label>
                <SelectControl
                  value={runAt}
                  onValueChange={setRunAt}
                  options={RUN_AT_OPTIONS}
                />
              </div>
            </section>
          ) : strategyType === "island_reversal" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "岛形反转参数" : "Island Reversal Parameters"}</h3>
              <div style={{ marginBottom: 14, color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "涉及幅度的字段都使用小数表示百分比，例如 0.02 = 2%，0.15 = 15%。"
                  : "Percent-style thresholds use decimals, for example 0.02 = 2% and 0.15 = 15%."}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "下跌回看窗口" : "Downtrend Lookback"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={islandDowntrendLookback}
                    onChange={(e) => setIslandDowntrendLookback(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最低下跌幅度" : "Min Downtrend Drop"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={islandDowntrendMinDropPct}
                    onChange={(e) => setIslandDowntrendMinDropPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "左侧缺口最小幅度" : "Left Gap Min Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={leftGapMinPct}
                    onChange={(e) => setLeftGapMinPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "右侧缺口最小幅度" : "Right Gap Min Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={rightGapMinPct}
                    onChange={(e) => setRightGapMinPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最少岛区 K 线数" : "Min Island Bars"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={minIslandBars}
                    onChange={(e) => setMinIslandBars(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最多岛区 K 线数" : "Max Island Bars"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={maxIslandBars}
                    onChange={(e) => setMaxIslandBars(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "左侧缩量上限" : "Left Volume Ratio Max"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={leftVolumeRatioMax}
                    onChange={(e) => setLeftVolumeRatioMax(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "右侧放量下限" : "Right Volume Ratio Min"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={rightVolumeRatioMin}
                    onChange={(e) => setRightVolumeRatioMin(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "回踩观察窗口" : "Retest Window"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={retestWindow}
                    onChange={(e) => setRetestWindow(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "回踩缩量上限" : "Retest Volume Ratio Max"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={retestVolumeRatioMax}
                    onChange={(e) => setRetestVolumeRatioMax(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "缺口容差" : "Gap Tolerance Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={supportTolerancePct}
                    onChange={(e) => setSupportTolerancePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "ATR 止损倍数" : "ATR Stop Loss"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={islandStopLossAtr}
                    onChange={(e) => setIslandStopLossAtr(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最大亏损强平比例" : "Max Loss Exit Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={islandMaxLossPct}
                    onChange={(e) => setIslandMaxLossPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "ATR 止盈倍数" : "ATR Take Profit"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="any"
                    style={inputStyle}
                    value={islandTakeProfitAtr}
                    onChange={(e) => setIslandTakeProfitAtr(Number(e.target.value))}
                  />
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "股票池" : "Universe"}</label>
                <input
                  style={inputStyle}
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                  placeholder={
                    isZh
                      ? "留空则默认绑定全部 common stock；也可以手动输入 AAPL,MSFT,NVDA"
                      : "Leave empty to use all common stocks by default, or enter symbols like AAPL,MSFT,NVDA"
                  }
                />
              </div>
              <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "股票池留空时，会在全部 common stock 中扫描岛形反转形态。"
                  : "If the universe is empty, the strategy scans for island reversal setups across all common stocks."}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "最大持仓数" : "Max Positions"}</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={maxPositions}
                    onChange={(e) => setMaxPositions(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "单票仓位比例" : "Position Size Pct"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="0.01"
                    style={inputStyle}
                    value={positionSizePct}
                    onChange={(e) => setPositionSizePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "调仓频率" : "Rebalance Frequency"}</label>
                  <SelectControl
                    value={rebalance}
                    onValueChange={setRebalance}
                    options={REBALANCE_OPTIONS}
                  />
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "运行时机" : "Run Timing"}</label>
                <SelectControl
                  value={runAt}
                  onValueChange={setRunAt}
                  options={RUN_AT_OPTIONS}
                />
              </div>
            </section>
          ) : strategyType === "double_bottom" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "双底形态参数" : "Double Bottom Parameters"}</h3>
              <div style={{ marginBottom: 14, color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "这是保守版双底：先用放量突破确认形态，再等待后续缩量回踩颈线时买入。左底前会额外检查下跌是否足够平滑。所有百分比字段均使用小数表示，例如 0.03 = 3%。"
                  : "This is the conservative double-bottom setup: it uses the breakout to confirm the pattern, then waits for a later low-volume retest of the neckline before buying. The left bottom also requires a smooth downtrend. Percent-style fields use decimals, for example 0.03 = 3%."}
              </div>

              <div style={{ display: "grid", gap: 14 }}>
                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "趋势背景" : "Downtrend Context"}</div>
                  <p style={groupedPanelHintStyle}>
                    {isZh
                      ? "定义左底出现前需要有多长、多深、且多平滑的下跌。"
                      : "Defines how long, how deep, and how smooth the pre-bottom decline must be."}
                  </p>
                  <div style={groupedGridStyle}>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "下跌回看窗口" : "Downtrend Lookback"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={doubleBottomDowntrendLookback}
                        onChange={(e) => setDoubleBottomDowntrendLookback(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "最低下跌幅度" : "Min Downtrend Drop"}</label>
                      <input
                        type="number"
                        min={0.01}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomDowntrendMinDropPct}
                        onChange={(e) => setDoubleBottomDowntrendMinDropPct(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "上涨天数占比上限" : "Downtrend Max Up-Day Ratio"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomDowntrendMaxUpDayRatio}
                        onChange={(e) => setDoubleBottomDowntrendMaxUpDayRatio(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "下跌最小线性拟合度" : "Downtrend Min R-Squared"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomDowntrendMinRSquared}
                        onChange={(e) => setDoubleBottomDowntrendMinRSquared(Number(e.target.value))}
                      />
                    </div>
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "形态结构" : "Pattern Structure"}</div>
                  <p style={groupedPanelHintStyle}>
                    {isZh
                      ? "控制左右底之间的距离、局部低点确认方式，以及中间反弹结构。"
                      : "Controls bottom spacing, local-minimum confirmation, and the rebound structure between the two lows."}
                  </p>
                  <div style={groupedGridStyle}>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "双底最小间距" : "Min Bottom Spacing"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={minBottomSpacing}
                        onChange={(e) => setMinBottomSpacing(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "双底最大间距" : "Max Bottom Spacing"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={maxBottomSpacing}
                        onChange={(e) => setMaxBottomSpacing(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "左底前置 K 线数" : "Left-Bottom Bars Before"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={leftBottomBeforeBars}
                        onChange={(e) => setLeftBottomBeforeBars(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "左底后置 K 线数" : "Left-Bottom Bars After"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={leftBottomAfterBars}
                        onChange={(e) => setLeftBottomAfterBars(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "双底容差" : "Bottom Tolerance Pct"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={bottomTolerancePct}
                        onChange={(e) => setBottomTolerancePct(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "颈线最小反弹幅度" : "Neckline Min Rebound"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={necklineMinReboundPct}
                        onChange={(e) => setNecklineMinReboundPct(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "左底到右底上涨天数占比下限" : "Left-to-Right Up-Day Ratio Min"}</label>
                      <input
                        type="number"
                        min={0.01}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={reboundUpDayRatioMin}
                        onChange={(e) => setReboundUpDayRatioMin(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "底部缩量上限" : "Bottom Volume Max"}</label>
                      <input
                        type="number"
                        min={0.1}
                        step="any"
                        style={inputStyle}
                        value={secondBottomVolumeRatioMax}
                        onChange={(e) => setSecondBottomVolumeRatioMax(Number(e.target.value))}
                      />
                    </div>
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "突破与回踩" : "Breakout And Retest"}</div>
                  <p style={groupedPanelHintStyle}>
                    {isZh
                      ? "管理右底之后的突破等待、放量确认条件，以及突破后回踩买点。"
                      : "Controls breakout timing after the right bottom, volume confirmation, and the post-breakout retest entry."}
                  </p>
                  <div style={groupedGridStyle}>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "突破放量下限" : "Breakout Volume Min"}</label>
                      <input
                        type="number"
                        min={0.1}
                        step="any"
                        style={inputStyle}
                        value={breakoutVolumeRatioMin}
                        onChange={(e) => setBreakoutVolumeRatioMin(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "右底后最大等待突破 K 线数" : "Max Bars To Break After Right Bottom"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={maxBreakoutBarsAfterRightBottom}
                        onChange={(e) => setMaxBreakoutBarsAfterRightBottom(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "突破缓冲" : "Breakout Buffer Pct"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={breakoutBufferPct}
                        onChange={(e) => setBreakoutBufferPct(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "回踩观察窗口" : "Retest Window"}</label>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        style={inputStyle}
                        value={doubleBottomRetestWindow}
                        onChange={(e) => setDoubleBottomRetestWindow(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "回踩缩量上限" : "Retest Volume Ratio Max"}</label>
                      <input
                        type="number"
                        min={0.1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomRetestVolumeRatioMax}
                        onChange={(e) => setDoubleBottomRetestVolumeRatioMax(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "颈线支撑容差" : "Support Tolerance Pct"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomSupportTolerancePct}
                        onChange={(e) => setDoubleBottomSupportTolerancePct(Number(e.target.value))}
                      />
                    </div>
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "风险与执行" : "Risk And Execution"}</div>
                  <p style={groupedPanelHintStyle}>
                    {isZh
                      ? "统一管理仓位规模、止损止盈，以及运行频率。"
                      : "Groups position sizing, exit thresholds, and execution cadence in one place."}
                  </p>
                  <div style={groupedCompactGridStyle}>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "最大持仓数" : "Max Positions"}</label>
                      <input
                        type="number"
                        min={1}
                        style={inputStyle}
                        value={maxPositions}
                        onChange={(e) => setMaxPositions(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "单票仓位比例" : "Position Size Pct"}</label>
                      <input
                        type="number"
                        min={0.01}
                        max={1}
                        step="0.01"
                        style={inputStyle}
                        value={positionSizePct}
                        onChange={(e) => setPositionSizePct(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "ATR 止损倍数" : "ATR Stop Loss"}</label>
                      <input
                        type="number"
                        min={0.1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomStopLossAtr}
                        onChange={(e) => setDoubleBottomStopLossAtr(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "最大亏损强平比例" : "Max Loss Exit Pct"}</label>
                      <input
                        type="number"
                        min={0.001}
                        max={1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomMaxLossPct}
                        onChange={(e) => setDoubleBottomMaxLossPct(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "ATR 止盈倍数" : "ATR Take Profit"}</label>
                      <input
                        type="number"
                        min={0.1}
                        step="any"
                        style={inputStyle}
                        value={doubleBottomTakeProfitAtr}
                        onChange={(e) => setDoubleBottomTakeProfitAtr(Number(e.target.value))}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "调仓频率" : "Rebalance Frequency"}</label>
                      <SelectControl
                        value={rebalance}
                        onValueChange={setRebalance}
                        options={REBALANCE_OPTIONS}
                      />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "运行时机" : "Run Timing"}</label>
                      <SelectControl
                        value={runAt}
                        onValueChange={setRunAt}
                        options={RUN_AT_OPTIONS}
                      />
                    </div>
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "股票池" : "Universe"}</div>
                  <p style={groupedPanelHintStyle}>
                    {isZh
                      ? "限制双底扫描范围；留空时默认遍历全部 common stock。"
                      : "Narrows the scan universe for double-bottom setups; leave it empty to scan all common stocks."}
                  </p>
                  <div style={groupedBoxStyle}>
                    <label>{isZh ? "股票池" : "Universe"}</label>
                    <input
                      style={inputStyle}
                      value={symbols}
                      onChange={(e) => setSymbols(e.target.value)}
                      placeholder={
                        isZh
                          ? "留空则默认绑定全部 common stock；也可以手动输入 AAPL,MSFT,NVDA"
                          : "Leave empty to use all common stocks by default, or enter symbols like AAPL,MSFT,NVDA"
                      }
                    />
                  </div>
                </div>
              </div>
            </section>
          ) : strategyType === "support_resistance" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "支撑 / 压力区域参数" : "Support / Resistance Zone Parameters"}</h3>
              <p style={{ marginTop: 0, color: "rgba(148, 163, 184, 0.88)", lineHeight: 1.6 }}>
                {isZh
                  ? "区域由已确认 Pivot 与 ATR 聚类生成；T 日仅使用 T-1 收盘后冻结的区域。日线信号在收盘产生，并在下一有效交易日开盘成交。"
                  : "Zones use confirmed Pivots and ATR clustering. Session T only sees zones frozen after T-1; close signals fill at the next valid session open."}
              </p>
              <div style={{ display: "grid", gap: 14 }}>
                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "固定市场状态规则" : "Fixed Market-Regime Policy"}</div>
                  <p style={groupedPanelHintStyle}>
                    {isZh
                      ? "v3 将每个交易日唯一归入上行、下行、震荡或过渡区间。上行允许三种入场，震荡仅允许支撑反弹，下行与过渡暂停买入；确认下行会触发下一交易日开盘退出。该规则没有额外可调参数。"
                      : "v3 assigns every session to exactly one uptrend, downtrend, range, or transition interval. Uptrends allow all three entries, ranges allow support bounces only, and downtrend/transition states pause buys; a confirmed downtrend exits at the next session open. This policy has no extra tunable parameters."}
                  </p>
                </div>
                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "入场模式" : "Entry Modes"}</div>
                  <p style={groupedPanelHintStyle}>{isZh ? "至少开启一种；同日多模式命中时只生成一个最高评分 BUY。" : "Enable at least one. Multiple same-day matches persist as events but emit one highest-scored BUY."}</p>
                  <div style={groupedCompactGridStyle}>
                    {[
                      { label: isZh ? "支撑反弹" : "Support Bounce", value: supportBounceEnabled, setValue: setSupportBounceEnabled },
                      { label: isZh ? "压力突破审计（不交易）" : "Breakout Audit (No Trade)", value: resistanceBreakoutEnabled, setValue: setResistanceBreakoutEnabled },
                      { label: isZh ? "突破回踩" : "Breakout Retest", value: breakoutRetestEnabled, setValue: setBreakoutRetestEnabled },
                    ].map((item) => (
                      <label key={item.label} style={{ ...groupedBoxStyle, flexDirection: "row", alignItems: "center" }}>
                        <input type="checkbox" checked={item.value} onChange={(event) => item.setValue(event.target.checked)} />
                        {item.label}
                      </label>
                    ))}
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "区域检测" : "Zone Detection"}</div>
                  <div style={groupedGridStyle}>
                    {srDetectionFields.map((field) => (
                      <div key={field.labelEn} style={groupedBoxStyle}>
                        <label>{isZh ? field.labelZh : field.labelEn}</label>
                        <input type="number" min={0.01} step="any" style={inputStyle} value={field.value} onChange={(event) => field.setValue(Number(event.target.value))} />
                      </div>
                    ))}
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "确认与向前评分" : "Confirmation And Forward Scoring"}</div>
                  <p style={groupedPanelHintStyle}>{isZh ? "Signal strength 决定同日候选排序；Beta 后验仅作为形态审计证据，并只使用当前日期以前已结束的同类事件。" : "Signal strength ranks same-day candidates. The Beta posterior is audit evidence only and uses same-mode outcomes resolved before the current date."}</p>
                  <div style={groupedGridStyle}>
                    {srSignalFields.map((field) => (
                      <div key={field.labelEn} style={groupedBoxStyle}>
                        <label>{isZh ? field.labelZh : field.labelEn}</label>
                        <input type="number" min={0.01} step="any" style={inputStyle} value={field.value} onChange={(event) => field.setValue(Number(event.target.value))} />
                      </div>
                    ))}
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "风险与执行" : "Risk And Execution"}</div>
                  <div style={groupedGridStyle}>
                    {srRiskFields.map((field) => (
                      <div key={field.labelEn} style={groupedBoxStyle}>
                        <label>{isZh ? field.labelZh : field.labelEn}</label>
                        <input type="number" min={0.01} step="any" style={inputStyle} value={field.value} onChange={(event) => field.setValue(Number(event.target.value))} />
                      </div>
                    ))}
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "调仓频率" : "Rebalance"}</label>
                      <SelectControl value={rebalance} onValueChange={setRebalance} options={[{ value: "daily", label: "daily" }]} />
                    </div>
                    <div style={groupedBoxStyle}>
                      <label>{isZh ? "运行时机" : "Run Timing"}</label>
                      <SelectControl value={runAt} onValueChange={setRunAt} options={[{ value: "close", label: "close" }]} />
                    </div>
                  </div>
                </div>

                <div style={groupedPanelStyle}>
                  <div style={groupedPanelTitleStyle}>{isZh ? "股票池" : "Universe"}</div>
                  <input style={inputStyle} value={symbols} onChange={(event) => setSymbols(event.target.value)} placeholder={isZh ? "留空使用全部 common stock，或输入 AAPL,MSFT" : "Leave empty for all common stocks, or enter AAPL,MSFT"} />
                </div>
              </div>
            </section>
          ) : (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "高级 JSON 配置" : "Advanced JSON Config"}</h3>
              <p style={{ marginTop: 0, color: "rgba(148, 163, 184, 0.88)", lineHeight: 1.6 }}>
                {selectedTemplate?.engine_ready
                  ? (
                    isZh
                      ? "这个策略类型已经接入后端执行器。这里直接编辑 JSON 参数模板，保存后即可用于回测和 paper trading。"
                      : "This strategy type is already wired into the backend evaluator. Edit the JSON template here and save it for backtesting and paper trading."
                  )
                  : (
                    isZh
                      ? "该策略类型目前先以 JSON/DSL 形式落库，当前后端支持存储和查询，等专门 evaluator 接好后即可执行。"
                      : "This strategy type is currently stored as JSON/DSL. The backend already supports persistence and retrieval, and it can execute once a dedicated evaluator is wired in."
                  )}
              </p>
              <textarea
                style={{
                  ...inputStyle,
                  minHeight: 320,
                  resize: "vertical",
                  fontFamily: "SFMono-Regular, Consolas, monospace",
                }}
                value={rawJson}
                onChange={(e) => setRawJson(e.target.value)}
              />
            </section>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              padding: "12px 18px",
              borderRadius: 12,
              border: 0,
              background: "#0f766e",
              color: "#fff",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {loading ? (isZh ? "提交中…" : "Submitting...") : isZh ? "保存策略" : "Save Strategy"}
          </button>
        </div>

        {!isEditMode || err || resp ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {!isEditMode ? (
              <section style={cardStyle}>
                <h3 style={{ marginTop: 0 }}>{isZh ? "提交预览" : "Submit Preview"}</h3>
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontSize: 13,
                    lineHeight: 1.6,
                    color: "#cbd5e1",
                  }}
                >
                  {JSON.stringify(previewPayload, null, 2)}
                </pre>
              </section>
            ) : null}

            {err && <div style={{ color: "#fda4af" }}>{err}</div>}

            {resp && (
              <section style={cardStyle}>
                <h3 style={{ marginTop: 0 }}>{isZh ? "后端响应" : "Backend Response"}</h3>
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontSize: 13,
                    lineHeight: 1.6,
                    color: "#cbd5e1",
                  }}
                >
                  {JSON.stringify(resp, null, 2)}
                </pre>
              </section>
            )}
          </div>
        ) : null}
      </div>
    </form>
  );
}
