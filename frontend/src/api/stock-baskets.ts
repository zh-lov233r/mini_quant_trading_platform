import http from "@/api/client";
import type { StockBasketCreate, StockBasketOut } from "@/types/stock-basket";

export function listStockBaskets(): Promise<StockBasketOut[]> {
  return http<StockBasketOut[]>("/api/stock-baskets", { method: "GET" });
}

export function createStockBasket(payload: StockBasketCreate): Promise<StockBasketOut> {
  return http<StockBasketOut>("/api/stock-baskets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateStockBasket(id: string, payload: StockBasketCreate): Promise<StockBasketOut> {
  return http<StockBasketOut>(`/api/stock-baskets/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
