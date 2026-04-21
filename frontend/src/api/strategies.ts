import http, { API_BASE, readApiError } from "@/api/client";
import type {
  StrategyCatalogItem,
  StrategyConfigUpdate,
  StrategyCreate,
  StrategyDeleteOut,
  StrategyFeatureSupport,
  StrategyRename,
  StrategyOut,
  StrategyRuntimeOut,
} from "@/types/strategy";

export const STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX =
  "strategy_delete_requires_position_close:";
export const STRATEGY_DELETE_MANUAL_RECONCILE_PREFIX =
  "strategy_delete_requires_manual_reconcile:";

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
    throw await readApiError(res, "/api/strategies");
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

export function getStrategyFeatureSupport(): Promise<StrategyFeatureSupport> {
  return http<StrategyFeatureSupport>("/api/strategies/feature-support", {
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

export function renameStrategy(
  strategyId: string,
  payload: StrategyRename
): Promise<StrategyOut> {
  return http<StrategyOut>(`/api/strategies/${strategyId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function updateStrategyConfig(
  strategyId: string,
  payload: StrategyConfigUpdate
): Promise<StrategyOut> {
  return http<StrategyOut>(`/api/strategies/${strategyId}/config`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteStrategy(
  strategyId: string,
  options?: { closePositions?: boolean }
): Promise<StrategyDeleteOut> {
  const params = new URLSearchParams();
  if (options?.closePositions) {
    params.set("close_positions", "true");
  }
  const query = params.toString();
  return http<StrategyDeleteOut>(`/api/strategies/${strategyId}${query ? `?${query}` : ""}`, {
    method: "DELETE",
  });
}

export function isStrategyDeleteCloseRequired(detail?: string | null): boolean {
  return String(detail || "")
    .trim()
    .toLowerCase()
    .startsWith(STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX);
}

export function extractStrategyDeleteConflictMessage(detail?: string | null): string {
  const raw = String(detail || "").trim();
  if (!raw) {
    return "";
  }

  const lower = raw.toLowerCase();
  if (lower.startsWith(STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX)) {
    return raw.slice(STRATEGY_DELETE_CLOSE_REQUIRED_PREFIX.length).trim();
  }
  if (lower.startsWith(STRATEGY_DELETE_MANUAL_RECONCILE_PREFIX)) {
    return raw.slice(STRATEGY_DELETE_MANUAL_RECONCILE_PREFIX.length).trim();
  }
  return raw;
}
