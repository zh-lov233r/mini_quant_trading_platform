export interface BacktestCreate {
  strategy_id: string;
  basket_id?: string | null;
  universe_policy?: import("@/types/research").PointInTimeUniversePolicy | null;
  start_date: string;
  end_date: string;
  initial_cash?: number;
  benchmark_symbol?: string | null;
  commission_bps?: number | null;
  commission_min?: number | null;
  slippage_bps?: number | null;
  persist_level?: BacktestPersistLevel;
}

export type BacktestPersistLevel = "summary" | "trades" | "full";

export type BacktestProgressPhase =
  | "queued"
  | "preparing"
  | "running"
  | "finalizing"
  | "completed"
  | "failed"
  | "cancelled";

export type BacktestFinalizingStage =
  | "zone_versions"
  | "regime_versions"
  | "run_events"
  | "backtest_details"
  | "committing";

export interface BacktestProgress {
  phase: BacktestProgressPhase;
  percent: number;
  completed_days?: number | null;
  total_days?: number | null;
  trade_date?: string | null;
  finalizing_stage?: BacktestFinalizingStage | null;
  completed_items?: number | null;
  total_items?: number | null;
  attempt: number;
  max_attempts: number;
  updated_at: string;
}

export interface BacktestWorkerStatus {
  execution_model: "process";
  configured_concurrency: number;
  intra_run_execution_model: "thread";
  configured_intra_run_threads: number;
  effective_intra_run_threads: number;
  available_slots: number;
  automation_available: boolean;
  manager_state: "idle" | "starting" | "running" | "backoff" | "standby" | "stopping" | "unavailable";
  live_managers: number;
  worker_active: boolean;
  active_jobs: number;
  queued_jobs: number;
  oldest_queued_at?: string | null;
  next_worker_start_at?: string | null;
  last_worker_exit_code?: number | null;
  heartbeat_stale_after_seconds: number;
  checked_at: string;
}

export type BacktestTaskSource = "manual" | "research" | "verification";
export type BacktestTaskStage =
  | "waiting_research"
  | "queued"
  | "preparing"
  | "running"
  | "finalizing"
  | "cancel_requested"
  | "completed"
  | "failed"
  | "cancelled";

export interface BacktestTask {
  task_key: string;
  source: BacktestTaskSource;
  stage: BacktestTaskStage;
  job_id?: string | null;
  run_id?: string | null;
  trial_id?: string | null;
  experiment_id?: string | null;
  candidate_id?: string | null;
  strategy_id?: string | null;
  strategy_name?: string | null;
  experiment_name?: string | null;
  trial_ordinal?: number | null;
  sample_kind?: string | null;
  cost_scenario?: string | null;
  window_start?: string | null;
  window_end?: string | null;
  progress?: BacktestProgress | null;
  attempt: number;
  max_attempts?: number | null;
  requested_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  cancel_requested_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  cancellable: boolean;
  retryable: boolean;
  deletable: boolean;
}

export interface BacktestTaskPage {
  items: BacktestTask[];
  total: number;
  counts: Record<string, number>;
}

// Milliseconds; emitted after completion. A warm cache skips the first three phases.
export interface BacktestPerformance extends Record<string, unknown> {
  sql_read_ms?: number;
  row_conversion_ms?: number;
  array_write_ms?: number;
  native_warmup_ms?: number;
}

export interface BacktestRunOut {
  market: "US" | "CN";
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
  summary_metrics: Record<string, unknown> & { performance?: BacktestPerformance };
  persist_level: BacktestPersistLevel;
  available_details: string[];
  progress?: BacktestProgress | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BacktestSummaryOut extends BacktestRunOut {
  latest_snapshot?: BacktestSnapshotPoint | null;
  transaction_count: number;
}

export interface BacktestPageOut<T> {
  items: T[];
  total: number;
  next_cursor?: string | null;
}

export interface BacktestEquityPoint {
  ts: string;
  equity: number;
  drawdown?: number | null;
  benchmark_symbol?: string | null;
  benchmark_close?: number | null;
  benchmark_equity?: number | null;
  benchmark_return?: number | null;
  benchmark_excess_return?: number | null;
}

export interface BacktestSnapshotPoint extends BacktestEquityPoint {
  cash?: number | null;
  gross_exposure?: number | null;
  net_exposure?: number | null;
  positions?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

export interface BacktestTransactionOut {
  id: string;
  run_id?: string | null;
  strategy_id?: string;
  instrument_id?: number | null;
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
  run_id?: string;
  strategy_id?: string;
  instrument_id?: number | null;
  ts?: string | null;
  symbol: string;
  signal: string;
  score?: number | null;
  strength?: SignalStrengthRecord | null;
  reason?: string | null;
  features?: Record<string, unknown>;
}

export interface SignalStrengthComponent {
  key: string;
  raw_value: number;
  normalized_score: number;
  weight: number;
}

export interface SignalStrengthRecord {
  score: number;
  level: "weak" | "medium" | "strong" | "very_strong";
  threshold: number;
  passes_threshold: boolean;
  rank: number | null;
  model_version: string;
  components: SignalStrengthComponent[];
}

export interface BacktestComparisonCurvePoint {
  ts?: string | null;
  symbol?: string | null;
  close?: number | null;
  equity?: number | null;
  return?: number | null;
}

export interface BacktestComparisonCurvesOut {
  run_id: string;
  comparison_curves: Record<string, BacktestComparisonCurvePoint[]>;
}

export interface BacktestDetailOut extends BacktestRunOut {
  latest_snapshot?: BacktestSnapshotPoint | null;
  transaction_count: number;
  equity_curve: BacktestEquityPoint[];
  comparison_curves?: Record<string, BacktestComparisonCurvePoint[]>;
  signals: BacktestSignalOut[];
  transactions: BacktestTransactionOut[];
}

export interface SupportResistanceMaterializationOut {
  id: string;
  cache_key: string;
  algorithm_version: string;
  detector_params: Record<string, unknown>;
  symbols: string[];
  coverage_start: string;
  coverage_end: string;
  price_semantics: string;
  status: string;
  statistics: Record<string, unknown>;
  completed_at?: string | null;
}

export interface SupportResistanceZoneVersionOut {
  id: string;
  symbol: string;
  zone_key: string;
  version: number;
  effective_from: string;
  effective_to?: string | null;
  role: "support" | "resistance";
  status: string;
  center_price: number;
  lower_price: number;
  upper_price: number;
  atr_width: number;
  anchor_session_index: number;
  slope_per_session: number;
  fit_residual_atr: number;
  projection_end: string;
  end_center_price: number;
  end_lower_price: number;
  end_upper_price: number;
  pivot_count: number;
  touch_count: number;
  source_metadata: Record<string, unknown>;
  geometry?: {
    start_date: string;
    end_date: string;
    start_center_price: number;
    start_lower_price: number;
    start_upper_price: number;
    end_center_price: number;
    end_lower_price: number;
    end_upper_price: number;
    slope_per_session: number;
  } | null;
}

export interface SupportResistanceRunEventOut {
  id: string;
  symbol: string;
  event_date: string;
  event_type: string;
  zone_key?: string | null;
  setup?: string | null;
  selected: boolean;
  score?: number | null;
  posterior_sample_count?: number | null;
  lower_price?: number | null;
  upper_price?: number | null;
  payload: Record<string, unknown>;
}

export type SupportResistanceRegime = "uptrend" | "downtrend" | "range" | "transition";

export interface SupportResistanceRegimeIntervalOut {
  version_id: string;
  symbol: string;
  regime: SupportResistanceRegime;
  start_date: string;
  end_date: string;
  session_count: number;
  lower_zone_key?: string | null;
  upper_zone_key?: string | null;
  reason_code: string;
  evidence: Record<string, unknown>;
}

export interface SupportResistanceBacktestOut {
  run_id: string;
  materialization?: SupportResistanceMaterializationOut | null;
  zone_versions: SupportResistanceZoneVersionOut[];
  regime_intervals: SupportResistanceRegimeIntervalOut[];
  events: SupportResistanceRunEventOut[];
}

export interface BacktestDeleteResult {
  run_id: string;
  deleted: boolean;
}
