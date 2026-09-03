import type { BacktestComparisonCurvePoint } from "@/types/backtest";

const COMPARISON_LABELS: Record<string, { zh: string; en: string }> = {
  "000001.SH": { zh: "上证指数", en: "Shanghai Composite" },
  "399001.SZ": { zh: "深证成指", en: "Shenzhen Component" },
};

export function comparisonCurveLabel(symbol: string, locale: string): string {
  const label = COMPARISON_LABELS[symbol];
  return label ? `${locale === "zh-CN" ? label.zh : label.en} (${symbol})` : symbol;
}

export function comparisonCurveReturn(
  points: readonly BacktestComparisonCurvePoint[] | undefined,
): number | null {
  const value = points?.[points.length - 1]?.return;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
