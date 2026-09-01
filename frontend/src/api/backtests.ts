import http from "@/api/client";
import type {
  BacktestCreate,
  BacktestDeleteResult,
  BacktestComparisonCurvesOut,
  BacktestDetailOut,
  BacktestEquityPoint,
  BacktestPageOut,
  BacktestRunOut,
  BacktestSignalOut,
  BacktestSummaryOut,
  BacktestTransactionOut,
  BacktestWorkerStatus,
  SupportResistanceBacktestOut,
} from "@/types/backtest";

export function createBacktest(payload: BacktestCreate): Promise<BacktestRunOut> {
  return http<BacktestRunOut>("/api/backtests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface SupportResistanceFilters {
  symbol?: string;
  zone_key?: string;
  start_date?: string;
  end_date?: string;
}

const supportResistanceRequestCache = new Map<string, Promise<SupportResistanceBacktestOut>>();

export function getBacktestSupportResistance(
  runId: string,
  filters: SupportResistanceFilters = {},
): Promise<SupportResistanceBacktestOut> {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  query.sort();
  const suffix = query.size ? `?${query.toString()}` : "";
  const path = `/api/backtests/${encodeURIComponent(runId)}/support-resistance${suffix}`;
  const cached = supportResistanceRequestCache.get(path);
  if (cached) return cached;

  const request = http<SupportResistanceBacktestOut>(
    path,
    { method: "GET" },
  )
    .then((detail) => {
      if (detail.materialization?.status !== "completed") {
        supportResistanceRequestCache.delete(path);
      }
      return detail;
    })
    .catch((error) => {
      supportResistanceRequestCache.delete(path);
      throw error;
    });
  supportResistanceRequestCache.set(path, request);
  return request;
}

export function listBacktests(strategyId?: string): Promise<BacktestRunOut[]> {
  const query = strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : "";
  return http<BacktestRunOut[]>(`/api/backtests${query}`, {
    method: "GET",
  });
}

export function getBacktestWorkerStatus(): Promise<BacktestWorkerStatus> {
  return http<BacktestWorkerStatus>("/api/backtests/worker-status", {
    method: "GET",
  });
}

export function getBacktest(runId: string): Promise<BacktestDetailOut> {
  return http<BacktestDetailOut>(`/api/backtests/${encodeURIComponent(runId)}`, {
    method: "GET",
  });
}

export function getBacktestSummary(runId: string): Promise<BacktestSummaryOut> {
  return http<BacktestSummaryOut>(`/api/backtests/${encodeURIComponent(runId)}/summary`, {
    method: "GET",
  });
}

export function getBacktestEquity(
  runId: string,
  maxPoints = 1500,
): Promise<BacktestEquityPoint[]> {
  const query = new URLSearchParams({ max_points: String(maxPoints), shape: "chart" });
  return http<BacktestEquityPoint[]>(
    `/api/backtests/${encodeURIComponent(runId)}/equity?${query.toString()}`,
    { method: "GET" },
  );
}

export function getBacktestComparisonCurves(
  runId: string,
  maxPoints = 1500,
): Promise<BacktestComparisonCurvesOut> {
  const query = new URLSearchParams({ max_points: String(maxPoints) });
  return http<BacktestComparisonCurvesOut>(
    `/api/backtests/${encodeURIComponent(runId)}/comparison-curves?${query.toString()}`,
    { method: "GET" },
  );
}

interface BacktestPageFilters {
  limit?: number;
  cursor?: string | null;
  symbol?: string | null;
}

function pageQuery(filters: BacktestPageFilters): string {
  const query = new URLSearchParams();
  query.set("limit", String(filters.limit ?? 100));
  if (filters.cursor) query.set("cursor", filters.cursor);
  if (filters.symbol) query.set("symbol", filters.symbol);
  return query.toString();
}

export function getBacktestSignals(
  runId: string,
  filters: BacktestPageFilters = {},
): Promise<BacktestPageOut<BacktestSignalOut>> {
  return http<BacktestPageOut<BacktestSignalOut>>(
    `/api/backtests/${encodeURIComponent(runId)}/signals?${pageQuery(filters)}`,
    { method: "GET" },
  );
}

export function getBacktestTransactions(
  runId: string,
  filters: BacktestPageFilters = {},
): Promise<BacktestPageOut<BacktestTransactionOut>> {
  return http<BacktestPageOut<BacktestTransactionOut>>(
    `/api/backtests/${encodeURIComponent(runId)}/transactions?${pageQuery(filters)}`,
    { method: "GET" },
  );
}

export function cancelBacktest(runId: string): Promise<BacktestRunOut> {
  return http<BacktestRunOut>(`/api/backtests/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export function deleteBacktest(runId: string): Promise<BacktestDeleteResult> {
  return http<BacktestDeleteResult>(`/api/backtests/${encodeURIComponent(runId)}`, {
    method: "DELETE",
  });
}
