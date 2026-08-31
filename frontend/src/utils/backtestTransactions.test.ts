import { describe, expect, it, vi } from "vitest";

import type { BacktestTransactionOut } from "@/types/backtest";
import {
  BacktestTransactionPageError,
  mergeBacktestTransactions,
  nextVisibleItemCount,
  streamBacktestTransactionPages,
} from "./backtestTransactions";

function transaction(id: string, ts: string): BacktestTransactionOut {
  return { id, ts, symbol: "AAA", side: "BUY", qty: 1, price: 10 };
}

describe("backtest transaction pagination", () => {
  it("streams every cursor page in order", async () => {
    const pages = {
      first: { items: [transaction("t2", "2025-01-02T14:30:00Z")], total: 3, next_cursor: "second" },
      second: { items: [transaction("t1", "2025-01-01T14:30:00Z")], total: 3, next_cursor: null },
    };
    const loadPage = vi.fn(async (cursor: string) => pages[cursor as keyof typeof pages]);
    const received: string[] = [];

    await streamBacktestTransactionPages({
      cursor: "first",
      loadPage,
      onPage: (page) => received.push(...page.items.map((item) => item.id)),
    });

    expect(loadPage.mock.calls.map(([cursor]) => cursor)).toEqual(["first", "second"]);
    expect(received).toEqual(["t2", "t1"]);
  });

  it("preserves the failed cursor for a retry", async () => {
    const failure = new Error("network unavailable");

    await expect(streamBacktestTransactionPages({
      cursor: "resume-here",
      loadPage: async () => { throw failure; },
      onPage: () => undefined,
    })).rejects.toMatchObject({
      name: "BacktestTransactionPageError",
      resumeCursor: "resume-here",
      cause: failure,
    });
  });

  it("stops a repeated cursor instead of looping forever", async () => {
    const loadPage = vi.fn(async () => ({
      items: [transaction("t1", "2025-01-01T14:30:00Z")],
      total: 2,
      next_cursor: "same",
    }));

    await expect(streamBacktestTransactionPages({
      cursor: "same",
      loadPage,
      onPage: () => undefined,
    })).rejects.toBeInstanceOf(BacktestTransactionPageError);
    expect(loadPage).toHaveBeenCalledTimes(1);
  });

  it("deduplicates and restores deterministic descending order", () => {
    const merged = mergeBacktestTransactions(
      [transaction("b", "2025-01-02T14:30:00Z"), transaction("a", "2025-01-01T14:30:00Z")],
      [transaction("b", "2025-01-02T14:30:00Z"), transaction("c", "2025-01-03T14:30:00Z")],
    );
    expect(merged.map((item) => item.id)).toEqual(["c", "b", "a"]);
  });

  it("advances visible windows without exceeding the total", () => {
    expect(nextVisibleItemCount(10, 25, 10)).toBe(20);
    expect(nextVisibleItemCount(20, 25, 10)).toBe(25);
  });
});
