import { describe, expect, it } from "vitest";

import {
  BACKTEST_REVIEW_TABS,
  DEFAULT_BACKTEST_REVIEW_TAB,
  backtestReviewTabLabel,
  nextBacktestReviewTab,
} from "./backtestReviewTabs";

describe("backtest review tabs", () => {
  it("opens the workbench on lifecycles", () => {
    expect(DEFAULT_BACKTEST_REVIEW_TAB).toBe("lifecycles");
  });

  it("keeps the requested five-module order and bilingual labels", () => {
    expect(BACKTEST_REVIEW_TABS).toEqual([
      "symbolPnl",
      "signalStrength",
      "lifecycles",
      "transactions",
      "positions",
    ]);
    expect(BACKTEST_REVIEW_TABS.map((tab) => backtestReviewTabLabel(tab, "zh-CN"))).toEqual([
      "个股盈亏",
      "信号强度排名",
      "生命周期",
      "交易明细",
      "最新持仓",
    ]);
    expect(backtestReviewTabLabel("positions", "en-US")).toBe("Latest Positions");
  });

  it("supports wrapped arrow navigation plus Home and End", () => {
    expect(nextBacktestReviewTab("symbolPnl", "ArrowLeft")).toBe("positions");
    expect(nextBacktestReviewTab("positions", "ArrowRight")).toBe("symbolPnl");
    expect(nextBacktestReviewTab("signalStrength", "ArrowRight")).toBe("lifecycles");
    expect(nextBacktestReviewTab("transactions", "Home")).toBe("symbolPnl");
    expect(nextBacktestReviewTab("symbolPnl", "End")).toBe("positions");
  });
});
