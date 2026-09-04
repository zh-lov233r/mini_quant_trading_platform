import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import BacktestProgressBar from "./BacktestProgressBar";
import type { BacktestProgressPhase } from "@/types/backtest";

it.each<BacktestProgressPhase>(["running", "completed", "failed", "cancelled"])(
  "preserves the actual percentage and exposes %s for motion styling",
  (phase) => {
    const html = renderToStaticMarkup(createElement(BacktestProgressBar, {
      progress: { phase, percent: 42.6, attempt: 1, max_attempts: 3, updated_at: "2026-09-03T00:00:00Z" },
      isZh: false,
    }));
    expect(html).toContain('aria-valuenow="42.6"');
    expect(html).toContain('width:42.6%');
    expect(html).toContain('42.6%');
    expect(html).toContain(`data-phase="${phase}"`);
  },
);
