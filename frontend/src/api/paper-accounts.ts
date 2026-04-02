import http from "@/api/client";
import type {
  PaperTradingAccountCreate,
  PaperTradingAccountOut,
  PaperTradingAccountOverviewOut,
  StrategyPortfolioCreate,
  StrategyPortfolioOut,
} from "@/types/paper-account";

export function listPaperAccounts(
  status?: string
): Promise<PaperTradingAccountOut[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return http<PaperTradingAccountOut[]>(`/api/paper-accounts${query}`, {
    method: "GET",
  });
}

export function createPaperAccount(
  payload: PaperTradingAccountCreate
): Promise<PaperTradingAccountOut> {
  return http<PaperTradingAccountOut>("/api/paper-accounts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPaperAccountOverview(
  accountId: string
): Promise<PaperTradingAccountOverviewOut> {
  return http<PaperTradingAccountOverviewOut>(
    `/api/paper-accounts/${accountId}/overview`,
    {
      method: "GET",
    }
  );
}

export function listStrategyPortfolios(
  paperAccountId?: string,
  status?: string
): Promise<StrategyPortfolioOut[]> {
  const params = new URLSearchParams();
  if (paperAccountId) {
    params.set("paper_account_id", paperAccountId);
  }
  if (status) {
    params.set("status", status);
  }
  const query = params.toString();
  return http<StrategyPortfolioOut[]>(
    `/api/strategy-portfolios${query ? `?${query}` : ""}`,
    {
      method: "GET",
    }
  );
}

export function createStrategyPortfolio(
  payload: StrategyPortfolioCreate
): Promise<StrategyPortfolioOut> {
  return http<StrategyPortfolioOut>("/api/strategy-portfolios", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
