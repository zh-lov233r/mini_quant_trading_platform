export interface PaperTradingAccountCreate {
  name: string;
  broker?: string;
  mode?: string;
  api_key_env?: string;
  secret_key_env?: string;
  base_url?: string;
  timeout_seconds?: number;
  notes?: string | null;
  status?: string;
}

export interface PaperTradingAccountOut {
  id: string;
  name: string;
  broker: string;
  mode: string;
  api_key_env: string;
  secret_key_env: string;
  base_url: string;
  timeout_seconds: number;
  notes?: string | null;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface StrategyPortfolioCreate {
  paper_account_id: string;
  name: string;
  description?: string | null;
  strategy_ids: string[];
  status?: string;
}

export interface StrategyPortfolioRename {
  name: string;
}

export interface StrategyPortfolioOut {
  id: string;
  paper_account_id: string;
  paper_account_name?: string | null;
  name: string;
  description?: string | null;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PortfolioStrategyOverviewOut {
  strategy_id: string;
  strategy_name: string;
  strategy_type: string;
  strategy_status: string;
  allocation_pct: number;
  capital_base?: number | null;
  allow_fractional: boolean;
  allocation_status: string;
  notes?: string | null;
  latest_run_id?: string | null;
  latest_run_status?: string | null;
  latest_run_requested_at?: string | null;
  latest_run_equity?: number | null;
}

export interface StrategyPortfolioOverviewOut {
  id: string;
  paper_account_id: string;
  name: string;
  description?: string | null;
  status: string;
  allocation_count: number;
  active_allocation_count: number;
  allocated_strategy_count: number;
  active_allocation_pct_total: number;
  latest_run_id?: string | null;
  latest_run_status?: string | null;
  latest_run_requested_at?: string | null;
  latest_run_equity?: number | null;
  strategies: PortfolioStrategyOverviewOut[];
}

export interface PaperTradingAccountOverviewOut {
  account: PaperTradingAccountOut;
  portfolio_count: number;
  active_portfolio_count: number;
  active_allocation_count: number;
  active_strategy_count: number;
  portfolios: StrategyPortfolioOverviewOut[];
}
