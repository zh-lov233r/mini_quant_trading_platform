import type {
  StrategyCatalogItem,
  StrategyOut,
  StrategyRuntimeOut,
} from "@/types/strategy";

export function getUniverseSymbols(strategy: StrategyOut): string[] {
  const maybeUniverse = (strategy.params as Record<string, unknown>)?.universe;
  if (!maybeUniverse || typeof maybeUniverse !== "object") {
    return [];
  }

  const symbols = (maybeUniverse as { symbols?: unknown }).symbols;
  if (!Array.isArray(symbols)) {
    return [];
  }

  return symbols
    .map((symbol) => String(symbol).trim().toUpperCase())
    .filter(Boolean);
}

export function getUniverseSummary(strategy: StrategyOut): string {
  const symbols = getUniverseSymbols(strategy);
  if (symbols.length === 0) {
    return "运行时选择或全市场";
  }
  if (symbols.length <= 4) {
    return symbols.join(", ");
  }
  return `${symbols.slice(0, 4).join(", ")} +${symbols.length - 4}`;
}

export function getStrategyDescription(strategy: StrategyOut): string {
  const description = strategy.description?.trim();
  if (description) {
    return description;
  }

  const maybeMetadata = (strategy.params as Record<string, unknown>)?.metadata;
  if (!maybeMetadata || typeof maybeMetadata !== "object") {
    return "暂无说明";
  }

  const fallback = (maybeMetadata as { description?: unknown }).description;
  return typeof fallback === "string" && fallback.trim() ? fallback.trim() : "暂无说明";
}

export function getTypeLabel(
  strategyType: string,
  catalog: StrategyCatalogItem[]
): string {
  const matched = catalog.find((item) => item.strategy_type === strategyType);
  return matched?.label || strategyType;
}

export function formatDateTime(value?: string | null): string {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function summarizeStrategies(strategies: StrategyOut[]) {
  const total = strategies.length;
  const active = strategies.filter((item) => item.status === "active").length;
  const drafts = strategies.filter((item) => item.status === "draft").length;
  const engineReady = strategies.filter((item) => item.engine_ready).length;

  const manualUniverse = strategies.filter((item) => getUniverseSymbols(item).length > 0).length;
  const totalUniverseSize = strategies.reduce(
    (sum, item) => sum + getUniverseSymbols(item).length,
    0
  );
  const averageUniverseSize =
    manualUniverse > 0 ? (totalUniverseSize / manualUniverse).toFixed(1) : "0";

  return {
    total,
    active,
    drafts,
    engineReady,
    manualUniverse,
    averageUniverseSize,
  };
}

export function getStrategyFieldNumber(
  strategy: StrategyOut,
  section: string,
  field: string
): number | null {
  const sectionValue = (strategy.params as Record<string, unknown>)?.[section];
  if (!sectionValue || typeof sectionValue !== "object") {
    return null;
  }
  const raw = (sectionValue as Record<string, unknown>)[field];
  if (typeof raw !== "number") {
    return null;
  }
  return raw;
}

export function getStrategyFieldText(
  strategy: StrategyOut,
  section: string,
  field: string
): string | null {
  const sectionValue = (strategy.params as Record<string, unknown>)?.[section];
  if (!sectionValue || typeof sectionValue !== "object") {
    return null;
  }
  const raw = (sectionValue as Record<string, unknown>)[field];
  if (typeof raw !== "string" || !raw.trim()) {
    return null;
  }
  return raw.trim();
}

export function formatPercent(value: number | null, digits = 0): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return `${(value * 100).toFixed(digits)}%`;
}

export function getRuntimeFieldText(
  runtime: StrategyRuntimeOut | null,
  section: string,
  field: string
): string | null {
  if (!runtime) {
    return null;
  }

  const sectionValue = runtime.params?.[section];
  if (!sectionValue || typeof sectionValue !== "object") {
    return null;
  }

  const raw = (sectionValue as Record<string, unknown>)[field];
  if (typeof raw !== "string" || !raw.trim()) {
    return null;
  }

  return raw.trim();
}
