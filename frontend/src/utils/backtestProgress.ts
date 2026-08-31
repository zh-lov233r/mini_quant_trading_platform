import type { BacktestFinalizingStage, BacktestProgressPhase } from "@/types/backtest";

export function clampBacktestPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

const phaseLabels: Record<BacktestProgressPhase, { zh: string; en: string }> = {
  queued: { zh: "等待执行", en: "Queued" },
  preparing: { zh: "准备数据", en: "Preparing" },
  running: { zh: "逐日回测", en: "Running" },
  finalizing: { zh: "写入结果", en: "Finalizing" },
  completed: { zh: "已完成", en: "Completed" },
  failed: { zh: "失败", en: "Failed" },
  cancelled: { zh: "已取消", en: "Cancelled" },
};

export function backtestPhaseLabel(phase: BacktestProgressPhase, isZh: boolean): string {
  return isZh ? phaseLabels[phase].zh : phaseLabels[phase].en;
}

const finalizingStageLabels: Record<BacktestFinalizingStage, { zh: string; en: string }> = {
  zone_versions: { zh: "写入压力/支撑区版本", en: "Writing zone versions" },
  run_events: { zh: "写入生命周期事件", en: "Writing lifecycle events" },
  backtest_details: { zh: "整理回测明细", en: "Preparing backtest details" },
  committing: { zh: "提交回测结果", en: "Committing backtest results" },
};

export function backtestFinalizingStageLabel(
  stage: BacktestFinalizingStage,
  isZh: boolean,
): string {
  return isZh ? finalizingStageLabels[stage].zh : finalizingStageLabels[stage].en;
}

export function shouldLoadBacktestDetails(status: string): boolean {
  return status === "completed";
}

export function backtestProgressAria(value: number, isZh: boolean) {
  const percent = Math.round(clampBacktestPercent(value) * 10) / 10;
  return {
    role: "progressbar" as const,
    "aria-label": isZh ? "回测进度" : "Backtest progress",
    "aria-valuemin": 0,
    "aria-valuemax": 100,
    "aria-valuenow": percent,
  };
}
