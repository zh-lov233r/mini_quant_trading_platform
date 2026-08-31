import { describe, expect, it } from "vitest";

import {
  buildCandleSeriesMarkers,
  buildEquityEventMarkers,
  candleTone,
  currentZoneOverlays,
  groupCandleOverlayMarkers,
  isDisplayableSupportResistanceEventType,
  latestVisibleZoneOverlaysByRole,
  normalizeCandleBars,
  normalizeEquityPoints,
  normalizeGapOverlays,
  normalizeZoneOverlays,
  toChartTime,
} from "./chartModels";
import { visibleZonePriceRange } from "./overlayPrimitive";

describe("chart models", () => {
  it("normalizes, sorts, deduplicates, and filters equity points", () => {
    const points = normalizeEquityPoints([
      { ts: "2025-01-03T21:00:00Z", equity: 103 },
      { ts: "invalid", equity: 99 },
      { ts: "2025-01-01T21:00:00Z", equity: 100 },
      { ts: "2025-01-03T22:00:00Z", equity: 104, drawdown: -0.02 },
      { ts: "2025-01-02T21:00:00Z", equity: Number.NaN },
    ]);
    expect(points.map((point) => [point.time, point.value])).toEqual([
      ["2025-01-01", 100],
      ["2025-01-03", 104],
    ]);
    expect(points[1].drawdown).toBe(-0.02);
  });

  it("normalizes OHLC bars and preserves flat/up/down colors", () => {
    const bars = normalizeCandleBars([
      { trade_date: "2025-01-03", open: 10, high: 12, low: 9, close: 10, volume: -1 },
      { trade_date: "2025-01-01", open: 10, high: 12, low: 9, close: 11, volume: 100 },
      { trade_date: "2025-01-02", open: 11, high: 12, low: 8, close: 9, volume: 120 },
    ]);
    expect(bars.map((bar) => bar.time)).toEqual(["2025-01-01", "2025-01-02", "2025-01-03"]);
    expect(bars.map((bar) => bar.wickColor)).toEqual(["#34d399", "#fb7185", "#fbbf24"]);
    expect(bars[2].volume).toBe(0);
    expect(candleTone(10, 10).borderColor).toBe("#fbbf24");
  });

  it("groups signal and fill markers by trade date and action", () => {
    const points = normalizeEquityPoints([
      { ts: "2025-01-01T21:00:00Z", equity: 100 },
      { ts: "2025-01-02T21:00:00Z", equity: 102 },
    ]);
    const markers = buildEquityEventMarkers(
      points,
      [
        { id: "s1", ts: "2025-01-02T20:00:00Z", symbol: "AAA", action: "BUY", reason: "one" },
        { id: "s2", ts: "2025-01-02T20:30:00Z", symbol: "BBB", action: "BUY", reason: "two" },
      ],
      [{ id: "t1", ts: "2025-01-02T14:30:00Z", symbol: "AAA", action: "SELL" }],
      "zh-CN",
    );
    expect(markers).toHaveLength(2);
    const buySignal = markers.find((marker) => marker.category === "buy_signal");
    const sellFill = markers.find((marker) => marker.category === "sell_fill");
    expect(buySignal).toMatchObject({
      category: "buy_signal",
      price: 102,
      shape: "circle",
      color: "#2563eb",
      text: "",
      title: "BUY 信号 (2)",
    });
    expect(buySignal?.details).toEqual(["AAA: one", "BBB: two"]);
    expect(sellFill).toMatchObject({
      category: "sell_fill",
      shape: "arrowDown",
      color: "#dc2626",
      text: "",
      title: "SELL 成交 (1)",
    });
  });

  it("validates gap and support/resistance overlay bounds", () => {
    const validGap = {
      key: "left",
      label: "Left Gap",
      referenceDate: "2025-01-01",
      anchorDate: "2025-01-02",
      lowPrice: 10,
      highPrice: 11,
      tone: "left_gap" as const,
      description: "gap",
    };
    expect(normalizeGapOverlays(
      [validGap, { ...validGap, key: "bad", highPrice: 9 }],
      new Set(["2025-01-01", "2025-01-02"]),
    )).toEqual([validGap]);

    const zone = {
      key: "horizontal",
      startDate: "2025-01-01",
      endDate: "2025-01-03",
      startCenterPrice: 9.5,
      startLowerPrice: 9,
      startUpperPrice: 10,
      endCenterPrice: 9.5,
      endLowerPrice: 9,
      endUpperPrice: 10,
      slopePerSession: 0,
      slopeAtrPerSession: 0,
      role: "support" as const,
      description: "zone",
    };
    const zones = normalizeZoneOverlays([
      zone,
      { ...zone, key: "rising", endCenterPrice: 11.5, endLowerPrice: 11, endUpperPrice: 12, slopePerSession: 1 },
      { ...zone, key: "falling", endCenterPrice: 7.5, endLowerPrice: 7, endUpperPrice: 8, slopePerSession: -1 },
      { ...zone, key: "future", startDate: "2025-02-01", endDate: "2025-02-03" },
      { ...zone, key: "missing-endpoint", endUpperPrice: Number.NaN },
    ], new Set(["2025-01-01", "2025-01-02", "2025-01-03"]));
    expect(zones.map((item) => item.key)).toEqual(["falling", "horizontal", "rising"]);
    expect(zones.map((item) => [item.startLowerPrice, item.endLowerPrice])).toEqual([
      [9, 7],
      [9, 9],
      [9, 11],
    ]);
  });

  it("expands autoscale to the complete visible sloped zone", () => {
    const zone = {
      key: "resistance",
      startDate: "2025-01-01",
      endDate: "2025-01-03",
      startCenterPrice: 250,
      startLowerPrice: 248,
      startUpperPrice: 252,
      endCenterPrice: 252,
      endLowerPrice: 250,
      endUpperPrice: 254,
      slopePerSession: 1,
      slopeAtrPerSession: 0.1,
      role: "resistance" as const,
      description: "resistance",
    };
    const indexes = new Map([
      ["2025-01-01", 0],
      ["2025-01-03", 2],
      ["2025-02-01", 10],
      ["2025-02-03", 12],
    ]);
    expect(visibleZonePriceRange(
      [
        zone,
        { ...zone, key: "far-below", startLowerPrice: 100, startUpperPrice: 102, endLowerPrice: 100, endUpperPrice: 102 },
        { ...zone, key: "outside", startDate: "2025-02-01", endDate: "2025-02-03", startUpperPrice: 999 },
      ],
      [
        { time: "2025-01-01", open: 245, high: 251, low: 243, close: 249, volume: 100, ...candleTone(245, 249) },
        { time: "2025-01-03", open: 249, high: 253, low: 247, close: 252, volume: 120, ...candleTone(249, 252) },
      ],
      0,
      3,
      (time) => indexes.get(time) ?? null,
    )).toEqual({ minValue: 248, maxValue: 254 });
  });

  it("does not let distant historical zones flatten the visible candles", () => {
    const indexes = new Map([
      ["2025-01-01", 0],
      ["2025-01-02", 1],
    ]);
    const distantZone = {
      key: "distant",
      startDate: "2025-01-01",
      endDate: "2025-01-02",
      startCenterPrice: 350,
      startLowerPrice: 345,
      startUpperPrice: 355,
      endCenterPrice: 350,
      endLowerPrice: 345,
      endUpperPrice: 355,
      slopePerSession: 0,
      slopeAtrPerSession: 0,
      role: "support" as const,
      description: "distant",
    };
    const bars = [
      { time: "2025-01-01", open: 500, high: 520, low: 495, close: 515, volume: 100, ...candleTone(500, 515) },
      { time: "2025-01-02", open: 515, high: 550, low: 510, close: 540, volume: 120, ...candleTone(515, 540) },
    ];

    expect(visibleZonePriceRange(
      [distantZone],
      bars,
      0,
      1,
      (time) => indexes.get(time) ?? null,
    )).toBeNull();
  });

  it("shows only zones active at the visible end as current legend items", () => {
    const base = {
      key: "historical-support",
      startDate: "2025-01-01",
      endDate: "2025-01-02",
      startCenterPrice: 10,
      startLowerPrice: 9,
      startUpperPrice: 11,
      endCenterPrice: 10,
      endLowerPrice: 9,
      endUpperPrice: 11,
      slopePerSession: 0,
      slopeAtrPerSession: 0,
      role: "support" as const,
      description: "historical",
    };
    const currentSupport = { ...base, key: "current-support", startDate: "2025-01-02", endDate: "2025-01-03" };
    const currentResistance = { ...base, key: "current-resistance", endDate: "2025-01-03", role: "resistance" as const };

    expect(currentZoneOverlays(
      [base, currentResistance, currentSupport],
      "2025-01-03",
    ).map((zone) => zone.key)).toEqual(["current-resistance", "current-support"]);
    expect(currentZoneOverlays([base], "2025-01-03")).toEqual([]);
  });

  it("keeps the latest visible support and resistance segments in the lifecycle legend", () => {
    const base = {
      key: "old-resistance",
      startDate: "2025-01-01",
      endDate: "2025-01-05",
      startCenterPrice: 10,
      startLowerPrice: 9,
      startUpperPrice: 11,
      endCenterPrice: 10,
      endLowerPrice: 9,
      endUpperPrice: 11,
      slopePerSession: 0,
      slopeAtrPerSession: 0,
      role: "resistance" as const,
      description: "old resistance",
    };
    const latestResistance = { ...base, key: "latest-resistance", startDate: "2025-01-06", endDate: "2025-01-10" };
    const flippedSupport = { ...base, key: "flipped-support", startDate: "2025-01-11", endDate: "2025-01-12", role: "support" as const };

    expect(latestVisibleZoneOverlaysByRole(
      [flippedSupport, base, latestResistance],
    ).map((zone) => zone.key)).toEqual(["latest-resistance", "flipped-support"]);
  });

  it("keeps text only on primary trade markers and groups same-day secondary events", () => {
    const grouped = groupCandleOverlayMarkers([
      { key: "buy", label: "买入", date: "2025-01-02", price: 10, tone: "buy", description: "fill" },
      { key: "t1", label: "触碰", date: "2025-01-02", price: 9, tone: "neckline", description: "first" },
      { key: "t2", label: "触碰", date: "2025-01-02", price: 9.1, tone: "neckline", description: "second" },
    ]);
    expect(grouped).toHaveLength(2);
    expect(grouped.find((item) => item.key === "buy")?.showText).toBe(true);
    const touch = grouped.find((item) => item.key.startsWith("group:"));
    expect(touch).toMatchObject({ showText: false, label: "触碰 ×2" });
    expect(touch?.details).toEqual(["first", "second"]);
  });

  it("omits invalidation audit markers from the chart and legend", () => {
    expect(isDisplayableSupportResistanceEventType("invalidation")).toBe(false);
    expect(isDisplayableSupportResistanceEventType("touch")).toBe(true);
    expect(isDisplayableSupportResistanceEventType("role_transition")).toBe(true);
  });

  it("places both fill arrows above their bars", () => {
    const bars = normalizeCandleBars([
      { trade_date: "2025-01-02", open: 10, high: 12, low: 9, close: 11, volume: 100 },
      { trade_date: "2025-01-03", open: 11, high: 13, low: 10, close: 12, volume: 120 },
    ]);
    const markers = buildCandleSeriesMarkers([
      { key: "buy", label: "买入", date: "2025-01-02", price: 10, tone: "buy", description: "buy" },
      { key: "sell", label: "卖出", date: "2025-01-03", price: 12, tone: "sell", description: "sell" },
    ], bars);
    expect(markers).toEqual([
      expect.objectContaining({ id: "buy", position: "aboveBar", shape: "arrowUp" }),
      expect.objectContaining({ id: "sell", position: "aboveBar", shape: "arrowDown" }),
    ]);
  });

  it("maps lifecycle and Double Bottom markers to exact dates and prices", () => {
    const bars = normalizeCandleBars([
      { trade_date: "2025-01-02", open: 10, high: 12, low: 9, close: 11, volume: 100 },
      { trade_date: "2025-01-03", open: 11, high: 13, low: 10, close: 12, volume: 120 },
    ]);
    const markers = buildCandleSeriesMarkers([
      { key: "left", label: "左底", date: "2025-01-02", price: 9.25, tone: "left_bottom", description: "左底" },
      { key: "neckline", label: "Neckline", date: "2025-01-03", price: null, tone: "neckline", description: "Neckline" },
      { key: "outside", label: "Outside", date: "2025-01-04", price: 13, tone: "breakout", description: "Outside" },
    ], bars);
    expect(markers).toEqual([
      expect.objectContaining({ id: "left", time: "2025-01-02", price: 9.25, position: "atPriceBottom", color: "#eab308" }),
      expect.objectContaining({ id: "neckline", time: "2025-01-03", price: 12, position: "atPriceTop", color: "#94a3b8" }),
    ]);
  });

  it("uses date-only chart keys without timezone conversion", () => {
    expect(toChartTime("2025-08-29T20:00:00-04:00")).toBe("2025-08-29");
    expect(toChartTime("bad")).toBeNull();
  });
});
