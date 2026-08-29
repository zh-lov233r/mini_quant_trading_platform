import type {
  StrategyCatalogItem,
  StrategyOut,
  StrategyRuntimeOut,
} from "@/types/strategy";
import { enUSMessages } from "@/i18n/messages/en-US";
import { zhCNMessages } from "@/i18n/messages/zh-CN";

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

export function getStrategyTemplateCopy(
  strategyType: string,
  locale: string = "zh-CN",
  fallbackLabel?: string,
  fallbackDescription?: string
): { label: string; description: string } {
  const isZh = locale === "zh-CN";

  switch (strategyType) {
    case "trend":
      return {
        label: isZh ? "趋势跟随" : "Trend Following",
        description: isZh
          ? "双均线趋势策略，带成交量过滤、ATR 风控和调仓配置。"
          : "Dual moving-average trend strategy with volume filter, ATR risk controls, and rebalance settings.",
      };
    case "mean_reversion":
      return {
        label: isZh ? "均值回归" : "Mean Reversion",
        description: isZh
          ? "均值回归配置模板，基于 z-score / ATR / 流动性特征做日线信号。"
          : "Mean reversion template using z-score, ATR, and liquidity features to generate daily signals.",
      };
    case "momentum_breakout": {
      const copy = isZh
        ? zhCNMessages.strategyTemplates.momentumBreakout
        : enUSMessages.strategyTemplates.momentumBreakout;
      return copy;
    }
    case "island_reversal":
      return {
        label: isZh ? "岛形反转底" : "Island Reversal Bottom",
        description: isZh
          ? "底部岛形反转策略，识别缩量向下衰竭缺口、放量向上突破缺口和缩量回踩缺口。"
          : "Bottom island reversal strategy using an exhaustion gap down, a volume-backed gap up breakout, and a low-volume gap retest.",
      };
    case "double_bottom":
      return {
        label: isZh ? "双底形态" : "Double Bottom",
        description: isZh
          ? "保守版双底形态策略，确认长期下跌后的双底、放量突破颈线与缩量回踩。"
          : "Conservative double-bottom strategy focused on a confirmed neckline breakout and low-volume retest after a prolonged decline.",
      };
    case "support_resistance":
      return {
        label: isZh ? "支撑 / 压力区域" : "Support / Resistance Zones",
        description: isZh
          ? "使用已确认 Pivot 与 ATR 聚类识别动态价格区，交易支撑反弹、压力突破和突破回踩。"
          : "Dynamic confirmed-Pivot and ATR-clustered zones for support bounces, resistance breakouts, and breakout retests.",
      };
    case "custom":
      return {
        label: isZh ? "自定义配置" : "Custom Config",
        description: isZh
          ? "自定义 JSON/DSL 策略定义。建议存储规则，不要直接存储可执行代码。"
          : "Custom JSON/DSL strategy definition. Prefer storing rules rather than executable code.",
      };
    default:
      return {
        label: fallbackLabel || strategyType,
        description: fallbackDescription || (isZh ? "暂无模板说明" : "No template description yet"),
      };
  }
}

export interface StrategyCategoryPresentation {
  label: string;
  description: string;
  accent: string;
  accentRgb: string;
}

const STRATEGY_CATEGORY_VISUALS: Record<
  string,
  Pick<StrategyCategoryPresentation, "accent" | "accentRgb">
> = {
  trend: { accent: "#2dd4bf", accentRgb: "45, 212, 191" },
  mean_reversion: { accent: "#a78bfa", accentRgb: "167, 139, 250" },
  momentum_breakout: { accent: "#fbbf24", accentRgb: "251, 191, 36" },
  island_reversal: { accent: "#fb7185", accentRgb: "251, 113, 133" },
  double_bottom: { accent: "#38bdf8", accentRgb: "56, 189, 248" },
  support_resistance: { accent: "#fb923c", accentRgb: "251, 146, 60" },
  custom: { accent: "#94a3b8", accentRgb: "148, 163, 184" },
};

export function getStrategyCategoryPresentation(
  strategyType: string,
  locale: string = "zh-CN",
  fallbackLabel?: string,
  fallbackDescription?: string
): StrategyCategoryPresentation {
  const copy = getStrategyTemplateCopy(
    strategyType,
    locale,
    fallbackLabel,
    fallbackDescription
  );
  const visual = STRATEGY_CATEGORY_VISUALS[strategyType] || STRATEGY_CATEGORY_VISUALS.custom;

  return {
    ...copy,
    ...visual,
  };
}

export function formatDateTime(
  value?: string | null,
  locale: string = "zh-CN"
): string {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(locale, {
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

export function formatDurationMs(
  value?: number | null,
  locale: string = "zh-CN"
): string {
  if (typeof value !== "number" || Number.isNaN(value) || value < 0) {
    return "-";
  }

  const totalMs = Math.round(value);
  if (totalMs < 1000) {
    return locale === "zh-CN" ? `${totalMs}毫秒` : `${totalMs} ms`;
  }

  const totalSeconds = Math.floor(totalMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (locale === "zh-CN") {
    const parts: string[] = [];
    if (hours > 0) {
      parts.push(`${hours}小时`);
    }
    if (minutes > 0 || hours > 0) {
      parts.push(`${minutes}分`);
    }
    parts.push(`${seconds}秒`);
    return parts.join("");
  }

  const parts: string[] = [];
  if (hours > 0) {
    parts.push(`${hours}h`);
  }
  if (minutes > 0 || hours > 0) {
    parts.push(`${minutes}m`);
  }
  parts.push(`${seconds}s`);
  return parts.join(" ");
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
