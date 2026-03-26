import http, { API_BASE } from "@/api/client";
import type {
  StrategyCatalogItem,
  StrategyCreate,
  StrategyOut,
  StrategyRuntimeOut,
} from "@/types/strategy";

export async function createStrategy(
  payload: StrategyCreate,
  idempotencyKey?: string
): Promise<StrategyOut> {
  const res = await fetch(`${API_BASE}/api/strategies`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${txt}`.trim());
  }
  return res.json() as Promise<StrategyOut>;
}

export function listStrategies(): Promise<StrategyOut[]> {
  return http<StrategyOut[]>("/api/strategies", { method: "GET" });
}

export function getStrategyCatalog(): Promise<StrategyCatalogItem[]> {
  return http<StrategyCatalogItem[]>("/api/strategies/catalog", {
    method: "GET",
  });
}

export function getStrategy(strategyId: string): Promise<StrategyOut> {
  return http<StrategyOut>(`/api/strategies/${strategyId}`, { method: "GET" });
}

export function getStrategyRuntime(strategyId: string): Promise<StrategyRuntimeOut> {
  return http<StrategyRuntimeOut>(`/api/strategies/${strategyId}/runtime`, {
    method: "GET",
  });
}
