import http from "@/api/client";
import type { ExperimentTrial, ResearchExperiment } from "@/types/research";

export function listResearchExperiments(): Promise<ResearchExperiment[]> {
  return http<ResearchExperiment[]>("/api/research/experiments", { method: "GET" });
}

export function getResearchExperiment(id: string): Promise<ResearchExperiment> {
  return http<ResearchExperiment>(`/api/research/experiments/${encodeURIComponent(id)}`, {
    method: "GET",
  });
}

export function listExperimentTrials(id: string): Promise<ExperimentTrial[]> {
  return http<ExperimentTrial[]>(
    `/api/research/experiments/${encodeURIComponent(id)}/trials`,
    { method: "GET" },
  );
}

export function getExperimentReport(id: string): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>(
    `/api/research/experiments/${encodeURIComponent(id)}/report`,
    { method: "GET" },
  );
}
