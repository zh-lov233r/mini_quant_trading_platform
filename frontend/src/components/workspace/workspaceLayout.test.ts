import { describe, expect, it } from "vitest";

import {
  isWorkspaceRouteActive,
  parseStoredSidebarCollapsed,
  resolveStableRowId,
  serializeSidebarCollapsed,
  shouldVirtualizeRows,
  WORKSPACE_NAV_ITEMS,
} from "./workspaceLayout";

describe("workspace navigation", () => {
  it("keeps the seven primary workbench destinations in a stable order", () => {
    expect(WORKSPACE_NAV_ITEMS.map((item) => item.href)).toEqual([
      "/dashboard", "/strategies", "/stock-baskets", "/backtests", "/backtest-tasks", "/research", "/paper-trading",
    ]);
  });

  it("matches detail routes without matching unrelated prefixes", () => {
    expect(isWorkspaceRouteActive("/backtests/run-1", "/backtests")).toBe(true);
    expect(isWorkspaceRouteActive("/strategies/new", "/strategies")).toBe(true);
    expect(isWorkspaceRouteActive("/strategy-allocations", "/strategies")).toBe(false);
  });
});

describe("sidebar persistence", () => {
  it("accepts only versioned state values", () => {
    expect(parseStoredSidebarCollapsed("collapsed")).toBe(true);
    expect(parseStoredSidebarCollapsed("expanded")).toBe(false);
    expect(parseStoredSidebarCollapsed("true")).toBeNull();
    expect(parseStoredSidebarCollapsed(null)).toBeNull();
    expect(serializeSidebarCollapsed(true)).toBe("collapsed");
  });
});

describe("dense table helpers", () => {
  it("virtualizes at the configured boundary", () => {
    expect(shouldVirtualizeRows(199)).toBe(false);
    expect(shouldVirtualizeRows(200)).toBe(true);
    expect(shouldVirtualizeRows(20, 20)).toBe(true);
  });

  it("resolves stable row ids before falling back to the index", () => {
    expect(resolveStableRowId({ id: "run-1" }, 4)).toBe("run-1");
    expect(resolveStableRowId({ symbol: "AAPL" }, 4, (row) => row.symbol)).toBe("AAPL");
    expect(resolveStableRowId({ symbol: "AAPL" }, 4)).toBe("4");
  });
});
