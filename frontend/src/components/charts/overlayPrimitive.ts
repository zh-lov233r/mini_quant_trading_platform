import type {
  AutoscaleInfo,
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

import {
  candleMarkerColor,
  toChartTime,
} from "@/components/charts/chartModels";
import type {
  CandleChartPoint,
  ChartGapOverlay,
  ChartOverlayMarker,
  ChartZoneOverlay,
} from "@/components/charts/chartModels";

type DrawingTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export class LifecycleOverlayPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view: LifecycleOverlayView;

  constructor(
    gaps: ChartGapOverlay[],
    zones: ChartZoneOverlay[],
    bars: CandleChartPoint[],
    leaderMarkers: ChartOverlayMarker[] = [],
  ) {
    this.view = new LifecycleOverlayView(
      gaps,
      zones,
      bars,
      leaderMarkers,
      () => this.chart,
      () => this.series,
    );
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>) {
    this.chart = param.chart;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
    this.requestUpdate();
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  updateAllViews() {
    this.view.invalidate();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    return this.view.hitTest(x, y);
  }

  autoscaleInfo(startTimePoint: Logical, endTimePoint: Logical): AutoscaleInfo | null {
    if (!this.chart) return null;
    const priceRange = visibleZonePriceRange(
      this.view.zones,
      this.view.bars,
      Number(startTimePoint),
      Number(endTimePoint),
      (time) => {
        const index = this.chart?.timeScale().timeToIndex(time, true);
        return index == null ? null : Number(index);
      },
    );
    if (!priceRange) return null;
    return {
      priceRange,
      margins: { above: 36, below: 12 },
    };
  }
}

class LifecycleOverlayView implements IPrimitivePaneView {
  private readonly rendererValue: LifecycleOverlayRenderer;

  constructor(
    gaps: ChartGapOverlay[],
    readonly zones: ChartZoneOverlay[],
    readonly bars: CandleChartPoint[],
    leaderMarkers: ChartOverlayMarker[],
    chart: () => IChartApiBase<Time> | null,
    series: () => ISeriesApi<SeriesType, Time> | null,
  ) {
    this.rendererValue = new LifecycleOverlayRenderer(gaps, zones, bars, leaderMarkers, chart, series);
  }

  zOrder() {
    return "top" as const;
  }

  renderer() {
    return this.rendererValue;
  }

  invalidate() {
    this.rendererValue.invalidate();
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    return this.rendererValue.hitTest(x, y);
  }
}

export function visibleZonePriceRange(
  zones: ChartZoneOverlay[],
  bars: CandleChartPoint[],
  visibleStartIndex: number,
  visibleEndIndex: number,
  timeToIndex: (time: string) => number | null,
): { minValue: number; maxValue: number } | null {
  const visibleBars = bars.filter((bar) => {
    const index = timeToIndex(bar.time);
    return index != null && index >= visibleStartIndex && index <= visibleEndIndex;
  });
  if (visibleBars.length === 0) return null;
  const candleMin = Math.min(...visibleBars.map((bar) => bar.low));
  const candleMax = Math.max(...visibleBars.map((bar) => bar.high));
  const candleSpan = Math.max(candleMax - candleMin, Math.abs(candleMax) * 0.02, 1);
  const proximityPadding = candleSpan * 0.25;
  const relevantMin = candleMin - proximityPadding;
  const relevantMax = candleMax + proximityPadding;
  const prices: number[] = [];
  zones.forEach((zone) => {
    const startIndex = timeToIndex(zone.startDate);
    const endIndex = timeToIndex(zone.endDate);
    if (startIndex == null || endIndex == null) return;
    if (endIndex < visibleStartIndex || startIndex > visibleEndIndex) return;
    const zonePrices = [
      zone.startLowerPrice,
      zone.startUpperPrice,
      zone.endLowerPrice,
      zone.endUpperPrice,
    ];
    const zoneMin = Math.min(...zonePrices);
    const zoneMax = Math.max(...zonePrices);
    if (zoneMax < relevantMin || zoneMin > relevantMax) return;
    prices.push(...zonePrices);
  });
  if (prices.length === 0) return null;
  return {
    minValue: Math.min(...prices),
    maxValue: Math.max(...prices),
  };
}

export interface MarkerLabelBounds {
  left: number;
  right: number;
}

export function chooseMarkerLabelPlacement(
  lanes: MarkerLabelBounds[][],
  candidates: MarkerLabelBounds[],
  gap = 4,
): { lane: number; bounds: MarkerLabelBounds } {
  for (let lane = 0; ; lane += 1) {
    const occupied = lanes[lane] || [];
    const bounds = candidates.find((candidate) =>
      occupied.every((item) =>
        candidate.right <= item.left - gap || candidate.left >= item.right + gap));
    if (bounds) return { lane, bounds };
  }
}

class LifecycleOverlayRenderer implements IPrimitivePaneRenderer {
  private cacheKey = "";
  private readonly barByTime: Map<string, CandleChartPoint>;
  private markerHits: Array<{
    key: string;
    left: number;
    right: number;
    top: number;
    bottom: number;
    centerX: number;
    centerY: number;
  }> = [];

  constructor(
    private readonly gaps: ChartGapOverlay[],
    private readonly zones: ChartZoneOverlay[],
    bars: CandleChartPoint[],
    private readonly leaderMarkers: ChartOverlayMarker[],
    private readonly chart: () => IChartApiBase<Time> | null,
    private readonly series: () => ISeriesApi<SeriesType, Time> | null,
  ) {
    this.barByTime = new Map(bars.map((bar) => [bar.time, bar]));
  }

  invalidate() {
    this.cacheKey = "";
    this.markerHits = [];
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    let closest: (typeof this.markerHits)[number] | null = null;
    let closestDistance = Number.POSITIVE_INFINITY;
    for (const hit of this.markerHits) {
      if (x < hit.left || x > hit.right || y < hit.top || y > hit.bottom) continue;
      const distance = Math.hypot(x - hit.centerX, y - hit.centerY);
      if (distance < closestDistance) {
        closest = hit;
        closestDistance = distance;
      }
    }
    if (!closest) return null;
    return {
      externalId: closest.key,
      zOrder: "top",
      cursorStyle: "pointer",
      itemType: "marker",
      hitTestPriority: 2,
      distance: closestDistance,
    };
  }

  drawBackground(target: DrawingTarget) {
    const chart = this.chart();
    const series = this.series();
    if (!chart || !series) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const nextKey = `${mediaSize.width}:${mediaSize.height}:${this.gaps.length}:${this.zones.length}`;
      this.cacheKey = nextKey;
      this.zones.forEach((zone) => {
        const startX = chart.timeScale().timeToCoordinate(zone.startDate);
        const endX = zone.endDate ? chart.timeScale().timeToCoordinate(zone.endDate) : mediaSize.width;
        const startUpperY = series.priceToCoordinate(zone.startUpperPrice);
        const startLowerY = series.priceToCoordinate(zone.startLowerPrice);
        const endUpperY = series.priceToCoordinate(zone.endUpperPrice);
        const endLowerY = series.priceToCoordinate(zone.endLowerPrice);
        if ([startX, endX, startUpperY, startLowerY, endUpperY, endLowerY].some((value) => value == null)) return;
        const support = zone.role === "support";
        context.fillStyle = support ? "rgba(34, 197, 94, 0.14)" : "rgba(239, 68, 68, 0.13)";
        context.strokeStyle = support ? "#22c55e" : "#ef4444";
        context.lineWidth = 1.25;
        context.setLineDash([6, 4]);
        context.beginPath();
        context.moveTo(startX!, startUpperY!);
        context.lineTo(endX!, endUpperY!);
        context.lineTo(endX!, endLowerY!);
        context.lineTo(startX!, startLowerY!);
        context.closePath();
        context.fill();
        context.stroke();
      });

      this.gaps.forEach((gap) => {
        const leftX = chart.timeScale().timeToCoordinate(gap.referenceDate);
        const rightX = chart.timeScale().timeToCoordinate(gap.anchorDate);
        const upperY = series.priceToCoordinate(gap.highPrice);
        const lowerY = series.priceToCoordinate(gap.lowPrice);
        if (leftX == null || rightX == null || upperY == null || lowerY == null) return;
        const x = Math.min(leftX, rightX);
        const width = Math.max(3, Math.abs(rightX - leftX));
        const y = Math.min(upperY, lowerY);
        const height = Math.max(3, Math.abs(lowerY - upperY));
        const leftGap = gap.tone === "left_gap";
        context.fillStyle = leftGap ? "rgba(245, 158, 11, 0.14)" : "rgba(6, 182, 212, 0.14)";
        context.strokeStyle = leftGap ? "#f59e0b" : "#06b6d4";
        context.lineWidth = 1.3;
        context.setLineDash([5, 4]);
        context.fillRect(x, y, width, height);
        context.strokeRect(x, y, width, height);
      });
      context.setLineDash([]);
    });
  }

  draw(target: DrawingTarget) {
    const chart = this.chart();
    const series = this.series();
    if (!chart || !series) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      this.markerHits = [];
      const occupied: Array<{ left: number; right: number }> = [];
      this.gaps.forEach((gap) => {
        const leftX = chart.timeScale().timeToCoordinate(gap.referenceDate);
        const rightX = chart.timeScale().timeToCoordinate(gap.anchorDate);
        const upperY = series.priceToCoordinate(gap.highPrice);
        if (leftX == null || rightX == null || upperY == null) return;
        const centerX = (leftX + rightX) / 2;
        const width = Math.max(58, gap.label.length * 7 + 18);
        let left = clamp(centerX - width / 2, 2, Math.max(2, mediaSize.width - width - 2));
        for (const item of occupied) {
          if (left < item.right + 4 && left + width > item.left - 4) {
            left = clamp(item.right + 4, 2, Math.max(2, mediaSize.width - width - 2));
          }
        }
        occupied.push({ left, right: left + width });
        const top = 6;
        const color = gap.tone === "left_gap" ? "#f59e0b" : "#06b6d4";
        context.strokeStyle = color;
        context.lineWidth = 1.2;
        context.setLineDash([4, 4]);
        context.beginPath();
        context.moveTo(centerX, Math.max(top + 22, upperY - 7));
        context.lineTo(left + width / 2, top + 20);
        context.stroke();
        context.setLineDash([]);
        roundedRect(context, left, top, width, 20, 10);
        context.fillStyle = gap.tone === "left_gap" ? "rgba(120, 53, 15, 0.94)" : "rgba(8, 47, 73, 0.94)";
        context.fill();
        context.strokeStyle = color;
        context.stroke();
        context.fillStyle = "#f8fafc";
        context.font = '700 10px "Avenir Next", "Segoe UI", sans-serif';
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(gap.label, left + width / 2, top + 10);
      });

      const occupiedMarkerLanes = {
        upper: [] as MarkerLabelBounds[][],
        lower: [] as MarkerLabelBounds[][],
      };
      this.leaderMarkers.forEach((marker) => {
        const time = toChartTime(marker.date);
        const bar = time ? this.barByTime.get(time) : null;
        const x = time ? chart.timeScale().timeToCoordinate(time) : null;
        if (!bar || x == null) return;
        const lowerGutter = marker.tone === "buy"
          || marker.tone === "buy_signal"
          || marker.tone === "left_bottom";
        const anchorY = series.priceToCoordinate(lowerGutter ? bar.low : bar.high);
        if (anchorY == null) return;

        const color = candleMarkerColor(marker.tone);
        context.save();
        context.font = '700 10px "Avenir Next", "Segoe UI", sans-serif';
        const showText = marker.showText !== false;
        const labelWidth = showText ? context.measureText(marker.label).width : 0;
        context.restore();
        const rightBounds = {
          left: x - 8,
          right: x + (showText ? labelWidth + 16 : 8),
        };
        const leftBounds = {
          left: x - (showText ? labelWidth + 16 : 8),
          right: x + 8,
        };
        const rightFits = rightBounds.right <= mediaSize.width - 2;
        const leftFits = leftBounds.left >= 2;
        const candidates = rightFits
          ? [rightBounds, ...(leftFits ? [leftBounds] : [])]
          : leftFits
            ? [leftBounds, rightBounds]
            : [rightBounds];
        const lanes = lowerGutter ? occupiedMarkerLanes.lower : occupiedMarkerLanes.upper;
        const placement = chooseMarkerLabelPlacement(lanes, candidates);
        if (!lanes[placement.lane]) lanes[placement.lane] = [];
        lanes[placement.lane].push(placement.bounds);
        const markerY = lowerGutter
          ? mediaSize.height - 18 - placement.lane * 26
          : 18 + placement.lane * 26;

        context.save();
        context.strokeStyle = color;
        context.lineWidth = 1.15;
        context.globalAlpha = 0.72;
        context.setLineDash([4, 4]);
        context.beginPath();
        context.moveTo(x, markerY + (lowerGutter ? -9 : 9));
        context.lineTo(x, anchorY + (lowerGutter ? 2 : -2));
        context.stroke();
        context.restore();

        const fillArrow = marker.tone === "buy" || marker.tone === "sell";
        context.save();
        context.fillStyle = color;
        context.strokeStyle = "rgba(3, 7, 18, 0.96)";
        context.lineWidth = 2;
        if (!fillArrow) {
          context.beginPath();
          context.arc(x, markerY, 6, 0, Math.PI * 2);
          context.fill();
          context.stroke();
        } else {
          context.beginPath();
          if (marker.tone === "buy") {
            context.moveTo(x, markerY - 9);
            context.lineTo(x - 7, markerY - 1);
            context.lineTo(x - 3, markerY - 1);
            context.lineTo(x - 3, markerY + 8);
            context.lineTo(x + 3, markerY + 8);
            context.lineTo(x + 3, markerY - 1);
            context.lineTo(x + 7, markerY - 1);
          } else {
            context.moveTo(x, markerY + 9);
            context.lineTo(x - 7, markerY + 1);
            context.lineTo(x - 3, markerY + 1);
            context.lineTo(x - 3, markerY - 8);
            context.lineTo(x + 3, markerY - 8);
            context.lineTo(x + 3, markerY + 1);
            context.lineTo(x + 7, markerY + 1);
          }
          context.closePath();
          context.fill();
          context.stroke();
        }

        if (showText) {
          context.font = '700 10px "Avenir Next", "Segoe UI", sans-serif';
          context.textBaseline = "middle";
          const placeRight = placement.bounds.right > x + 8;
          const labelX = placeRight ? x + 10 : x - 10;
          context.textAlign = placeRight ? "left" : "right";
          context.lineWidth = 3;
          context.strokeStyle = "rgba(3, 7, 18, 0.96)";
          context.strokeText(marker.label, labelX, markerY);
          context.fillStyle = color;
          context.fillText(marker.label, labelX, markerY);
        }
        context.restore();

        this.markerHits.push({
          key: marker.key,
          left: placement.bounds.left,
          right: placement.bounds.right,
          top: markerY - 12,
          bottom: markerY + 12,
          centerX: x,
          centerY: markerY,
        });
      });
    });
  }
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const safeRadius = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.roundRect(x, y, width, height, safeRadius);
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
