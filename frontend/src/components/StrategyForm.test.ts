import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import StrategyForm from "./StrategyForm";
import type { StrategyOut } from "@/types/strategy";

vi.mock("next/router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/i18n/provider", () => ({ useI18n: () => ({ locale: "zh-CN" }) }));

it("renders fractional strategy risks as percentage inputs without converting ATR multiples", () => {
  const strategy = {
    id: "test", name: "Test", strategy_type: "trend", status: "draft", version: 1,
    params: { signal: { atr_multiplier: 1.5 }, universe: {}, risk: { stop_loss_pct: 0.005, position_size_pct: 0.15 } },
  } as StrategyOut;
  const html = renderToStaticMarkup(createElement(StrategyForm, { initialStrategy: strategy, mode: "edit" }));
  expect(html).toMatch(/固定止损比例[^<]*.*?value="0.5"/);
  expect(html).toMatch(/单票仓位比例[^<]*.*?value="15"/);
  expect(html).toMatch(/ATR 止损倍数.*?value="1.5"/);
  expect(strategy.params.risk).toMatchObject({ stop_loss_pct: 0.005, position_size_pct: 0.15 });
});
