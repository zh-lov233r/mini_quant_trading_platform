import { describe, expect, it } from "vitest";

import type { BacktestRunOut } from "@/types/backtest";
import { filterBacktestRuns } from "@/utils/backtestFilters";

function run(overrides: Partial<BacktestRunOut>): BacktestRunOut {
  return {
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

const strategyTypes = new Map([
  ["strategy-1", "trend"],
  ["strategy-2", "mean_reversion"],
]);

describe("backtest run filtering", () => {
  const runs = [
    run({ id: "run-1", strategy_name: "Long Trend", basket_name: "Core Tech" }),
    run({ id: "run-2", strategy_id: "strategy-2", strategy_name: "Reversal", status: "failed" }),
    run({ id: "run-3", strategy_id: "missing", strategy_name: "Archived Strategy" }),
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

  it("keeps missing historical strategies under the unknown type", () => {
    expect(filterBacktestRuns(runs, strategyTypes, { query: "", status: "all", strategyType: "unknown" }))
      .toEqual([runs[2]]);
  });
});
