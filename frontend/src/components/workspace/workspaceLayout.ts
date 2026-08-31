export const SIDEBAR_STORAGE_KEY = "quant.workspace.sidebar.v1";
export const DEFAULT_VIRTUALIZE_ABOVE = 200;

export type WorkspaceNavKey =
  | "dashboard"
  | "strategies"
  | "stockBaskets"
  | "backtests"
  | "research"
  | "paperTrading";

export interface WorkspaceNavItem {
  href: string;
  key: WorkspaceNavKey;
}

export const WORKSPACE_NAV_ITEMS: WorkspaceNavItem[] = [
  { href: "/dashboard", key: "dashboard" },
  { href: "/strategies", key: "strategies" },
  { href: "/stock-baskets", key: "stockBaskets" },
  { href: "/backtests", key: "backtests" },
  { href: "/research", key: "research" },
  { href: "/paper-trading", key: "paperTrading" },
];

export function isWorkspaceRouteActive(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  if (href === "/strategies" && pathname === "/strategies/new") return true;
  return pathname.startsWith(`${href}/`);
}

export function parseStoredSidebarCollapsed(value: string | null): boolean | null {
  if (value === "collapsed") return true;
  if (value === "expanded") return false;
  return null;
}

export function serializeSidebarCollapsed(collapsed: boolean): string {
  return collapsed ? "collapsed" : "expanded";
}

export function shouldVirtualizeRows(
  rowCount: number,
  virtualizeAbove = DEFAULT_VIRTUALIZE_ABOVE
): boolean {
  return Number.isFinite(rowCount) && rowCount >= Math.max(1, virtualizeAbove);
}

export function resolveStableRowId<T>(
  row: T,
  index: number,
  getRowId?: (row: T, index: number) => string
): string {
  if (getRowId) return getRowId(row, index);
  if (row && typeof row === "object" && "id" in row) {
    const value = (row as { id?: unknown }).id;
    if (typeof value === "string" || typeof value === "number") return String(value);
  }
  return String(index);
}
