import { describe, expect, it } from "vitest";

import { getStrategyCategoryPresentation } from "./strategy";

describe("getStrategyCategoryPresentation", () => {
  it("returns the configured category accent", () => {
    expect(getStrategyCategoryPresentation("support_resistance", "zh-CN").accent).toBe("#fb923c");
  });

  it("uses the custom visual fallback for unknown categories", () => {
    const presentation = getStrategyCategoryPresentation("future_strategy", "en-US");
    expect(presentation.label).toBe("future_strategy");
    expect(presentation.accent).toBe("#94a3b8");
  });
});
