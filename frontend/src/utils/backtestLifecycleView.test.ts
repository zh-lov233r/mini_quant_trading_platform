import { describe, expect, it } from "vitest";

import { lifecycleChartDisplayState } from "./backtestLifecycleView";

describe("lifecycle chart display state", () => {
  it("keeps an existing chart mounted while a new range is loading", () => {
    expect(lifecycleChartDisplayState({ loading: true, error: null, barCount: 42 })).toBe("chart");
  });

  it("uses a loading placeholder only before the first bars arrive", () => {
    expect(lifecycleChartDisplayState({ loading: true, error: null, barCount: 0 })).toBe("loading");
  });

  it("preserves error and empty states", () => {
    expect(lifecycleChartDisplayState({ loading: false, error: "failed", barCount: 0 })).toBe("error");
    expect(lifecycleChartDisplayState({ loading: false, error: null, barCount: 0 })).toBe("empty");
  });
});
