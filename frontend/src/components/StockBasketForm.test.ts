import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { StockBasketForm } from "@/pages/stock-baskets";
import type { StockBasketOut } from "@/types/stock-basket";

vi.mock("@/i18n/provider", () => ({ useI18n: () => ({ locale: "zh-CN" }) }));

it("does not mount the 5,555-line editor when editing basket metadata", () => {
  const basket: StockBasketOut = {
    id: "large-basket", name: "Large basket", description: "Research only", status: "draft",
    symbols: Array.from({ length: 5555 }, (_, i) => `${String(i + 1).padStart(6, "0")}.SZ`),
    symbol_count: 5555,
  };
  const html = renderToStaticMarkup(createElement(StockBasketForm, { basket, onSaved: vi.fn() }));
  expect(html).not.toContain('aria-label="股票代码"');
  expect(html).not.toContain(basket.symbols[5554]);
  expect(html).toContain('aria-expanded="false"');
  expect(html).toContain("编辑股票代码（5555 只）");
  expect(html).toContain(`预览: ${basket.symbols.slice(0, 12).join(", ")} +5543`);
  expect(html).toContain('value="Large basket"');
  expect(html).toContain("Research only");
});

it("starts a new form with empty fields rather than leaking the last edited basket", () => {
  const html = renderToStaticMarkup(createElement(StockBasketForm, { basket: null, onSaved: vi.fn() }));
  expect(html).toContain("创建股票组合");
  expect(html).toMatch(/aria-label="组合名称"[^>]*value=""/);
  expect(html).toMatch(/aria-label="股票代码"[^>]*><\/textarea>/);
  expect(html).toContain('spellcheck="false" autoCorrect="off" autoCapitalize="none"');
});
