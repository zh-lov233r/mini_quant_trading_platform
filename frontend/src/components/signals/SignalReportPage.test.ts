import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement, type ReactNode } from "react";

vi.mock("next/router", () => ({ useRouter: () => ({ query: {}, push: vi.fn() }) }));
vi.mock("@/i18n/provider", () => ({ useI18n: () => ({ locale: "en-US" }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: ReactNode }) => createElement("main", null, children) }));
import SignalReportPage from "@/pages/signals/reports/[reportId]";

describe("signal report strategy filter", () => {
  it("shows All strategies before a strategy is selected", () => {
    const markup = renderToStaticMarkup(createElement(SignalReportPage));
    const trigger = markup.match(/<button[^>]*aria-label="Strategy instances"[^>]*>[\s\S]*?<\/button>/)?.[0];
    expect(trigger).toContain("All strategies");
    expect(trigger).not.toContain("data-placeholder");
  });
});
