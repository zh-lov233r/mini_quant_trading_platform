import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
} from "lightweight-charts";
import type {
  CandlestickData,
  HistogramData,
  IChartApi,
  ISeriesApi,
  MouseEventParams,
  Time,
} from "lightweight-charts";

import {
  buildLifecycleLeaderMarkers,
  normalizeGapOverlays,
  normalizeCandleBars,
  normalizeRegimeOverlays,
  normalizeZoneOverlays,
} from "@/components/charts/chartModels";
import type {
  ChartGapOverlay,
  ChartOverlayMarker,
  ChartRegimeOverlay,
  ChartZoneOverlay,
} from "@/components/charts/chartModels";
import { LifecycleOverlayPrimitive } from "@/components/charts/overlayPrimitive";
import { SelectControl } from "@/components/workspace/SelectControl";
import type { CandleBarOut } from "@/types/quote";

interface Props {
  bars: CandleBarOut[];
  markers?: ChartOverlayMarker[];
  gaps?: ChartGapOverlay[];
  zones?: ChartZoneOverlay[];
  regimes?: ChartRegimeOverlay[];
  requireRegimeCoverage?: boolean;
  regimeCoverageStart?: string | null;
  regimeCoverageEnd?: string | null;
  locale: string;
  showVolume?: boolean;
  height?: number;
  ariaLabel?: string;
  framed?: boolean;
}

type CandleApi = ISeriesApi<"Candlestick">;
type VolumeApi = ISeriesApi<"Histogram">;
const EMPTY_MARKERS: ChartOverlayMarker[] = [];
const EMPTY_GAPS: ChartGapOverlay[] = [];
const EMPTY_ZONES: ChartZoneOverlay[] = [];
const EMPTY_REGIMES: ChartRegimeOverlay[] = [];

export default function CandlestickLightweightChart({
  bars,
  markers = EMPTY_MARKERS,
  gaps = EMPTY_GAPS,
  zones = EMPTY_ZONES,
  regimes = EMPTY_REGIMES,
  requireRegimeCoverage = false,
  regimeCoverageStart = null,
  regimeCoverageEnd = null,
  locale,
  showVolume = true,
  height = 384,
  ariaLabel,
  framed = true,
}: Props) {
  const isZh = locale === "zh-CN";
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<CandleApi | null>(null);
  const volumeRef = useRef<VolumeApi | null>(null);
  const primitiveRef = useRef<LifecycleOverlayPrimitive | null>(null);
  const [pinnedMarker, setPinnedMarker] = useState<ChartOverlayMarker | null>(null);
  const [pinnedRegime, setPinnedRegime] = useState<ChartRegimeOverlay | null>(null);
  const [activeZoneKey, setActiveZoneKey] = useState<string | null>(null);
  const pinnedZoneRef = useRef<string | null>(null);
  const zoneByKey = useMemo(() => new Map(zones.map((zone) => [zone.zoneKey || zone.key, zone])), [zones]);
  const zoneByKeyRef = useRef(zoneByKey);
  zoneByKeyRef.current = zoneByKey;
  const activeZone = activeZoneKey ? zoneByKey.get(activeZoneKey) : null;
  const normalizedBars = useMemo(() => normalizeCandleBars(bars), [bars]);
  const normalizedGaps = useMemo(
    () => normalizeGapOverlays(gaps, new Set(normalizedBars.map((bar) => bar.time))),
    [gaps, normalizedBars],
  );
  const availableTimes = useMemo(
    () => new Set(normalizedBars.map((bar) => bar.time)),
    [normalizedBars],
  );
  const normalizedZones = useMemo(
    () => normalizeZoneOverlays(zones, availableTimes),
    [availableTimes, zones],
  );
  const normalizedRegimeResult = useMemo(
    () => normalizeRegimeOverlays(
      regimes,
      normalizedBars.map((bar) => bar.time),
      requireRegimeCoverage,
      { startDate: regimeCoverageStart, endDate: regimeCoverageEnd },
    ),
    [normalizedBars, regimes, regimeCoverageEnd, regimeCoverageStart, requireRegimeCoverage],
  );
  const normalizedRegimes = normalizedRegimeResult.intervals;
  const regimeByDate = useMemo(() => {
    const output = new Map<string, ChartRegimeOverlay>();
    normalizedBars.forEach((bar) => {
      const regime = normalizedRegimes.find(
        (item) => bar.time >= item.startDate && bar.time <= item.endDate,
      );
      if (regime) output.set(bar.time, regime);
    });
    return output;
  }, [normalizedBars, normalizedRegimes]);
  // Lifecycle events are rendered only by the gutter primitive. Keeping the
  // built-in series-marker plugin out of this chart makes it impossible for a
  // new marker tone to fall back to an inline dot over a candle.
  const leaderMarkers = useMemo(() => buildLifecycleLeaderMarkers(markers), [markers]);
  const hasLeaderMarkers = leaderMarkers.length > 0;
  const markerById = useMemo(
    () => new Map(leaderMarkers.map((marker) => [marker.key, marker])),
    [leaderMarkers],
  );
  const markerByIdRef = useRef(markerById);
  const regimeByDateRef = useRef(regimeByDate);
  const localeRef = useRef(locale);
  markerByIdRef.current = markerById;
  regimeByDateRef.current = regimeByDate;
  localeRef.current = locale;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      autoSize: true,
      height,
      layout: {
        attributionLogo: true,
        background: { type: ColorType.Solid, color: "rgba(3, 7, 18, 0.96)" },
        textColor: "rgba(226, 232, 240, 0.74)",
        fontFamily: '"Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif',
        panes: {
          enableResize: showVolume,
          separatorColor: "rgba(71, 85, 105, 0.28)",
          separatorHoverColor: "rgba(56, 189, 248, 0.38)",
        },
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.08)" },
        horzLines: { color: "rgba(148, 163, 184, 0.12)" },
      },
      rightPriceScale: {
        borderColor: "rgba(71, 85, 105, 0.36)",
        scaleMargins: hasLeaderMarkers
          ? { top: 0.24, bottom: 0.22 }
          : { top: 0.18, bottom: showVolume ? 0.08 : 0.12 },
      },
      timeScale: {
        borderColor: "rgba(71, 85, 105, 0.36)",
        rightOffset: 2,
        barSpacing: 9,
        minBarSpacing: 2,
      },
      crosshair: {
        vertLine: { color: "rgba(56, 189, 248, 0.38)", labelBackgroundColor: "#0369a1" },
        horzLine: { color: "rgba(56, 189, 248, 0.28)", labelBackgroundColor: "#0369a1" },
      },
      handleScroll: true,
      handleScale: true,
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "rgba(52, 211, 153, 0.32)",
      downColor: "rgba(251, 113, 133, 0.32)",
      borderUpColor: "#34d399",
      borderDownColor: "#fb7185",
      wickUpColor: "#34d399",
      wickDownColor: "#fb7185",
      priceLineVisible: false,
    }, 0);
    let volume: VolumeApi | null = null;
    if (showVolume) {
      volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      }, 1);
      chart.panes()[1]?.setHeight(82);
    }
    chartRef.current = chart;
    candleRef.current = candle;
    volumeRef.current = volume;

    const onCrosshairMove = (param: MouseEventParams<Time>) => {
      const zoneKey = typeof param.hoveredObjectId === "string" && param.hoveredObjectId.startsWith("zone:")
        ? param.hoveredObjectId.slice(5) : null;
      if (!pinnedZoneRef.current) setActiveZoneKey(zoneKey);
      const tooltip = tooltipRef.current;
      if (!tooltip || !param.point || !param.time) {
        if (tooltip) tooltip.style.display = "none";
        return;
      }
      const candleData = param.seriesData.get(candle) as CandlestickData<Time> | undefined;
      if (!candleData || !("open" in candleData)) {
        tooltip.style.display = "none";
        return;
      }
      const marker = typeof param.hoveredObjectId === "string"
        ? markerByIdRef.current.get(param.hoveredObjectId)
        : null;
      const regime = regimeByDateRef.current.get(String(param.time));
      const activeLocale = localeRef.current;
      tooltip.replaceChildren();
      const title = document.createElement("div");
      title.style.fontWeight = "800";
      title.style.marginBottom = "4px";
      title.textContent = (zoneKey ? zoneByKeyRef.current.get(zoneKey)?.description : null)
        || marker?.description || regime?.description || String(param.time);
      tooltip.appendChild(title);
      const row = document.createElement("div");
      row.textContent = `O ${formatPrice(candleData.open, activeLocale)}  H ${formatPrice(candleData.high, activeLocale)}  L ${formatPrice(candleData.low, activeLocale)}  C ${formatPrice(candleData.close, activeLocale)}`;
      tooltip.appendChild(row);
      if (regime && marker) {
        const regimeRow = document.createElement("div");
        regimeRow.style.marginTop = "4px";
        regimeRow.style.color = "rgba(203, 213, 225, 0.9)";
        regimeRow.textContent = regime.description;
        tooltip.appendChild(regimeRow);
      }
      tooltip.style.display = "block";
      tooltip.style.left = `${Math.min(param.point.x + 14, Math.max(8, container.clientWidth - 340))}px`;
      tooltip.style.top = `${Math.max(8, param.point.y - 68)}px`;
    };
    chart.subscribeCrosshairMove(onCrosshairMove);
    const onClick = (param: MouseEventParams<Time>) => {
      const key = typeof param.hoveredObjectId === "string" && param.hoveredObjectId.startsWith("zone:")
        ? param.hoveredObjectId.slice(5) : null;
      pinnedZoneRef.current = key;
      setActiveZoneKey(key);
      const marker = typeof param.hoveredObjectId === "string"
        ? markerByIdRef.current.get(param.hoveredObjectId) || null
        : null;
      setPinnedMarker(marker);
      setPinnedRegime(marker || !param.time ? null : regimeByDateRef.current.get(String(param.time)) || null);
    };
    chart.subscribeClick(onClick);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshairMove);
      chart.unsubscribeClick(onClick);
      // Disconnect Lightweight Charts' ResizeObserver before its canvas
      // bindings are disposed. A queued observer callback can otherwise race
      // with remove() when lifecycle rows are switched quickly.
      chart.applyOptions({ autoSize: false });
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      primitiveRef.current = null;
    };
  }, [hasLeaderMarkers, height, showVolume]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        pinnedZoneRef.current = null;
        setActiveZoneKey(null);
        setPinnedMarker(null);
        setPinnedRegime(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle) return;
    chart.applyOptions({
      localization: {
        locale,
        priceFormatter: (value: number) => formatPrice(value, locale),
      },
    });
    candle.setData(normalizedBars.map((bar) => ({
      time: bar.time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      color: bar.color,
      borderColor: bar.borderColor,
      wickColor: bar.wickColor,
    })));
    volumeRef.current?.setData(normalizedBars.map((bar) => ({
      time: bar.time,
      value: bar.volume,
      color: bar.close === bar.open
        ? "rgba(251, 191, 36, 0.82)"
        : bar.close > bar.open ? "rgba(52, 211, 153, 0.78)" : "rgba(251, 113, 133, 0.78)",
    } as HistogramData<Time>)));
    chart.timeScale().fitContent();
  }, [locale, normalizedBars]);

  useEffect(() => {
    const candle = candleRef.current;
    if (!candle) return;
    if (primitiveRef.current) candle.detachPrimitive(primitiveRef.current);
    const primitive = new LifecycleOverlayPrimitive(
      normalizedGaps,
      normalizedZones,
      normalizedRegimes,
      normalizedBars,
      leaderMarkers,
    );
    primitiveRef.current = primitive;
    primitive.selectZone(pinnedZoneRef.current);
    candle.attachPrimitive(primitive);
    return () => {
      if (candleRef.current !== candle || chartRef.current == null) {
        if (primitiveRef.current === primitive) primitiveRef.current = null;
        return;
      }
      try {
        candle.detachPrimitive(primitive);
      } catch {
        // The chart may already be removed during the parent cleanup.
      }
      if (primitiveRef.current === primitive) primitiveRef.current = null;
    };
  }, [leaderMarkers, normalizedBars, normalizedGaps, normalizedRegimes, normalizedZones]);

  useEffect(() => {
    primitiveRef.current?.selectZone(activeZoneKey);
  }, [activeZoneKey]);

  return (
    <div
      role="group"
      tabIndex={0}
      aria-label={ariaLabel || (isZh ? "交互式蜡烛图" : "Interactive candlestick chart")}
      style={{
        position: "relative",
        width: "100%",
        minHeight: height,
        overflow: "hidden",
        borderRadius: framed ? 18 : 0,
        border: framed ? "1px solid rgba(71, 85, 105, 0.24)" : "none",
        background: "rgba(3, 7, 18, 0.96)",
      }}
    >
      <div ref={containerRef} style={{ width: "100%", height }} />
      <div ref={tooltipRef} style={tooltipStyle} />
      {zoneByKey.size > 0 ? (
        <div style={{ padding: "10px 12px", fontSize: 12 }}>
          <label>
            {isZh ? "区域明细（点击固定，Esc 清除）：" : "Zone details (click to pin, Esc to clear): "}
            <SelectControl style={{ maxWidth: "100%" }} density="compact"
              placeholder={isZh ? "选择区域" : "Select zone"}
              aria-label={isZh ? "区域明细（点击固定，Esc 清除）" : "Zone details (click to pin, Esc to clear)"}
              value={activeZoneKey || ""} onValueChange={(value) => {
              const key = value || null;
              pinnedZoneRef.current = key;
              setActiveZoneKey(key);
            }} options={[
              { value: "", label: isZh ? "选择区域" : "Select zone" },
              ...[...zoneByKey].map(([key, zone]) => ({ value: key, label: zone.description })),
            ]} />
          </label>
          {activeZone ? <div aria-live="polite" style={{ marginTop: 8, overflowWrap: "anywhere" }}>
            {(activeZone.details || [activeZone.description]).map((detail, index) => <div key={index}>{detail}</div>)}
          </div> : null}
        </div>
      ) : null}
      {normalizedRegimeResult.error ? (
        <div role="alert" style={regimeErrorStyle}>
          {isZh ? "市场状态区间数据不完整，已停止绘制状态背景：" : "Regime interval data is invalid; background rendering was stopped: "}
          {normalizedRegimeResult.error}
        </div>
      ) : null}
      {pinnedMarker ? (
        <div role="dialog" aria-label={pinnedMarker.label} style={detailCardStyle}>
          <button
            type="button"
            onClick={() => setPinnedMarker(null)}
            aria-label={isZh ? "关闭详情" : "Close details"}
            style={detailCloseStyle}
          >
            ×
          </button>
          <div style={{ fontWeight: 800, paddingRight: 26 }}>{pinnedMarker.label}</div>
          <div style={{ color: "rgba(148, 163, 184, 0.9)", margin: "3px 0 7px" }}>
            {pinnedMarker.date}{pinnedMarker.price == null ? "" : ` · ${formatPrice(pinnedMarker.price, locale)}`}
          </div>
          {(pinnedMarker.details || [pinnedMarker.description]).map((detail, index) => (
            <div key={`${pinnedMarker.key}:${index}`} style={{ marginTop: index ? 5 : 0 }}>
              {detail}
            </div>
          ))}
        </div>
      ) : pinnedRegime ? (
        <div role="dialog" aria-label={pinnedRegime.label} style={detailCardStyle}>
          <button
            type="button"
            onClick={() => setPinnedRegime(null)}
            aria-label={isZh ? "关闭详情" : "Close details"}
            style={detailCloseStyle}
          >
            ×
          </button>
          <div style={{ fontWeight: 800, paddingRight: 26 }}>{pinnedRegime.label}</div>
          <div style={{ color: "rgba(148, 163, 184, 0.9)", margin: "3px 0 7px" }}>
            {pinnedRegime.startDate} - {pinnedRegime.endDate} · {pinnedRegime.sessionCount} {isZh ? "个交易日" : "sessions"}
          </div>
          <div>{pinnedRegime.description}</div>
        </div>
      ) : null}
    </div>
  );
}

function formatPrice(value: number, locale: string) {
  return value.toLocaleString(locale, {
    minimumFractionDigits: value >= 100 ? 0 : 2,
    maximumFractionDigits: 2,
  });
}

const tooltipStyle = {
  display: "none",
  position: "absolute",
  zIndex: 5,
  width: 320,
  maxHeight: 180,
  overflowY: "auto",
  pointerEvents: "none",
  padding: "10px 12px",
  borderRadius: 12,
  border: "1px solid rgba(56, 189, 248, 0.24)",
  background: "rgba(15, 23, 42, 0.96)",
  color: "#f8fafc",
  boxShadow: "0 18px 40px rgba(0, 0, 0, 0.28)",
  fontSize: 12,
  lineHeight: 1.5,
} as const;

const detailCardStyle = {
  position: "absolute",
  right: 12,
  bottom: 12,
  zIndex: 6,
  width: 340,
  maxWidth: "calc(100% - 24px)",
  maxHeight: 210,
  overflowY: "auto",
  padding: "12px 14px",
  borderRadius: 12,
  border: "1px solid rgba(56, 189, 248, 0.34)",
  background: "rgba(15, 23, 42, 0.98)",
  color: "#f8fafc",
  boxShadow: "0 18px 44px rgba(0, 0, 0, 0.42)",
  fontSize: 12,
  lineHeight: 1.5,
} as const;

const detailCloseStyle = {
  position: "absolute",
  top: 6,
  right: 8,
  width: 26,
  height: 26,
  border: 0,
  borderRadius: 8,
  background: "transparent",
  color: "#cbd5e1",
  cursor: "pointer",
  fontSize: 19,
} as const;

const regimeErrorStyle = {
  position: "absolute",
  top: 10,
  left: 10,
  right: 70,
  zIndex: 7,
  padding: "8px 10px",
  borderRadius: 10,
  border: "1px solid rgba(248, 113, 113, 0.42)",
  background: "rgba(69, 10, 10, 0.94)",
  color: "#fecaca",
  fontSize: 12,
} as const;
