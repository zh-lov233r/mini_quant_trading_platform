import type { BacktestProgress } from "@/types/backtest";
import motion from "@/styles/Motion.module.css";
import {
  backtestFinalizingStageLabel,
  backtestPhaseLabel,
  backtestProgressAria,
  clampBacktestPercent,
} from "@/utils/backtestProgress";

interface BacktestProgressBarProps {
  progress: BacktestProgress;
  isZh: boolean;
  showDetails?: boolean;
}

export default function BacktestProgressBar({
  progress,
  isZh,
  showDetails = false,
}: BacktestProgressBarProps) {
  const percent = clampBacktestPercent(progress.percent);
  const roundedPercent = Math.round(percent * 10) / 10;
  const hasDayProgress = progress.completed_days != null && progress.total_days != null;
  const hasItemProgress = progress.completed_items != null && progress.total_items != null;
  const stageLabel = progress.phase === "finalizing" && progress.finalizing_stage
    ? backtestFinalizingStageLabel(progress.finalizing_stage, isZh)
    : null;
  const itemFormatter = new Intl.NumberFormat(isZh ? "zh-CN" : "en-US");

  return (
    <div
      style={{
        display: "grid",
        gap: 8,
        padding: showDetails ? 16 : 0,
        borderRadius: showDetails ? 16 : 0,
        border: showDetails ? "1px solid rgba(56, 189, 248, 0.18)" : "none",
        background: showDetails ? "rgba(8, 47, 73, 0.24)" : "transparent",
        color: "#e2e8f0",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 13 }}>
        <span style={{ color: "#67e8f9", fontWeight: 700 }}>
          {stageLabel || backtestPhaseLabel(progress.phase, isZh)}
        </span>
        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>
          {roundedPercent.toFixed(1)}%
        </span>
      </div>
      <div
        {...backtestProgressAria(percent, isZh)}
        style={{
          height: 9,
          overflow: "hidden",
          borderRadius: 999,
          background: "rgba(71, 85, 105, 0.52)",
        }}
      >
        <div
          className={motion.progress}
          data-phase={progress.phase}
          style={{
            width: `${percent}%`,
            height: "100%",
            borderRadius: 999,
            background: progress.phase === "failed" ? "#fb7185" : "linear-gradient(90deg, #0891b2, #22d3ee)",
          }}
        />
      </div>
      {showDetails ? (
        <div style={{ color: "rgba(148, 163, 184, 0.92)", fontSize: 13 }}>
          {hasDayProgress
            ? `${progress.completed_days} / ${progress.total_days} ${isZh ? "个交易日" : "trading days"}`
            : (isZh ? "尚未开始逐日计算" : "Daily processing has not started")}
          {progress.trade_date
            ? ` · ${isZh ? "最近交易日" : "Latest trading day"}: ${progress.trade_date}`
            : ""}
          {hasItemProgress
            ? ` · ${itemFormatter.format(progress.completed_items as number)} / ${itemFormatter.format(progress.total_items as number)}`
            : ""}
          {` · ${isZh ? "尝试" : "Attempt"} ${progress.attempt}/${progress.max_attempts}`}
        </div>
      ) : null}
    </div>
  );
}
