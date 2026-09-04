import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { StrategyUniversePicker } from "./StrategyUniversePicker";
import { MESSAGES } from "@/i18n";
import { useI18n } from "@/i18n/provider";

vi.mock("next/router", () => ({ useRouter: () => ({ asPath: "/strategies/new" }) }));
vi.mock("@/i18n/provider", () => ({ useI18n: vi.fn() }));

it.each(["zh-CN", "en-US"] as const)("shows only eight symbols for a large universe in %s", (locale) => {
  vi.mocked(useI18n).mockReturnValue({ locale, messages: MESSAGES[locale], setLocale: vi.fn(), t: vi.fn() });
  const symbols = Array.from({ length: 5555 }, (_, i) => `${String(i + 1).padStart(6, "0")}.SZ`);
  const html = renderToStaticMarkup(createElement(StrategyUniversePicker, { symbols, onChange: vi.fn() }));
  expect(html).toContain("5555");
  expect(html).toContain("5547");
  expect(html).toContain(symbols[7]);
  expect(html).not.toContain(symbols[8]);
  expect(html).not.toContain(symbols[5554]);
  expect(html).not.toContain("<textarea");
  expect(html).not.toContain('type="checkbox"');
  expect(symbols).toHaveLength(5555);
});

it("explains the unchanged all-US default for an empty universe", () => {
  vi.mocked(useI18n).mockReturnValue({ locale: "zh-CN", messages: MESSAGES["zh-CN"], setLocale: vi.fn(), t: vi.fn() });
  expect(renderToStaticMarkup(createElement(StrategyUniversePicker, { symbols: [], onChange: vi.fn() })))
    .toContain(MESSAGES["zh-CN"].strategyCreate.basics.universeEmpty);
});
