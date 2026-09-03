import { describe, expect, it } from "vitest";

import type { BacktestWorkerStatus } from "@/types/backtest";
import {
  formatBacktestWorkerCapacity,
  formatBacktestWorkerExecutionModel,
} from "@/utils/backtestWorkerStatus";

function status(activeJobs: number, availableSlots: number, queuedJobs: number): BacktestWorkerStatus {
  return {
    execution_model: "process",
    configured_concurrency: 2,
    intra_run_execution_model: "thread",
    configured_intra_run_threads: 4,
    effective_intra_run_threads: 4,
    available_slots: availableSlots,
    automation_available: true,
    manager_state: "running",
    live_managers: 1,
    worker_active: activeJobs > 0,
    active_jobs: activeJobs,
    queued_jobs: queuedJobs,
    heartbeat_stale_after_seconds: 15,
    checked_at: "2026-08-31T00:00:00Z",
  };
}

describe("backtest worker capacity presentation", () => {
  it("formats zero, partial, and full process capacity", () => {
    expect(formatBacktestWorkerCapacity(status(0, 2, 0), true)).toBe("进程 0/2 · 可用 2 · 排队 0");
    expect(formatBacktestWorkerCapacity(status(1, 1, 3), true)).toBe("进程 1/2 · 可用 1 · 排队 3");
    expect(formatBacktestWorkerCapacity(status(2, 0, 4), false)).toBe("Processes 2/2 · Available 0 · Queued 4");
  });

  it("formats process by effective per-run thread capacity", () => {
    expect(formatBacktestWorkerExecutionModel(status(1, 1, 3), true)).toBe(
      "2 进程 × 每个 run 4 线程",
    );
    expect(formatBacktestWorkerExecutionModel(status(1, 1, 3), false)).toBe(
      "2 processes × 4 threads/run",
    );
  });
});
