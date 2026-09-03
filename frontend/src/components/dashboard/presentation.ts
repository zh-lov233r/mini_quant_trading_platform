import type { DashboardHealth } from "@/types/dashboard";

export const healthPresentation: Record<DashboardHealth, { icon: string; tone: "success" | "warning" | "neutral" | "info" }> = {
  healthy: { icon: "●", tone: "success" },
  degraded: { icon: "▲", tone: "warning" },
  failed: { icon: "!", tone: "warning" },
  unknown: { icon: "?", tone: "neutral" },
  disabled: { icon: "○", tone: "neutral" },
};

export function dashboardNumber(value: number | null, locale: string, percent = false): string {
  if (value === null) return "—";
  return new Intl.NumberFormat(locale, percent
    ? { style: "percent", maximumFractionDigits: 2 }
    : { maximumFractionDigits: 2 }).format(value);
}

export const progressLinks = {
  experiments: "/research",
  evaluated_candidates: "/research",
  verified_candidates: "/research",
  promoted_strategies: "/strategies",
  paper_strategies: "/paper-trading",
} as const;
