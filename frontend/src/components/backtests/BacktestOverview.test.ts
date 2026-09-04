import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { MESSAGES } from "@/i18n";
import { useI18n } from "@/i18n/provider";
import type { BacktestSummaryOut } from "@/types/backtest";
import BacktestOverview from "./BacktestOverview";

vi.mock("@/i18n/provider", () => ({ useI18n: vi.fn() }));

const summary: BacktestSummaryOut = {
  id: "summary-without-equity-data",
  market: "CN",
  strategy_id: "strategy-1",
  strategy_name: "Summary Strategy",
  strategy_version: 1,
  mode: "backtest",
  status: "completed",
  persist_level: "full",
  available_details: ["equity", "transactions"],
  summary_metrics: {},
  transaction_count: 2,
  initial_cash: 100000,
  final_equity: 110000,
  latest_snapshot: { ts: "2026-07-31T00:00:00Z", equity: 110000, cash: 25000, drawdown: .03 },
};

it.each(["zh-CN", "en-US"] as const)("renders overview from summary alone with snapshot collapsed in %s", (locale) => {
  vi.mocked(useI18n).mockReturnValue({ locale, messages: MESSAGES[locale], setLocale: vi.fn(), t: vi.fn() });
  const html = renderToStaticMarkup(createElement(BacktestOverview, { run: summary }, createElement("span", null, "10.00%")));
  expect(html).toContain(locale === "zh-CN" ? "回测概览" : "Backtest Overview");
  expect(html).toContain(summary.id);
  expect(html).toContain("110,000");
  expect(html).toContain("10.00%");
  expect(html).toMatch(/<summary>[^<]*<\/summary>/);
  expect(html).not.toMatch(/<details[^>]*\sopen(?:[\s=>])/);
});

it("omits an unavailable snapshot while keeping summary and error information", () => {
  vi.mocked(useI18n).mockReturnValue({ locale: "en-US", messages: MESSAGES["en-US"], setLocale: vi.fn(), t: vi.fn() });
  const html = renderToStaticMarkup(createElement(BacktestOverview, {
    run: { ...summary, latest_snapshot: null, error_message: "Details unavailable" },
  }));
  expect(html).not.toContain("<details");
  expect(html).toContain(summary.id);
  expect(html).toContain("Details unavailable");
});
