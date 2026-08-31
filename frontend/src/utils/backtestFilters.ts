import type { BacktestRunOut } from "@/types/backtest";

export interface BacktestRunFilters {
  query: string;
  status: string;
  strategyType: string;
}

export function filterBacktestRuns(
  runs: readonly BacktestRunOut[],
  strategyTypesById: ReadonlyMap<string, string>,
  filters: BacktestRunFilters,
): BacktestRunOut[] {
  const query = filters.query.trim().toLocaleLowerCase();

  return runs.filter((run) => {
    const strategyType = strategyTypesById.get(run.strategy_id) || "unknown";
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
