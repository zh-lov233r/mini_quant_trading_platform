import http from "@/api/client";
import type { StockFilters, StockMarket, StockSearchResult } from "@/types/stock-screening";

export function searchStocks(filters: StockFilters, page: number, signal: AbortSignal) {
  const params = new URLSearchParams({ limit: "20", offset: String(page * 20) });
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return http<StockSearchResult>(`/api/stock-screening/stocks?${params}`, { method: "GET", signal });
}

export function getStockIndustries(market: StockMarket, signal: AbortSignal) {
  return http<string[]>(`/api/stock-screening/industries?market=${market}`, { method: "GET", signal });
}

export function resolveStockSymbols(filters: StockFilters, signal: AbortSignal) {
  return http<string[]>("/api/stock-screening/symbols", { method: "POST", body: JSON.stringify(filters), signal });
}
