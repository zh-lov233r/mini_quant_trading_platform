import http from "@/api/client";
import type {
  StrategyAllocationOut,
  StrategyAllocationUpsert,
} from "@/types/strategy-allocation";

export function listStrategyAllocations(
  portfolioName?: string
): Promise<StrategyAllocationOut[]> {
  const query = portfolioName
    ? `?portfolio_name=${encodeURIComponent(portfolioName)}`
    : "";
  return http<StrategyAllocationOut[]>(`/api/strategy-allocations${query}`, {
    method: "GET",
  });
}

export function upsertStrategyAllocation(
  payload: StrategyAllocationUpsert
): Promise<StrategyAllocationOut> {
  return http<StrategyAllocationOut>("/api/strategy-allocations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
