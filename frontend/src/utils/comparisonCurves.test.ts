import { describe, expect, it } from "vitest";

import { comparisonCurveLabel, comparisonCurveReturn } from "./comparisonCurves";

describe("comparisonCurveReturn", () => {
  it("labels A-share market indices without renaming US proxies", () => {
    expect(comparisonCurveLabel("000001.SH", "zh-CN")).toBe("上证指数 (000001.SH)");
    expect(comparisonCurveLabel("399001.SZ", "en-US")).toBe("Shenzhen Component (399001.SZ)");
    expect(comparisonCurveLabel("SPY", "zh-CN")).toBe("SPY");
  });

  it("returns the final finite QQQ return", () => {
    expect(comparisonCurveReturn([
      { ts: "2024-01-02T00:00:00Z", return: 0 },
      { ts: "2026-07-31T00:00:00Z", return: 0.7089098089868104 },
    ])).toBeCloseTo(0.7089098089868104);
  });

  it("returns null when a curve is empty or invalid", () => {
    expect(comparisonCurveReturn([])).toBeNull();
    expect(comparisonCurveReturn([{ return: Number.NaN }])).toBeNull();
  });
});
