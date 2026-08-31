import { describe, expect, it } from "vitest";

import {
  backtestFinalizingStageLabel,
  backtestPhaseLabel,
  backtestProgressAria,
  clampBacktestPercent,
  shouldLoadBacktestDetails,
} from "@/utils/backtestProgress";

describe("backtest progress presentation", () => {
  it("clamps invalid and out-of-range percentages", () => {
    expect(clampBacktestPercent(Number.NaN)).toBe(0);
    expect(clampBacktestPercent(-4)).toBe(0);
    expect(clampBacktestPercent(42.6)).toBe(42.6);
    expect(clampBacktestPercent(104)).toBe(100);
  });

  it("provides bilingual phase labels", () => {
    expect(backtestPhaseLabel("preparing", true)).toBe("准备数据");
    expect(backtestPhaseLabel("finalizing", false)).toBe("Finalizing");
    expect(backtestFinalizingStageLabel("run_events", true)).toBe("写入生命周期事件");
    expect(backtestFinalizingStageLabel("committing", false)).toBe("Committing backtest results");
  });

  it("loads persisted details only after completion", () => {
    expect(shouldLoadBacktestDetails("queued")).toBe(false);
    expect(shouldLoadBacktestDetails("running")).toBe(false);
    expect(shouldLoadBacktestDetails("failed")).toBe(false);
    expect(shouldLoadBacktestDetails("completed")).toBe(true);
  });

  it("renders clamped accessible progress semantics", () => {
    expect(backtestProgressAria(140, false)).toEqual({
      role: "progressbar",
      "aria-label": "Backtest progress",
      "aria-valuemin": 0,
      "aria-valuemax": 100,
      "aria-valuenow": 100,
    });
  });
});
