import { describe, expect, it } from "vitest";

import { getStrategyCategoryPresentation } from "./strategy";

describe("getStrategyCategoryPresentation", () => {
  it("returns the configured category accent", () => {
    expect(getStrategyCategoryPresentation("support_resistance", "zh-CN").accent).toBe("#fb923c");
  });

  it("rejects unregistered categories instead of mapping them to custom", () => {
    expect(() => getStrategyCategoryPresentation("future_strategy" as never, "en-US")).toThrow();
  });
});
