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

export interface PaperTradingAccountUpdate {
  name: string;
  api_key_env: string;
  secret_key_env: string;
  base_url: string;
  timeout_seconds: number;
  notes?: string | null;
  status?: string;
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
  auto_run_enabled: boolean;
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

export interface BrokerSyncOut {
  status: string;
  fetched_at?: string | null;
  error?: string | null;
}

export interface BrokerAccountSummaryOut {
  broker_account_id?: string | null;
  account_number?: string | null;
  status?: string | null;
  currency?: string | null;
  cash?: number | null;
  equity?: number | null;
  buying_power?: number | null;
  portfolio_value?: number | null;
  long_market_value?: number | null;
  short_market_value?: number | null;
  last_equity?: number | null;
  daytrade_count?: number | null;
  pattern_day_trader?: boolean | null;
  trading_blocked?: boolean | null;
  transfers_blocked?: boolean | null;
  account_blocked?: boolean | null;
}

export interface BrokerClockOut {
  timestamp?: string | null;
  is_open?: boolean | null;
  next_open?: string | null;
  next_close?: string | null;
}

export interface BrokerPortfolioHistoryPointOut {
  ts: string;
  equity: number;
  profit_loss?: number | null;
  profit_loss_pct?: number | null;
}

export interface BrokerPortfolioHistoryOut {
  range_label: string;
  start_at: string;
  end_at: string;
  base_value?: number | null;
  start_value: number;
  end_value: number;
  absolute_change: number;
  percent_change?: number | null;
  points: BrokerPortfolioHistoryPointOut[];
}

export interface BrokerPositionOut {
  symbol: string;
  side?: string | null;
  qty?: number | null;
  market_value?: number | null;
  cost_basis?: number | null;
  avg_entry_price?: number | null;
  unrealized_pl?: number | null;
  unrealized_plpc?: number | null;
  current_price?: number | null;
  change_today?: number | null;
}

export interface BrokerOrderOut {
  id?: string | null;
  client_order_id?: string | null;
  symbol?: string | null;
  side?: string | null;
  type?: string | null;
  time_in_force?: string | null;
  status?: string | null;
  qty?: number | null;
  filled_qty?: number | null;
  filled_avg_price?: number | null;
  limit_price?: number | null;
  stop_price?: number | null;
  submitted_at?: string | null;
  filled_at?: string | null;
  canceled_at?: string | null;
}

export interface PaperAccountTransactionOut {
  id: string;
  run_id?: string | null;
  ts?: string | null;
  portfolio_name?: string | null;
  strategy_id: string;
  strategy_name?: string | null;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  fee: number;
  order_id?: string | null;
  source?: string | null;
  broker_status?: string | null;
  net_cash_flow: number;
}

export interface StrategyPortfolioWorkspaceOut extends StrategyPortfolioOverviewOut {
  transaction_count: number;
  net_cash_flow: number;
  latest_transaction_at?: string | null;
  latest_run_return_pct?: number | null;
}

export interface PaperTradingWorkspaceStatsOut {
  portfolio_count: number;
  active_portfolio_count: number;
  active_allocation_count: number;
  active_strategy_count: number;
  position_count: number;
  order_count: number;
  transaction_count: number;
}

export interface PaperTradingWorkspaceOut {
  account: PaperTradingAccountOut;
  broker_sync: BrokerSyncOut;
  broker_account?: BrokerAccountSummaryOut | null;
  broker_clock?: BrokerClockOut | null;
  portfolio_history?: BrokerPortfolioHistoryOut | null;
  positions: BrokerPositionOut[];
  recent_orders: BrokerOrderOut[];
  recent_transactions: PaperAccountTransactionOut[];
  portfolios: StrategyPortfolioWorkspaceOut[];
  stats: PaperTradingWorkspaceStatsOut;
}

export interface DeleteResultOut {
  id: string;
  deleted: boolean;
}
