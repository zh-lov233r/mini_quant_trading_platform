export interface StrategyAllocationUpsert {
  strategy_id: string;
  portfolio_name: string;
  allocation_pct: number;
  capital_base?: number | null;
  allow_fractional?: boolean;
  auto_run_enabled?: boolean;
  notes?: string | null;
  status?: string;
}

export interface StrategyAllocationOut {
  id: string;
  strategy_id: string;
  strategy_name?: string | null;
  portfolio_name: string;
  paper_account_id?: string | null;
  paper_account_name?: string | null;
  allocation_pct: number;
  capital_base?: number | null;
  allow_fractional: boolean;
  auto_run_enabled: boolean;
  notes?: string | null;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}
