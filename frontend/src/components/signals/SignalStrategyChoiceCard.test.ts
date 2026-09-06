import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SignalStrategyChoiceCard } from "./SignalStrategyChoiceCard";
import type { StrategyOut } from "@/types/strategy";

const strategy = {
  id: "strategy-1",
  name: "Island reversal",
  strategy_type: "island_reversal",
  version: 4,
} as StrategyOut;

function render(selected: boolean, supported: boolean) {
  return renderToStaticMarkup(createElement(SignalStrategyChoiceCard, {
    strategy,
    locale: "en-US",
    selected,
    supported,
    supportedLabel: "Scannable",
    unsupportedLabel: "Not scannable",
    onChange: vi.fn(),
  }));
}

describe("signal strategy choice card", () => {
  it("shows the library category hierarchy and native selected state", () => {
    const markup = render(true, true);
    expect(markup).toContain("Island Reversal Bottom");
    expect(markup).toContain("island_reversal · v4");
    expect(markup).toContain("Island reversal");
    expect(markup).toContain("Scannable");
    expect(markup).toContain('type="checkbox"');
    expect(markup).toContain("checked");
    expect(markup).toContain('aria-label="Island reversal · Island Reversal Bottom"');
  });

  it("keeps unsupported strategies disabled", () => {
    const markup = render(false, false);
    expect(markup).toContain("Not scannable");
    expect(markup).toContain("disabled");
    expect(markup).not.toContain("checked");
  });
});
