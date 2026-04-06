import { useEffect, useRef, useState } from "react";
import type { FormEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";

import { getCandleSeries } from "@/api/quotes";
import type { CandleBarOut, CandleSeriesOut } from "@/types/quote";
import { useI18n } from "@/i18n/provider";

interface CandleQueryForm {
  symbol: string;
  start_date: string;
  end_date: string;
}

interface PanelLayout {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface ActiveGesture {
  mode: "drag" | "resize";
  originX: number;
  originY: number;
  startLayout: PanelLayout;
}

const DEFAULT_SYMBOL = "AAPL";
const PANEL_Z_INDEX = 1300;
const BUTTON_Z_INDEX = 1310;
const PANEL_DEFAULT_WIDTH = 460;
const PANEL_DEFAULT_HEIGHT = 760;
const PANEL_MIN_WIDTH = 360;
const PANEL_MIN_HEIGHT = 420;
const VIEWPORT_MARGIN = 16;

export default function StockCandleWidget() {
  const { messages, locale } = useI18n();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<CandleSeriesOut | null>(null);
  const [form, setForm] = useState<CandleQueryForm>({
    symbol: DEFAULT_SYMBOL,
    start_date: "",
    end_date: "",
  });
  const autoRequestedRef = useRef(false);
  const initializedRef = useRef(false);
  const [panelLayout, setPanelLayout] = useState<PanelLayout | null>(null);
  const [gesture, setGesture] = useState<ActiveGesture | null>(null);

  useEffect(() => {
    if (initializedRef.current) {
      return;
    }
    initializedRef.current = true;

    const endDate = new Date();
    const startDate = new Date(endDate);
    startDate.setDate(startDate.getDate() - 90);

    setForm({
      symbol: DEFAULT_SYMBOL,
      start_date: formatDateInput(startDate),
      end_date: formatDateInput(endDate),
    });
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (!open || panelLayout) {
      return;
    }
    setPanelLayout(getDefaultPanelLayout());
  }, [open, panelLayout]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    function handleViewportResize() {
      setPanelLayout((current) => {
        if (!current) {
          return current;
        }
        return clampPanelLayout(current, window.innerWidth, window.innerHeight);
      });
    }

    window.addEventListener("resize", handleViewportResize);
    return () => {
      window.removeEventListener("resize", handleViewportResize);
    };
  }, []);

  useEffect(() => {
    if (!gesture) {
      return;
    }

    const previousUserSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    document.body.style.userSelect = "none";
    document.body.style.cursor = gesture.mode === "drag" ? "grabbing" : "nwse-resize";

    function handlePointerMove(event: PointerEvent) {
      event.preventDefault();
      const deltaX = event.clientX - gesture.originX;
      const deltaY = event.clientY - gesture.originY;

      if (gesture.mode === "drag") {
        setPanelLayout(
          clampPanelLayout({
            ...gesture.startLayout,
            x: gesture.startLayout.x + deltaX,
            y: gesture.startLayout.y + deltaY,
          })
        );
        return;
      }

      setPanelLayout(
        clampPanelLayout({
          ...gesture.startLayout,
          width: gesture.startLayout.width + deltaX,
          height: gesture.startLayout.height + deltaY,
        })
      );
    }

    function handlePointerUp() {
      setGesture(null);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = previousCursor;
    };
  }, [gesture]);

  useEffect(() => {
    if (!open || autoRequestedRef.current) {
      return;
    }
    if (!form.symbol.trim() || !form.start_date || !form.end_date) {
      return;
    }
    if (form.start_date > form.end_date) {
      autoRequestedRef.current = true;
      setError(messages.marketViewer.invalidRange);
      setSeries(null);
      return;
    }

    autoRequestedRef.current = true;
    let active = true;
    const normalizedSymbol = form.symbol.trim().toUpperCase();

    setLoading(true);
    setError(null);

    void getCandleSeries({
      symbol: normalizedSymbol,
      start_date: form.start_date,
      end_date: form.end_date,
    })
      .then((nextSeries) => {
        if (!active) {
          return;
        }
        setForm((current) => ({
          ...current,
          symbol: normalizedSymbol,
        }));
        setSeries(nextSeries);
      })
      .catch((err) => {
        if (!active) {
          return;
        }
        setSeries(null);
        setError(err instanceof Error ? err.message : messages.marketViewer.loadFailed);
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [
    form.end_date,
    form.start_date,
    form.symbol,
    messages.marketViewer.invalidRange,
    messages.marketViewer.loadFailed,
    open,
  ]);

  async function loadSeries(nextForm: CandleQueryForm) {
    const normalizedSymbol = nextForm.symbol.trim().toUpperCase();
    if (!normalizedSymbol) {
      setError(messages.marketViewer.loadFailed);
      setSeries(null);
      return;
    }
    if (nextForm.start_date > nextForm.end_date) {
      setError(messages.marketViewer.invalidRange);
      setSeries(null);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const nextSeries = await getCandleSeries({
        symbol: normalizedSymbol,
        start_date: nextForm.start_date,
        end_date: nextForm.end_date,
      });
      setForm((current) => ({
        ...current,
        symbol: normalizedSymbol,
      }));
      setSeries(nextSeries);
    } catch (err) {
      setSeries(null);
      setError(err instanceof Error ? err.message : messages.marketViewer.loadFailed);
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await loadSeries(form);
  }

  function handleDragStart(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !panelLayout) {
      return;
    }
    event.preventDefault();
    setGesture({
      mode: "drag",
      originX: event.clientX,
      originY: event.clientY,
      startLayout: panelLayout,
    });
  }

  function handleResizeStart(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !panelLayout) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setGesture({
      mode: "resize",
      originX: event.clientX,
      originY: event.clientY,
      startLayout: panelLayout,
    });
  }

  const bars = series?.bars ?? [];
  const firstBar = bars[0] ?? null;
  const latestBar = bars[bars.length - 1] ?? null;
  const lowestLow = bars.length ? Math.min(...bars.map((bar) => bar.low)) : null;
  const highestHigh = bars.length ? Math.max(...bars.map((bar) => bar.high)) : null;
  const intervalReturn =
    firstBar && latestBar && firstBar.close > 0
      ? (latestBar.close - firstBar.close) / firstBar.close
      : null;
  const isDragging = gesture?.mode === "drag";
  const isResizing = gesture?.mode === "resize";

  return (
    <>
      <button
        type="button"
        aria-label={messages.marketViewer.title}
        title={messages.marketViewer.title}
        onClick={() => setOpen((current) => !current)}
        style={{
          position: "fixed",
          top: 20,
          right: 24,
          zIndex: BUTTON_Z_INDEX,
          display: "inline-flex",
          alignItems: "center",
          gap: 10,
          padding: "11px 15px",
          borderRadius: 999,
          border: open ? "1px solid rgba(94, 234, 212, 0.46)" : "1px solid rgba(148, 163, 184, 0.26)",
          background: open ? "rgba(8, 47, 73, 0.94)" : "rgba(8, 15, 24, 0.92)",
          color: "#f8fafc",
          boxShadow: "0 18px 42px rgba(2, 6, 23, 0.34)",
          backdropFilter: "blur(10px)",
          cursor: "pointer",
          fontSize: 13,
          fontWeight: 700,
          letterSpacing: "0.01em",
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6 19V8" />
          <path d="M18 16V5" />
          <path d="M12 21V3" />
          <path d="M4.5 8h3" />
          <path d="M10.5 11h3" />
          <path d="M16.5 6h3" />
          <path d="M10.5 16h3" />
          <path d="M16.5 13h3" />
        </svg>
        <span>{messages.marketViewer.trigger}</span>
      </button>

      {open && panelLayout ? (
        <section
          role="dialog"
          aria-modal="false"
          aria-label={messages.marketViewer.title}
          style={{
            position: "fixed",
            top: panelLayout.y,
            left: panelLayout.x,
            zIndex: PANEL_Z_INDEX,
            width: panelLayout.width,
            height: panelLayout.height,
            overflowY: "auto",
            overflowX: "hidden",
            maxWidth: `calc(100vw - ${VIEWPORT_MARGIN * 2}px)`,
            maxHeight: `calc(100vh - ${VIEWPORT_MARGIN * 2}px)`,
            borderRadius: 24,
            border: "1px solid rgba(71, 85, 105, 0.38)",
            background:
              "linear-gradient(180deg, rgba(4, 10, 18, 0.98) 0%, rgba(7, 18, 32, 0.96) 100%)",
            boxShadow: "0 30px 80px rgba(2, 6, 23, 0.48)",
            backdropFilter: "blur(14px)",
            color: "#e2e8f0",
          }}
        >
          <div style={{ padding: "18px 18px 30px" }}>
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                gap: 12,
                marginBottom: 16,
              }}
            >
              <div
                onPointerDown={handleDragStart}
                style={{
                  flex: 1,
                  minWidth: 0,
                  cursor: isDragging ? "grabbing" : "grab",
                  userSelect: "none",
                  touchAction: "none",
                }}
              >
                <div
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    marginBottom: 10,
                  }}
                >
                  <span style={dragGripStyle} />
                  <span style={dragGripStyle} />
                  <span style={dragGripStyle} />
                </div>
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 800,
                    color: "#f8fafc",
                    marginBottom: 6,
                    fontFamily:
                      "\"Iowan Old Style\", \"Palatino Linotype\", \"Book Antiqua\", Georgia, serif",
                  }}
                >
                  {messages.marketViewer.title}
                </div>
                <div
                  style={{
                    fontSize: 13,
                    lineHeight: 1.55,
                    color: "rgba(226, 232, 240, 0.76)",
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  {messages.marketViewer.subtitle}
                </div>
              </div>

              <button
                type="button"
                aria-label={messages.marketViewer.closeWindow}
                title={messages.marketViewer.closeWindow}
                onClick={() => setOpen(false)}
                onPointerDown={(event) => event.stopPropagation()}
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 999,
                  border: "1px solid rgba(71, 85, 105, 0.42)",
                  background: "rgba(15, 23, 42, 0.85)",
                  color: "#cbd5e1",
                  cursor: "pointer",
                  fontSize: 18,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </div>

            <form onSubmit={handleSubmit} style={{ marginBottom: 16 }}>
              <div
                style={{
                  display: "grid",
                  gap: 12,
                  marginBottom: 12,
                }}
              >
                <FieldLabel label={messages.marketViewer.symbol}>
                  <input
                    type="text"
                    value={form.symbol}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={messages.marketViewer.symbolPlaceholder}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        symbol: event.target.value.toUpperCase(),
                      }))
                    }
                    style={inputStyle}
                  />
                </FieldLabel>

                <div
                  style={{
                    display: "grid",
                    gap: 12,
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  }}
                >
                  <FieldLabel label={messages.marketViewer.startDate}>
                    <input
                      type="date"
                      value={form.start_date}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          start_date: event.target.value,
                        }))
                      }
                      style={inputStyle}
                    />
                  </FieldLabel>

                  <FieldLabel label={messages.marketViewer.endDate}>
                    <input
                      type="date"
                      value={form.end_date}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          end_date: event.target.value,
                        }))
                      }
                      style={inputStyle}
                    />
                  </FieldLabel>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                style={{
                  width: "100%",
                  padding: "12px 14px",
                  borderRadius: 14,
                  border: "1px solid rgba(45, 212, 191, 0.28)",
                  background: "linear-gradient(135deg, rgba(13, 148, 136, 0.94), rgba(14, 116, 144, 0.94))",
                  color: "#f8fafc",
                  cursor: loading ? "progress" : "pointer",
                  fontSize: 14,
                  fontWeight: 800,
                }}
              >
                {loading ? messages.marketViewer.loading : messages.marketViewer.load}
              </button>
            </form>

            {error ? (
              <div
                style={{
                  marginBottom: 14,
                  padding: "12px 14px",
                  borderRadius: 14,
                  border: "1px solid rgba(251, 113, 133, 0.28)",
                  background: "rgba(69, 10, 30, 0.5)",
                  color: "#fecdd3",
                  fontSize: 13,
                  lineHeight: 1.55,
                }}
              >
                {error}
              </div>
            ) : null}

            <div
              style={{
                marginBottom: 14,
                padding: "10px 12px",
                borderRadius: 14,
                border: "1px solid rgba(71, 85, 105, 0.3)",
                background: "rgba(15, 23, 42, 0.46)",
                color: "rgba(226, 232, 240, 0.78)",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              {messages.marketViewer.initialHint}
            </div>

            {series ? (
              <>
                <div
                  style={{
                    display: "grid",
                    gap: 10,
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                    marginBottom: 16,
                  }}
                >
                  <StatCard
                    label={messages.marketViewer.latestClose}
                    value={latestBar ? formatPrice(latestBar.close, locale) : "-"}
                  />
                  <StatCard
                    label={messages.marketViewer.intervalReturn}
                    value={formatPercent(intervalReturn, locale)}
                    tone={intervalReturn}
                  />
                  <StatCard
                    label={messages.marketViewer.priceRange}
                    value={
                      lowestLow !== null && highestHigh !== null
                        ? `${formatPrice(lowestLow, locale)} - ${formatPrice(highestHigh, locale)}`
                        : "-"
                    }
                  />
                  <StatCard
                    label={messages.marketViewer.barCount}
                    value={String(series.bar_count)}
                  />
                </div>

                {bars.length ? (
                  <>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 12,
                        marginBottom: 10,
                        color: "rgba(226, 232, 240, 0.82)",
                        fontSize: 12,
                        fontWeight: 700,
                        letterSpacing: "0.02em",
                        textTransform: "uppercase",
                      }}
                    >
                      <span>
                        {series.symbol} · {messages.marketViewer.dailyHint}
                      </span>
                      <span>
                        {series.start_date} - {series.end_date}
                      </span>
                    </div>

                    <CandleChart bars={bars} />

                    {latestBar ? (
                      <div
                        style={{
                          marginTop: 12,
                          padding: "12px 14px",
                          borderRadius: 16,
                          border: "1px solid rgba(71, 85, 105, 0.28)",
                          background: "rgba(15, 23, 42, 0.44)",
                          color: "#cbd5e1",
                          fontSize: 13,
                          lineHeight: 1.6,
                        }}
                      >
                        <div style={{ marginBottom: 6, fontWeight: 700, color: "#f8fafc" }}>
                          {messages.marketViewer.latestBar}: {latestBar.trade_date}
                        </div>
                        <div>
                          {messages.marketViewer.openPrice} {formatPrice(latestBar.open, locale)} ·{" "}
                          {messages.marketViewer.highPrice} {formatPrice(latestBar.high, locale)} ·{" "}
                          {messages.marketViewer.lowPrice} {formatPrice(latestBar.low, locale)} ·{" "}
                          {messages.marketViewer.closePrice} {formatPrice(latestBar.close, locale)} ·{" "}
                          {messages.marketViewer.volume} {formatVolume(latestBar.volume, locale)}
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <EmptyState
                    title={messages.marketViewer.empty}
                    description={messages.marketViewer.emptyHint}
                  />
                )}
              </>
            ) : loading ? null : (
              <EmptyState
                title={messages.marketViewer.title}
                description={messages.marketViewer.initialHint}
              />
            )}
          </div>
          <div
            onPointerDown={handleResizeStart}
            style={{
              position: "absolute",
              right: 10,
              bottom: 10,
              width: 24,
              height: 24,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 10,
              background: isResizing ? "rgba(14, 116, 144, 0.32)" : "rgba(15, 23, 42, 0.42)",
              border: "1px solid rgba(71, 85, 105, 0.32)",
              cursor: "nwse-resize",
              touchAction: "none",
            }}
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="rgba(226, 232, 240, 0.82)"
              strokeWidth="1.8"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M8 16h8" />
              <path d="M12 12h4" />
              <path d="M16 8h0" />
            </svg>
          </div>
        </section>
      ) : null}
    </>
  );
}

function CandleChart({ bars }: { bars: CandleBarOut[] }) {
  const svgWidth = Math.max(620, bars.length * 11 + 90);
  const svgHeight = 300;
  const padding = { top: 18, right: 56, bottom: 34, left: 14 };
  const lowestLow = Math.min(...bars.map((bar) => bar.low));
  const highestHigh = Math.max(...bars.map((bar) => bar.high));
  const priceSpan = highestHigh - lowestLow || Math.max(highestHigh, 1);
  const chartLow = lowestLow - priceSpan * 0.05;
  const chartHigh = highestHigh + priceSpan * 0.05;
  const chartSpan = chartHigh - chartLow || 1;
  const plotWidth = svgWidth - padding.left - padding.right;
  const plotHeight = svgHeight - padding.top - padding.bottom;
  const candleStep = plotWidth / Math.max(bars.length, 1);
  const candleBodyWidth = Math.max(3, Math.min(10, candleStep * 0.58));
  const midBar = bars[Math.floor(bars.length / 2)] ?? bars[0];
  const tickValues = Array.from({ length: 4 }, (_, index) => {
    return chartHigh - (chartSpan * index) / 3;
  });

  function priceToY(price: number) {
    return padding.top + ((chartHigh - price) / chartSpan) * plotHeight;
  }

  return (
    <div
      style={{
        overflowX: "auto",
        overflowY: "hidden",
        borderRadius: 18,
        border: "1px solid rgba(71, 85, 105, 0.24)",
        background:
          "radial-gradient(circle at top, rgba(14, 116, 144, 0.18), transparent 42%), rgba(3, 7, 18, 0.88)",
        padding: 10,
      }}
    >
      <svg
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        role="img"
        aria-label="candlestick chart"
      >
        {tickValues.map((value) => {
          const y = priceToY(value);
          return (
            <g key={value}>
              <line
                x1={padding.left}
                y1={y}
                x2={svgWidth - padding.right}
                y2={y}
                stroke="rgba(148, 163, 184, 0.14)"
                strokeDasharray="4 6"
              />
              <text
                x={svgWidth - padding.right + 8}
                y={y + 4}
                fill="rgba(226, 232, 240, 0.66)"
                fontSize="11"
                fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
              >
                {formatRawPrice(value)}
              </text>
            </g>
          );
        })}

        {bars.map((bar, index) => {
          const centerX = padding.left + candleStep * index + candleStep / 2;
          const highY = priceToY(bar.high);
          const lowY = priceToY(bar.low);
          const openY = priceToY(bar.open);
          const closeY = priceToY(bar.close);
          const bodyTop = Math.min(openY, closeY);
          const bodyHeight = Math.max(1.8, Math.abs(closeY - openY));
          const isUp = bar.close > bar.open;
          const isFlat = bar.close === bar.open;
          const color = isFlat ? "#fbbf24" : isUp ? "#34d399" : "#fb7185";

          return (
            <g key={`${bar.trade_date}-${index}`}>
              <line
                x1={centerX}
                y1={highY}
                x2={centerX}
                y2={lowY}
                stroke={color}
                strokeWidth="1.4"
              />
              <rect
                x={centerX - candleBodyWidth / 2}
                y={bodyTop}
                width={candleBodyWidth}
                height={bodyHeight}
                rx="1.5"
                fill={isFlat ? color : color}
                fillOpacity={isFlat ? 0.8 : 0.28}
                stroke={color}
                strokeWidth="1.3"
              />
            </g>
          );
        })}

        <text
          x={padding.left}
          y={svgHeight - 8}
          fill="rgba(226, 232, 240, 0.72)"
          fontSize="11"
          fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
        >
          {bars[0]?.trade_date}
        </text>
        <text
          x={svgWidth / 2}
          y={svgHeight - 8}
          textAnchor="middle"
          fill="rgba(226, 232, 240, 0.6)"
          fontSize="11"
          fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
        >
          {midBar.trade_date}
        </text>
        <text
          x={svgWidth - padding.right}
          y={svgHeight - 8}
          textAnchor="end"
          fill="rgba(226, 232, 240, 0.72)"
          fontSize="11"
          fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
        >
          {bars[bars.length - 1]?.trade_date}
        </text>
      </svg>
    </div>
  );
}

function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div
      style={{
        padding: "18px 16px",
        borderRadius: 18,
        border: "1px solid rgba(71, 85, 105, 0.26)",
        background: "rgba(15, 23, 42, 0.46)",
      }}
    >
      <div style={{ fontWeight: 800, color: "#f8fafc", marginBottom: 6 }}>{title}</div>
      <div style={{ color: "rgba(226, 232, 240, 0.74)", fontSize: 13, lineHeight: 1.6 }}>
        {description}
      </div>
    </div>
  );
}

function FieldLabel({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label
      style={{
        display: "grid",
        gap: 8,
        fontSize: 12,
        fontWeight: 700,
        color: "rgba(226, 232, 240, 0.76)",
        letterSpacing: "0.02em",
        textTransform: "uppercase",
      }}
    >
      <span>{label}</span>
      {children}
    </label>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: number | null;
}) {
  const color =
    tone == null
      ? "#f8fafc"
      : tone > 0
        ? "#86efac"
        : tone < 0
          ? "#fda4af"
          : "#f8fafc";

  return (
    <div
      style={{
        padding: "12px 14px",
        borderRadius: 16,
        border: "1px solid rgba(71, 85, 105, 0.26)",
        background: "rgba(15, 23, 42, 0.52)",
      }}
    >
      <div
        style={{
          marginBottom: 6,
          color: "rgba(226, 232, 240, 0.66)",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.02em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      <div style={{ color, fontSize: 17, fontWeight: 800 }}>{value}</div>
    </div>
  );
}

function formatDateInput(value: Date) {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, "0");
  const day = `${value.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatPrice(value: number, locale: string) {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatRawPrice(value: number) {
  return value >= 1000 ? value.toFixed(0) : value.toFixed(2);
}

function formatPercent(value: number | null, locale: string) {
  if (value == null || !Number.isFinite(value)) {
    return "-";
  }

  const formatted = new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "always",
  }).format(value);
  return formatted.replace("+0.00%", "0.00%");
}

function formatVolume(value: number | null | undefined, locale: string) {
  if (value == null) {
    return "-";
  }
  return new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function getDefaultPanelLayout(): PanelLayout {
  return clampPanelLayout({
    x: window.innerWidth - PANEL_DEFAULT_WIDTH - VIEWPORT_MARGIN,
    y: 72,
    width: PANEL_DEFAULT_WIDTH,
    height: Math.min(PANEL_DEFAULT_HEIGHT, window.innerHeight - 88),
  });
}

function clampPanelLayout(
  layout: PanelLayout,
  viewportWidth = window.innerWidth,
  viewportHeight = window.innerHeight
): PanelLayout {
  const maxWidth = Math.max(240, viewportWidth - VIEWPORT_MARGIN * 2);
  const maxHeight = Math.max(280, viewportHeight - VIEWPORT_MARGIN * 2);
  const minWidth = Math.min(PANEL_MIN_WIDTH, maxWidth);
  const minHeight = Math.min(PANEL_MIN_HEIGHT, maxHeight);
  const width = clampNumber(layout.width, minWidth, maxWidth);
  const height = clampNumber(layout.height, minHeight, maxHeight);
  const maxX = Math.max(VIEWPORT_MARGIN, viewportWidth - width - VIEWPORT_MARGIN);
  const maxY = Math.max(VIEWPORT_MARGIN, viewportHeight - height - VIEWPORT_MARGIN);

  return {
    x: clampNumber(layout.x, VIEWPORT_MARGIN, maxX),
    y: clampNumber(layout.y, VIEWPORT_MARGIN, maxY),
    width,
    height,
  };
}

function clampNumber(value: number, min: number, max: number) {
  const lower = Math.min(min, max);
  const upper = Math.max(min, max);
  return Math.min(Math.max(value, lower), upper);
}

const inputStyle = {
  width: "100%",
  minWidth: 0,
  padding: "11px 12px",
  borderRadius: 12,
  border: "1px solid rgba(71, 85, 105, 0.38)",
  background: "rgba(8, 15, 24, 0.82)",
  color: "#f8fafc",
  outline: "none",
} as const;

const dragGripStyle = {
  display: "inline-block",
  width: 26,
  height: 4,
  borderRadius: 999,
  background: "rgba(148, 163, 184, 0.42)",
} as const;
