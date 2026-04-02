import type { CSSProperties, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";

import { getBacktest, listBacktests } from "@/api/backtests";
import {
  getStrategy,
  getStrategyCatalog,
  getStrategyRuntime,
  renameStrategy,
} from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import type { BacktestDetailOut, BacktestRunOut } from "@/types/backtest";
import type {
  StrategyCatalogItem,
  StrategyOut,
  StrategyRuntimeOut,
} from "@/types/strategy";
import {
  formatDateTime,
  formatPercent,
  getStrategyDescription,
  getStrategyFieldNumber,
  getStrategyFieldText,
  getTypeLabel,
  getUniverseSymbols,
} from "@/utils/strategy";

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

function infoRow(label: string, value: string) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "110px minmax(0, 1fr)",
        gap: 12,
        padding: "10px 0",
        borderBottom: "1px solid rgba(226, 232, 240, 0.9)",
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
      }}
    >
      <div style={{ color: "#64748b", fontWeight: 600 }}>{label}</div>
      <div style={{ color: "#0f172a", wordBreak: "break-word" }}>{value}</div>
    </div>
  );
}

function formatCurrency(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function getMetric(summary: Record<string, unknown>, key: string): number | null {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getRunTone(status: string): "neutral" | "success" | "warning" | "info" {
  if (status === "completed") {
    return "success";
  }
  if (status === "failed") {
    return "warning";
  }
  if (status === "running" || status === "queued") {
    return "info";
  }
  return "neutral";
}

function previewSymbols(symbols: string[], limit = 8): string {
  if (symbols.length === 0) {
    return "运行时选择或空";
  }
  if (symbols.length <= limit) {
    return symbols.join(", ");
  }
  return `${symbols.slice(0, limit).join(", ")} ... +${symbols.length - limit}`;
}

function jsonSummary(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "没有可展示的结构化字段";
  }
  const keys = Object.keys(value as Record<string, unknown>);
  if (keys.length === 0) {
    return "当前对象为空";
  }
  const preview = keys.slice(0, 5).join(", ");
  return keys.length > 5 ? `顶层字段 ${keys.length} 个: ${preview} ...` : `顶层字段 ${keys.length} 个: ${preview}`;
}

function sectionCard(title: string, subtitle: string, children: React.ReactNode) {
  return (
    <section
      style={{
        padding: 22,
        borderRadius: 24,
        border: "1px solid rgba(148, 163, 184, 0.18)",
        background: "rgba(255,255,255,0.82)",
        boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{title}</h2>
        <p
          style={{
            margin: 0,
            color: "#64748b",
            lineHeight: 1.6,
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {subtitle}
        </p>
      </div>
      {children}
    </section>
  );
}

export default function StrategyDetailPage() {
  const router = useRouter();
  const strategyId = Array.isArray(router.query.strategyId)
    ? router.query.strategyId[0]
    : router.query.strategyId;

  const [strategy, setStrategy] = useState<StrategyOut | null>(null);
  const [runtime, setRuntime] = useState<StrategyRuntimeOut | null>(null);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [recentRuns, setRecentRuns] = useState<BacktestRunOut[]>([]);
  const [latestRunDetail, setLatestRunDetail] = useState<BacktestDetailOut | null>(null);
  const [showParamsJson, setShowParamsJson] = useState(false);
  const [showRuntimeJson, setShowRuntimeJson] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renameSaving, setRenameSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [runsLoading, setRunsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);

  useEffect(() => {
    if (!router.isReady || !strategyId) {
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setRunsError(null);

    const load = async () => {
      try {
        const [strategyData, runtimeData, catalogData, runs] = await Promise.all([
          getStrategy(strategyId),
          getStrategyRuntime(strategyId),
          getStrategyCatalog(),
          listBacktests(strategyId),
        ]);
        if (cancelled) {
          return;
        }
        setStrategy(strategyData);
        setRuntime(runtimeData);
        setRenameValue(strategyData.name);
        setCatalog(catalogData);
        setRecentRuns(runs.slice(0, 5));

        if (runs.length > 0) {
          setRunsLoading(true);
          try {
            const detail = await getBacktest(runs[0].id);
            if (!cancelled) {
              setLatestRunDetail(detail);
            }
          } catch (runErr: any) {
            if (!cancelled) {
              setLatestRunDetail(null);
              setRunsError(runErr?.message || "加载最近回测详情失败");
            }
          } finally {
            if (!cancelled) {
              setRunsLoading(false);
            }
          }
        } else {
          setLatestRunDetail(null);
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || "加载策略详情失败");
          setRecentRuns([]);
          setLatestRunDetail(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [router.isReady, strategyId]);

  const universeSymbols = useMemo(
    () => (strategy ? getUniverseSymbols(strategy) : []),
    [strategy]
  );
  const latestSignalPreview = useMemo(
    () =>
      latestRunDetail?.signals
        ? latestRunDetail.signals.slice(-3).reverse()
        : [],
    [latestRunDetail]
  );
  const latestTransactionPreview = useMemo(
    () => (latestRunDetail?.transactions ? latestRunDetail.transactions.slice(0, 3) : []),
    [latestRunDetail]
  );

  const handleRename = async (event: FormEvent) => {
    event.preventDefault();
    if (!strategy) {
      return;
    }

    const nextName = renameValue.trim();
    if (!nextName) {
      setRenameError("策略名不能为空");
      return;
    }

    try {
      setRenameSaving(true);
      setRenameError(null);
      const updated = await renameStrategy(strategy.id, { name: nextName });
      setStrategy(updated);
      setRuntime((prev) =>
        prev
          ? {
              ...prev,
              name: updated.name,
            }
          : prev
      );
      setRenameValue(updated.name);
    } catch (err: any) {
      setRenameError(err?.message || "改名失败");
    } finally {
      setRenameSaving(false);
    }
  };

  if (!loading && !error && !strategy) {
    return (
      <AppShell
        title="策略详情"
        subtitle="当前没有找到目标策略，可能是链接失效，或者后端里还不存在这个 ID。"
        actions={actionLink("/strategies", "返回策略库")}
      >
        <p>未找到策略。</p>
      </AppShell>
    );
  }

  return (
    <AppShell
      title={strategy?.name || "策略详情"}
      subtitle="把单个策略的定义、运行时标准化结果和执行条件放在一个页面里，后面接回测和运行记录时就能自然扩展。"
      actions={
        <>
          {actionLink("/strategies", "返回策略库")}
          {actionLink(
            strategy ? `/backtests?strategyId=${encodeURIComponent(strategy.id)}` : "/backtests",
            "用它回测"
          )}
          {actionLink("/strategies/new", "创建新策略", true)}
        </>
      }
    >
      {loading ? <p>加载中...</p> : null}
      {error ? <p style={{ color: "crimson" }}>{error}</p> : null}

      {!loading && !error && strategy ? (
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
              label="策略版本"
              value={`v${strategy.version}`}
              hint="你现在的策略是带版本语义的，详情页非常适合继续扩展成版本比较和复制新版本的入口。"
              accent="#0f766e"
            />
            <MetricCard
              label="股票池规模"
              value={String(universeSymbols.length)}
              hint="当前策略显式配置的手动股票池数量。没有配置时，通常意味着你后续会在运行时决定 universe。"
              accent="#2563eb"
            />
            <MetricCard
              label="最大持仓"
              value={String(getStrategyFieldNumber(strategy, "risk", "max_positions") ?? "-")}
              hint="这个指标和回测的持仓上限、信号选股过程直接相关，适合放在详情页顶上。"
              accent="#ca8a04"
            />
            <MetricCard
              label="单票仓位"
              value={formatPercent(
                getStrategyFieldNumber(strategy, "risk", "position_size_pct"),
                0
              )}
              hint="当前策略的单票 sizing 约束。后面做回测页时，也建议默认从这里自动带出。"
              accent="#b45309"
            />
          </section>

          <section style={{ marginBottom: 18 }}>
            {sectionCard(
              "概览",
              "把策略身份、运行约束和执行检查放在同一块横向总览里，首屏就能把这套策略看完整。",
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1.15fr) minmax(320px, 0.85fr)",
                  gap: 24,
                  alignItems: "start",
                }}
              >
                <div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                    <Badge tone={strategy.engine_ready ? "success" : "warning"}>
                      {strategy.engine_ready ? "engine-ready" : "stored-only"}
                    </Badge>
                    <Badge>{strategy.status}</Badge>
                    <Badge tone="info">{getTypeLabel(strategy.strategy_type, catalog)}</Badge>
                  </div>

                  <div
                    style={{
                      marginBottom: 16,
                      color: "#475569",
                      lineHeight: 1.7,
                      fontFamily:
                        "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    {getStrategyDescription(strategy)}
                  </div>

                  {infoRow("策略 ID", strategy.id)}
                  {infoRow("策略族 Key", strategy.strategy_key)}
                  {infoRow("类型", String(strategy.strategy_type))}
                  {infoRow(
                    "调仓频率",
                    getStrategyFieldText(strategy, "execution", "rebalance") || "-"
                  )}
                  {infoRow(
                    "运行时机",
                    getStrategyFieldText(strategy, "execution", "run_at") || "-"
                  )}
                  {infoRow(
                    "时间框架",
                    getStrategyFieldText(strategy, "execution", "timeframe") || "-"
                  )}
                  {infoRow(
                    "股票池",
                    previewSymbols(universeSymbols, 20)
                  )}
                  {infoRow("创建时间", formatDateTime(strategy.created_at))}
                  {infoRow("更新时间", formatDateTime(strategy.updated_at))}
                </div>

                <div
                  style={{
                    display: "grid",
                    gap: 12,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  <div style={{ display: "grid", gap: 6 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>执行检查</div>
                    <div style={{ color: "#64748b", lineHeight: 1.6 }}>
                      这块不是后端校验的替代，而是让你在前端一眼看懂这个策略是否已具备进入引擎的条件。
                    </div>
                  </div>
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 18,
                      background: strategy.engine_ready ? "#f0fdf4" : "#fffbeb",
                      color: strategy.engine_ready ? "#166534" : "#92400e",
                    }}
                  >
                    引擎可执行: {strategy.engine_ready ? "是" : "否"}
                  </div>
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 18,
                      background:
                        getStrategyFieldText(strategy, "execution", "timeframe") === "1d"
                          ? "#eff6ff"
                          : "#fff7ed",
                      color:
                        getStrategyFieldText(strategy, "execution", "timeframe") === "1d"
                          ? "#1d4ed8"
                          : "#9a3412",
                    }}
                  >
                    时间框架检查:{" "}
                    {getStrategyFieldText(strategy, "execution", "timeframe") || "未设置"}
                  </div>
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 18,
                      background: universeSymbols.length > 0 ? "#f8fafc" : "#fff7ed",
                      color: universeSymbols.length > 0 ? "#334155" : "#9a3412",
                    }}
                  >
                    股票池检查:{" "}
                    {universeSymbols.length > 0
                      ? `已配置 ${universeSymbols.length} 个 symbol`
                      : "当前没有手动股票池，回测可能无法直接运行"}
                  </div>
                </div>
              </div>
            )}
          </section>

          <section id="rename" style={{ marginBottom: 18 }}>
            {sectionCard(
              "改名",
              "第一版改名会直接修改这条策略记录的 name，但只允许改成一个全新的名字，不允许并入另一个已存在名字的版本族。",
              <form
                onSubmit={handleRename}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(260px, 1fr) auto",
                  gap: 12,
                  alignItems: "end",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
              >
                <label style={{ display: "grid", gap: 8 }}>
                  <span style={{ color: "#0f172a", fontWeight: 700 }}>新的策略名</span>
                  <input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    placeholder="输入新的策略名称"
                    style={{
                      padding: 12,
                      borderRadius: 14,
                      border: "1px solid #dbe4ee",
                      background: "#fff",
                      fontSize: 14,
                      color: "#0f172a",
                    }}
                  />
                </label>
                <button
                  type="submit"
                  disabled={renameSaving}
                  style={{
                    padding: "12px 16px",
                    borderRadius: 14,
                    border: "none",
                    background: "#0f766e",
                    color: "#fff",
                    fontWeight: 700,
                    cursor: "pointer",
                    minWidth: 120,
                  }}
                >
                  {renameSaving ? "保存中..." : "保存新名称"}
                </button>
                {renameError ? (
                  <div
                    style={{
                      gridColumn: "1 / -1",
                      color: "crimson",
                      fontSize: 14,
                    }}
                  >
                    {renameError}
                  </div>
                ) : null}
                <div
                  style={{
                    gridColumn: "1 / -1",
                    color: "#64748b",
                    lineHeight: 1.6,
                    fontSize: 14,
                  }}
                >
                  当前规则下，改名不会改变 `strategy_id`、`strategy_key`、`version`、已有回测和 allocation 关联，只会更新展示名称。
                </div>
              </form>
            )}
          </section>

          {sectionCard(
            "运行与回测",
            "这里直接挂接你已经有的 StrategyRun / Signal / Transaction 链路，让策略详情页不只是定义页，也能看到最近跑出来了什么。",
            <div
              style={{
                display: "grid",
                gap: 14,
                fontFamily:
                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              }}
            >
              {recentRuns.length === 0 && !runsLoading ? (
                <div
                  style={{
                    padding: 16,
                    borderRadius: 18,
                    background: "#f8fafc",
                    color: "#475569",
                    lineHeight: 1.7,
                  }}
                >
                  这个策略还没有回测记录。你可以直接点上面的“用它回测”，把最近一次运行结果回挂到这里。
                </div>
              ) : null}

              {recentRuns.length > 0 ? (
                <>
                  <div
                    style={{
                      padding: 16,
                      borderRadius: 20,
                      background: "linear-gradient(135deg, rgba(240,253,250,0.95), rgba(255,255,255,0.96))",
                      border: "1px solid rgba(94, 234, 212, 0.28)",
                      display: "grid",
                      gap: 12,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "flex-start",
                        gap: 12,
                        flexWrap: "wrap",
                      }}
                    >
                      <div style={{ display: "grid", gap: 6 }}>
                        <div style={{ fontSize: 18, fontWeight: 500, color: "#0f172a" }}>
                          最近一次运行
                        </div>
                        <div style={{ color: "#64748b", lineHeight: 1.6, fontWeight: 400 }}>
                          {formatDateTime(recentRuns[0].finished_at || recentRuns[0].updated_at)}
                        </div>
                      </div>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <Badge tone={getRunTone(recentRuns[0].status)}>{recentRuns[0].status}</Badge>
                        <Badge tone="info">v{recentRuns[0].strategy_version}</Badge>
                      </div>
                    </div>

                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
                        gap: 10,
                      }}
                    >
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>总收益</div>
                        <div style={miniValueStyle}>
                          {formatPercent(getMetric(recentRuns[0].summary_metrics, "total_return"), 2)}
                        </div>
                      </div>
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>最大回撤</div>
                        <div style={miniValueStyle}>
                          {formatPercent(getMetric(recentRuns[0].summary_metrics, "max_drawdown"), 2)}
                        </div>
                      </div>
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>终点权益</div>
                        <div style={miniValueStyle}>{formatCurrency(recentRuns[0].final_equity)}</div>
                      </div>
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>交易数</div>
                        <div style={miniValueStyle}>
                          {String(getMetric(recentRuns[0].summary_metrics, "trade_count") ?? "-")}
                        </div>
                      </div>
                    </div>

                    <div
                      style={{
                        display: "grid",
                        gap: 8,
                        color: "#475569",
                        lineHeight: 1.6,
                      }}
                    >
                      <div>
                        区间: {recentRuns[0].window_start || "-"} 到 {recentRuns[0].window_end || "-"}
                      </div>
                      <div>
                        基准: {recentRuns[0].benchmark_symbol || "未设置"}，初始资金{" "}
                        {formatCurrency(recentRuns[0].initial_cash)}
                      </div>
                      {recentRuns[0].basket_name ? <div>股票组合: {recentRuns[0].basket_name}</div> : null}
                    </div>

                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      {actionLink(`/backtests/${encodeURIComponent(recentRuns[0].id)}`, "查看本次回测")}
                      {actionLink(
                        `/backtests?strategyId=${encodeURIComponent(strategy.id)}`,
                        "查看全部记录",
                        true
                      )}
                    </div>
                  </div>

                  <div style={{ display: "grid", gap: 10 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>最近运行记录</div>
                    {recentRuns.map((run) => (
                      <Link
                        key={run.id}
                        href={`/backtests/${encodeURIComponent(run.id)}`}
                        style={{
                          textDecoration: "none",
                          color: "inherit",
                          padding: 14,
                          borderRadius: 18,
                          border: "1px solid rgba(226, 232, 240, 0.9)",
                          background: "rgba(248,250,252,0.82)",
                          display: "grid",
                          gap: 8,
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            gap: 12,
                            flexWrap: "wrap",
                            alignItems: "center",
                          }}
                        >
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                            <Badge tone={getRunTone(run.status)}>{run.status}</Badge>
                            <span style={{ fontWeight: 700, color: "#0f172a" }}>
                              {run.window_start || "-"} {"->"} {run.window_end || "-"}
                            </span>
                          </div>
                          <span style={{ color: "#64748b", fontSize: 13 }}>
                            {formatDateTime(run.finished_at || run.updated_at)}
                          </span>
                        </div>
                        <div
                          style={{
                            display: "grid",
                            gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
                            gap: 8,
                            color: "#475569",
                            fontSize: 14,
                          }}
                        >
                          <div>收益 {formatPercent(getMetric(run.summary_metrics, "total_return"), 2)}</div>
                          <div>回撤 {formatPercent(getMetric(run.summary_metrics, "max_drawdown"), 2)}</div>
                          <div>终点 {formatCurrency(run.final_equity)}</div>
                        </div>
                      </Link>
                    ))}
                  </div>

                  <div style={{ display: "grid", gap: 12 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>最近信号与成交</div>
                    {runsLoading ? (
                      <div style={emptyRunBlockStyle}>正在加载最近一次回测详情...</div>
                    ) : null}
                    {!runsLoading && runsError ? <div style={errorBlockStyle}>{runsError}</div> : null}
                    {!runsLoading && !runsError && latestRunDetail ? (
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                          gap: 12,
                        }}
                      >
                        <div style={eventPanelStyle}>
                          <div style={eventPanelTitleStyle}>最近信号</div>
                          {latestSignalPreview.length === 0 ? (
                            <div style={eventEmptyStyle}>这次运行还没有可展示的 BUY / SELL 信号。</div>
                          ) : (
                            latestSignalPreview.map((signal) => (
                              <div key={signal.id} style={eventRowStyle}>
                                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                                  <Badge tone={signal.signal === "BUY" ? "info" : "warning"}>
                                    {signal.signal}
                                  </Badge>
                                  <strong style={{ color: "#0f172a" }}>{signal.symbol}</strong>
                                </div>
                                <div style={eventMetaStyle}>
                                  {formatDateTime(signal.ts)}{signal.reason ? ` · ${signal.reason}` : ""}
                                </div>
                              </div>
                            ))
                          )}
                        </div>

                        <div style={eventPanelStyle}>
                          <div style={eventPanelTitleStyle}>最近成交</div>
                          {latestTransactionPreview.length === 0 ? (
                            <div style={eventEmptyStyle}>这次运行还没有成交记录。</div>
                          ) : (
                            latestTransactionPreview.map((txn) => (
                              <div key={txn.id} style={eventRowStyle}>
                                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                                  <Badge tone={txn.side === "BUY" ? "success" : "warning"}>
                                    {txn.side}
                                  </Badge>
                                  <strong style={{ color: "#0f172a" }}>{txn.symbol}</strong>
                                </div>
                                <div style={eventMetaStyle}>
                                  {formatDateTime(txn.ts)} · {txn.qty} @ {formatCurrency(txn.price)}
                                </div>
                              </div>
                            ))
                          )}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </>
              ) : null}
            </div>
          )}

          <section
            style={{
              marginTop: 18,
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gap: 18,
              alignItems: "start",
            }}
          >
            {sectionCard(
              "标准化参数",
              "这里展示后端返回的标准化策略参数，也就是你真正持久化并拿去驱动执行逻辑的配置。",
              <div style={{ display: "grid", gap: 14 }}>
                <div style={collapsedSummaryStyle}>
                  <div style={collapsedSummaryTextStyle}>
                    {jsonSummary(strategy.params)}
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowParamsJson((current) => !current)}
                    style={collapseButtonStyle}
                  >
                    {showParamsJson ? "收起详情" : "展开详情"}
                  </button>
                </div>
                {showParamsJson ? (
                  <pre
                    style={{
                      margin: 0,
                      padding: 18,
                      borderRadius: 18,
                      background: "#0f172a",
                      color: "#e2e8f0",
                      overflowX: "auto",
                      fontSize: 13,
                      lineHeight: 1.65,
                    }}
                  >
                    {JSON.stringify(strategy.params, null, 2)}
                  </pre>
                ) : null}
              </div>
            )}

            {sectionCard(
              "Runtime Payload",
              "这里展示 `/runtime` 返回的运行时 payload。以后接引擎、回测和调仓时，前端就可以明确看到后端实际消费的那份结构。",
              <div style={{ display: "grid", gap: 14 }}>
                <div style={collapsedSummaryStyle}>
                  <div style={collapsedSummaryTextStyle}>
                    {jsonSummary(runtime)}
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowRuntimeJson((current) => !current)}
                    style={collapseButtonStyle}
                  >
                    {showRuntimeJson ? "收起详情" : "展开详情"}
                  </button>
                </div>
                {showRuntimeJson ? (
                  <pre
                    style={{
                      margin: 0,
                      padding: 18,
                      borderRadius: 18,
                      background: "#102a43",
                      color: "#f8fafc",
                      overflowX: "auto",
                      fontSize: 13,
                      lineHeight: 1.65,
                    }}
                  >
                    {JSON.stringify(runtime, null, 2)}
                  </pre>
                ) : null}
              </div>
            )}
          </section>
        </>
      ) : null}
    </AppShell>
  );
}

const miniCardStyle: CSSProperties = {
  padding: 12,
  borderRadius: 16,
  background: "rgba(255,255,255,0.78)",
  border: "1px solid rgba(226, 232, 240, 0.8)",
};

const miniLabelStyle: CSSProperties = {
  color: "#64748b",
  fontSize: 12,
  fontWeight: 500,
  marginBottom: 6,
};

const miniValueStyle: CSSProperties = {
  color: "#0f172a",
  fontSize: 20,
  fontWeight: 600,
  lineHeight: 1.2,
};

const emptyRunBlockStyle: CSSProperties = {
  padding: 14,
  borderRadius: 18,
  background: "#f8fafc",
  color: "#475569",
  lineHeight: 1.7,
};

const errorBlockStyle: CSSProperties = {
  padding: 14,
  borderRadius: 18,
  background: "#fff1f2",
  color: "#b91c1c",
  lineHeight: 1.7,
};

const eventPanelStyle: CSSProperties = {
  padding: 14,
  borderRadius: 18,
  background: "rgba(248,250,252,0.82)",
  border: "1px solid rgba(226, 232, 240, 0.9)",
  display: "grid",
  gap: 10,
};

const eventPanelTitleStyle: CSSProperties = {
  color: "#0f172a",
  fontSize: 15,
  fontWeight: 800,
};

const eventEmptyStyle: CSSProperties = {
  color: "#64748b",
  lineHeight: 1.7,
};

const eventRowStyle: CSSProperties = {
  display: "grid",
  gap: 6,
  paddingTop: 10,
  borderTop: "1px solid rgba(226, 232, 240, 0.8)",
};

const eventMetaStyle: CSSProperties = {
  color: "#64748b",
  fontSize: 13,
  lineHeight: 1.6,
};

const collapsedSummaryStyle: CSSProperties = {
  padding: 16,
  borderRadius: 18,
  border: "1px solid rgba(226, 232, 240, 0.9)",
  background: "rgba(248,250,252,0.82)",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};

const collapsedSummaryTextStyle: CSSProperties = {
  color: "#475569",
  lineHeight: 1.7,
  flex: "1 1 280px",
};

const collapseButtonStyle: CSSProperties = {
  border: "1px solid rgba(15, 118, 110, 0.18)",
  background: "rgba(15,118,110,0.1)",
  color: "#0f766e",
  borderRadius: 999,
  padding: "9px 14px",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};
