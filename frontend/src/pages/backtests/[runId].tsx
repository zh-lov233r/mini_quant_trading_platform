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
        border: filled ? "none" : "1px solid rgba(148, 163, 184, 0.28)",
        background: filled ? "#0f766e" : "rgba(255,255,255,0.8)",
        color: filled ? "#fff" : "#0f172a",
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

function buildLinePath(values: number[], width: number, height: number, padding: number): string {
  if (values.length === 0) {
    return "";
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const xStep = values.length > 1 ? (width - padding * 2) / (values.length - 1) : 0;
  const yRange = max - min || 1;

  return values
    .map((value, index) => {
      const x = padding + index * xStep;
      const y = height - padding - ((value - min) / yRange) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildAreaPath(values: number[], width: number, height: number, padding: number): string {
  if (values.length === 0) {
    return "";
  }

  const linePath = buildLinePath(values, width, height, padding);
  const xStep = values.length > 1 ? (width - padding * 2) / (values.length - 1) : 0;
  const lastX = padding + (values.length - 1) * xStep;
  const baseY = height - padding;
  return `${linePath} L ${lastX.toFixed(2)} ${baseY.toFixed(2)} L ${padding.toFixed(
    2
  )} ${baseY.toFixed(2)} Z`;
}

type ChartMarker = {
  key: string;
  x: number;
  y: number;
  shape: "circle" | "triangle-up" | "triangle-down";
  stroke: string;
  fill: string;
  label: string;
};

function toTimeValue(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

function buildEventMarkers(
  normalizedPoints: Array<{ ts: string; equity: number }>,
  width: number,
  height: number,
  padding: number,
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
  const xStep = normalizedPoints.length > 1 ? (width - padding * 2) / (normalizedPoints.length - 1) : 0;
  const pointTimes = normalizedPoints.map((point) => toTimeValue(point.ts));
  const occupancy = new Map<string, number>();

  const indexForTime = (rawTs?: string | null) => {
    const target = toTimeValue(rawTs);
    if (target == null) {
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
    const x = padding + index * xStep;
    const y = height - padding - ((normalizedPoints[index].equity - min) / yRange) * (height - padding * 2);
    return { x, y };
  };

  const markers: ChartMarker[] = [];

  signals.forEach((signal) => {
    if (signal.signal === "HOLD") {
      return;
    }
    const index = indexForTime(signal.ts);
    if (index == null) {
      return;
    }
    const stackKey = `signal-${index}`;
    const stack = occupancy.get(stackKey) || 0;
    occupancy.set(stackKey, stack + 1);
    const position = pointPosition(index);
    const isBuy = signal.signal === "BUY";
    markers.push({
      key: `signal-${signal.id}`,
      x: position.x,
      y: position.y - 12 - stack * 8,
      shape: "circle",
      stroke: isBuy ? "#2563eb" : "#d97706",
      fill: "#ffffff",
      label: `${signal.signal} signal ${signal.symbol}`,
    });
  });

  transactions.forEach((txn) => {
    const index = indexForTime(txn.ts);
    if (index == null) {
      return;
    }
    const stackKey = `transaction-${index}-${txn.side}`;
    const stack = occupancy.get(stackKey) || 0;
    occupancy.set(stackKey, stack + 1);
    const position = pointPosition(index);
    const isBuy = txn.side === "BUY";
    markers.push({
      key: `transaction-${txn.id}`,
      x: position.x,
      y: position.y + 14 + stack * 10,
      shape: isBuy ? "triangle-up" : "triangle-down",
      stroke: isBuy ? "#16a34a" : "#dc2626",
      fill: isBuy ? "#16a34a" : "#dc2626",
      label: `${txn.side} fill ${txn.symbol}`,
    });
  });

  return markers;
}

function EquityCurveCard({
  points,
  signals,
  transactions,
  initialCash,
}: {
  points: BacktestSnapshotPoint[];
  signals: BacktestSignalOut[];
  transactions: BacktestTransactionOut[];
  initialCash?: number | null;
}) {
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

  const width = 960;
  const height = 260;
  const padding = 18;
  const linePath = buildLinePath(values, width, height, padding);
  const areaPath = buildAreaPath(values, width, height, padding);
  const latestPoint = normalizedPoints[normalizedPoints.length - 1];
  const markers = buildEventMarkers(
    normalizedPoints,
    width,
    height,
    padding,
    signals,
    transactions
  );
  const buySignalCount = signals.filter((item) => item.signal === "BUY").length;
  const sellSignalCount = signals.filter((item) => item.signal === "SELL").length;
  const buyCount = transactions.filter((item) => item.side === "BUY").length;
  const sellCount = transactions.filter((item) => item.side === "SELL").length;

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

      <div
        style={{
          padding: 14,
          borderRadius: 18,
          background: "linear-gradient(180deg, rgba(240,253,250,0.9), rgba(255,255,255,0.95))",
          border: "1px solid rgba(94, 234, 212, 0.28)",
        }}
      >
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: 260 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(15,118,110,0.28)" />
              <stop offset="100%" stopColor="rgba(15,118,110,0.02)" />
            </linearGradient>
          </defs>
          <path d={areaPath} fill="url(#equityFill)" />
          <path
            d={linePath}
            fill="none"
            stroke="#0f766e"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {markers.map((marker) => {
            if (marker.shape === "circle") {
              return (
                <g key={marker.key}>
                  <circle
                    cx={marker.x}
                    cy={marker.y}
                    r="5"
                    fill={marker.fill}
                    stroke={marker.stroke}
                    strokeWidth="2.5"
                  />
                  <title>{marker.label}</title>
                </g>
              );
            }

            const pointsText =
              marker.shape === "triangle-up"
                ? `${marker.x},${marker.y - 6} ${marker.x - 6},${marker.y + 6} ${marker.x + 6},${marker.y + 6}`
                : `${marker.x},${marker.y + 6} ${marker.x - 6},${marker.y - 6} ${marker.x + 6},${marker.y - 6}`;
            return (
              <g key={marker.key}>
                <polygon points={pointsText} fill={marker.fill} stroke={marker.stroke} strokeWidth="1.5" />
                <title>{marker.label}</title>
              </g>
            );
          })}
        </svg>
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
    </section>
  );
}

function TransactionsCard({ transactions }: { transactions: BacktestTransactionOut[] }) {
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
        <Badge tone="info">{transactions.length} trades</Badge>
      </div>

      {transactions.length === 0 ? (
        <div style={emptyStateStyle}>这次 run 还没有写入任何交易记录。</div>
      ) : (
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
              {transactions.map((txn) => {
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
      subtitle="这页把单次 run 的状态、权益曲线、摘要指标、交易明细和最新持仓放在一起，方便你快速复盘。"
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
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.3fr) minmax(320px, 0.85fr)",
              gap: 18,
              alignItems: "start",
              marginBottom: 18,
            }}
          >
            <EquityCurveCard
              points={run.equity_curve}
              signals={run.signals}
              transactions={run.transactions}
              initialCash={run.initial_cash}
            />

            <section style={sectionCardStyle}>
              <div style={{ marginBottom: 16 }}>
                <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>Run 概览</h2>
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
                    background: "#f8fafc",
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
            </section>
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
                      <div style={{ color: "#0f172a", wordBreak: "break-word" }}>{renderValue(value)}</div>
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
                <div style={emptyStateStyle}>当前没有持仓，或者回测结束时已经全部平仓。</div>
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
