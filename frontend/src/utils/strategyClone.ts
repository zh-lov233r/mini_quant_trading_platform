import type { StrategyOut, StrategyType } from "@/types/strategy";
import { cloneRecord, getPathValue } from "@/utils/strategyCreateGuidance";

const STRATEGY_NAME_MAX_LENGTH = 128;
const CLONE_NAME_SUFFIX = " Copy";

const CURRENT_STRATEGY_TYPES = new Set<StrategyType>([
  "trend",
  "mean_reversion",
  "momentum_breakout",
  "island_reversal",
  "double_bottom",
  "head_shoulders_bottom",
  "rounded_bottom",
  "v_reversal",
  "support_resistance",
  "custom",
]);

export interface StrategyCloneDraft {
  name: string;
  description: string;
  strategyType: StrategyType;
  params: Record<string, unknown>;
  rawJson: string;
  symbolsText: string;
}

export function isCurrentStrategyType(value: string): value is StrategyType {
  return CURRENT_STRATEGY_TYPES.has(value as StrategyType);
}

export function buildStrategyCloneName(sourceName: string): string {
  const cleanName = sourceName.trim();
  const prefixLimit = STRATEGY_NAME_MAX_LENGTH - CLONE_NAME_SUFFIX.length;
  return `${cleanName.slice(0, prefixLimit).trimEnd()}${CLONE_NAME_SUFFIX}`;
}

export function buildStrategyCloneDraft(source: StrategyOut): StrategyCloneDraft {
  if (!isCurrentStrategyType(source.strategy_type)) {
    throw new Error(`unsupported strategy type: ${source.strategy_type}`);
  }

  const params = cloneRecord(source.params as Record<string, unknown>);
  const metadataDescription = getPathValue(params, "metadata.description");
  const symbols = getPathValue(params, "universe.symbols");
  return {
    name: buildStrategyCloneName(source.name),
    description: source.description?.trim()
      || (typeof metadataDescription === "string" ? metadataDescription.trim() : ""),
    strategyType: source.strategy_type,
    params,
    rawJson: JSON.stringify(params, null, 2),
    symbolsText: Array.isArray(symbols) ? symbols.map(String).join(", ") : "",
  };
}
