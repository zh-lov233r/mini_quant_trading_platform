import { describe, expect, it } from "vitest";

import type { BacktestSignalOut } from "@/types/backtest";
import {
  signalStrengthLevelLabel,
  signalStrengthResultLabel,
  sortBuySignalsByStrength,
} from "./signalStrength";

function signal(symbol: string, rank: number | null, ts = "2026-01-02T21:00:00Z"): BacktestSignalOut {
  return {
    id: `${symbol}-${rank}`,
    symbol,
    signal: "BUY",
    ts,
    strength: rank == null ? null : {
      score: 75,
      level: "strong",
      threshold: 50,
      passes_threshold: true,
      rank,
      model_version: "test:v1",
      components: [],
    },
  };
}

describe("signal strength presentation", () => {
  it("sorts each date by frozen rank and puts legacy rows last", () => {
    expect(sortBuySignalsByStrength([
      signal("LEGACY", null),
      signal("SECOND", 2),
      signal("FIRST", 1),
    ]).map((item) => item.symbol)).toEqual(["FIRST", "SECOND", "LEGACY"]);
  });

  it("renders bilingual levels and a legacy fallback", () => {
    expect(signalStrengthLevelLabel("weak", "zh-CN")).toBe("弱");
    expect(signalStrengthLevelLabel("medium", "zh-CN")).toBe("中");
    expect(signalStrengthLevelLabel("strong", "zh-CN")).toBe("强");
    expect(signalStrengthLevelLabel("very_strong", "zh-CN")).toBe("很强");
    expect(signalStrengthLevelLabel("medium", "en-US")).toBe("Medium");
    expect(signalStrengthLevelLabel(undefined, "zh-CN")).toBe("旧结果未计算");
  });

  it("renders bilingual threshold and fill results", () => {
    const belowThreshold = { ...signal("LOW", 1).strength!, passes_threshold: false };
    expect(signalStrengthResultLabel(belowThreshold, false, "zh-CN")).toBe("低于阈值");
    expect(signalStrengthResultLabel(belowThreshold, false, "en-US")).toBe("Below threshold");
    expect(signalStrengthResultLabel(belowThreshold, true, "en-US")).toBe("Filled");
    expect(signalStrengthResultLabel(null, false, "zh-CN")).toBe("未成交");
  });
});
