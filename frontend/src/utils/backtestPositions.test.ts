import { describe, expect, it } from "vitest";

import { compactBacktestPositions } from "@/utils/backtestPositions";

describe("compact backtest positions", () => {
  it("keeps only the fields used by the compact UI", () => {
    expect(compactBacktestPositions({
      MSFT: {
        qty: 4.5,
        avg_entry_price: 410,
        close: 425,
        market_value: 1912.5,
        entry_signal_features: { very: "large" },
      },
    })).toEqual([{
      symbol: "MSFT",
      quantity: 4.5,
      averageEntryPrice: 410,
      closePrice: 425,
      marketValue: 1912.5,
    }]);
  });

  it("supports legacy numeric quantities and sorts symbols", () => {
    expect(compactBacktestPositions({ ZZZ: null, AAA: 2 })).toEqual([
      {
        symbol: "AAA",
        quantity: 2,
        averageEntryPrice: null,
        closePrice: null,
        marketValue: null,
      },
      {
        symbol: "ZZZ",
        quantity: null,
        averageEntryPrice: null,
        closePrice: null,
        marketValue: null,
      },
    ]);
  });
});
