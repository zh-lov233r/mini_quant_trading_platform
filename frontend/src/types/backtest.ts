export interface BacktestCreate {
  strategy_id: string;
  basket_id?: string | null;
  start_date: string;
  end_date: string;
  initial_cash?: number;
  benchmark_symbol?: string | null;
  commission_bps?: number | null;
  commission_min?: number | null;
  slippage_bps?: number | null;
}

export interface BacktestRunOut {
  id: string;
  strategy_id: string;
  strategy_name?: string | null;
  basket_id?: string | null;
  basket_name?: string | null;
  strategy_version: number;
  mode: string;
  status: string;
  requested_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  runtime_ms?: number | null;
  window_start?: string | null;
  window_end?: string | null;
  initial_cash?: number | null;
  final_equity?: number | null;
  benchmark_symbol?: string | null;
  summary_metrics: Record<string, unknown>;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BacktestSnapshotPoint {
  ts?: string | null;
  cash?: number | null;
  equity?: number | null;
  gross_exposure?: number | null;
  net_exposure?: number | null;
  drawdown?: number | null;
  benchmark_symbol?: string | null;
  benchmark_close?: number | null;
  benchmark_equity?: number | null;
  benchmark_return?: number | null;
  benchmark_excess_return?: number | null;
  positions?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

export interface BacktestTransactionOut {
  id: string;
  ts?: string | null;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  fee?: number | null;
  order_id?: string | null;
  meta?: Record<string, unknown>;
}

export interface BacktestSignalOut {
  id: string;
  ts?: string | null;
  symbol: string;
  signal: string;
  score?: number | null;
  reason?: string | null;
  features?: Record<string, unknown>;
}

export interface BacktestComparisonCurvePoint {
  ts?: string | null;
  symbol?: string | null;
  close?: number | null;
  equity?: number | null;
  return?: number | null;
}

export interface BacktestDetailOut extends BacktestRunOut {
  latest_snapshot?: BacktestSnapshotPoint | null;
  transaction_count: number;
  equity_curve: BacktestSnapshotPoint[];
  comparison_curves?: Record<string, BacktestComparisonCurvePoint[]>;
  signals: BacktestSignalOut[];
  transactions: BacktestTransactionOut[];
}
