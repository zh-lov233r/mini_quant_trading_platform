export const BACKTEST_REVIEW_TABS = [
  "symbolPnl",
  "signalStrength",
  "lifecycles",
  "transactions",
  "positions",
] as const;

export type BacktestReviewTab = (typeof BACKTEST_REVIEW_TABS)[number];
export type BacktestReviewTabKey = "ArrowLeft" | "ArrowRight" | "Home" | "End";

const LABELS: Record<BacktestReviewTab, { zh: string; en: string }> = {
  symbolPnl: { zh: "个股盈亏", en: "Per-Symbol PnL" },
  signalStrength: { zh: "信号强度排名", en: "Signal Strength" },
  lifecycles: { zh: "生命周期", en: "Lifecycles" },
  transactions: { zh: "交易明细", en: "Transactions" },
  positions: { zh: "最新持仓", en: "Latest Positions" },
};

export function backtestReviewTabLabel(tab: BacktestReviewTab, locale: string): string {
  return locale === "zh-CN" ? LABELS[tab].zh : LABELS[tab].en;
}

export function nextBacktestReviewTab(
  current: BacktestReviewTab,
  key: BacktestReviewTabKey,
): BacktestReviewTab {
  if (key === "Home") return BACKTEST_REVIEW_TABS[0];
  if (key === "End") return BACKTEST_REVIEW_TABS[BACKTEST_REVIEW_TABS.length - 1];

  const currentIndex = BACKTEST_REVIEW_TABS.indexOf(current);
  const delta = key === "ArrowRight" ? 1 : -1;
  const nextIndex = (currentIndex + delta + BACKTEST_REVIEW_TABS.length) % BACKTEST_REVIEW_TABS.length;
  return BACKTEST_REVIEW_TABS[nextIndex];
}
