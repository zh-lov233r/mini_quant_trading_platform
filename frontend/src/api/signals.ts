import http, { API_BASE } from "./client";
import type { Page, Plan, PlanInput, ScanInput, Scan, Report, Observation, ChartSnapshot } from "@/types/signals";
const post = <T>(path: string, body: unknown) => http<T>(path, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(body) });
export const listSignalPlans = () => http<Page<Plan>>("/api/signal-scan-plans", { method: "GET" });
export const saveSignalPlan = (body: PlanInput, id?: string) => id ? http<Plan>(`/api/signal-scan-plans/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) : post<Plan>("/api/signal-scan-plans", body);
export const toggleSignalPlan = (id: string, enabled: boolean) => post<Plan>(`/api/signal-scan-plans/${id}/enabled`, { enabled });
export const startSignalScan = (body: ScanInput) => post<Scan>("/api/signal-scans", body);
export const listSignalReports = (offset = 0) => http<Page<Report>>(`/api/signal-reports?offset=${offset}`, { method: "GET" });
export const getSignalReport = (id: string) => http<Report>(`/api/signal-reports/${id}`, { method: "GET" });
export const getSignalObservations = (id: string, query: string) => http<Page<Observation>>(`/api/signal-reports/${id}/observations?${query}`, { method: "GET" });
export const deliverSignalReport = (id: string, recipients: string[]) => post(`/api/signal-reports/${id}/deliveries`, { recipients });
export const retrySignalRendering = (id: string) => post(`/api/signal-reports/${id}/render`, {});
export const retrySignalDelivery = (id: string, delivery: string) => post(`/api/signal-reports/${id}/deliveries/${delivery}/retry`, {});
export const signalExportUrl = (id: string, format: string) => `${API_BASE}/api/signal-reports/${id}/export?format=${format}`;
const charts = new Map<string, Promise<ChartSnapshot>>();
export function getSignalChart(id: string, instrument: number, strategy: string, signal: string, limit: number, before?: string): Promise<ChartSnapshot> {
  const path = `/api/signal-reports/${id}/instruments/${instrument}/chart?${new URLSearchParams({ strategy_run_id: strategy, signal_id: signal, limit: String(limit), ...(before ? { before } : {}) })}`;
  let pending = charts.get(path);
  if (!pending) {
    pending = http<ChartSnapshot>(path, { method: "GET" }).catch(error => { charts.delete(path); throw error; });
    charts.set(path, pending);
    while (charts.size > 12) charts.delete(charts.keys().next().value!);
  }
  return pending;
}
