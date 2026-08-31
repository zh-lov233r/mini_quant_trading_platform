import { describe, expect, it } from "vitest";

import type { StrategyOut } from "@/types/strategy";
import { buildStrategyCloneDraft, buildStrategyCloneName } from "./strategyClone";

function strategy(overrides: Partial<StrategyOut> = {}): StrategyOut {
  return {
    id: "source-id",
    strategy_key: "source-key",
    name: "Source Strategy",
    strategy_type: "mean_reversion",
    status: "active",
    version: 7,
    params: {
      signal: { lookback_window: 20 },
      universe: { symbols: ["AAPL", "MSFT"] },
      metadata: { description: "Source description", schema_version: 1 },
    },
    engine_ready: true,
    ...overrides,
  };
}

describe("strategy clone draft", () => {
  it("builds an editable copy without mutating the source params", () => {
    const source = strategy();
    const draft = buildStrategyCloneDraft(source);

    expect(draft.name).toBe("Source Strategy Copy");
    expect(draft.description).toBe("Source description");
    expect(draft.strategyType).toBe("mean_reversion");
    expect(draft.symbolsText).toBe("AAPL, MSFT");
    expect(draft.params).not.toBe(source.params);

    (draft.params.signal as Record<string, unknown>).lookback_window = 10;
    expect((source.params.signal as Record<string, unknown>).lookback_window).toBe(20);
  });

  it("keeps the generated copy name within the API limit", () => {
    const cloneName = buildStrategyCloneName("A".repeat(128));
    expect(cloneName).toHaveLength(128);
    expect(cloneName.endsWith(" Copy")).toBe(true);
  });

  it("rejects historical strategy types that are not in the current catalog", () => {
    expect(() => buildStrategyCloneDraft(strategy({ strategy_type: "retired_type" }))).toThrow(
      "unsupported strategy type",
    );
  });
});
