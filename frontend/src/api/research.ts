import http from "@/api/client";
import type {
  ExperimentCandidate,
  ExperimentRound,
  ExperimentTrial,
  ResearchExperiment,
} from "@/types/research";
import type { BacktestDeleteResult } from "@/types/backtest";

export function listResearchExperiments(): Promise<ResearchExperiment[]> {
  return http<ResearchExperiment[]>("/api/research/experiments", { method: "GET" });
}

export function getResearchExperiment(id: string): Promise<ResearchExperiment> {
  return http<ResearchExperiment>(`/api/research/experiments/${encodeURIComponent(id)}`, {
    method: "GET",
  });
}

export function listExperimentChildren(id: string): Promise<ResearchExperiment[]> {
  return http<ResearchExperiment[]>(
    `/api/research/experiments/${encodeURIComponent(id)}/children`,
    { method: "GET" },
  );
}

export type ResearchArtifactKind = "json" | "markdownZh" | "markdownEn" | "pdfZh" | "pdfEn";

export function researchArtifactUrl(id: string, kind: ResearchArtifactKind): string {
  return `/api/research/experiments/${encodeURIComponent(id)}/report-artifacts/${kind}`;
}

export function listExperimentTrials(id: string): Promise<ExperimentTrial[]> {
  return http<ExperimentTrial[]>(
    `/api/research/experiments/${encodeURIComponent(id)}/trials`,
    { method: "GET" },
  );
}

export function listExperimentRounds(id: string): Promise<ExperimentRound[]> {
  return http<ExperimentRound[]>(
    `/api/research/experiments/${encodeURIComponent(id)}/rounds`,
    { method: "GET" },
  );
}

export function listExperimentCandidates(id: string): Promise<ExperimentCandidate[]> {
  return http<ExperimentCandidate[]>(
    `/api/research/experiments/${encodeURIComponent(id)}/candidates`,
    { method: "GET" },
  );
}

export function getExperimentReport(id: string): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>(
    `/api/research/experiments/${encodeURIComponent(id)}/report`,
    { method: "GET" },
  );
}

export function deleteResearchBacktest(
  experimentId: string,
  runId: string,
): Promise<BacktestDeleteResult> {
  return http<BacktestDeleteResult>(
    `/api/research/experiments/${encodeURIComponent(experimentId)}/backtests/${encodeURIComponent(runId)}`,
    { method: "DELETE" },
  );
}
