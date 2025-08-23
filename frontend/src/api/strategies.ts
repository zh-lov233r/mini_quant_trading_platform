import http from "./client";
import type { StrategyCreate, StrategyOut } from "@/types/strategy";

export function createStrategy(payload: StrategyCreate, idem?: string) {
  return http<StrategyOut>("/api/strategies", {
    method: "POST",
    body: JSON.stringify(payload),
    headers: idem ? { "Idempotency-Key": idem } : undefined,
  });
}
