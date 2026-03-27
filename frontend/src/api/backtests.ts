import http from "@/api/client";
import type {
  BacktestCreate,
  BacktestDetailOut,
  BacktestRunOut,
} from "@/types/backtest";

export function createBacktest(payload: BacktestCreate): Promise<BacktestRunOut> {
  return http<BacktestRunOut>("/api/backtests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listBacktests(strategyId?: string): Promise<BacktestRunOut[]> {
  const query = strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : "";
  return http<BacktestRunOut[]>(`/api/backtests${query}`, {
    method: "GET",
  });
}

export function getBacktest(runId: string): Promise<BacktestDetailOut> {
  return http<BacktestDetailOut>(`/api/backtests/${encodeURIComponent(runId)}`, {
    method: "GET",
  });
}
