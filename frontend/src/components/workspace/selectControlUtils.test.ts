import { describe, expect, it } from "vitest";

import { normalizeSelectControlValue } from "./selectControlUtils";

describe("normalizeSelectControlValue", () => {
  it("normalizes string and numeric option values for Radix Select", () => {
    expect(normalizeSelectControlValue("daily")).toBe("daily");
    expect(normalizeSelectControlValue(50)).toBe("50");
  });
});
