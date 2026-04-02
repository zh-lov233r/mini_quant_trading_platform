export interface PaperTradingRunRequest {
  strategy_id: string;
  trade_date: string;
  portfolio_name: string;
  basket_id?: string | null;
  submit_orders?: boolean;
}

export interface MultiStrategyPaperTradingRunRequest {
  trade_date: string;
  portfolio_name: string;
  submit_orders?: boolean;
  continue_on_error?: boolean;
}

export interface PaperTradingRunOut {
  run_id: string;
  strategy_id: string;
  status: string;
  trade_date: string;
  portfolio_name: string;
  allocation_pct: number;
  capital_base: number;
  signal_count: number;
  order_count: number;
  submitted_order_count: number;
  skipped_order_count: number;
  failed_order_count: number;
  final_cash: number;
  final_equity: number;
}

export interface MultiStrategyPaperTradingRunOut {
  portfolio_name: string;
  trade_date: string;
  total_runs: number;
  completed_runs: number;
  failed_runs: number;
  results: PaperTradingRunOut[];
}
