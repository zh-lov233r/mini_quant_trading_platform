export const STOCK_PAGE_SIZE = 20;

export function addBasketSymbols(current: string[], additions: string[]): string[] {
  return [...new Set([...current, ...additions].map((symbol) => symbol.trim().toUpperCase()).filter(Boolean))];
}

export function removeBasketSymbols(current: string[], removed: readonly string[]): string[] {
  const excluded = new Set(removed);
  return current.filter((symbol) => !excluded.has(symbol));
}

export function selectedSymbolPage(symbols: string[], query: string, page: number) {
  const filtered = symbols.filter((symbol) => symbol.includes(query.trim().toUpperCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / STOCK_PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  return { items: filtered.slice(currentPage * STOCK_PAGE_SIZE, (currentPage + 1) * STOCK_PAGE_SIZE),
    total: filtered.length, page: currentPage, pages };
}

export function singleTicker(value: string): string | null {
  const ticker = value.trim().toUpperCase();
  return /^[A-Z0-9][A-Z0-9.\-^/]{0,31}$/.test(ticker) ? ticker : null;
}
