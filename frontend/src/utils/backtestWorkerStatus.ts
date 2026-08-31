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
