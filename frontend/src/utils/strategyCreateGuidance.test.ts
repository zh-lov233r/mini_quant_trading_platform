import { describe, expect, it } from "vitest";

import { parseGuidedNumberInput } from "./strategyCreateGuidance";

describe("guided strategy numeric inputs", () => {
  it("keeps a cleared value blank until the user enters a replacement", () => {
    expect(parseGuidedNumberInput("", false)).toBe("");
    expect(parseGuidedNumberInput("", true)).toBe("");
    expect(parseGuidedNumberInput("12", false)).toBe(12);
    expect(parseGuidedNumberInput("12", true)).toBe(0.12);
  });
});
