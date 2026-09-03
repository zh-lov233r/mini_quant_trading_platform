import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("keeps lifecycle collapse controls on the summary row, not the expanded detail panel", () => {
  const source = readFileSync(new URL("../pages/backtests/[runId].tsx", import.meta.url), "utf8");
  const detail = source.slice(source.indexOf("function LifecycleDetailPanel("), source.indexOf("function PositionLifecycleCard("));
  expect(detail).not.toContain("onCollapse");
  expect(detail).not.toContain("collapsePointer");
  expect(source).toContain('aria-expanded={expanded}');
  expect(source).toContain('event.key !== "Enter" && event.key !== " "');
});
