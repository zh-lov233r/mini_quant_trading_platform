import type { BacktestTaskStage } from "@/types/backtest";

export const ACTIVE_BACKTEST_TASK_STAGES = new Set<BacktestTaskStage>([
  "waiting_research",
  "queued",
  "preparing",
  "running",
  "finalizing",
  "cancel_requested",
]);

export function isActiveBacktestTask(stage: BacktestTaskStage): boolean {
  return ACTIVE_BACKTEST_TASK_STAGES.has(stage);
}

export function shouldPollBacktestTasks(tasks: Array<{ stage: BacktestTaskStage }>): boolean {
  return tasks.some((task) => isActiveBacktestTask(task.stage));
}

export function shouldShowBacktestTaskProgress(stage: BacktestTaskStage, hasProgress: boolean): boolean {
  return hasProgress && stage !== "completed";
}

export function pageIndexAfterBacktestTaskDelete(pageIndex: number, itemCount: number): number {
  return pageIndex > 0 && itemCount === 1 ? pageIndex - 1 : pageIndex;
}

export function backtestTaskStageLabel(stage: BacktestTaskStage, isZh: boolean): string {
  const labels: Record<BacktestTaskStage, [string, string]> = {
    waiting_research: ["等待研究调度", "Waiting for research scheduler"],
    queued: ["等待回测执行", "Queued for backtest"],
    preparing: ["准备数据", "Preparing"],
    running: ["执行中", "Running"],
    finalizing: ["保存结果", "Finalizing"],
    cancel_requested: ["取消中", "Cancelling"],
    completed: ["已完成", "Completed"],
    failed: ["失败", "Failed"],
    cancelled: ["已取消", "Cancelled"],
  };
  return labels[stage][isZh ? 0 : 1];
}
