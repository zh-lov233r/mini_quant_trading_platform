export function pageCount(totalItems: number, pageSize: number): number {
  if (!Number.isFinite(totalItems) || !Number.isFinite(pageSize) || pageSize <= 0) {
    return 1;
  }
  return Math.max(1, Math.ceil(Math.max(0, totalItems) / pageSize));
}

export function clampPageIndex(pageIndex: number, totalItems: number, pageSize: number): number {
  const lastPageIndex = pageCount(totalItems, pageSize) - 1;
  if (!Number.isFinite(pageIndex)) {
    return 0;
  }
  return Math.min(Math.max(0, Math.trunc(pageIndex)), lastPageIndex);
}

export function paginateItems<T>(items: readonly T[], pageIndex: number, pageSize: number): T[] {
  const safePageSize = Number.isFinite(pageSize) && pageSize > 0 ? Math.trunc(pageSize) : items.length || 1;
  const safePageIndex = clampPageIndex(pageIndex, items.length, safePageSize);
  const start = safePageIndex * safePageSize;
  return items.slice(start, start + safePageSize);
}
