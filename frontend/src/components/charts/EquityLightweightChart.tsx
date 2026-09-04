import { useEffect, useMemo, useRef, useState } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
} from "lightweight-charts";
import type {
  AreaData,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  LineData,
  LogicalRange,
  MouseEventParams,
  SeriesMarker,
  Time,
} from "lightweight-charts";

import type { ChartEventMarker, EquityChartPoint } from "@/components/charts/chartModels";

export interface EquityComparisonSeries {
  key: string;
  color: string;
  points: Array<{ time: string; value: number }>;
}

interface Props {
  points: EquityChartPoint[];
  comparisons: EquityComparisonSeries[];
  markers: ChartEventMarker[];
  strategyVisible: boolean;
  comparisonVisibility: Record<string, boolean>;
  markerVisibility: Record<ChartEventMarker["category"], boolean>;
  initialValue: number | null;
  locale: string;
}

type AreaApi = ISeriesApi<"Area">;
type LineApi = ISeriesApi<"Line">;

export default function EquityLightweightChart({
  points,
  comparisons,
  markers,
  strategyVisible,
  comparisonVisibility,
  markerVisibility,
  initialValue,
  locale,
}: Props) {
  const isZh = locale === "zh-CN";
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const strategyRef = useRef<AreaApi | null>(null);
  const comparisonRefs = useRef<Record<string, LineApi>>({});
  const markerPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const referenceLineRef = useRef<ReturnType<AreaApi["createPriceLine"]> | null>(null);
  const [visibleWindow, setVisibleWindow] = useState<{ from: string; to: string; count: number } | null>(null);
  const markerById = useMemo(() => new Map(markers.map((marker) => [marker.id, marker])), [markers]);
  const markerByIdRef = useRef(markerById);
  const pointsRef = useRef(points);
  const localeRef = useRef(locale);
  const isZhRef = useRef(isZh);
  markerByIdRef.current = markerById;
  pointsRef.current = points;
  localeRef.current = locale;
  isZhRef.current = isZh;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      autoSize: true,
      height: 380,
      layout: {
        attributionLogo: true,
        background: { type: ColorType.Solid, color: "rgba(8, 15, 24, 0.98)" },
        textColor: "rgba(226, 232, 240, 0.76)",
        fontFamily: '"Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif',
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.08)" },
        horzLines: { color: "rgba(148, 163, 184, 0.14)" },
      },
      rightPriceScale: {
        borderColor: "rgba(71, 85, 105, 0.38)",
        scaleMargins: { top: 0.08, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "rgba(71, 85, 105, 0.38)",
        rightOffset: 1,
        timeVisible: false,
      },
      crosshair: {
        vertLine: { color: "rgba(94, 234, 212, 0.42)", labelBackgroundColor: "#0f766e" },
        horzLine: { color: "rgba(94, 234, 212, 0.32)", labelBackgroundColor: "#0f766e" },
      },
      handleScroll: true,
      handleScale: true,
    });
    const strategy = chart.addSeries(AreaSeries, {
      lineColor: "#0f766e",
      lineWidth: 3,
      topColor: "rgba(15, 118, 110, 0.32)",
      bottomColor: "rgba(15, 118, 110, 0.02)",
      priceLineVisible: false,
      lastValueVisible: true,
    });
    chartRef.current = chart;
    strategyRef.current = strategy;
    comparisonRefs.current = {};
    markerPluginRef.current = createSeriesMarkers(strategy, []);

    const onCrosshairMove = (param: MouseEventParams<Time>) => {
      const tooltip = tooltipRef.current;
      if (!tooltip || !param.point || !param.time) {
        if (tooltip) tooltip.style.display = "none";
        return;
      }
      const hoveredId = typeof param.hoveredObjectId === "string" ? param.hoveredObjectId : null;
      const marker = hoveredId ? markerByIdRef.current.get(hoveredId) : null;
      const strategyData = param.seriesData.get(strategy) as AreaData<Time> | undefined;
      const value = strategyData && "value" in strategyData ? strategyData.value : null;
      const time = String(param.time);
      tooltip.replaceChildren();
      const title = document.createElement("div");
      title.style.fontWeight = "800";
      title.style.marginBottom = "4px";
      title.textContent = marker?.title || time;
      tooltip.appendChild(title);
      if (marker) {
        marker.details.slice(0, 12).forEach((detail) => {
          const row = document.createElement("div");
          row.textContent = detail;
          tooltip.appendChild(row);
        });
      } else if (typeof value === "number") {
        const row = document.createElement("div");
        row.textContent = `${isZhRef.current ? "权益" : "Equity"}: ${formatCurrency(value, localeRef.current)}`;
        tooltip.appendChild(row);
      }
      tooltip.style.display = "block";
      tooltip.style.left = `${Math.min(param.point.x + 14, Math.max(8, container.clientWidth - 300))}px`;
      tooltip.style.top = `${Math.max(8, param.point.y - 72)}px`;
    };
    chart.subscribeCrosshairMove(onCrosshairMove);

    const onRange = (range: LogicalRange | null) => {
      if (!range) return;
      const activePoints = pointsRef.current;
      const fromIndex = Math.max(0, Math.floor(range.from));
      const toIndex = Math.min(activePoints.length - 1, Math.ceil(range.to));
      if (!activePoints[fromIndex] || !activePoints[toIndex]) return;
      setVisibleWindow({
        from: activePoints[fromIndex].time,
        to: activePoints[toIndex].time,
        count: Math.max(0, toIndex - fromIndex + 1),
      });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      chart.remove();
      chartRef.current = null;
      strategyRef.current = null;
      comparisonRefs.current = {};
      markerPluginRef.current = null;
      referenceLineRef.current = null;
    };
  }, []); // Create the imperative chart once; prop updates are handled below.

  useEffect(() => {
    const chart = chartRef.current;
    const strategy = strategyRef.current;
    if (!chart || !strategy) return;
    chart.applyOptions({
      localization: {
        locale,
        priceFormatter: (value: number) => formatCurrency(value, locale),
      },
    });
    strategy.setData(points.map((point) => ({ time: point.time, value: point.value })));
    const activeKeys = new Set(comparisons.map((comparison) => comparison.key));
    Object.entries(comparisonRefs.current).forEach(([key, series]) => {
      if (!activeKeys.has(key)) {
        chart.removeSeries(series);
        delete comparisonRefs.current[key];
      }
    });
    comparisons.forEach((comparison) => {
      const series = comparisonRefs.current[comparison.key] || chart.addSeries(LineSeries, {
        color: comparison.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      comparisonRefs.current[comparison.key] = series;
      series.applyOptions({ color: comparison.color });
      series.setData(
        comparison.points.map((point) => ({ time: point.time, value: point.value } as LineData<Time>)),
      );
    });
    if (referenceLineRef.current) {
      strategy.removePriceLine(referenceLineRef.current);
      referenceLineRef.current = null;
    }
    if (typeof initialValue === "number" && Number.isFinite(initialValue)) {
      referenceLineRef.current = strategy.createPriceLine({
        price: initialValue,
        color: "rgba(148, 163, 184, 0.52)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: isZh ? "起始资金" : "Starting Capital",
      });
    }
    chart.timeScale().fitContent();
    if (points.length > 0) {
      setVisibleWindow({ from: points[0].time, to: points[points.length - 1].time, count: points.length });
    }
  }, [comparisons, initialValue, isZh, locale, points]);

  useEffect(() => {
    strategyRef.current?.applyOptions({ visible: strategyVisible });
    Object.entries(comparisonRefs.current).forEach(([key, series]) => {
      series.applyOptions({ visible: comparisonVisibility[key] ?? true });
    });
  }, [comparisonVisibility, strategyVisible]);

  useEffect(() => {
    const visibleMarkers = markers.filter((marker) => markerVisibility[marker.category]);
    const mapped: SeriesMarker<Time>[] = visibleMarkers.map((marker) => ({
      id: marker.id,
      time: marker.time,
      price: marker.price,
      position: marker.position,
      shape: marker.shape,
      color: marker.color,
      text: marker.text,
      size: 1,
    }));
    markerPluginRef.current?.setMarkers(mapped);
  }, [markerVisibility, markers]);

  const changeZoom = (factor: number) => {
    const timeScale = chartRef.current?.timeScale();
    const range = timeScale?.getVisibleLogicalRange();
    if (!timeScale || !range) return;
    const center = (range.from + range.to) / 2;
    const half = Math.max(2, ((range.to - range.from) * factor) / 2);
    timeScale.setVisibleLogicalRange({ from: center - half, to: center + half });
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
        <span style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13 }}>
          {visibleWindow
            ? `${isZh ? "当前窗口" : "Current Window"}: ${visibleWindow.from} → ${visibleWindow.to} · ${visibleWindow.count}/${points.length}`
            : isZh ? "正在初始化图表" : "Initializing chart"}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" style={buttonStyle} onClick={() => changeZoom(1.3)}>{isZh ? "缩小" : "Zoom Out"}</button>
          <button type="button" style={buttonStyle} onClick={() => chartRef.current?.timeScale().fitContent()}>{isZh ? "还原" : "Reset"}</button>
          <button type="button" style={buttonStyle} onClick={() => changeZoom(0.75)}>{isZh ? "放大" : "Zoom In"}</button>
        </div>
      </div>
      <div
        role="img"
        aria-label={isZh ? "回测权益曲线交互图" : "Interactive backtest equity chart"}
        style={{
          position: "relative",
          minHeight: 380,
          overflow: "hidden",
          background: "rgba(8, 15, 24, 0.98)",
        }}
      >
        <div ref={containerRef} style={{ width: "100%", height: 380 }} />
        <div ref={tooltipRef} style={tooltipStyle} />
      </div>
    </div>
  );
}

function formatCurrency(value: number, locale: string) {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

const buttonStyle = {
  border: "1px solid rgba(71, 85, 105, 0.4)",
  borderRadius: 10,
  background: "rgba(15, 23, 42, 0.76)",
  color: "#e2e8f0",
  padding: "7px 11px",
  cursor: "pointer",
  fontSize: 12,
  fontWeight: 700,
} as const;

const tooltipStyle = {
  display: "none",
  position: "absolute",
  zIndex: 5,
  width: 280,
  maxHeight: 220,
  overflowY: "auto",
  pointerEvents: "none",
  padding: "10px 12px",
  borderRadius: 12,
  border: "1px solid rgba(94, 234, 212, 0.24)",
  background: "rgba(15, 23, 42, 0.96)",
  color: "#f8fafc",
  boxShadow: "0 18px 40px rgba(0, 0, 0, 0.28)",
  fontSize: 12,
  lineHeight: 1.5,
} as const;
