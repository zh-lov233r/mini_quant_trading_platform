import { describe, expect, it } from "vitest";

import {
  isLifecycleInteractiveTarget,
  LIFECYCLE_INTERACTIVE_TARGET_SELECTOR,
} from "@/utils/lifecycleInteraction";

describe("lifecycle row interaction", () => {
  it("keeps candlestick chart clicks inside the expanded lifecycle", () => {
    const chart = {
      closest: (selector: string) => selector.includes("[role='img']") ? chart : null,
    } as unknown as Pick<Element, "closest">;

    expect(LIFECYCLE_INTERACTIVE_TARGET_SELECTOR).toContain("[role='img']");
    expect(isLifecycleInteractiveTarget(chart)).toBe(true);
  });

  it("allows non-interactive lifecycle content to collapse the row", () => {
    const content = { closest: () => null } as unknown as Pick<Element, "closest">;
    expect(isLifecycleInteractiveTarget(content)).toBe(false);
  });
});
