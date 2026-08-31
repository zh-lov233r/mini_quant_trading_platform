import type { BacktestComparisonCurvePoint } from "@/types/backtest";

export function comparisonCurveReturn(
  points: readonly BacktestComparisonCurvePoint[] | undefined,
): number | null {
  const value = points?.[points.length - 1]?.return;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
