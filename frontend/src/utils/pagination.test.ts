import { describe, expect, it } from "vitest";

import { clampPageIndex, pageCount, paginateItems } from "@/utils/pagination";

describe("pagination helpers", () => {
  it("keeps empty collections on a single valid page", () => {
    expect(pageCount(0, 10)).toBe(1);
    expect(clampPageIndex(4, 0, 10)).toBe(0);
    expect(paginateItems([], 4, 10)).toEqual([]);
  });

  it("clamps stale page indexes after the collection shrinks", () => {
    expect(clampPageIndex(8, 21, 10)).toBe(2);
    expect(paginateItems([0, 1, 2, 3, 4], 3, 2)).toEqual([4]);
  });

  it("returns only the requested page", () => {
    expect(pageCount(50, 10)).toBe(5);
    expect(paginateItems([0, 1, 2, 3, 4, 5], 1, 3)).toEqual([3, 4, 5]);
  });
});
