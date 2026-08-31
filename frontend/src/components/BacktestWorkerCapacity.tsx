import type { BacktestWorkerStatus } from "@/types/backtest";
import { formatBacktestWorkerCapacity } from "@/utils/backtestWorkerStatus";

interface BacktestWorkerCapacityProps {
  status: BacktestWorkerStatus;
  isZh: boolean;
}

export default function BacktestWorkerCapacity({ status, isZh }: BacktestWorkerCapacityProps) {
  return (
    <div
      role="status"
      style={{
        marginBottom: 18,
        padding: "10px 14px",
        borderRadius: 14,
        border: "1px solid rgba(56, 189, 248, 0.22)",
        background: "rgba(8, 47, 73, 0.24)",
        color: "#a5f3fc",
        fontSize: 13,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {formatBacktestWorkerCapacity(status, isZh)}
    </div>
  );
}
