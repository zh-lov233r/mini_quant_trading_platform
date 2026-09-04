import { describe, expect, it } from "vitest";

import type { BacktestSignalOut } from "@/types/backtest";
import {
  buildPatternLifecycleMarkers,
  earliestPatternAnchorDate,
  selectLifecyclePattern,
} from "./patternLifecycle";

function signal(
  patternType: string,
  stage: number,
  ts: string,
  setup: Record<string, unknown>,
): BacktestSignalOut {
  return {
    id: `${patternType}-${stage}`,
    ts,
    symbol: "TEST",
    signal: "BUY",
    features: {
      close: 20 + stage,
      setup: {
        pattern_type: patternType,
        setup_id: `${patternType}:TEST:one`,
        stage_index: stage,
        ...setup,
      },
    },
  };
}

const bars = [
  { trade_date: "2025-01-02", open: 12, high: 13, low: 10, close: 11 },
  { trade_date: "2025-01-03", open: 11, high: 12, low: 8, close: 9 },
  { trade_date: "2025-01-06", open: 10, high: 14, low: 9, close: 13 },
  { trade_date: "2025-01-07", open: 14, high: 16, low: 13, close: 15 },
];

describe("pattern lifecycle markers", () => {
  it("uses the highest stage for the same setup and exposes the earliest anchor", () => {
    const first = signal("head_shoulders_bottom", 1, "2025-01-03T21:00:00Z", {
      anchors: { left_shoulder: "2025-01-02", head: "2025-01-03" },
      left_shoulder_low: 10,
      head_low: 8,
    });
    const final = signal("head_shoulders_bottom", 3, "2025-01-07T21:00:00Z", {
      anchors: {
        left_shoulder: "2025-01-02",
        head: "2025-01-03",
        right_shoulder: "2025-01-06",
      },
      left_shoulder_low: 10,
      head_low: 8,
      neckline_price: 14,
    });
    const selection = selectLifecyclePattern([final, first], first, "TEST", "2025-01-08");

    expect(selection?.latestSignal).toBe(final);
    expect(earliestPatternAnchorDate(first)).toBe("2025-01-02");
    expect(buildPatternLifecycleMarkers(selection, bars, "zh-CN").map((marker) => [marker.label, marker.date, marker.price])).toEqual([
      ["左肩", "2025-01-02", 10],
      ["头部低点", "2025-01-03", 8],
      ["右肩", "2025-01-06", 9],
      ["颈线突破", "2025-01-07", 23],
    ]);
  });

  it.each([
    ["double_bottom", { anchors: {}, left_bottom_trade_date: "2025-01-02", left_bottom_low: 10, right_bottom_trade_date: "2025-01-03", right_bottom_low: 8, neckline_trade_date: "2025-01-06", neckline_price: 14, breakout_trade_date: "2025-01-07", breakout_close: 15 }, ["左底", "右底", "颈线", "反转确认"]],
    ["rounded_bottom", { anchors: { bottom: "2025-01-03", pullbacks: ["2025-01-06"] }, rim_price: 14 }, ["圆弧底部", "右侧回踩 1", "碗口突破"]],
    ["v_reversal", { anchors: { pivot: "2025-01-03", breakout: "2025-01-06" }, pivot_low: 8, consolidation_top: 14 }, ["V 型转折", "整理区突破", "回踩确认"]],
  ])("marks the known %s structure and reversal", (patternType, setup, labels) => {
    const first = signal(patternType, 3, "2025-01-07T21:00:00Z", setup);
    const selection = selectLifecyclePattern([first], first, "TEST", "2025-01-08");
    expect(buildPatternLifecycleMarkers(selection, bars, "zh-CN").map((marker) => marker.label)).toEqual(labels);
  });

  it("finds the island bottom inside the two gaps and marks its reversal", () => {
    const setup = {
      anchors: { left_gap_trade_date: "2025-01-02", breakout_trade_date: "2025-01-07" },
      island_low: 8,
      breakout_gap_low: 13,
    };
    const first = signal("island_reversal", 2, "2025-01-07T21:00:00Z", setup);
    const selection = selectLifecyclePattern([first], first, "TEST", "2025-01-08");
    expect(buildPatternLifecycleMarkers(selection, bars, "en-US").map((marker) => [marker.label, marker.date, marker.price])).toEqual([
      ["Island Bottom", "2025-01-03", 8],
      ["Reversal Confirmed", "2025-01-07", 13],
    ]);
  });
  it("shows confirmed weakness anchors and the SELL reason only within the lifecycle", () => {
    const first = signal("rounded_bottom", 2, "2025-01-03T21:00:00Z", { anchors: { bottom: "2025-01-02" } });
    const exit = signal("rounded_bottom", 2, "2025-01-07T21:00:00Z", {
      anchors: { failure_peak: "2025-01-03", lower_high: "2025-01-06", failure_pullback: "2025-01-02" },
      failure_peak_price: 14, lower_high_price: 13, failure_support_price: 10, exit_stage: "right_side_failure",
    });
    exit.signal = "SELL";
    const selection = selectLifecyclePattern([first, exit], first, "TEST", "2025-01-07");
    expect(selection?.latestSignal).toBe(first);
    expect(buildPatternLifecycleMarkers(selection, bars, "en-US").map((m) => [m.label, m.price])).toContainEqual(["Confirmed Lower High", 13]);
    expect(buildPatternLifecycleMarkers(selection, bars, "zh-CN").map((m) => m.label)).toContain("右侧走弱退出");
    expect(selectLifecyclePattern([first, exit], first, "TEST", "2025-01-06")?.exitSignal).toBeNull();
  });

  it("marks the platform and consolidation bounds from audit prices", () => {
    const head = signal("head_shoulders_bottom", 2, "2025-01-07T21:00:00Z", {
      anchors: { platform_start: "2025-01-02", platform_end: "2025-01-06" }, platform_low: 10, platform_high: 14,
    });
    expect(buildPatternLifecycleMarkers(selectLifecyclePattern([head], head, "TEST", null), bars, "en-US")
      .map((m) => [m.label, m.price])).toContainEqual(["Left Platform Floor", 10]);
    const v = signal("v_reversal", 3, "2025-01-07T21:00:00Z", {
      anchors: { consolidation_start: "2025-01-02", consolidation_end: "2025-01-06", reversal: "2025-01-03" },
      consolidation_low: 9, consolidation_top: 14,
    });
    expect(buildPatternLifecycleMarkers(selectLifecyclePattern([v], v, "TEST", null), bars, "en-US")
      .map((m) => [m.label, m.price])).toContainEqual(["Range Ceiling", 14]);
  });

});
