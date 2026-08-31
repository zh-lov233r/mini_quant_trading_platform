import type { BacktestPageOut, BacktestTransactionOut } from "@/types/backtest";

export const INITIAL_TRANSACTION_PAGE_SIZE = 100;
export const TRANSACTION_TAIL_PAGE_SIZE = 500;
export const TRANSACTION_TABLE_BATCH_SIZE = 10;
export const INITIAL_LIFECYCLE_TRANSACTION_LIMIT = 100;
export const LIFECYCLE_TRANSACTION_BATCH_SIZE = 100;
export const LIFECYCLE_ROW_BATCH_SIZE = 12;

export class BacktestTransactionPageError extends Error {
  readonly resumeCursor: string;
  readonly cause: unknown;

  constructor(resumeCursor: string, cause: unknown) {
    super(cause instanceof Error ? cause.message : "Failed to load a transaction page");
    this.name = "BacktestTransactionPageError";
    this.resumeCursor = resumeCursor;
    this.cause = cause;
  }
}

function compareTransactionsDescending(
  left: BacktestTransactionOut,
  right: BacktestTransactionOut,
): number {
  const tsOrder = (right.ts || "").localeCompare(left.ts || "");
  return tsOrder || right.id.localeCompare(left.id);
}

export function mergeBacktestTransactions(
  existing: BacktestTransactionOut[],
  incoming: BacktestTransactionOut[],
): BacktestTransactionOut[] {
  const byId = new Map<string, BacktestTransactionOut>();
  [...existing, ...incoming].forEach((transaction) => {
    if (!byId.has(transaction.id)) byId.set(transaction.id, transaction);
  });
  return Array.from(byId.values()).sort(compareTransactionsDescending);
}

export function nextVisibleItemCount(current: number, total: number, step: number): number {
  return Math.min(Math.max(0, total), Math.max(0, current) + Math.max(1, step));
}

export async function streamBacktestTransactionPages({
  cursor,
  loadPage,
  onPage,
}: {
  cursor: string;
  loadPage: (cursor: string) => Promise<BacktestPageOut<BacktestTransactionOut>>;
  onPage: (page: BacktestPageOut<BacktestTransactionOut>) => void;
}): Promise<void> {
  let nextCursor: string | null = cursor;
  const requestedCursors = new Set<string>();

  while (nextCursor) {
    const requestedCursor = nextCursor;
    if (requestedCursors.has(requestedCursor)) {
      throw new BacktestTransactionPageError(
        requestedCursor,
        new Error("The transaction API returned a repeated cursor"),
      );
    }
    requestedCursors.add(requestedCursor);

    let page: BacktestPageOut<BacktestTransactionOut>;
    try {
      page = await loadPage(requestedCursor);
    } catch (cause) {
      throw new BacktestTransactionPageError(requestedCursor, cause);
    }

    onPage(page);
    nextCursor = page.next_cursor || null;
  }
}
