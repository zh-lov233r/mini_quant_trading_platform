import { describe, expect, it } from "vitest";

import {
  buildCandleSeriesMarkers,
  buildSupportResistancePivotMarkers,
  buildLifecycleLeaderMarkers,
  buildEquityEventMarkers,
  candleTone,
  currentZoneOverlays,
  groupCandleOverlayMarkers,
  isDisplayableSupportResistanceEventType,
  isLowerGutterMarkerTone,
  latestVisibleZoneOverlaysByRole,
  normalizeCandleBars,
  normalizeEquityPoints,
  normalizeGapOverlays,
  normalizeRegimeOverlays,
  normalizeZoneOverlays,
  toChartTime,
} from "./chartModels";
import { chooseMarkerLabelPlacement, markerLabelCandidates, visibleZonePriceRange } from "./overlayPrimitive";

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

  it("keeps staged fills distinct and labels their cumulative entry stage", () => {
    const points = normalizeEquityPoints([
      { ts: "2025-01-02T21:00:00Z", equity: 102 },
    ]);
    const markers = buildEquityEventMarkers(
      points,
      [],
      [
        { id: "t1", ts: "2025-01-02T14:30:00Z", symbol: "AAA", action: "BUY", stageIndex: 1, stageKey: "stage_1" },
        { id: "t2", ts: "2025-01-02T14:30:00Z", symbol: "BBB", action: "BUY", stageIndex: 2, stageKey: "stage_2" },
        { id: "t3", ts: "2025-01-02T14:30:00Z", symbol: "CCC", action: "BUY", stageIndex: 3, stageKey: "stage_3" },
      ],
      "zh-CN",
    );

    expect(markers.map((marker) => ({
      text: marker.text,
      shape: marker.shape,
      color: marker.color,
      title: marker.title,
    }))).toEqual([
      { text: "试仓", shape: "circle", color: "#0ea5e9", title: "试仓成交 (1)" },
      { text: "加仓", shape: "arrowUp", color: "#f59e0b", title: "加仓成交 (1)" },
      { text: "确认仓", shape: "arrowUp", color: "#16a34a", title: "确认仓成交 (1)" },
    ]);
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

  it("requires regime intervals to cover each visible session exactly once", () => {
    const dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06"];
    const base = {
      key: "up",
      startDate: "2025-01-01",
      endDate: "2025-01-02",
      regime: "uptrend" as const,
      sessionCount: 2,
      label: "Uptrend",
      description: "up",
    };
    const valid = normalizeRegimeOverlays(
      [
        base,
        {
          ...base,
          key: "range",
          startDate: "2025-01-03",
          endDate: "2025-01-06",
          regime: "range" as const,
          label: "Range",
        },
      ],
      dates,
      true,
    );
    expect(valid.error).toBeNull();
    expect(valid.intervals.map((item) => item.key)).toEqual(["up", "range"]);

    const overlap = normalizeRegimeOverlays(
      [base, { ...base, key: "overlap", startDate: "2025-01-02", endDate: "2025-01-06" }],
      dates,
      true,
    );
    expect(overlap.intervals).toEqual([]);
    expect(overlap.error).toContain("overlapping");

    const gap = normalizeRegimeOverlays([base], dates, true);
    expect(gap.intervals).toEqual([]);
    expect(gap.error).toContain("missing regime interval");

    const invalid = normalizeRegimeOverlays(
      [{ ...base, key: "invalid", sessionCount: 0 }],
      dates,
      true,
    );
    expect(invalid.intervals).toEqual([]);
    expect(invalid.error).toContain("invalid regime interval");
  });

  it("ignores visible sessions outside the materialization coverage window", () => {
    const transition = {
      key: "transition",
      startDate: "2026-07-30",
      endDate: "2026-07-31",
      regime: "transition" as const,
      sessionCount: 2,
      label: "Transition",
      description: "transition",
    };
    const result = normalizeRegimeOverlays(
      [transition],
      ["2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"],
      true,
      { startDate: "2024-01-01", endDate: "2026-07-31" },
    );

    expect(result.error).toBeNull();
    expect(result.intervals).toEqual([transition]);
    expect(normalizeRegimeOverlays(
      [],
      ["2026-08-03", "2026-08-04"],
      true,
      { startDate: "2024-01-01", endDate: "2026-07-31" },
    )).toEqual({ intervals: [], error: null });
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

  it("keeps primary and explicitly requested pattern labels while grouping secondary events", () => {
    const grouped = groupCandleOverlayMarkers([
      { key: "buy", label: "买入", date: "2025-01-02", price: 10, tone: "buy", description: "fill" },
      { key: "t1", label: "触碰", date: "2025-01-02", price: 9, tone: "neckline", description: "first" },
      { key: "t2", label: "触碰", date: "2025-01-02", price: 9.1, tone: "neckline", description: "second" },
      { key: "bottom", label: "圆弧底部", date: "2025-01-03", price: 8, tone: "pattern_bottom", description: "bottom", showText: true },
    ]);
    expect(grouped).toHaveLength(3);
    expect(grouped.find((item) => item.key === "buy")?.showText).toBe(true);
    expect(grouped.find((item) => item.label === "圆弧底部")?.showText).toBe(true);
    const touch = grouped.find((item) => item.key.startsWith("group:"));
    expect(touch).toMatchObject({ showText: false, label: "触碰 ×2" });
    expect(touch?.details).toEqual(["first", "second"]);
  });

  it("routes every lifecycle marker tone to the non-overlapping leader layer", () => {
    const markers = [
      { key: "buy", label: "买入", date: "2025-01-02", price: 10, tone: "buy", description: "buy" },
      { key: "sell-signal", label: "卖信号", date: "2025-01-03", price: 11, tone: "sell_signal", description: "sell signal" },
      { key: "neckline", label: "颈线", date: "2025-01-04", price: 11, tone: "neckline", description: "neckline" },
      { key: "mark", label: "候选", date: "2025-01-05", price: 11, tone: "mark", description: "candidate" },
      { key: "right", label: "回踩", date: "2025-01-06", price: 11, tone: "right_bottom", description: "retest" },
      { key: "breakout", label: "突破", date: "2025-01-07", price: 11, tone: "breakout", description: "breakout" },
      { key: "future", label: "新事件", date: "2025-01-08", price: 11, tone: "future_tone", description: "future" },
    ];
    expect(buildLifecycleLeaderMarkers(markers).map((marker) => marker.tone)).toEqual(
      markers.map((marker) => marker.tone),
    );
  });

  it("moves overlapping marker labels to another side or lane", () => {
    const lanes = [[{ left: 20, right: 70 }]];
    expect(chooseMarkerLabelPlacement(lanes, [
      { left: 55, right: 95 },
      { left: 100, right: 140 },
    ])).toEqual({ lane: 0, bounds: { left: 100, right: 140 } });
    expect(chooseMarkerLabelPlacement(lanes, [
      { left: 50, right: 90 },
      { left: 10, right: 45 },
    ])).toEqual({ lane: 1, bounds: { left: 50, right: 90 } });
  });

  it("only shows recorded touches when a support/resistance zone is selected", () => {
    expect(isDisplayableSupportResistanceEventType("invalidation")).toBe(false);
    expect(isDisplayableSupportResistanceEventType("touch")).toBe(true);
    expect(isDisplayableSupportResistanceEventType("role_transition")).toBe(false);
    expect(isDisplayableSupportResistanceEventType("retest")).toBe(false);
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

  it("keeps pattern lows and pullbacks in the lower label gutter", () => {
    expect(["pattern_bottom", "shoulder", "pullback"].every(isLowerGutterMarkerTone)).toBe(true);
    expect(isLowerGutterMarkerTone("reversal")).toBe(false);
  });

  it("uses date-only chart keys without timezone conversion", () => {
    expect(toChartTime("2025-08-29T20:00:00-04:00")).toBe("2025-08-29");
    expect(toChartTime("bad")).toBeNull();
  });
});

it("plots persisted high/low pivot members once and only within the loaded candle window", () => {
  const versions = [{ symbol: "AAA", source_metadata: { pivot_keys: ["low:2025-01-02", "high:2025-01-02", "low:2024-12-01", "bad", null] } }];
  const bars = [{ trade_date: "2025-01-02", open: 10, high: 12, low: 8, close: 11, volume: 100 }];
  const markers = buildSupportResistancePivotMarkers([...versions, ...versions], bars, "zh-CN");
  expect(markers).toHaveLength(2);
  expect(markers.every(marker => marker.showText)).toBe(true);
  expect(markers.map(marker => marker.price)).toEqual([12, 8]);
  expect(markers[0].description).toContain("非当日信号");
});

it("keeps crowded right-edge lifecycle labels inside the chart", () => {
  const candidates = markerLabelCandidates(970, 120, 1000);
  expect(candidates).toEqual([{ left: 834, right: 978 }]);
  const placement = chooseMarkerLabelPlacement([[{ left: 800, right: 960 }]], candidates);
  expect(placement.bounds.right).toBeLessThanOrEqual(998);
  expect(placement.lane).toBe(1);
});
