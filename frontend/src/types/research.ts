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
  symbols: string[];
  inSample: DateWindow;
  outOfSample: DateWindow;
  parameterGrid: Record<string, unknown[]>;
  costScenarios: CostScenario[];
  initialCash: number;
  benchmarkSymbol?: string | null;
  stopPolicy?: ExperimentStopPolicy | null;
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
  workflowRunId: string;
  status: string;
  spec: ExperimentSpec & Record<string, unknown>;
  runManifest: Record<string, unknown>;
  progress: Record<string, number>;
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
  metrics: Record<string, unknown>;
  attempt: number;
  errorCode: string | null;
  errorMessage: string | null;
}
