import { describe, expect, it } from "vitest";

import { toTradeDateKey } from "./tradingDate";

describe("trading date", () => {
  it("preserves an explicit market date", () => {
    expect(toTradeDateKey("2026-07-31")).toBe("2026-07-31");
  });

  it("converts an offset timestamp to its New York trading date", () => {
    expect(toTradeDateKey("2026-07-30T21:00:00-07:00")).toBe("2026-07-31");
    expect(toTradeDateKey("2026-07-31T20:00:00Z")).toBe("2026-07-31");
  });

  it("rejects missing or invalid timestamps", () => {
    expect(toTradeDateKey(null)).toBeNull();
    expect(toTradeDateKey("not-a-date")).toBeNull();
  });
});
