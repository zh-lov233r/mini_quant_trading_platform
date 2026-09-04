import { describe, expect, it } from "vitest";

import type { BacktestRunOut } from "@/types/backtest";
import type { StrategyType } from "@/types/strategy";
import { defaultBenchmarkForBasket, filterBacktestRuns } from "@/utils/backtestFilters";

function run(overrides: Partial<BacktestRunOut>): BacktestRunOut {
  return {
    market: "US",
    id: "run-1",
    strategy_id: "strategy-1",
    strategy_version: 1,
    mode: "backtest",
    status: "completed",
    summary_metrics: {},
    persist_level: "full",
    available_details: [],
    ...overrides,
  };
}

const strategyTypes = new Map<string, StrategyType>([
  ["strategy-1", "trend"],
  ["strategy-2", "mean_reversion"],
]);

describe("backtest run filtering", () => {
  const runs = [
    run({ id: "run-1", strategy_name: "Long Trend", basket_name: "Core Tech" }),
    run({ id: "run-2", strategy_id: "strategy-2", strategy_name: "Reversal", status: "failed" }),
  ];

  it("matches strategy and basket keywords case-insensitively", () => {
    expect(filterBacktestRuns(runs, strategyTypes, { query: "core tech", status: "all", strategyType: "all" }))
      .toHaveLength(1);
    expect(filterBacktestRuns(runs, strategyTypes, { query: "REVERSAL", status: "all", strategyType: "all" })[0]?.id)
      .toBe("run-2");
  });

  it("combines status and strategy-type filters", () => {
    expect(filterBacktestRuns(runs, strategyTypes, { query: "", status: "failed", strategyType: "mean_reversion" }))
      .toEqual([runs[1]]);
  });

  it("filters by persisted run market, independent of basket names", () => {
    const cn = run({ id: "cn", market: "CN", basket_name: "Core Tech" });
    expect(filterBacktestRuns([...runs, cn], strategyTypes, { query: "", status: "all", strategyType: "all", market: "CN" })).toEqual([cn]);
  });

  it("rejects a run whose strategy contract is missing", () => {
    const missing = run({ id: "run-3", strategy_id: "missing", strategy_name: "Archived Strategy" });
    expect(() => filterBacktestRuns([missing], strategyTypes, { query: "", status: "all", strategyType: "all" }))
      .toThrow("Missing strategy type");
  });
});

describe("backtest basket defaults", () => {
  it("uses a market-appropriate benchmark for each basket", () => {
    expect(defaultBenchmarkForBasket({ symbols: ["600000.SH", "000001.SZ"] })).toBe("000001.SH");
    expect(defaultBenchmarkForBasket({ symbols: ["AAPL", "MSFT"] })).toBe("SPY");
  });
});
