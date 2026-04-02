import http from "@/api/client";
import type {
  LatestPaperTradingTradeDateOut,
  MultiStrategyPaperTradingRunOut,
  MultiStrategyPaperTradingRunRequest,
  PaperTradingRunOut,
  PaperTradingRunRequest,
} from "@/types/paper-trading";

export function getLatestPaperTradingTradeDate(): Promise<LatestPaperTradingTradeDateOut> {
  return http<LatestPaperTradingTradeDateOut>("/api/paper-trading/latest-trade-date", {
    method: "GET",
  });
}

export function createPaperTradingRun(
  payload: PaperTradingRunRequest
): Promise<PaperTradingRunOut> {
  return http<PaperTradingRunOut>("/api/paper-trading/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createMultiStrategyPaperTradingRun(
  payload: MultiStrategyPaperTradingRunRequest
): Promise<MultiStrategyPaperTradingRunOut> {
  return http<MultiStrategyPaperTradingRunOut>("/api/paper-trading/run-multi", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
