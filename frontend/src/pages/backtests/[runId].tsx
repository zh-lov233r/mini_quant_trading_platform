import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";

import { getBacktest } from "@/api/backtests";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import type {
  BacktestDetailOut,
  BacktestSnapshotPoint,
  BacktestSignalOut,
  BacktestTransactionOut,
} from "@/types/backtest";
import { formatDateTime, formatPercent } from "@/utils/strategy";

function actionLink(href: string, label: string, filled = false) {
  return (
    <Link
      href={href}
      style={{
        padding: "11px 16px",
        borderRadius: 14,
        border: filled ? "none" : "1px solid rgba(148, 163, 184, 0.16)",
        background: filled ? "#0891b2" : "rgba(15, 23, 42, 0.72)",
        color: filled ? "#f8fafc" : "#dbeafe",
        textDecoration: "none",
        fontWeight: 700,
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
      }}
    >
      {label}
    </Link>
  );
}

function metricNumber(summary: Record<string, unknown>, key: string): number | null {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const SUMMARY_SYMBOLS_LIMIT = 20;

function renderValue(value: unknown): string {
  if (typeof value === "number") {
    return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (value == null) {
    return "-";
  }
  if (Array.isArray(value)) {
    return value.map((item) => renderValue(item)).join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function renderSummaryValue(key: string, value: unknown): string {
  if (key !== "symbols_loaded") {
    return renderValue(value);
  }

  const symbols = Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter(Boolean)
    : typeof value === "string"
      ? value.split(",").map((item) => item.trim()).filter(Boolean)
      : null;

  if (!symbols) {
    return renderValue(value);
  }

  const visibleSymbols = symbols.slice(0, SUMMARY_SYMBOLS_LIMIT);
  const remainingCount = symbols.length - visibleSymbols.length;
  if (remainingCount <= 0) {
    return visibleSymbols.join(", ");
  }
  return `${visibleSymbols.join(", ")} +${remainingCount}`;
}

function formatCurrency(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function getMetaText(meta: Record<string, unknown> | undefined, key: string): string | null {
  const value = meta?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function getMetaNumber(meta: Record<string, unknown> | undefined, key: string): number | null {
  const value = meta?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function buildLinePath(
  values: number[],
  width: number,
  height: number,
  paddingX: number,
  paddingTop: number,
  paddingBottom: number
): string {
  if (values.length === 0) {
    return "";
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const xStep = values.length > 1 ? (width - paddingX * 2) / (values.length - 1) : 0;
  const yRange = max - min || 1;
  const usableHeight = height - paddingTop - paddingBottom;

  return values
    .map((value, index) => {
      const x = paddingX + index * xStep;
      const y = height - paddingBottom - ((value - min) / yRange) * usableHeight;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildAreaPath(
  values: number[],
  width: number,
  height: number,
  paddingX: number,
  paddingTop: number,
  paddingBottom: number
): string {
  if (values.length === 0) {
    return "";
  }

  const linePath = buildLinePath(values, width, height, paddingX, paddingTop, paddingBottom);
  const xStep = values.length > 1 ? (width - paddingX * 2) / (values.length - 1) : 0;
  const lastX = paddingX + (values.length - 1) * xStep;
  const baseY = height - paddingBottom;
  return `${linePath} L ${lastX.toFixed(2)} ${baseY.toFixed(2)} L ${paddingX.toFixed(
    2
  )} ${baseY.toFixed(2)} Z`;
}

function buildYAxisTicks(
  min: number,
  max: number,
  height: number,
  paddingTop: number,
  paddingBottom: number,
  count = 5
) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || count < 2) {
    return [];
  }

  let visibleMin = min;
  let visibleMax = max;
  if (visibleMin === visibleMax) {
    const delta = Math.max(Math.abs(visibleMin) * 0.02, 1);
    visibleMin -= delta;
    visibleMax += delta;
  }

  const range = visibleMax - visibleMin || 1;
  const usableHeight = height - paddingTop - paddingBottom;
  return Array.from({ length: count }, (_, index) => {
    const ratio = index / (count - 1);
    const value = visibleMax - ratio * range;
    const y = paddingTop + ratio * usableHeight;
    return { value, y };
  });
}

type ChartMarker = {
  key: string;
  x: number;
  y: number;
  category: "buy_signal" | "sell_signal" | "buy_fill" | "sell_fill";
  shape: "circle" | "triangle-up" | "triangle-down";
  stroke: string;
  fill: string;
  title: string;
  details: string[];
};

type MarkerVisibility = Record<ChartMarker["category"], boolean>;

type SymbolPnlRow = {
  symbol: string;
  realizedPnl: number;
  unrealizedPnl: number;
  totalPnl: number;
  netQty: number;
  marketValue: number;
  lastPrice: number | null;
  tradeCount: number;
};

function toTimeValue(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

function toTradeDateKey(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  if (value.includes("T")) {
    const [datePart] = value.split("T");
    return datePart || null;
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) {
    return value.slice(0, 10);
  }
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    return null;
  }
  return parsed.toISOString().slice(0, 10);
}

function normalizePositionPayload(value: unknown): { qty: number; close: number; marketValue: number } | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const qty = typeof payload.qty === "number" ? payload.qty : null;
  const close = typeof payload.close === "number" ? payload.close : null;
  const marketValue = typeof payload.market_value === "number" ? payload.market_value : null;
  if (qty == null || close == null || marketValue == null) {
    return null;
  }
  return { qty, close, marketValue };
}

function buildSymbolPnlRows(run: BacktestDetailOut): SymbolPnlRow[] {
  const positions = run.latest_snapshot?.positions || {};
  const bySymbol = new Map<
    string,
    { qty: number; avgCost: number; realizedPnl: number; tradeCount: number }
  >();

  const orderedTransactions = [...run.transactions].sort((left, right) => {
    const leftTs = toTimeValue(left.ts) || 0;
    const rightTs = toTimeValue(right.ts) || 0;
    return leftTs - rightTs;
  });

  orderedTransactions.forEach((txn) => {
    const symbol = txn.symbol.toUpperCase();
    const state = bySymbol.get(symbol) || { qty: 0, avgCost: 0, realizedPnl: 0, tradeCount: 0 };
    const fee = typeof txn.fee === "number" ? txn.fee : 0;
    state.tradeCount += 1;

    if (txn.side === "BUY") {
      const currentCost = state.qty * state.avgCost;
      const boughtCost = txn.qty * txn.price + fee;
      const newQty = state.qty + txn.qty;
      state.avgCost = newQty > 0 ? (currentCost + boughtCost) / newQty : 0;
      state.qty = newQty;
      bySymbol.set(symbol, state);
      return;
    }

    if (txn.side === "SELL") {
      const sellQty = Math.min(state.qty, txn.qty);
      const proceeds = sellQty * txn.price - fee;
      const costBasis = sellQty * state.avgCost;
      state.realizedPnl += proceeds - costBasis;
      state.qty = Math.max(state.qty - sellQty, 0);
      if (state.qty <= 1e-9) {
        state.qty = 0;
        state.avgCost = 0;
      }
      bySymbol.set(symbol, state);
    }
  });

  const rows: SymbolPnlRow[] = [];
  bySymbol.forEach((state, symbol) => {
    const positionPayload = normalizePositionPayload(positions[symbol]);
    const netQty = positionPayload?.qty ?? state.qty;
    const marketValue = positionPayload?.marketValue ?? 0;
    const lastPrice = positionPayload?.close ?? null;
    const unrealizedPnl = netQty > 0 ? marketValue - netQty * state.avgCost : 0;
    rows.push({
      symbol,
      realizedPnl: state.realizedPnl,
      unrealizedPnl,
      totalPnl: state.realizedPnl + unrealizedPnl,
      netQty,
      marketValue,
      lastPrice,
      tradeCount: state.tradeCount,
    });
  });

  return rows.sort((left, right) => Math.abs(right.totalPnl) - Math.abs(left.totalPnl));
}

function buildEventMarkers(
  normalizedPoints: Array<{ ts: string; equity: number }>,
  width: number,
  height: number,
  paddingLeft: number,
  paddingTop: number,
  paddingBottom: number,
  signals: BacktestSignalOut[],
  transactions: BacktestTransactionOut[]
): ChartMarker[] {
  if (normalizedPoints.length === 0) {
    return [];
  }

  const values = normalizedPoints.map((point) => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const yRange = max - min || 1;
  const xStep = normalizedPoints.length > 1 ? (width - paddingLeft * 2) / (normalizedPoints.length - 1) : 0;
  const usableHeight = height - paddingTop - paddingBottom;
  const pointTimes = normalizedPoints.map((point) => toTimeValue(point.ts));
  const firstPointTime = pointTimes[0] ?? null;
  const lastPointTime = pointTimes[pointTimes.length - 1] ?? null;
  const occupancy = new Map<string, number>();

  const indexForTime = (rawTs?: string | null) => {
    const target = toTimeValue(rawTs);
    if (target == null) {
      return null;
    }
    if (firstPointTime != null && target < firstPointTime) {
      return null;
    }
    if (lastPointTime != null && target > lastPointTime) {
      return null;
    }
    let bestIndex = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let index = 0; index < pointTimes.length; index += 1) {
      const candidate = pointTimes[index];
      if (candidate == null) {
        continue;
      }
      const distance = Math.abs(candidate - target);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    }
    return bestIndex >= 0 ? bestIndex : null;
  };

  const pointPosition = (index: number) => {
    const x = paddingLeft + index * xStep;
    const y = height - paddingBottom - ((normalizedPoints[index].equity - min) / yRange) * usableHeight;
    return { x, y };
  };

  const markers: ChartMarker[] = [];

  const groupedSignals = new Map<
    string,
    { ts: string; signal: "BUY" | "SELL"; items: BacktestSignalOut[] }
  >();
  signals.forEach((signal) => {
    if (signal.signal !== "BUY" && signal.signal !== "SELL") {
      return;
    }
    const dateKey = toTradeDateKey(signal.ts);
    if (!dateKey || !signal.ts) {
      return;
    }
    const groupKey = `${dateKey}-${signal.signal}`;
    const existing = groupedSignals.get(groupKey);
    if (existing) {
      existing.items.push(signal);
      return;
    }
    groupedSignals.set(groupKey, {
      ts: signal.ts,
      signal: signal.signal,
      items: [signal],
    });
  });

  Array.from(groupedSignals.values())
    .sort((left, right) => (toTimeValue(left.ts) || 0) - (toTimeValue(right.ts) || 0))
    .forEach((group) => {
      const index = indexForTime(group.ts);
      if (index == null) {
        return;
      }
      const stackKey = `signal-${index}`;
      const stack = occupancy.get(stackKey) || 0;
      occupancy.set(stackKey, stack + 1);
      const position = pointPosition(index);
      const isBuy = group.signal === "BUY";
      markers.push({
        key: `signal-${group.signal}-${group.ts}`,
        x: position.x,
        y: position.y - 12 - stack * 8,
        category: isBuy ? "buy_signal" : "sell_signal",
        shape: "circle",
        stroke: isBuy ? "#2563eb" : "#d97706",
        fill: "#ffffff",
        title: `${group.signal} 信号 ${group.items.length} 个`,
        details: group.items.map((signal) =>
          signal.reason ? `${signal.symbol}: ${signal.reason}` : signal.symbol
        ),
      });
    });

  const groupedTransactions = new Map<
    string,
    { ts: string; side: "BUY" | "SELL"; items: BacktestTransactionOut[] }
  >();
  transactions.forEach((txn) => {
    if (txn.side !== "BUY" && txn.side !== "SELL") {
      return;
    }
    const dateKey = toTradeDateKey(txn.ts);
    if (!dateKey || !txn.ts) {
      return;
    }
    const groupKey = `${dateKey}-${txn.side}`;
    const existing = groupedTransactions.get(groupKey);
    if (existing) {
      existing.items.push(txn);
      return;
    }
    groupedTransactions.set(groupKey, {
      ts: txn.ts,
      side: txn.side,
      items: [txn],
    });
  });

  Array.from(groupedTransactions.values())
    .sort((left, right) => (toTimeValue(left.ts) || 0) - (toTimeValue(right.ts) || 0))
    .forEach((group) => {
      const index = indexForTime(group.ts);
      if (index == null) {
        return;
      }
      const stackKey = `transaction-${index}`;
      const stack = occupancy.get(stackKey) || 0;
      occupancy.set(stackKey, stack + 1);
      const position = pointPosition(index);
      const isBuy = group.side === "BUY";
      markers.push({
        key: `transaction-${group.side}-${group.ts}`,
        x: position.x,
        y: position.y + 14 + stack * 10,
        category: isBuy ? "buy_fill" : "sell_fill",
        shape: isBuy ? "triangle-up" : "triangle-down",
        stroke: isBuy ? "#16a34a" : "#dc2626",
        fill: isBuy ? "#16a34a" : "#dc2626",
        title: `${group.side} 成交 ${group.items.length} 个`,
        details: group.items.map(
          (txn) =>
            `${txn.symbol} ${txn.qty.toLocaleString("en-US", { maximumFractionDigits: 4 })} @ ${txn.price.toLocaleString("en-US", {
              maximumFractionDigits: 2,
            })}`
        ),
      });
    });

  return markers;
}

function RunOverviewPanel({ run }: { run: BacktestDetailOut }) {
  return (
    <div
      style={{
        marginBottom: 18,
        padding: 18,
        borderRadius: 18,
        background: "rgba(248,250,252,0.92)",
        border: "1px solid rgba(226, 232, 240, 0.9)",
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 22 }}>Run 概览</h3>
        <p style={sectionSubtitleStyle}>先看运行状态、窗口和最终权益，判断这次回测是不是按预期结束。</p>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Badge tone="info">{run.mode}</Badge>
        {run.basket_name ? <Badge>{run.basket_name}</Badge> : null}
        <Badge
          tone={
            run.status === "completed"
              ? "success"
              : run.status === "failed"
                ? "warning"
                : "neutral"
          }
        >
          {run.status}
        </Badge>
        <Badge>v{run.strategy_version}</Badge>
      </div>

      <div style={infoGridStyle}>
        <div>
          <div style={labelStyle}>Run ID</div>
          <div style={valueStyle}>{run.id}</div>
        </div>
        <div>
          <div style={labelStyle}>策略</div>
          <div style={valueStyle}>{run.strategy_name || run.strategy_id}</div>
        </div>
        <div>
          <div style={labelStyle}>区间</div>
          <div style={valueStyle}>
            {run.window_start} {"->"} {run.window_end}
          </div>
        </div>
        <div>
          <div style={labelStyle}>股票组合</div>
          <div style={valueStyle}>{run.basket_name || "沿用策略原始股票池"}</div>
        </div>
        <div>
          <div style={labelStyle}>初始资金</div>
          <div style={valueStyle}>{formatCurrency(run.initial_cash)}</div>
        </div>
        <div>
          <div style={labelStyle}>期末权益</div>
          <div style={valueStyle}>{formatCurrency(run.final_equity)}</div>
        </div>
        <div>
          <div style={labelStyle}>完成时间</div>
          <div style={valueStyle}>{formatDateTime(run.finished_at || run.requested_at)}</div>
        </div>
      </div>

      {run.latest_snapshot ? (
        <div
          style={{
            marginTop: 16,
            padding: 16,
            borderRadius: 16,
            background: "#ffffff",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          <div style={{ marginBottom: 10, fontWeight: 700, color: "#0f172a" }}>最新快照</div>
          <div style={{ display: "grid", gap: 8, color: "#475569" }}>
            <div>时间: {formatDateTime(run.latest_snapshot.ts || null)}</div>
            <div>现金: {formatCurrency(run.latest_snapshot.cash)}</div>
            <div>权益: {formatCurrency(run.latest_snapshot.equity)}</div>
            <div>回撤: {formatPercent(run.latest_snapshot.drawdown ?? null, 2)}</div>
          </div>
        </div>
      ) : null}

      {run.error_message ? (
        <div
          style={{
            marginTop: 16,
            padding: 14,
            borderRadius: 14,
            background: "#fef2f2",
            color: "#b91c1c",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {run.error_message}
        </div>
      ) : null}
    </div>
  );
}

function SymbolPnlCard({ rows }: { rows: SymbolPnlRow[] }) {
  const [showAllSymbols, setShowAllSymbols] = useState(false);
  const visibleRows = showAllSymbols ? rows : rows.slice(0, 20);

  return (
    <div style={{ marginTop: 18 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <div>
          <h3 style={{ margin: "0 0 8px", fontSize: 22 }}>个股盈亏</h3>
          <p style={sectionSubtitleStyle}>按单只股票汇总本次策略运行期间的已实现和未实现盈亏，未平仓按最后一天价格估值。</p>
        </div>
        {rows.length > 20 ? (
          <button
            type="button"
            onClick={() => setShowAllSymbols((current) => !current)}
            style={tableToggleButtonStyle}
          >
            {showAllSymbols ? "收起到前 20 支" : "展开全部"}
          </button>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <div style={emptyStateStyle}>这次回测还没有可统计的个股盈亏。</div>
      ) : (
        <>
          <div style={{ marginBottom: 10, color: "#64748b", fontSize: 13 }}>
            当前显示 {visibleRows.length} / {rows.length} 支股票，按绝对盈亏排序。
          </div>
          <div
            style={{
              overflowX: "auto",
              borderRadius: 18,
              border: "1px solid rgba(226, 232, 240, 0.9)",
              background: "#fff",
            }}
          >
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                minWidth: 860,
                fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              }}
            >
              <thead>
                <tr style={{ background: "#f8fafc", color: "#475569", textAlign: "left" }}>
                  {["标的", "总盈亏", "已实现", "未实现", "持仓数量", "最新价格", "持仓市值", "交易次数"].map((label) => (
                    <th
                      key={label}
                      style={{
                        padding: "12px 14px",
                        fontSize: 12,
                        fontWeight: 700,
                        letterSpacing: "0.03em",
                        textTransform: "uppercase",
                        borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
                      }}
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const totalTone = row.totalPnl >= 0 ? "#15803d" : "#b91c1c";
                  return (
                    <tr key={row.symbol} style={{ borderBottom: "1px solid rgba(241, 245, 249, 1)" }}>
                      <td style={cellStyle}>{row.symbol}</td>
                      <td style={{ ...cellStyle, color: totalTone, fontWeight: 700 }}>{formatCurrency(row.totalPnl)}</td>
                      <td style={cellStyle}>{formatCurrency(row.realizedPnl)}</td>
                      <td style={cellStyle}>{formatCurrency(row.unrealizedPnl)}</td>
                      <td style={cellStyle}>{row.netQty.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td>
                      <td style={cellStyle}>{formatCurrency(row.lastPrice)}</td>
                      <td style={cellStyle}>{formatCurrency(row.marketValue)}</td>
                      <td style={cellStyle}>{row.tradeCount}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function EquityCurveCard({
  run,
  points,
  signals,
  transactions,
  initialCash,
}: {
  run: BacktestDetailOut;
  points: BacktestSnapshotPoint[];
  signals: BacktestSignalOut[];
  transactions: BacktestTransactionOut[];
  initialCash?: number | null;
}) {
  const [hoveredMarkerKey, setHoveredMarkerKey] = useState<string | null>(null);
  const [chartZoom, setChartZoom] = useState(1);
  const [windowOffset, setWindowOffset] = useState(0);
  const [markerVisibility, setMarkerVisibility] = useState<MarkerVisibility>({
    buy_signal: true,
    sell_signal: true,
    buy_fill: true,
    sell_fill: true,
  });
  const normalizedPoints = points.filter(
    (point): point is BacktestSnapshotPoint & { ts: string; equity: number } =>
      typeof point.ts === "string" &&
      typeof point.equity === "number" &&
      Number.isFinite(point.equity)
  );
  const values = normalizedPoints.map((point) => point.equity);

  const startValue = values[0] ?? initialCash ?? null;
  const endValue = values.length > 0 ? values[values.length - 1] : null;
  const peakValue = values.length > 0 ? Math.max(...values) : null;
  const troughValue = values.length > 0 ? Math.min(...values) : null;

  if (values.length < 2) {
    return (
      <section style={sectionCardStyle}>
        <div style={{ marginBottom: 16 }}>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>权益曲线</h2>
          <p style={sectionSubtitleStyle}>当前还没有足够的快照点来绘制曲线。</p>
        </div>
        <div style={emptyStateStyle}>至少需要两条 portfolio snapshot 才能看到走势。</div>
      </section>
    );
  }

  const width = 1340;
  const height = 320;
  const axisLeftPadding = 96;
  const axisRightPadding = 28;
  const chartTopPadding = 26;
  const chartBottomPadding = 42;
  const latestPoint = normalizedPoints[normalizedPoints.length - 1];
  const visiblePointCount = Math.max(2, Math.floor(normalizedPoints.length / chartZoom));
  const maxWindowOffset = Math.max(0, normalizedPoints.length - visiblePointCount);
  const safeWindowOffset = Math.min(windowOffset, maxWindowOffset);
  const visiblePoints = normalizedPoints.slice(safeWindowOffset, safeWindowOffset + visiblePointCount);
  const visibleValues = visiblePoints.map((point) => point.equity);
  const linePath = buildLinePath(
    visibleValues,
    width,
    height,
    axisLeftPadding,
    chartTopPadding,
    chartBottomPadding
  );
  const areaPath = buildAreaPath(
    visibleValues,
    width,
    height,
    axisLeftPadding,
    chartTopPadding,
    chartBottomPadding
  );
  const min = Math.min(...visibleValues);
  const max = Math.max(...visibleValues);
  const yRange = max - min || 1;
  const referenceValue = typeof startValue === "number" ? startValue : null;
  const referenceLineY =
    referenceValue == null
      ? null
      : height -
          chartBottomPadding -
          ((referenceValue - min) / yRange) * (height - chartTopPadding - chartBottomPadding);
  const visibleWindowStart = visiblePoints[0]?.ts || null;
  const visibleWindowEnd = visiblePoints[visiblePoints.length - 1]?.ts || null;
  const yAxisTicks = buildYAxisTicks(min, max, height, chartTopPadding, chartBottomPadding);
  const markers = buildEventMarkers(
    visiblePoints,
    width,
    height,
    axisLeftPadding,
    chartTopPadding,
    chartBottomPadding,
    signals,
    transactions
  );
  const symbolPnlRows = useMemo(() => buildSymbolPnlRows(run), [run]);
  const visibleMarkers = markers.filter((marker) => markerVisibility[marker.category]);
  const hoveredMarker = hoveredMarkerKey
    ? visibleMarkers.find((marker) => marker.key === hoveredMarkerKey) || null
    : null;
  const buySignalCount = signals.filter((item) => item.signal === "BUY").length;
  const sellSignalCount = signals.filter((item) => item.signal === "SELL").length;
  const buyCount = transactions.filter((item) => item.side === "BUY").length;
  const sellCount = transactions.filter((item) => item.side === "SELL").length;
  const setAllMarkers = (visible: boolean) => {
    setMarkerVisibility({
      buy_signal: visible,
      sell_signal: visible,
      buy_fill: visible,
      sell_fill: visible,
    });
    if (!visible) {
      setHoveredMarkerKey(null);
    }
  };
  const toggleMarkerCategory = (category: keyof MarkerVisibility) => {
    setMarkerVisibility((current) => ({
      ...current,
      [category]: !current[category],
    }));
  };
  const zoomOut = () => {
    setChartZoom((current) => Math.max(1, Number((current - 0.25).toFixed(2))));
    setHoveredMarkerKey(null);
  };
  const zoomIn = () => {
    setChartZoom((current) => Math.min(6, Number((current + 0.25).toFixed(2))));
    setHoveredMarkerKey(null);
  };
  const resetZoom = () => {
    setChartZoom(1);
    setWindowOffset(0);
    setHoveredMarkerKey(null);
  };

  return (
    <section style={sectionCardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>权益曲线</h2>
          <p style={sectionSubtitleStyle}>直接用 portfolio snapshots 绘制，先确认回测收益曲线是否连续、合理。</p>
        </div>
        <div style={chartMetaStyle}>
          <div>起点 {formatCurrency(startValue)}</div>
          <div>终点 {formatCurrency(endValue)}</div>
        </div>
      </div>

      <RunOverviewPanel run={run} />

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 12,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <div style={{ color: "#64748b", fontSize: 13 }}>
          放大后会只显示更短的一段时间窗口，便于看清局部走势和事件细节，虚线表示起始资金基准线。
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" style={zoomButtonStyle} onClick={zoomOut}>
            缩小
          </button>
          <button type="button" style={zoomButtonStyle} onClick={resetZoom}>
            还原
          </button>
          <button type="button" style={zoomButtonStyle} onClick={zoomIn}>
            放大
          </button>
          <span style={{ color: "#475569", fontSize: 13 }}>缩放 {Math.round(chartZoom * 100)}%</span>
        </div>
      </div>

      <div
        style={{
          marginBottom: 14,
          display: "grid",
          gap: 8,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", color: "#475569", fontSize: 13 }}>
          <span>
            当前窗口: {formatDateTime(visibleWindowStart)} {"->"} {formatDateTime(visibleWindowEnd)}
          </span>
          <span>
            显示 {visiblePoints.length} / {normalizedPoints.length} 个快照点
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={maxWindowOffset}
          step={1}
          value={safeWindowOffset}
          disabled={maxWindowOffset === 0}
          onChange={(event) => {
            setWindowOffset(Number(event.target.value));
            setHoveredMarkerKey(null);
          }}
          style={{ width: "100%", accentColor: "#0f766e" }}
        />
      </div>

      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          marginBottom: 14,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <button type="button" style={markerToggleButtonStyle(true)} onClick={() => setAllMarkers(true)}>
          全部开启
        </button>
        <button type="button" style={markerToggleButtonStyle(false)} onClick={() => setAllMarkers(false)}>
          全部关闭
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.buy_signal, "#2563eb")}
          onClick={() => toggleMarkerCategory("buy_signal")}
        >
          BUY 信号
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.sell_signal, "#d97706")}
          onClick={() => toggleMarkerCategory("sell_signal")}
        >
          SELL 信号
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.buy_fill, "#16a34a")}
          onClick={() => toggleMarkerCategory("buy_fill")}
        >
          BUY 成交
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.sell_fill, "#dc2626")}
          onClick={() => toggleMarkerCategory("sell_fill")}
        >
          SELL 成交
        </button>
      </div>

      <div
        style={{
          position: "relative",
          padding: 18,
          borderRadius: 18,
          background: "linear-gradient(180deg, rgba(240,253,250,0.9), rgba(255,255,255,0.95))",
          border: "1px solid rgba(94, 234, 212, 0.28)",
        }}
      >
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: 320, display: "block" }}>
          <defs>
            <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(15,118,110,0.28)" />
              <stop offset="100%" stopColor="rgba(15,118,110,0.02)" />
            </linearGradient>
          </defs>
          {yAxisTicks.map((tick) => (
            <g key={`y-axis-${tick.y}`}>
              <line
                x1={axisLeftPadding}
                x2={width - axisRightPadding}
                y1={tick.y}
                y2={tick.y}
                stroke="rgba(148, 163, 184, 0.18)"
                strokeWidth="1"
              />
              <text
                x={axisLeftPadding - 12}
                y={tick.y + 4}
                textAnchor="end"
                fill="#64748b"
                fontSize="11"
                fontWeight="600"
                fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
              >
                {formatCurrency(tick.value)}
              </text>
            </g>
          ))}
          {referenceLineY != null ? (
            <g>
              <line
                x1={axisLeftPadding}
                x2={width - axisRightPadding}
                y1={referenceLineY}
                y2={referenceLineY}
                stroke="#0f172a"
                strokeDasharray="7 6"
                strokeOpacity="0.32"
                strokeWidth="1.5"
              />
              <text
                x={width - axisRightPadding}
                y={referenceLineY - 8}
                textAnchor="end"
                fill="#0f172a"
                fillOpacity="0.62"
                fontSize="12"
                fontWeight="700"
                fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
              >
                起始资金 {formatCurrency(referenceValue)}
              </text>
            </g>
          ) : null}
          <path d={areaPath} fill="url(#equityFill)" />
          <path
            d={linePath}
            fill="none"
            stroke="#0f766e"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {visibleMarkers.map((marker) => {
            if (marker.shape === "circle") {
              return (
                <g
                  key={marker.key}
                  onMouseEnter={() => setHoveredMarkerKey(marker.key)}
                  onMouseLeave={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                  onFocus={() => setHoveredMarkerKey(marker.key)}
                  onBlur={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                  style={{ cursor: "pointer" }}
                >
                  <circle
                    cx={marker.x}
                    cy={marker.y}
                    r="5"
                    fill={marker.fill}
                    stroke={marker.stroke}
                    strokeWidth="2.5"
                  />
                  <title>{[marker.title, ...marker.details].join("\n")}</title>
                </g>
              );
            }

            const pointsText =
              marker.shape === "triangle-up"
                ? `${marker.x},${marker.y - 6} ${marker.x - 6},${marker.y + 6} ${marker.x + 6},${marker.y + 6}`
                : `${marker.x},${marker.y + 6} ${marker.x - 6},${marker.y - 6} ${marker.x + 6},${marker.y - 6}`;
            return (
              <g
                key={marker.key}
                onMouseEnter={() => setHoveredMarkerKey(marker.key)}
                onMouseLeave={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                onFocus={() => setHoveredMarkerKey(marker.key)}
                onBlur={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                style={{ cursor: "pointer" }}
              >
                <polygon points={pointsText} fill={marker.fill} stroke={marker.stroke} strokeWidth="1.5" />
                <title>{[marker.title, ...marker.details].join("\n")}</title>
              </g>
            );
          })}
        </svg>
        {hoveredMarker ? (
          <div
            style={{
              position: "absolute",
              top: `${(hoveredMarker.y / height) * 100}%`,
              ...(hoveredMarker.x / width > 0.68
                ? {
                    right: `${100 - (hoveredMarker.x / width) * 100}%`,
                    transform: "translate(-12px, -100%)",
                  }
                : {
                    left: `${(hoveredMarker.x / width) * 100}%`,
                    transform: "translate(12px, -100%)",
                  }),
              maxWidth: 360,
              maxHeight: 240,
              overflowY: "auto",
              padding: 12,
              borderRadius: 14,
              border: "1px solid rgba(15, 23, 42, 0.08)",
              background: "rgba(15, 23, 42, 0.94)",
              color: "#f8fafc",
              boxShadow: "0 18px 40px rgba(15, 23, 42, 0.24)",
              fontSize: 12,
              lineHeight: 1.5,
              pointerEvents: "none",
              zIndex: 2,
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 6 }}>{hoveredMarker.title}</div>
            <div style={{ display: "grid", gap: 4 }}>
              {hoveredMarker.details.map((detail) => (
                <div key={`${hoveredMarker.key}-${detail}`}>{detail}</div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div
        style={{
          marginTop: 12,
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
          color: "#475569",
          fontSize: 13,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <span style={legendItemStyle}>
          <span style={{ ...legendDotStyle, border: "2px solid #2563eb", background: "#fff" }} />
          BUY 信号 {buySignalCount}
        </span>
        <span style={legendItemStyle}>
          <span style={{ ...legendDotStyle, border: "2px solid #d97706", background: "#fff" }} />
          SELL 信号 {sellSignalCount}
        </span>
        <span style={legendItemStyle}>
          <span style={legendTriangleUpStyle} />
          BUY 成交 {buyCount}
        </span>
        <span style={legendItemStyle}>
          <span style={legendTriangleDownStyle} />
          SELL 成交 {sellCount}
        </span>
      </div>

      <div
        style={{
          marginTop: 14,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <div style={miniMetricStyle}>
          <div style={labelStyle}>快照点数</div>
          <div style={miniMetricValueStyle}>{normalizedPoints.length}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>曲线峰值</div>
          <div style={miniMetricValueStyle}>{formatCurrency(peakValue)}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>曲线谷值</div>
          <div style={miniMetricValueStyle}>{formatCurrency(troughValue)}</div>
        </div>
          <div style={miniMetricStyle}>
            <div style={labelStyle}>最后快照</div>
            <div style={miniMetricValueStyle}>{formatDateTime(latestPoint?.ts || null)}</div>
          </div>
        </div>

      <SymbolPnlCard rows={symbolPnlRows} />
    </section>
  );
}

function markerToggleButtonStyle(isPrimary: boolean) {
  return {
    border: "none",
    borderRadius: 999,
    padding: "8px 14px",
    background: isPrimary ? "#0f766e" : "#e2e8f0",
    color: isPrimary ? "#ffffff" : "#0f172a",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  } as const;
}

function markerToggleChipStyle(active: boolean, accent: string) {
  return {
    borderRadius: 999,
    padding: "8px 14px",
    border: `1px solid ${active ? accent : "rgba(148, 163, 184, 0.24)"}`,
    background: active ? `${accent}14` : "rgba(255,255,255,0.85)",
    color: active ? accent : "#475569",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  } as const;
}

const zoomButtonStyle = {
  borderRadius: 999,
  padding: "8px 14px",
  border: "1px solid rgba(148, 163, 184, 0.28)",
  background: "rgba(255,255,255,0.92)",
  color: "#0f172a",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const tableToggleButtonStyle = {
  borderRadius: 999,
  padding: "8px 14px",
  border: "1px solid rgba(37, 99, 235, 0.24)",
  background: "rgba(239, 246, 255, 0.9)",
  color: "#1d4ed8",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

function TransactionsCard({ transactions }: { transactions: BacktestTransactionOut[] }) {
  const [showAllTransactions, setShowAllTransactions] = useState(false);
  const visibleTransactions = showAllTransactions ? transactions : transactions.slice(0, 20);

  return (
    <section style={sectionCardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>交易明细</h2>
          <p style={sectionSubtitleStyle}>这里直接读 transactions，方便核对成交方向、价格、费用和信号原因。</p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Badge tone="info">{transactions.length} trades</Badge>
          {transactions.length > 20 ? (
            <button
              type="button"
              onClick={() => setShowAllTransactions((current) => !current)}
              style={tableToggleButtonStyle}
            >
              {showAllTransactions ? "收起到前 20 条" : "展开全部"}
            </button>
          ) : null}
        </div>
      </div>

      {transactions.length === 0 ? (
        <div style={emptyStateStyle}>这次 run 还没有写入任何交易记录。</div>
      ) : (
        <>
          {transactions.length > 20 ? (
            <div style={{ marginBottom: 10, color: "#64748b", fontSize: 13 }}>
              当前显示 {visibleTransactions.length} / {transactions.length} 条交易记录。
            </div>
          ) : null}
          <div
            style={{
              overflowX: "auto",
              borderRadius: 18,
              border: "1px solid rgba(226, 232, 240, 0.9)",
              background: "#fff",
            }}
          >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              minWidth: 980,
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            }}
          >
            <thead>
              <tr style={{ background: "#f8fafc", color: "#475569", textAlign: "left" }}>
                {["时间", "方向", "标的", "数量", "成交价", "费用", "现金流", "信号时间", "原因"].map(
                  (label) => (
                    <th
                      key={label}
                      style={{
                        padding: "12px 14px",
                        fontSize: 12,
                        fontWeight: 700,
                        letterSpacing: "0.03em",
                        textTransform: "uppercase",
                        borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
                      }}
                    >
                      {label}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {visibleTransactions.map((txn) => {
                const netCashFlow = getMetaNumber(txn.meta, "net_cash_flow");
                const reason = getMetaText(txn.meta, "reason");
                const signalTs = getMetaText(txn.meta, "signal_ts");
                return (
                  <tr key={txn.id} style={{ borderBottom: "1px solid rgba(241, 245, 249, 1)" }}>
                    <td style={cellStyle}>{formatDateTime(txn.ts || null)}</td>
                    <td style={cellStyle}>
                      <Badge tone={txn.side === "BUY" ? "success" : "warning"}>{txn.side}</Badge>
                    </td>
                    <td style={cellStyle}>{txn.symbol}</td>
                    <td style={cellStyle}>{txn.qty.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td>
                    <td style={cellStyle}>{formatCurrency(txn.price)}</td>
                    <td style={cellStyle}>{formatCurrency(txn.fee ?? null)}</td>
                    <td style={cellStyle}>{formatCurrency(netCashFlow)}</td>
                    <td style={cellStyle}>{formatDateTime(signalTs)}</td>
                    <td style={cellStyle}>{reason || "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      )}
    </section>
  );
}

export default function BacktestDetailPage() {
  const router = useRouter();
  const runId = Array.isArray(router.query.runId) ? router.query.runId[0] : router.query.runId;

  const [run, setRun] = useState<BacktestDetailOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!router.isReady || !runId) {
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    getBacktest(runId)
      .then((item) => {
        if (!cancelled) {
          setRun(item);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || "加载回测详情失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [router.isReady, runId]);

  const summaryEntries = useMemo(
    () => Object.entries(run?.summary_metrics || {}).sort(([a], [b]) => a.localeCompare(b)),
    [run]
  );
  const positionEntries = useMemo(
    () => Object.entries(run?.latest_snapshot?.positions || {}).sort(([a], [b]) => a.localeCompare(b)),
    [run]
  );
  const totalReturn = metricNumber(run?.summary_metrics || {}, "total_return");
  const maxDrawdown = metricNumber(run?.summary_metrics || {}, "max_drawdown");
  const signalCount = metricNumber(run?.summary_metrics || {}, "signal_count");
  const tradeCount = metricNumber(run?.summary_metrics || {}, "trade_count");

  if (!loading && !error && !run) {
    return (
      <AppShell
        title="回测详情"
        subtitle="当前没有找到目标回测记录，可能 run id 不存在，或者后端尚未完成落库。"
        actions={actionLink("/backtests", "返回回测列表")}
      >
        <p>未找到回测记录。</p>
      </AppShell>
    );
  }

  return (
    <AppShell
      title={run?.strategy_name ? `${run.strategy_name} 回测结果` : "回测详情"}
      subtitle="本页把单次回测的状态、权益曲线、摘要指标、交易明细和最新持仓放在一起，方便快速复盘。"
      actions={
        <>
          {actionLink("/backtests", "返回回测列表")}
          {actionLink(
            run ? `/strategies/${encodeURIComponent(run.strategy_id)}` : "/strategies",
            "查看策略"
          )}
        </>
      }
    >
      {loading ? <p>加载中...</p> : null}
      {error ? <p style={{ color: "crimson" }}>{error}</p> : null}

      {!loading && !error && run ? (
        <>
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 16,
              marginBottom: 18,
            }}
          >
            <MetricCard
              label="总收益"
              value={formatPercent(totalReturn, 2)}
              hint="来自 strategy_run.summary_metrics.total_return。"
              accent="#0f766e"
            />
            <MetricCard
              label="最大回撤"
              value={formatPercent(maxDrawdown, 2)}
              hint="用来快速判断这次曲线是否过于激进。"
              accent="#b45309"
            />
            <MetricCard
              label="Signals"
              value={signalCount != null ? String(signalCount) : "-"}
              hint="这次 run 中生成的信号数量。"
              accent="#2563eb"
            />
            <MetricCard
              label="Transactions"
              value={String(run.transaction_count ?? tradeCount ?? "-")}
              hint="已经写入 transactions 的成交记录数量。"
              accent="#ca8a04"
            />
          </section>

          <section
            style={{
              marginBottom: 18,
            }}
          >
            <EquityCurveCard
              run={run}
              points={run.equity_curve}
              signals={run.signals}
              transactions={run.transactions}
              initialCash={run.initial_cash}
            />
          </section>

          <TransactionsCard transactions={run.transactions} />

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.1fr) minmax(320px, 0.9fr)",
              gap: 18,
              marginTop: 18,
              alignItems: "start",
            }}
          >
            <section style={sectionCardStyle}>
              <div style={{ marginBottom: 16 }}>
                <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>摘要指标</h2>
                <p style={sectionSubtitleStyle}>当前直接展示后端 summary_metrics，便于检查指标是否正确落库。</p>
              </div>

              {summaryEntries.length === 0 ? (
                <div style={emptyStateStyle}>这次 run 还没有 summary metrics。</div>
              ) : (
                <div style={{ display: "grid", gap: 10 }}>
                  {summaryEntries.map(([key, value]) => (
                    <div key={key} style={infoRowStyle}>
                      <div style={{ color: "#64748b", fontWeight: 600 }}>{key}</div>
                      <div style={{ color: "#0f172a", wordBreak: "break-word" }}>{renderSummaryValue(key, value)}</div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section style={sectionCardStyle}>
              <div style={{ marginBottom: 16 }}>
                <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>最新持仓</h2>
                <p style={sectionSubtitleStyle}>这里展示最新 snapshot 的 positions，方便确认回测结束时还持有哪些仓位。</p>
              </div>

              {positionEntries.length === 0 ? (
                <div style={emptyStateStyle}>当前没有持仓，或回测结束时已经全部平仓。</div>
              ) : (
                <div style={{ display: "grid", gap: 10 }}>
                  {positionEntries.map(([symbol, value]) => (
                    <div key={symbol} style={infoRowStyle}>
                      <div style={{ color: "#64748b", fontWeight: 700 }}>{symbol}</div>
                      <div style={{ color: "#0f172a", wordBreak: "break-word" }}>{renderValue(value)}</div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </section>
        </>
      ) : null}
    </AppShell>
  );
}

const sectionCardStyle = {
  padding: 22,
  borderRadius: 24,
  border: "1px solid rgba(148, 163, 184, 0.18)",
  background: "rgba(255,255,255,0.82)",
  boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
} as const;

const sectionSubtitleStyle = {
  margin: 0,
  color: "#64748b",
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const emptyStateStyle = {
  padding: 16,
  borderRadius: 16,
  background: "#f8fafc",
  color: "#475569",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const infoGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: 14,
} as const;

const labelStyle = {
  marginBottom: 6,
  color: "#94a3b8",
  fontSize: 12,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const valueStyle = {
  color: "#0f172a",
  lineHeight: 1.6,
  wordBreak: "break-word" as const,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const miniMetricStyle = {
  padding: 14,
  borderRadius: 16,
  background: "#f8fafc",
} as const;

const miniMetricValueStyle = {
  color: "#0f172a",
  fontWeight: 700,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const chartMetaStyle = {
  color: "#475569",
  lineHeight: 1.7,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const legendItemStyle = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
} as const;

const legendDotStyle = {
  display: "inline-block",
  width: 10,
  height: 10,
  borderRadius: "999px",
} as const;

const legendTriangleUpStyle = {
  width: 0,
  height: 0,
  borderLeft: "6px solid transparent",
  borderRight: "6px solid transparent",
  borderBottom: "10px solid #16a34a",
} as const;

const legendTriangleDownStyle = {
  width: 0,
  height: 0,
  borderLeft: "6px solid transparent",
  borderRight: "6px solid transparent",
  borderTop: "10px solid #dc2626",
} as const;

const cellStyle = {
  padding: "12px 14px",
  color: "#0f172a",
  fontSize: 14,
  lineHeight: 1.5,
} as const;

const infoRowStyle = {
  display: "grid",
  gridTemplateColumns: "180px minmax(0, 1fr)",
  gap: 12,
  padding: "10px 0",
  borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;
