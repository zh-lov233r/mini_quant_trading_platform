import { describe, expect, it } from "vitest";

import { backtestTaskStageLabel, isActiveBacktestTask, shouldPollBacktestTasks, shouldShowBacktestTaskProgress } from "./backtestTasks";

describe("backtest task presentation", () => {
  it("keeps scheduler waiting and cancellation requests active", () => {
    expect(isActiveBacktestTask("waiting_research")).toBe(true);
    expect(isActiveBacktestTask("cancel_requested")).toBe(true);
    expect(isActiveBacktestTask("completed")).toBe(false);
  });

  it("uses distinct labels for the two queue layers", () => {
    expect(backtestTaskStageLabel("waiting_research", true)).toBe("等待研究调度");
    expect(backtestTaskStageLabel("queued", true)).toBe("等待回测执行");
  });

  it("polls only while the current server page has active work", () => {
    expect(shouldPollBacktestTasks([{ stage: "completed" }, { stage: "failed" }])).toBe(false);
    expect(shouldPollBacktestTasks([{ stage: "completed" }, { stage: "finalizing" }])).toBe(true);
  });

  it("hides the redundant 100% bar for completed history", () => {
    expect(shouldShowBacktestTaskProgress("completed", true)).toBe(false);
    expect(shouldShowBacktestTaskProgress("failed", true)).toBe(true);
    expect(shouldShowBacktestTaskProgress("cancelled", true)).toBe(true);
  });
});
