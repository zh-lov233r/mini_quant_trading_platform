import type { BacktestSignalOut, SignalStrengthRecord } from "@/types/backtest";

export function signalStrengthLevelLabel(
  level: SignalStrengthRecord["level"] | undefined,
  locale: string,
): string {
  const isZh = locale === "zh-CN";
  if (level === "weak") return isZh ? "弱" : "Weak";
  if (level === "medium") return isZh ? "中" : "Medium";
  if (level === "strong") return isZh ? "强" : "Strong";
  if (level === "very_strong") return isZh ? "很强" : "Very Strong";
  return isZh ? "旧结果未计算" : "Not computed for legacy run";
}

export function signalStrengthResultLabel(
  strength: SignalStrengthRecord | null | undefined,
  filled: boolean,
  locale: string,
): string {
  const isZh = locale === "zh-CN";
  if (filled) return isZh ? "已成交" : "Filled";
  if (strength?.passes_threshold === false) return isZh ? "低于阈值" : "Below threshold";
  return isZh ? "未成交" : "Not filled";
}

export function sortBuySignalsByStrength(signals: BacktestSignalOut[]): BacktestSignalOut[] {
  return signals
    .filter((signal) => signal.signal === "BUY")
    .slice()
    .sort((left, right) => {
      const timeOrder = String(right.ts || "").localeCompare(String(left.ts || ""));
      if (timeOrder !== 0) return timeOrder;
      const rankOrder = (left.strength?.rank ?? Number.MAX_SAFE_INTEGER)
        - (right.strength?.rank ?? Number.MAX_SAFE_INTEGER);
      if (rankOrder !== 0) return rankOrder;
      return left.symbol.localeCompare(right.symbol);
    });
}
