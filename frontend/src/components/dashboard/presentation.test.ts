import { describe, expect, it, vi } from "vitest";
import { createElement, type ReactNode } from "react";
import { Provider as TooltipProvider } from "radix-ui/tooltip";
import { renderToStaticMarkup } from "react-dom/server";
import { translate } from "@/i18n";
import { I18nProvider } from "@/i18n/provider";
import { dashboardEnUS } from "@/i18n/messages/dashboard/en-US";
import { dashboardZhCN } from "@/i18n/messages/dashboard/zh-CN";
import type { DashboardOverview } from "@/types/dashboard";
import { DashboardContent, DashboardSkeleton } from "./DashboardContent";
import { dashboardNumber, healthPresentation } from "./presentation";

vi.mock("next/router", () => ({ useRouter: () => ({ asPath: "/dashboard", events: { on: vi.fn(), off: vi.fn() } }) }));

function Providers({ children }: { children: ReactNode }) {
  return createElement(I18nProvider, null, createElement(TooltipProvider, null, children));
}

const empty: DashboardOverview = {
  generated_at: "2026-09-03T00:00:00Z", system: [],
  research_kpis: { active_strategies: 0, running_experiments: 0, running_backtests: 0, queued_backtests: 0 },
  task_summary: { waiting_research: 0, completed_last_24h: 0, failed_backtests_last_24h: 0, failed_research_last_24h: 0 },
  research_progress: { experiments: 0, evaluated_candidates: 0, verified_candidates: 0, promoted_strategies: 0, paper_strategies: 0 },
  strategy_evidence: [], paper_summary: { account_count: 0, portfolio_count: 0, portfolios: [] }, alerts: [], activity: [],
};

describe("dashboard presentation", () => {
  it("distinguishes missing values from zero and preserves signs", () => {
    expect(dashboardNumber(null, "en-US")).toBe("—");
    expect(dashboardNumber(0, "en-US", true)).toBe("0%");
    expect(dashboardNumber(-.1234, "en-US", true)).toBe("-12.34%");
  });
  it("has complete localized status and alert labels", () => {
    for (const locale of ["en-US", "zh-CN"] as const) {
      for (const state of Object.keys(healthPresentation)) expect(translate(locale, `dashboard.health.${state}`)).not.toContain("dashboard.");
      for (const code of Object.keys(dashboardZhCN.alerts)) expect(translate(locale, `dashboard.alerts.${code}`)).not.toContain("dashboard.");
    }
    expect(Object.keys(dashboardEnUS)).toEqual(Object.keys(dashboardZhCN));
    expect(healthPresentation.disabled.tone).toBe("neutral");
    expect(healthPresentation.unknown.icon).not.toBe(healthPresentation.healthy.icon);
  });
  it("renders empty research, paper and activity states with next steps", () => {
    const html = renderToStaticMarkup(createElement(Providers, null, createElement(DashboardContent, { data: empty })));
    expect(html).toContain(dashboardZhCN.noStrategies);
    expect(html).toContain(dashboardZhCN.noPaper);
    expect(html).toContain(dashboardZhCN.noAlertsHint);
    expect(html).toContain('href="/strategies/new"');
    expect(html).not.toContain("Latest Completed Return");
  });
  it("preserves server alert order, links and the first-five limit", () => {
    const data = { ...empty, alerts: Array.from({ length: 6 }, (_, i) => ({ id: `a${i}`, code: "backtests_failed", severity: "warning" as const, count: i + 1, occurred_at: null, href: `/backtest-tasks?item=${i}` })) };
    const html = renderToStaticMarkup(createElement(Providers, null, createElement(DashboardContent, { data })));
    expect(html).toContain('href="/backtest-tasks?item=4"');
    expect(html).not.toContain('href="/backtest-tasks?item=5"');
    expect(html).toContain('aria-expanded="false"');
    const skeleton = renderToStaticMarkup(createElement(Providers, null, createElement(DashboardSkeleton)));
    expect(skeleton).toContain('role="status"');
  });
});
