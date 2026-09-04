import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { MESSAGES } from "@/i18n";
import { useI18n } from "@/i18n/provider";
import { STRATEGY_GUIDANCE, setPathValue } from "@/utils/strategyCreateGuidance";
import BottomReversalFields, { BOTTOM_REVERSAL_TYPES } from "./BottomReversalFields";

vi.mock("@/i18n/provider", () => ({ useI18n: vi.fn() }));

it.each(["zh-CN", "en-US"] as const)("renders every bottom pattern's numeric fields with labels in %s", (locale) => {
  vi.mocked(useI18n).mockReturnValue({ locale, messages: MESSAGES[locale], setLocale: vi.fn(), t: vi.fn() });
  for (const type of BOTTOM_REVERSAL_TYPES) {
    let params: Record<string, unknown> = {};
    for (const field of [...STRATEGY_GUIDANCE[type].signal, ...STRATEGY_GUIDANCE[type].risk]) {
      expect(MESSAGES[locale].strategyCreate.fields[field.key as keyof typeof MESSAGES[typeof locale]["strategyCreate"]["fields"]]).toBeDefined();
      params = setPathValue(params, field.path, field.min || 1);
    }
    params = setPathValue(params, "risk.stage_1_target_pct", .2);
    params = setPathValue(params, "risk.stage_2_target_pct", .5);
    params = setPathValue(params, "risk.stage_3_target_pct", 1);
    const html = renderToStaticMarkup(createElement(BottomReversalFields, { type, params, onChange: vi.fn() }));
    expect(html).toContain('id="bottom-risk.stage_2_target_pct"');
    expect(html).toContain('value="50"');
    expect(html).not.toContain("undefined");
  }
});

it("shows invalid rebound ranges without discarding the entered values", () => {
  vi.mocked(useI18n).mockReturnValue({ locale: "en-US", messages: MESSAGES["en-US"], setLocale: vi.fn(), t: vi.fn() });
  const html = renderToStaticMarkup(createElement(BottomReversalFields, {
    type: "double_bottom", onChange: vi.fn(), params: {
      signal: { rebound_volume_ratio_min: 2, rebound_volume_ratio_max: 1 },
      risk: { stage_1_target_pct: .2, stage_2_target_pct: .5, stage_3_target_pct: 1 },
    },
  }));
  expect(html).toContain('aria-invalid="true"');
  expect(html).toContain(MESSAGES["en-US"].strategyCreate.errors.reboundVolumeRange);
  expect(html).toContain('value="2"');
  expect(html).toContain('step="any"');
});
