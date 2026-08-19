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
}

export interface ResearchExperiment {
  id: string;
  workflowRunId: string;
  status: string;
  spec: ExperimentSpec & Record<string, unknown>;
  runManifest: Record<string, unknown>;
  progress: Record<string, number>;
  report: Record<string, unknown>;
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
