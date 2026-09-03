import type { BacktestRunOut } from "@/types/backtest";
import type { StrategyType } from "@/types/strategy";

export interface BacktestRunFilters {
  query: string;
  status: string;
  strategyType: string;
  market?: string;
}

export function filterBacktestRuns(
  runs: readonly BacktestRunOut[],
  strategyTypesById: ReadonlyMap<string, StrategyType>,
  filters: BacktestRunFilters,
): BacktestRunOut[] {
  const query = filters.query.trim().toLocaleLowerCase();

  return runs.filter((run) => {
    const strategyType = strategyTypesById.get(run.strategy_id);
    if (!strategyType) {
      throw new Error(`Missing strategy type for backtest run: ${run.id}`);
    }
    if (filters.market && filters.market !== "all" && run.market !== filters.market) return false;
    if (filters.status !== "all" && run.status !== filters.status) {
      return false;
    }
    if (filters.strategyType !== "all" && strategyType !== filters.strategyType) {
      return false;
    }
    if (!query) {
      return true;
    }

    return [
      run.strategy_name,
      run.strategy_id,
      run.basket_name,
      run.mode,
      run.status,
      run.window_start,
      run.window_end,
    ].some((value) => String(value || "").toLocaleLowerCase().includes(query));
  });
}
