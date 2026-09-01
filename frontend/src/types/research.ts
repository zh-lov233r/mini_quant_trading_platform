export interface DateWindow {
  startDate: string;
  endDate: string;
}

export interface CostScenario {
  name: string;
  commissionBps: number;
  commissionMin: number;
  slippageBps: number;
}

export interface PointInTimeUniversePolicy {
  type: "point_in_time_liquid";
  assetTypes: string[];
  exchanges: string[];
  minUnadjustedClose: number;
  minDollarVolume20: number;
  minHistorySessions: number;
  membershipAsOf: "signal_close";
  existingPositionPolicy: "exit_only";
  delistingValuePolicy: "zero_with_last_close_sensitivity";
}

export interface SupportResistanceValidationProtocol {
  kind: "support_resistance_effectiveness_v3";
  maxBacktests: 200;
  bootstrapSeed: 20260828;
  bootstrapReplicates: 10000;
  eventHorizons: [1, 5, 10, 20, 40];
  dedupeSessions: 40;
  reportFormats: ["json", "markdown_zh", "markdown_en", "pdf_zh", "pdf_en"];
}

export type ResearchTargetMetric =
  | "total_return"
  | "sharpe"
  | "max_drawdown"
  | "excess_return";

export interface TargetMetricCondition {
  metric: ResearchTargetMetric;
  operator: "gte" | "lte";
  value: number;
  sampleKind: "in_sample" | "out_of_sample";
  costScenario: string;
}

export interface ExperimentStopPolicy {
  maxDurationSeconds?: number | null;
  tokenBudget?: number | null;
  targetMetric?: TargetMetricCondition | null;
}

export interface ExperimentSpec {
  name: string;
  hypothesis: string;
  strategyId: string;
  basketId?: string | null;
  symbols?: string[];
  universePolicy?: PointInTimeUniversePolicy;
  validationProtocol?: SupportResistanceValidationProtocol;
  inSample?: DateWindow;
  outOfSample?: DateWindow;
  parameterGrid?: Record<string, unknown[]>;
  costScenarios?: CostScenario[];
  initialCash: number;
  benchmarkSymbol?: string | null;
  stopPolicy?: ExperimentStopPolicy | null;
}

export type ParetoObjectiveMetric =
  | "oos_total_return"
  | "oos_annualized_return"
  | "oos_sharpe"
  | "oos_sortino"
  | "oos_excess_return"
  | "oos_max_drawdown"
  | "oos_turnover"
  | "pnl_concentration"
  | "cost_decay"
  | "is_oos_abs_gap";

export interface ExperimentRound {
  id: string;
  experimentId: string;
  ordinal: number;
  status: string;
  proposal: Record<string, unknown>;
  validationIssues: Array<Record<string, unknown>>;
  resultSummary: Record<string, unknown>;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface ExperimentCandidate {
  id: string;
  experimentId: string;
  roundId: string;
  ordinal: number;
  overrides: Record<string, unknown>;
  params: Record<string, unknown>;
  paramsHash: string;
  rationale: string | null;
  aggregateMetrics: Record<string, unknown>;
  paretoRank: number | null;
  promotedStrategyId: string | null;
}

export interface ExperimentTermination {
  reason: "all_trials_completed" | "time_limit_reached" | "token_budget_reached" | "target_reached" | string;
  earlyStopped: boolean;
  triggeredConditions: Array<Record<string, unknown>>;
  stoppedAt?: string;
}

export interface WorkflowTokenUsage {
  inputTokens?: number;
  cachedInputTokens?: number;
  outputTokens?: number;
  reasoningOutputTokens?: number;
  totalTokens?: number;
}

export interface ExperimentReport extends Record<string, unknown> {
  status?: string;
  disclaimer?: string;
  termination?: ExperimentTermination;
  tokenUsage?: WorkflowTokenUsage;
  counts?: Record<string, number>;
  bestOutOfSampleTrial?: {
    trialId: string;
    backtestRunId?: string | null;
    metrics?: Record<string, unknown>;
  } | null;
}

export interface ResearchExperiment {
  id: string;
  parentExperimentId: string | null;
  studyKind: string;
  workflowRunId: string;
  status: string;
  spec: ExperimentSpec & Record<string, unknown>;
  runManifest: Record<string, unknown>;
  progress: Record<string, number | string | null>;
  report: ExperimentReport;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ExperimentTrial {
  id: string;
  trialKey: string;
  ordinal: number;
  status: string;
  sampleKind: "in_sample" | "out_of_sample" | string;
  costScenario: string;
  params: Record<string, unknown>;
  paramsHash: string;
  windowStart: string;
  windowEnd: string;
  costConfig: Record<string, unknown>;
  dataFingerprint: string | null;
  backtestRunId: string | null;
  backtestDeletedAt: string | null;
  candidateId: string | null;
  metrics: Record<string, unknown>;
  attempt: number;
  errorCode: string | null;
  errorMessage: string | null;
}
