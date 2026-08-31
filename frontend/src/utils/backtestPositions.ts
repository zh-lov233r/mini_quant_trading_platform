export interface CompactBacktestPosition {
  symbol: string;
  quantity: number | null;
  averageEntryPrice: number | null;
  closePrice: number | null;
  marketValue: number | null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function compactBacktestPositions(
  positions: Record<string, unknown> | null | undefined,
): CompactBacktestPosition[] {
  return Object.entries(positions || {})
    .map(([symbol, value]) => {
      if (typeof value === "number") {
        return {
          symbol,
          quantity: finiteNumber(value),
          averageEntryPrice: null,
          closePrice: null,
          marketValue: null,
        };
      }

      const position = value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
      return {
        symbol,
        quantity: finiteNumber(position.qty),
        averageEntryPrice: finiteNumber(position.avg_entry_price),
        closePrice: finiteNumber(position.close),
        marketValue: finiteNumber(position.market_value),
      };
    })
    .sort((left, right) => left.symbol.localeCompare(right.symbol));
}
