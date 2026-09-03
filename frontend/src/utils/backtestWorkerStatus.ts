import type { BacktestWorkerStatus } from "@/types/backtest";

export function formatBacktestWorkerCapacity(
  status: BacktestWorkerStatus,
  isZh: boolean,
): string {
  if (isZh) {
    return `进程 ${status.active_jobs}/${status.configured_concurrency} · 可用 ${status.available_slots} · 排队 ${status.queued_jobs}`;
  }
  return `Processes ${status.active_jobs}/${status.configured_concurrency} · Available ${status.available_slots} · Queued ${status.queued_jobs}`;
}

export function formatBacktestWorkerExecutionModel(
  status: BacktestWorkerStatus,
  isZh: boolean,
): string {
  if (isZh) {
    return `${status.configured_concurrency} 进程 × 每个 run ${status.effective_intra_run_threads} 线程`;
  }
  return `${status.configured_concurrency} processes × ${status.effective_intra_run_threads} threads/run`;
}
