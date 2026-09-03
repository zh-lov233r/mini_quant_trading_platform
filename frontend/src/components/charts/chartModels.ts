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
  stageIndex?: number | null;
  stageKey?: string | null;
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
  role: "support" | "resistance" | "entry_channel";
  description: string;
}

export type ChartMarketRegime = "uptrend" | "downtrend" | "range" | "transition";

export interface ChartRegimeOverlay {
  key: string;
  startDate: string;
  endDate: string;
  regime: ChartMarketRegime;
  sessionCount: number;
  label: string;
  description: string;
}

export interface NormalizedRegimeOverlays {
  intervals: ChartRegimeOverlay[];
  error: string | null;
}

interface RegimeCoverageWindow {
  startDate?: string | null;
  endDate?: string | null;
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
  "entry_channel_rejection",
  "execution_rejection",
  "direct_breakout_audit",
  "channel_fill_violation",
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

export function normalizeRegimeOverlays(
  intervals: ChartRegimeOverlay[],
  availableDates: string[],
  requireCoverage = false,
  coverage: RegimeCoverageWindow = {},
): NormalizedRegimeOverlays {
  const dates = Array.from(new Set(availableDates))
    .filter((tradeDate) =>
      (!coverage.startDate || tradeDate >= coverage.startDate)
      && (!coverage.endDate || tradeDate <= coverage.endDate))
    .sort();
  if (dates.length === 0) {
    return { intervals: [], error: null };
  }
  if (intervals.length === 0) {
    return {
      intervals: [],
      error: requireCoverage ? "missing regime intervals" : null,
    };
  }
  const validRegimes = new Set<ChartMarketRegime>(["uptrend", "downtrend", "range", "transition"]);
  const invalid = intervals.find(
    (item) => !validRegimes.has(item.regime) || item.endDate < item.startDate || item.sessionCount < 1,
  );
  if (invalid) {
    return { intervals: [], error: `invalid regime interval ${invalid.key}` };
  }
  const normalized = intervals
    .map((item) => {
      const covered = dates.filter((tradeDate) => tradeDate >= item.startDate && tradeDate <= item.endDate);
      if (covered.length === 0) return null;
      return { ...item, startDate: covered[0], endDate: covered[covered.length - 1] };
    })
    .filter((item): item is ChartRegimeOverlay => item !== null)
    .sort((left, right) => left.startDate.localeCompare(right.startDate) || left.key.localeCompare(right.key));
  if (normalized.length === 0) {
    return { intervals: [], error: "regime intervals do not intersect visible market sessions" };
  }
  const ownership = new Map<string, string>();
  for (const interval of normalized) {
    for (const tradeDate of dates) {
      if (tradeDate < interval.startDate || tradeDate > interval.endDate) continue;
      const existing = ownership.get(tradeDate);
      if (existing) {
        return { intervals: [], error: `overlapping regime intervals on ${tradeDate}` };
      }
      ownership.set(tradeDate, interval.key);
    }
  }
  if (requireCoverage) {
    const uncovered = dates.find((tradeDate) => !ownership.has(tradeDate));
    if (uncovered) return { intervals: [], error: `missing regime interval on ${uncovered}` };
  }
  return { intervals: normalized, error: null };
}

export function currentZoneOverlays(
  zones: ChartZoneOverlay[],
  visibleEndDate: string | null,
): ChartZoneOverlay[] {
  if (!visibleEndDate) return [];
  const current = new Map<ChartZoneOverlay["role"], ChartZoneOverlay>();
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
  const latest = new Map<ChartZoneOverlay["role"], ChartZoneOverlay>();
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
        showText: marker.showText ?? false,
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
    pattern_bottom: "#eab308",
    shoulder: "#a78bfa",
    pullback: "#14b8a6",
    reversal: "#f97316",
  };
  return colors[tone] || "#94a3b8";
}

export function isLowerGutterMarkerTone(tone: string): boolean {
  return tone === "buy"
    || tone === "buy_signal"
    || tone === "left_bottom"
    || tone === "right_bottom"
    || tone === "pattern_bottom"
    || tone === "shoulder"
    || tone === "pullback";
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
  const grouped = new Map<string, {
    time: string;
    action: "BUY" | "SELL";
    stageIndex: number | null;
    items: EquityEventInput[];
  }>();
  events.forEach((event) => {
    if (event.action !== "BUY" && event.action !== "SELL") return;
    const time = toChartTime(event.ts);
    if (!time || !equityByDate.has(time)) return;
    const stageIndex = !signal && event.action === "BUY" && [1, 2, 3].includes(event.stageIndex ?? 0)
      ? event.stageIndex as number
      : null;
    const key = `${time}-${event.action}-${stageIndex ?? 0}`;
    const existing = grouped.get(key);
    if (existing) existing.items.push(event);
    else grouped.set(key, { time, action: event.action, stageIndex, items: [event] });
  });

  return Array.from(grouped.values()).map((group) => {
    const buy = group.action === "BUY";
    const category: EquityMarkerCategory = signal
      ? buy ? "buy_signal" : "sell_signal"
      : buy ? "buy_fill" : "sell_fill";
    const noun = signal
      ? locale === "zh-CN" ? "信号" : "Signals"
      : locale === "zh-CN" ? "成交" : "Fills";
    const stageLabel = group.stageIndex === 1
      ? locale === "zh-CN" ? "试仓" : "Probe"
      : group.stageIndex === 2
        ? locale === "zh-CN" ? "加仓" : "Add"
        : group.stageIndex === 3
          ? locale === "zh-CN" ? "确认仓" : "Confirmed"
          : null;
    return {
      id: `${signal ? "signal" : "fill"}-${group.action}-${group.stageIndex ?? 0}-${group.time}`,
      time: group.time,
      price: equityByDate.get(group.time) as number,
      category,
      color: signal
        ? buy ? "#2563eb" : "#d97706"
        : group.stageIndex === 1 ? "#0ea5e9"
          : group.stageIndex === 2 ? "#f59e0b"
            : buy ? "#16a34a" : "#dc2626",
      shape: signal || group.stageIndex === 1 ? "circle" : buy ? "arrowUp" : "arrowDown",
      position: buy ? "atPriceBottom" : "atPriceTop",
      text: stageLabel || "",
      title: stageLabel
        ? `${stageLabel}${locale === "zh-CN" ? noun : ` ${noun}`} (${group.items.length})`
        : `${group.action} ${noun} (${group.items.length})`,
      details: group.items.map((item) => {
        const stageKey = item.stageKey ? ` · ${item.stageKey}` : "";
        return item.reason ? `${item.symbol}: ${item.reason}${stageKey}` : `${item.symbol}${stageKey}`;
      }),
    };
  });
}

/** Mark only persisted zone-member pivots, never infer new signals from the visible window. */
export function buildSupportResistancePivotMarkers(
  versions: readonly { symbol: string; source_metadata: Record<string, unknown> }[],
  bars: readonly CandleBarOut[],
  locale: string,
): ChartOverlayMarker[] {
  const byDate = new Map(bars.map(bar => [bar.trade_date, bar]));
  const markers = new Map<string, ChartOverlayMarker>();
  for (const version of versions) {
    const keys = version.source_metadata.pivot_keys;
    if (!Array.isArray(keys)) continue;
    for (const key of keys) {
      if (typeof key !== "string") continue;
      const match = /^(low|high):(\d{4}-\d{2}-\d{2})$/.exec(key);
      if (!match) continue;
      const [, kind, date] = match;
      const bar = byDate.get(date);
      if (!bar) continue;
      const id = `sr-pivot-${version.symbol}-${key}`;
      const label = kind === "low" ? (locale === "zh-CN" ? "低点 Pivot" : "Pivot low") : (locale === "zh-CN" ? "高点 Pivot" : "Pivot high");
      markers.set(id, { key: id, date, showText: true, price: kind === "low" ? bar.low : bar.high,
        label, tone: kind === "low" ? "left_bottom" : "neckline",
        description: `${label} · ${date} · ${locale === "zh-CN" ? "区域拟合锚点（事后审计，非当日信号）" : "Zone fitting anchor (retrospective audit, not a same-day signal)"}` });
    }
  }
  return [...markers.values()].sort((a, b) => a.date.localeCompare(b.date) || a.key.localeCompare(b.key));
}
