import { backtestTaskDurationMs } from "./backtestTasks";
import { describe, expect, it } from "vitest";

import { backtestTaskStageLabel, isActiveBacktestTask, pageIndexAfterBacktestTaskDelete, shouldPollBacktestTasks, shouldShowBacktestTaskProgress } from "./backtestTasks";

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

  it("returns to the previous page after deleting its last task", () => {
    expect(pageIndexAfterBacktestTaskDelete(2, 1)).toBe(1);
    expect(pageIndexAfterBacktestTaskDelete(2, 2)).toBe(2);
    expect(pageIndexAfterBacktestTaskDelete(0, 1)).toBe(0);
  });
});

it("shows completed execution duration including finalization, with missing timestamps unknown", () => {
  expect(backtestTaskDurationMs({ stage: "completed", started_at: "2026-09-03T10:00:00Z", finished_at: "2026-09-03T11:02:03Z" })).toBe(3723000);
  expect(backtestTaskDurationMs({ stage: "completed", started_at: null, finished_at: null })).toBeNull();
  expect(backtestTaskDurationMs({ stage: "running", started_at: "2026-09-03T10:00:00Z", finished_at: null })).toBeNull();
  expect(backtestTaskDurationMs({ stage: "completed", started_at: "invalid", finished_at: "invalid" })).toBeNull();
});
