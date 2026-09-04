import { describe, expect, it } from "vitest";
import type { BacktestSignalOut, SupportResistanceZoneVersionOut } from "@/types/backtest";
import { lifecycleSignalFeatures, lifecycleTradeZoneKeys, lifecycleZoneBands } from "./supportResistanceLifecycle";

const dates = ["2026-07-09", "2026-07-10", "2026-07-13", "2026-07-14"];
function version(number: number, start: number, end: number): SupportResistanceZoneVersionOut {
  const center = 10 + start * 0.1;
  const endCenter = 10 + end * 0.1;
  return {
    id: `v${number}`, symbol: "GRCE", zone_key: "support", version: number,
    effective_from: dates[start], effective_to: dates[end], role: "support", status: "active",
    center_price: center, lower_price: center - 1, upper_price: center + 1,
    atr_width: 2, anchor_session_index: start, slope_per_session: 0.1,
    fit_residual_atr: 0, projection_end: dates[end], end_center_price: endCenter,
    end_lower_price: endCenter - 1, end_upper_price: endCenter + 1,
    pivot_count: 3, touch_count: 1, source_metadata: {},
    geometry: {
      start_date: dates[start], end_date: dates[end],
      start_center_price: center, start_lower_price: center - 1, start_upper_price: center + 1,
      end_center_price: endCenter, end_lower_price: endCenter - 1, end_upper_price: endCenter + 1,
      slope_per_session: 0.1,
    },
  };
}

describe("trade-linked support/resistance history", () => {
  it("separates retrospective fitting from the frozen effective segment", () => {
    const active = version(1, 2, 3);
    active.formation_geometry = version(1, 0, 2).geometry;
    const bands = lifecycleZoneBands([active], dates, new Set(["support"]), false);
    expect(bands.map((band) => band.retrospective)).toEqual([true, false]);
    expect(bands[0].geometry.end_date).toBe(bands[1].geometry.start_date);
    expect(bands[1].geometry.end_date).toBe(dates[3]);
  });
  it("uses selected entry/exit evidence, not candidates or incidental regime boundaries", () => {
    const entry = { support_resistance: {
      zone_key: "support", zone: { zone_key: "support" },
      entry_channel: { support_zone_key: "support", resistance_zone_key: "target" },
      candidates: [{ zone_key: "rejected" }], regime_evidence: { lower_zone_key: "incidental" },
    } };
    const exit = { support_resistance: {
      zone_key: "exit", exit_reason_code: "stop",
      exit_regime_evidence: { lower_zone_key: "down-low", upper_zone_key: "down-high" },
    } };
    expect([...lifecycleTradeZoneKeys(entry, exit)]).toEqual(["support", "target", "exit"]);
    exit.support_resistance.exit_reason_code = "downtrend";
    expect([...lifecycleTradeZoneKeys(entry, exit)]).toEqual(["support", "target", "exit", "down-low", "down-high"]);
    expect(lifecycleTradeZoneKeys({ support_resistance: [] }, null).size).toBe(0);
  });

  it("matches the exact signal instant instead of another signal on that day or symbol", () => {
    const signals: BacktestSignalOut[] = [
      { id: "wrong-time", symbol: "GRCE", signal: "SELL", ts: "2026-07-29T03:00:00Z", features: { wrong: true } },
      { id: "sell", symbol: "GRCE", signal: "SELL", ts: "2026-07-28T21:00:00-07:00", features: { correct: true } },
    ];
    expect(lifecycleSignalFeatures(signals, "grce", "SELL", "2026-07-29T04:00:00Z")).toEqual({ correct: true });
    expect(lifecycleSignalFeatures(signals, "OTHER", "SELL", signals[1].ts!)).toBeNull();
    expect(lifecycleSignalFeatures(signals, "GRCE", "BUY", signals[1].ts!)).toBeNull();
    expect(lifecycleSignalFeatures(signals, "GRCE", "SELL", null)).toBeNull();
  });

  it("defaults to explicit trade links and restores other zones only in show-all mode", () => {
    const versions = [version(1, 0, 1), { ...version(1, 0, 1), id: "other", zone_key: "other" }];
    expect(lifecycleZoneBands(versions, dates, new Set(["support"]), false).map((band) => band.zone_key)).toEqual(["support"]);
    expect(lifecycleZoneBands(versions, dates, new Set(), false)).toEqual([]);
    expect(lifecycleZoneBands(versions, dates, new Set(), true)).toHaveLength(2);
  });

  it("does not stitch separately persisted definitions or change the input", () => {
    const versions = [version(2, 2, 3), version(1, 0, 1)];
    const before = structuredClone(versions);
    const bands = lifecycleZoneBands(versions, dates, new Set(), true);
    expect(bands).toHaveLength(2);
    expect(bands[0].geometry).toEqual(versions[0].geometry);
    expect(versions).toEqual(before);
  });

  it.each(["role", "slope", "width", "reanchor", "gap", "version", "key", "symbol", "expiration"])(
    "preserves a breakpoint for %s", (change) => {
      const first = version(1, 0, 1);
      const next = version(2, 2, 3);
      const versions = [first, next];
      if (change === "role") next.role = "resistance";
      if (change === "slope") next.geometry!.slope_per_session = 0.2;
      if (change === "width") next.geometry!.start_upper_price += 0.01;
      if (change === "reanchor") next.geometry!.start_center_price += 0.01;
      if (change === "gap") next.geometry!.start_date = dates[3];
      if (change === "version") next.version = 3;
      if (change === "key") next.zone_key = "another";
      if (change === "symbol") next.symbol = "OTHER";
      if (change === "expiration") {
        next.version = 3;
        versions.splice(1, 0, { ...version(2, 2, 2), status: "expired", geometry: null });
      }
      expect(lifecycleZoneBands(versions, dates, new Set(), true)).toHaveLength(2);
    },
  );
});
