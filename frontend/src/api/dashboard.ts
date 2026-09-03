import http from "@/api/client";
import type { DashboardOverview } from "@/types/dashboard";

let pending: Promise<DashboardOverview> | null = null;

/** Share the current request across StrictMode mounts and refresh triggers. */
export function getDashboardOverview(): Promise<DashboardOverview> {
  if (!pending) {
    pending = http<DashboardOverview>("/api/dashboard/overview", { method: "GET" })
      .finally(() => { pending = null; });
  }
  return pending;
}
