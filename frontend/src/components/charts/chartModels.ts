import type { CandleBarOut } from "@/types/quote";

export type ChartTime = string;

export interface EquityPointInput {
  ts?: string | null;
  equity?: number | null;
  drawdown?: number | null;
  benchmark_equity?: number | null;
}

export interface EquityChartPoint {
  time: ChartTime;
  sourceTs: string;
  value: number;
  drawdown: number | null;
  benchmarkEquity: number | null;
}

export interface EquityEventInput {
  id: string;
  ts?: string | null;
  symbol: string;
  action: string;
  reason?: string | null;
}

export type EquityMarkerCategory = "buy_signal" | "sell_signal" | "buy_fill" | "sell_fill";

export interface ChartEventMarker {
  id: string;
  time: ChartTime;
  price: number;
  category: EquityMarkerCategory;
  color: string;
  shape: "circle" | "arrowUp" | "arrowDown";
  position: "atPriceTop" | "atPriceBottom";
  text: string;
  title: string;
  details: string[];
}

export interface CandleChartPoint {
  time: ChartTime;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  color: string;
  wickColor: string;
  borderColor: string;
}

export interface ChartOverlayMarker {
  key: string;
  label: string;
  date: string;
  price: number | null;
  tone: string;
  description: string;
  details?: string[];
  showText?: boolean;
  groupKey?: string;
}

export interface ChartGapOverlay {
  key: string;
  label: string;
  referenceDate: string;
  anchorDate: string;
  lowPrice: number;
  highPrice: number;
  tone: "left_gap" | "right_gap";
  description: string;
}

export interface ChartZoneOverlay {
  key: string;
  startDate: string;
  endDate: string;
  startCenterPrice: number;
  startLowerPrice: number;
  startUpperPrice: number;
  endCenterPrice: number;
  endLowerPrice: number;
  endUpperPrice: number;
  slopePerSession: number;
  slopeAtrPerSession: number | null;
  role: "support" | "resistance";
  description: string;
}

interface CandleSeriesMarkerBase {
  id: string;
  time: ChartTime;
  shape: "circle" | "arrowUp" | "arrowDown";
  color: string;
  text: string;
}

export type CandleSeriesMarker = CandleSeriesMarkerBase & (
  | { position: "aboveBar" | "belowBar" }
  | { position: "atPriceTop" | "atPriceBottom"; price: number }
);

const DISPLAYABLE_SUPPORT_RESISTANCE_EVENT_TYPES = new Set([
  "touch",
  "breakout",
  "retest",
  "candidate",
  "selection",
  "role_transition",
]);

export function isDisplayableSupportResistanceEventType(eventType: string): boolean {
  return DISPLAYABLE_SUPPORT_RESISTANCE_EVENT_TYPES.has(eventType);
}

export function toChartTime(value?: string | null): ChartTime | null {
  if (!value) return null;
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match?.[1] ?? null;
}

export function normalizeEquityPoints(points: EquityPointInput[]): EquityChartPoint[] {
  const byTime = new Map<ChartTime, EquityChartPoint>();
  points.forEach((point) => {
    const time = toChartTime(point.ts);
    if (!time || typeof point.equity !== "number" || !Number.isFinite(point.equity)) return;
    byTime.set(time, {
      time,
      sourceTs: point.ts || time,
      value: point.equity,
      drawdown:
        typeof point.drawdown === "number" && Number.isFinite(point.drawdown)
          ? point.drawdown
          : null,
      benchmarkEquity:
        typeof point.benchmark_equity === "number" && Number.isFinite(point.benchmark_equity)
          ? point.benchmark_equity
          : null,
    });
  });
  return Array.from(byTime.values()).sort((left, right) => left.time.localeCompare(right.time));
}

export function normalizeCandleBars(bars: CandleBarOut[]): CandleChartPoint[] {
  const byTime = new Map<ChartTime, CandleChartPoint>();
  bars.forEach((bar) => {
    const time = toChartTime(bar.trade_date);
    const values = [bar.open, bar.high, bar.low, bar.close];
    if (!time || values.some((value) => typeof value !== "number" || !Number.isFinite(value))) return;
    const tone = candleTone(bar.open, bar.close);
    byTime.set(time, {
      time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume: typeof bar.volume === "number" && Number.isFinite(bar.volume) ? Math.max(0, bar.volume) : 0,
      ...tone,
    });
  });
  return Array.from(byTime.values()).sort((left, right) => left.time.localeCompare(right.time));
}

export function candleTone(open: number, close: number) {
  if (close === open) {
    return { color: "#fbbf24", wickColor: "#fbbf24", borderColor: "#fbbf24" };
  }
  if (close > open) {
    return { color: "rgba(52, 211, 153, 0.32)", wickColor: "#34d399", borderColor: "#34d399" };
  }
  return { color: "rgba(251, 113, 133, 0.32)", wickColor: "#fb7185", borderColor: "#fb7185" };
}

export function normalizeGapOverlays(
  gaps: ChartGapOverlay[],
  availableTimes: Set<string>,
): ChartGapOverlay[] {
  return gaps
    .filter((gap) =>
      availableTimes.has(gap.referenceDate)
      && availableTimes.has(gap.anchorDate)
      && Number.isFinite(gap.lowPrice)
      && Number.isFinite(gap.highPrice)
      && gap.highPrice > gap.lowPrice)
    .sort((left, right) =>
      left.referenceDate.localeCompare(right.referenceDate) || left.key.localeCompare(right.key));
}

export function normalizeZoneOverlays(
  zones: ChartZoneOverlay[],
  availableTimes: Set<string>,
): ChartZoneOverlay[] {
  return zones
    .filter((zone) =>
      availableTimes.has(zone.startDate)
      && availableTimes.has(zone.endDate)
      && zone.endDate >= zone.startDate
      && [
        zone.startCenterPrice,
        zone.startLowerPrice,
        zone.startUpperPrice,
        zone.endCenterPrice,
        zone.endLowerPrice,
        zone.endUpperPrice,
        zone.slopePerSession,
      ].every(Number.isFinite)
      && zone.startUpperPrice >= zone.startLowerPrice
      && zone.endUpperPrice >= zone.endLowerPrice)
    .sort((left, right) =>
      left.startDate.localeCompare(right.startDate) || left.key.localeCompare(right.key));
}

export function currentZoneOverlays(
  zones: ChartZoneOverlay[],
  visibleEndDate: string | null,
): ChartZoneOverlay[] {
  if (!visibleEndDate) return [];
  const current = new Map<"support" | "resistance", ChartZoneOverlay>();
  zones.forEach((zone) => {
    if (zone.endDate !== visibleEndDate) return;
    const existing = current.get(zone.role);
    if (
      !existing
      || existing.startDate < zone.startDate
      || (existing.startDate === zone.startDate && existing.key < zone.key)
    ) {
      current.set(zone.role, zone);
    }
  });
  return Array.from(current.values()).sort((left, right) => left.role.localeCompare(right.role));
}

export function latestVisibleZoneOverlaysByRole(
  zones: ChartZoneOverlay[],
): ChartZoneOverlay[] {
  const latest = new Map<"support" | "resistance", ChartZoneOverlay>();
  zones.forEach((zone) => {
    const existing = latest.get(zone.role);
    if (
      !existing
      || existing.endDate < zone.endDate
      || (existing.endDate === zone.endDate && existing.startDate < zone.startDate)
      || (
        existing.endDate === zone.endDate
        && existing.startDate === zone.startDate
        && existing.key < zone.key
      )
    ) {
      latest.set(zone.role, zone);
    }
  });
  return Array.from(latest.values()).sort((left, right) => left.role.localeCompare(right.role));
}

const CANDLE_PRIMARY_MARKER_TONES = new Set(["buy", "sell", "buy_signal", "sell_signal"]);
export function groupCandleOverlayMarkers(markers: ChartOverlayMarker[]): ChartOverlayMarker[] {
  const grouped = new Map<string, ChartOverlayMarker>();
  markers.forEach((marker) => {
    if (CANDLE_PRIMARY_MARKER_TONES.has(marker.tone)) {
      grouped.set(marker.key, { ...marker, showText: true });
      return;
    }
    const key = `${marker.date}:${marker.groupKey || marker.tone}:${marker.label}`;
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, {
        ...marker,
        key: `group:${key}`,
        showText: false,
        details: marker.details || [marker.description],
      });
      return;
    }
    const details = [...(existing.details || [existing.description]), marker.description];
    grouped.set(key, {
      ...existing,
      label: `${existing.label} ×${details.length}`,
      description: details.join("\n"),
      details,
      price: existing.price ?? marker.price,
    });
  });
  return Array.from(grouped.values()).sort((left, right) =>
    left.date.localeCompare(right.date) || left.key.localeCompare(right.key));
}

export function buildLifecycleLeaderMarkers(markers: ChartOverlayMarker[]): ChartOverlayMarker[] {
  // Every lifecycle event belongs to the custom gutter layer. There is no
  // tone allowlist, so future event colors cannot fall back over a candle.
  return groupCandleOverlayMarkers(markers);
}

export function buildCandleSeriesMarkers(
  markers: ChartOverlayMarker[],
  bars: CandleChartPoint[],
): CandleSeriesMarker[] {
  const closeByTime = new Map(bars.map((bar) => [bar.time, bar.close]));
  return markers
    .map((marker): CandleSeriesMarker | null => {
      const time = toChartTime(marker.date);
      if (!time || !closeByTime.has(time)) return null;
      const price = marker.price ?? closeByTime.get(time);
      if (typeof price !== "number" || !Number.isFinite(price)) return null;
      const isFill = marker.tone === "buy" || marker.tone === "sell";
      const base = {
        id: marker.key,
        time,
        shape: marker.tone === "buy" ? "arrowUp" as const : marker.tone === "sell" ? "arrowDown" as const : "circle" as const,
        color: candleMarkerColor(marker.tone),
        text: marker.showText === false ? "" : marker.label,
      };
      if (isFill) {
        return {
          ...base,
          position: "aboveBar",
        };
      }
      return {
        ...base,
        price,
        position: marker.tone === "buy" || marker.tone === "buy_signal" || marker.tone === "left_bottom"
          ? "atPriceBottom"
          : "atPriceTop",
      };
    })
    .filter((marker): marker is CandleSeriesMarker => marker !== null)
    .sort((left, right) => left.time.localeCompare(right.time) || left.id.localeCompare(right.id));
}

export function candleMarkerColor(tone: string) {
  const colors: Record<string, string> = {
    buy: "#22c55e",
    buy_signal: "#38bdf8",
    sell: "#ef4444",
    sell_signal: "#f59e0b",
    mark: "#38bdf8",
    neckline: "#94a3b8",
    breakout: "#f97316",
    left_bottom: "#eab308",
    right_bottom: "#14b8a6",
  };
  return colors[tone] || "#94a3b8";
}

export function buildEquityEventMarkers(
  points: EquityChartPoint[],
  signals: EquityEventInput[],
  fills: EquityEventInput[],
  locale: string,
): ChartEventMarker[] {
  const equityByDate = new Map(points.map((point) => [point.time, point.value]));
  const markers = [
    ...groupEquityEvents(signals, equityByDate, locale, true),
    ...groupEquityEvents(fills, equityByDate, locale, false),
  ];
  return markers.sort((left, right) => left.time.localeCompare(right.time) || left.id.localeCompare(right.id));
}

function groupEquityEvents(
  events: EquityEventInput[],
  equityByDate: Map<string, number>,
  locale: string,
  signal: boolean,
): ChartEventMarker[] {
  const grouped = new Map<string, { time: string; action: "BUY" | "SELL"; items: EquityEventInput[] }>();
  events.forEach((event) => {
    if (event.action !== "BUY" && event.action !== "SELL") return;
    const time = toChartTime(event.ts);
    if (!time || !equityByDate.has(time)) return;
    const key = `${time}-${event.action}`;
    const existing = grouped.get(key);
    if (existing) existing.items.push(event);
    else grouped.set(key, { time, action: event.action, items: [event] });
  });

  return Array.from(grouped.values()).map((group) => {
    const buy = group.action === "BUY";
    const category: EquityMarkerCategory = signal
      ? buy ? "buy_signal" : "sell_signal"
      : buy ? "buy_fill" : "sell_fill";
    const noun = signal
      ? locale === "zh-CN" ? "信号" : "Signals"
      : locale === "zh-CN" ? "成交" : "Fills";
    return {
      id: `${signal ? "signal" : "fill"}-${group.action}-${group.time}`,
      time: group.time,
      price: equityByDate.get(group.time) as number,
      category,
      color: signal ? (buy ? "#2563eb" : "#d97706") : buy ? "#16a34a" : "#dc2626",
      shape: signal ? "circle" : buy ? "arrowUp" : "arrowDown",
      position: buy ? "atPriceBottom" : "atPriceTop",
      text: "",
      title: `${group.action} ${noun} (${group.items.length})`,
      details: group.items.map((item) => item.reason ? `${item.symbol}: ${item.reason}` : item.symbol),
    };
  });
}
