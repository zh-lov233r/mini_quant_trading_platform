export type DashboardHealth = "healthy" | "degraded" | "failed" | "unknown" | "disabled";
export interface DashboardSystemItem {
  key: "market_data" | "backtest" | "research" | "scheduler" | "broker";
  status: DashboardHealth;
  reason: string;
  checked_at: string;
  observed_at: string | null;
  enabled: boolean | null;
  submit_orders: boolean | null;
  active: number | null;
  capacity: number | null;
  queued: number | null;
  last_run_status: string | null;
}
export interface DashboardStrategyEvidence {
  strategy_id: string;
  name: string;
  strategy_type: string;
  version: number;
  engine_ready: boolean;
  backtest_id: string | null;
  evidence_status: "available" | "missing" | "configuration_changed" | "invalid";
  total_return: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  trade_count: number | null;
  window_start: string | null;
  window_end: string | null;
  finished_at: string | null;
  experiment_id: string | null;
  candidate_id: string | null;
  verification_status: "queued" | "running" | "completed" | "failed" | "cancelled" | null;
  issues: string[];
}
export interface DashboardPaperPortfolio {
  portfolio_id: string;
  account_name: string;
  name: string;
  allocation_count: number;
  allocation_total: number;
  auto_run_eligible: boolean;
  latest_run_id: string | null;
  latest_strategy_name: string | null;
  latest_run_status: string | null;
  latest_run_at: string | null;
  submit_orders: boolean | null;
}
export interface DashboardAlert {
  id: string;
  code: string;
  severity: "critical" | "warning" | "info";
  count: number;
  occurred_at: string | null;
  href: string;
}
export interface DashboardActivity {
  strategy_type?: string | null;
  id: string;
  category: "backtest" | "research" | "strategy" | "paper";
  status: string;
  name: string;
  occurred_at: string;
  href: string;
}
export interface DashboardOverview {
  generated_at: string;
  system: DashboardSystemItem[];
  research_kpis: {
    active_strategies: number;
    running_experiments: number;
    running_backtests: number;
    queued_backtests: number;
  };
  task_summary: {
    waiting_research: number;
    completed_last_24h: number;
    failed_backtests_last_24h: number;
    failed_research_last_24h: number;
  };
  research_progress: {
    experiments: number;
    evaluated_candidates: number;
    verified_candidates: number;
    promoted_strategies: number;
    paper_strategies: number;
  };
  strategy_evidence: DashboardStrategyEvidence[];
  paper_summary: { account_count: number; portfolio_count: number; portfolios: DashboardPaperPortfolio[] };
  alerts: DashboardAlert[];
  activity: DashboardActivity[];
}
