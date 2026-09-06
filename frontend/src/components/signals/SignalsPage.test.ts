import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/provider", () => ({ useI18n: () => ({ locale: "en-US" }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: ReactNode }) => createElement("main", null, children) }));
import SignalsPage from "@/pages/signals";

describe("signal center page", () => {
  it("keeps manual submission while omitting the scan history module", () => {
    const markup = renderToStaticMarkup(createElement(SignalsPage));
    expect(markup).toContain("Start scan");
    expect(markup).toContain("this page does not show scan history, progress, or results");
    expect(markup).toContain("Signal reports");
    expect(markup).not.toContain(">Scans<");
    expect(markup).not.toContain("Generate report");
  });
});
