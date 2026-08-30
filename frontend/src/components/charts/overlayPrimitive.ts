import type {
  AutoscaleInfo,
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

import type {
  CandleChartPoint,
  ChartGapOverlay,
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
  ) {
    this.view = new LifecycleOverlayView(
      gaps,
      zones,
      bars,
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
    chart: () => IChartApiBase<Time> | null,
    series: () => ISeriesApi<SeriesType, Time> | null,
  ) {
    this.rendererValue = new LifecycleOverlayRenderer(gaps, zones, chart, series);
  }

  zOrder() {
    return "normal" as const;
  }

  renderer() {
    return this.rendererValue;
  }

  invalidate() {
    this.rendererValue.invalidate();
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

class LifecycleOverlayRenderer implements IPrimitivePaneRenderer {
  private cacheKey = "";

  constructor(
    private readonly gaps: ChartGapOverlay[],
    private readonly zones: ChartZoneOverlay[],
    private readonly chart: () => IChartApiBase<Time> | null,
    private readonly series: () => ISeriesApi<SeriesType, Time> | null,
  ) {}

  invalidate() {
    this.cacheKey = "";
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
