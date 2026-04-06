import http from "@/api/client";
import type {
  DeleteResultOut,
  PaperTradingAccountCreate,
  PaperTradingAccountOut,
  PaperTradingAccountOverviewOut,
  PaperTradingAccountUpdate,
  PaperTradingWorkspaceOut,
  StrategyPortfolioCreate,
  StrategyPortfolioOut,
  StrategyPortfolioRename,
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

export function updatePaperAccount(
  accountId: string,
  payload: PaperTradingAccountUpdate
): Promise<PaperTradingAccountOut> {
  return http<PaperTradingAccountOut>(`/api/paper-accounts/${accountId}`, {
    method: "PATCH",
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

export function getPaperAccountWorkspace(
  accountId: string
): Promise<PaperTradingWorkspaceOut> {
  return http<PaperTradingWorkspaceOut>(
    `/api/paper-accounts/${accountId}/workspace`,
    {
      method: "GET",
    }
  );
}

export function deletePaperAccount(
  accountId: string
): Promise<DeleteResultOut> {
  return http<DeleteResultOut>(`/api/paper-accounts/${accountId}`, {
    method: "DELETE",
  });
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

export function renameStrategyPortfolio(
  portfolioId: string,
  payload: StrategyPortfolioRename
): Promise<StrategyPortfolioOut> {
  return http<StrategyPortfolioOut>(`/api/strategy-portfolios/${portfolioId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function archiveStrategyPortfolio(
  portfolioId: string
): Promise<StrategyPortfolioOut> {
  return http<StrategyPortfolioOut>(
    `/api/strategy-portfolios/${portfolioId}/archive`,
    {
      method: "PATCH",
    }
  );
}

export function deleteStrategyPortfolio(
  portfolioId: string
): Promise<DeleteResultOut> {
  return http<DeleteResultOut>(`/api/strategy-portfolios/${portfolioId}`, {
    method: "DELETE",
  });
}
