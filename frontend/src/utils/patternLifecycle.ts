import type { ChartOverlayMarker } from "@/components/charts/chartModels";
import type { BacktestSignalOut } from "@/types/backtest";
import type { CandleBarOut } from "@/types/quote";
import { toTradeDateKey } from "@/utils/tradingDate";

type PatternType =
  | "island_reversal"
  | "double_bottom"
  | "head_shoulders_bottom"
  | "rounded_bottom"
  | "v_reversal";

type SetupRecord = Record<string, unknown>;

export interface LifecyclePatternSelection {
  patternType: PatternType;
  setupId: string;
  latestSignal: BacktestSignalOut;
  stageSignals: Map<number, BacktestSignalOut>;
  exitSignal: BacktestSignalOut | null;
}

const PATTERN_TYPES = new Set<PatternType>([
  "island_reversal",
  "double_bottom",
  "head_shoulders_bottom",
  "rounded_bottom",
  "v_reversal",
]);

function objectValue(value: unknown): SetupRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as SetupRecord
    : null;
}

function setupOf(signal: BacktestSignalOut | null | undefined): SetupRecord | null {
  return objectValue(objectValue(signal?.features)?.setup);
}

function textValue(record: SetupRecord, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberValue(record: SetupRecord, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function textList(record: SetupRecord, key: string): string[] {
  const value = record[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function stageIndex(signal: BacktestSignalOut): number {
  return numberValue(setupOf(signal) || {}, "stage_index") ?? 0;
}

export function earliestPatternAnchorDate(signal: BacktestSignalOut | null): string | null {
  const setup = setupOf(signal);
  const anchors = objectValue(setup?.anchors);
  if (!setup || !anchors) return null;

  const dates = Object.values(anchors).flatMap((value) => {
    if (typeof value === "string") return [toTradeDateKey(value)].filter(Boolean) as string[];
    if (!Array.isArray(value)) return [];
    return value.map((item) => typeof item === "string" ? toTradeDateKey(item) : null)
      .filter((item): item is string => item != null);
  });
  return dates.sort()[0] || null;
}

export function selectLifecyclePattern(
  signals: BacktestSignalOut[],
  initialSignal: BacktestSignalOut | null,
  symbol: string,
  endTs: string | null,
): LifecyclePatternSelection | null {
  const initialSetup = setupOf(initialSignal);
  if (!initialSignal || !initialSetup) return null;
  const patternType = textValue(initialSetup, "pattern_type") as PatternType | null;
  const setupId = textValue(initialSetup, "setup_id");
  if (!patternType || !PATTERN_TYPES.has(patternType) || !setupId) return null;

  const startDate = toTradeDateKey(initialSignal.ts);
  const endDate = toTradeDateKey(endTs);
  const normalizedSymbol = symbol.toUpperCase();
  const matching = signals.filter((signal) => {
    const setup = setupOf(signal);
    const date = toTradeDateKey(signal.ts);
    return (signal.signal === "BUY" || signal.signal === "SELL")
      && signal.symbol.toUpperCase() === normalizedSymbol
      && textValue(setup || {}, "setup_id") === setupId
      && (!startDate || !date || date >= startDate)
      && (!endDate || !date || date <= endDate);
  });
  if (!matching.includes(initialSignal)) matching.push(initialSignal);

  const exitSignal = matching.filter((signal) => signal.signal === "SELL")
    .sort((left, right) => (toTradeDateKey(left.ts) || "").localeCompare(toTradeDateKey(right.ts) || "")).at(-1) || null;
  const buys = matching.filter((signal) => signal.signal === "BUY");
  buys.sort((left, right) =>
    stageIndex(left) - stageIndex(right)
    || (toTradeDateKey(left.ts) || "").localeCompare(toTradeDateKey(right.ts) || ""));
  const stageSignals = new Map<number, BacktestSignalOut>();
  buys.forEach((signal) => stageSignals.set(stageIndex(signal), signal));
  return {
    patternType,
    setupId,
    latestSignal: buys[buys.length - 1],
    exitSignal,
    stageSignals,
  };
}

export function buildPatternLifecycleMarkers(
  selection: LifecyclePatternSelection | null,
  bars: CandleBarOut[],
  locale: string,
): ChartOverlayMarker[] {
  if (!selection) return [];
  const setup = setupOf(selection.latestSignal);
  const anchors = objectValue(setup?.anchors);
  if (!setup || !anchors) return [];
  const isZh = locale === "zh-CN";
  const barsByDate = new Map(bars.map((bar) => [bar.trade_date, bar]));
  const markers: ChartOverlayMarker[] = [];
  const strategyLabels: Record<PatternType, [string, string]> = {
    island_reversal: ["岛形反转", "Island Reversal"],
    double_bottom: ["双底", "Double Bottom"],
    head_shoulders_bottom: ["头肩底", "Head-and-Shoulders Bottom"],
    rounded_bottom: ["圆弧底", "Rounded Bottom"],
    v_reversal: ["V 型反转", "V Reversal"],
  };
  const strategyLabel = strategyLabels[selection.patternType][isZh ? 0 : 1];
  const priceText = (price: number | null) => price == null
    ? null
    : price.toLocaleString(locale, { minimumFractionDigits: price >= 100 ? 0 : 2, maximumFractionDigits: 2 });
  const add = (
    key: string,
    date: string | null,
    price: number | null,
    tone: string,
    labels: [string, string],
  ) => {
    if (!date) return;
    const label = labels[isZh ? 0 : 1];
    markers.push({
      key: `pattern-${selection.setupId}-${key}`,
      label,
      date,
      price,
      tone,
      groupKey: selection.setupId,
      showText: true,
      description: [strategyLabel, label, date, priceText(price)].filter(Boolean).join(" · "),
    });
  };
  const anchorDate = (key: string) => textValue(anchors, key);
  const barLow = (date: string | null) => date ? barsByDate.get(date)?.low ?? null : null;
  const barHigh = (date: string | null) => date ? barsByDate.get(date)?.high ?? null : null;
  const signalDate = (stage: number) => toTradeDateKey(selection.stageSignals.get(stage)?.ts);
  const signalClose = (stage: number) => numberValue(objectValue(selection.stageSignals.get(stage)?.features) || {}, "close");

  if (selection.patternType === "island_reversal") {
    const leftDate = anchorDate("left_gap_trade_date");
    const breakoutDate = anchorDate("breakout_trade_date");
    const knownThroughDate = breakoutDate || toTradeDateKey(selection.latestSignal.ts);
    const islandBars = bars.filter((bar) =>
      (!leftDate || bar.trade_date >= leftDate)
      && (!knownThroughDate || bar.trade_date <= knownThroughDate)
      && (!breakoutDate || bar.trade_date < breakoutDate));
    const bottom = islandBars.reduce<CandleBarOut | null>(
      (current, bar) => current == null || bar.low < current.low ? bar : current,
      null,
    );
    add("island-bottom", bottom?.trade_date || leftDate, bottom?.low ?? numberValue(setup, "island_low"), "pattern_bottom", ["岛底", "Island Bottom"]);
    add("reversal", breakoutDate, numberValue(setup, "breakout_gap_low") ?? barLow(breakoutDate), "reversal", ["反转确认", "Reversal Confirmed"]);
  } else if (selection.patternType === "double_bottom") {
    const leftDate = textValue(setup, "left_bottom_trade_date");
    const rightDate = textValue(setup, "right_bottom_trade_date");
    const necklineDate = textValue(setup, "neckline_trade_date");
    const breakoutDate = textValue(setup, "breakout_trade_date") || signalDate(3);
    add("left-bottom", leftDate, numberValue(setup, "left_bottom_low") ?? barLow(leftDate), "pattern_bottom", ["左底", "Left Bottom"]);
    add("right-bottom", rightDate, numberValue(setup, "right_bottom_low") ?? barLow(rightDate), "pattern_bottom", ["右底", "Right Bottom"]);
    add("neckline", necklineDate, numberValue(setup, "neckline_price") ?? barHigh(necklineDate), "neckline", ["颈线", "Neckline"]);
    add("reversal", breakoutDate, numberValue(setup, "breakout_close") ?? signalClose(3), "reversal", ["反转确认", "Reversal Confirmed"]);
  } else if (selection.patternType === "head_shoulders_bottom") {
    const leftDate = anchorDate("left_shoulder");
    const headDate = anchorDate("head");
    const rightDate = anchorDate("right_shoulder");
    add("platform-start", anchorDate("platform_start"), numberValue(setup, "platform_low"), "shoulder", ["左肩平台下沿", "Left Platform Floor"]);
    add("platform-end", anchorDate("platform_end"), numberValue(setup, "platform_high"), "shoulder", ["左肩平台上沿", "Left Platform Ceiling"]);
    add("left-shoulder", leftDate, numberValue(setup, "left_shoulder_low") ?? barLow(leftDate), "shoulder", ["左肩", "Left Shoulder"]);
    add("head", headDate, numberValue(setup, "head_low") ?? barLow(headDate), "pattern_bottom", ["头部低点", "Head Low"]);
    add("right-shoulder", rightDate, barLow(rightDate), "shoulder", ["右肩", "Right Shoulder"]);
    add("reversal", signalDate(3), signalClose(3) ?? numberValue(setup, "neckline_price"), "reversal", ["颈线突破", "Neckline Breakout"]);
  } else if (selection.patternType === "rounded_bottom") {
    const bottomDate = anchorDate("bottom");
    add("bottom", bottomDate, barLow(bottomDate) ?? numberValue(setup, "invalidation_price"), "pattern_bottom", ["圆弧底部", "Bowl Bottom"]);
    textList(anchors, "pullbacks").forEach((date, index) => {
      add(`pullback-${index + 1}`, date, barLow(date), "pullback", [
        `右侧回踩 ${index + 1}`,
        `Right Pullback ${index + 1}`,
      ]);
    });
    textList(anchors, "rebound_peaks").forEach((date, index) => {
      add(`rebound-peak-${index + 1}`, date, barHigh(date), "neckline", [`右侧反弹高点 ${index + 1}`, `Right Rebound Peak ${index + 1}`]);
    });
    add("reversal", signalDate(3), signalClose(3) ?? numberValue(setup, "rim_price"), "reversal", ["碗口突破", "Rim Breakout"]);
  } else {
    const pivotDate = anchorDate("pivot");
    const breakoutDate = anchorDate("breakout");
    add("pivot", pivotDate, numberValue(setup, "pivot_low") ?? barLow(pivotDate), "pattern_bottom", ["V 型转折", "V Pivot"]);
    add("volume-reversal", anchorDate("reversal"), signalClose(1), "reversal", ["放量转折确认", "Volume Reversal Confirmed"]);
    add("range-start", anchorDate("consolidation_start"), numberValue(setup, "consolidation_low"), "neckline", ["整理区下沿", "Range Floor"]);
    add("range-end", anchorDate("consolidation_end"), numberValue(setup, "consolidation_top"), "neckline", ["整理区上沿", "Range Ceiling"]);
    add("continuation", signalDate(2), signalClose(2), "pullback", ["反转延续", "Reversal Continuation"]);
    add("breakout", breakoutDate, numberValue(setup, "consolidation_top") ?? barHigh(breakoutDate), "neckline", ["整理区突破", "Range Breakout"]);
    add("reversal", signalDate(3), signalClose(3), "reversal", ["回踩确认", "Retest Confirmed"]);
  }

  const exitSetup = setupOf(selection.exitSignal);
  if (exitSetup) {
    const exitAnchors = objectValue(exitSetup.anchors) || {};
    add("failure-peak", textValue(exitAnchors, "failure_peak"), numberValue(exitSetup, "failure_peak_price"), "neckline", ["走弱前峰值", "Peak Before Weakness"]);
    add("lower-high", textValue(exitAnchors, "lower_high"), numberValue(exitSetup, "lower_high_price"), "neckline", ["已确认更低高点", "Confirmed Lower High"]);
    add("failure-support", textValue(exitAnchors, "failure_pullback"), numberValue(exitSetup, "failure_support_price"), "pullback", ["失守回踩支撑", "Broken Pullback Support"]);
    const reasons: Record<string, [string, string]> = {
      right_side_failure: ["右侧走弱退出", "Right-side Weakness Exit"],
      bearish_volume_failure: ["巨量阴线退出", "High-volume Bearish Exit"],
      pattern_invalidation: ["形态失效退出", "Pattern Invalidation Exit"],
      max_loss_stop: ["最大亏损退出", "Maximum Loss Exit"],
      atr_stop: ["ATR 止损退出", "ATR Stop Exit"],
      take_profit: ["止盈退出", "Take-profit Exit"],
    };
    add("exit", toTradeDateKey(selection.exitSignal?.ts), numberValue(objectValue(selection.exitSignal?.features) || {}, "close"), "sell_signal",
      reasons[textValue(exitSetup, "exit_stage") || ""] || ["形态退出", "Pattern Exit"]);
  }
  return markers;
}
