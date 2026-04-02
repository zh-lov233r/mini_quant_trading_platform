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
import { useI18n } from "@/i18n/provider";
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
      <div style={{ color: "#475569", fontWeight: 600 }}>{label}</div>
      <div style={{ color: "#0f172a", wordBreak: "break-word" }}>{value}</div>
    </div>
  );
}

function formatCurrency(value?: number | null, locale = "en-US"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return new Intl.NumberFormat(locale, {
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

function previewSymbols(symbols: string[], limit = 8, locale = "zh-CN"): string {
  if (symbols.length === 0) {
    return locale === "zh-CN" ? "运行时选择或空" : "Runtime-selected or empty";
  }
  if (symbols.length <= limit) {
    return symbols.join(", ");
  }
  return `${symbols.slice(0, limit).join(", ")} ... +${symbols.length - limit}`;
}

function jsonSummary(value: unknown, locale = "zh-CN"): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return locale === "zh-CN"
      ? "没有可展示的结构化字段"
      : "No structured fields to preview";
  }
  const keys = Object.keys(value as Record<string, unknown>);
  if (keys.length === 0) {
    return locale === "zh-CN" ? "当前对象为空" : "This object is empty";
  }
  const preview = keys.slice(0, 5).join(", ");
  return locale === "zh-CN"
    ? keys.length > 5
      ? `顶层字段 ${keys.length} 个: ${preview} ...`
      : `顶层字段 ${keys.length} 个: ${preview}`
    : keys.length > 5
      ? `${keys.length} top-level fields: ${preview} ...`
      : `${keys.length} top-level fields: ${preview}`;
}

function sectionCard(title: string, subtitle: string, children: React.ReactNode) {
  return (
    <section
      style={{
        padding: 22,
        borderRadius: 24,
        border: "1px solid rgba(148, 163, 184, 0.18)",
        background: "rgba(255,255,255,0.82)",
        color: "#0f172a",
        boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{title}</h2>
        <p
          style={{
            margin: 0,
            color: "#475569",
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
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const strategyId = Array.isArray(router.query.strategyId)
    ? router.query.strategyId[0]
    : router.query.strategyId;

  const [strategy, setStrategy] = useState<StrategyOut | null>(null);
  const [runtime, setRuntime] = useState<StrategyRuntimeOut | null>(null);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [recentRuns, setRecentRuns] = useState<BacktestRunOut[]>([]);
  const [latestRunDetail, setLatestRunDetail] = useState<BacktestDetailOut | null>(null);
  const [showLatestEvents, setShowLatestEvents] = useState(false);
  const [showParamsJson, setShowParamsJson] = useState(false);
  const [showRuntimeJson, setShowRuntimeJson] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renameSuccess, setRenameSuccess] = useState<string | null>(null);
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
    setRunsLoading(false);
    setError(null);
    setRunsError(null);
    setShowLatestEvents(false);
    setLatestRunDetail(null);

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
        setLoading(false);
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || (isZh ? "加载策略详情失败" : "Failed to load strategy detail"));
          setRecentRuns([]);
          setLatestRunDetail(null);
          setLoading(false);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [isZh, router.isReady, strategyId]);

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

  const handleToggleLatestEvents = async () => {
    if (showLatestEvents) {
      setShowLatestEvents(false);
      return;
    }

    setShowLatestEvents(true);
    if (latestRunDetail || recentRuns.length === 0) {
      return;
    }

    try {
      setRunsLoading(true);
      setRunsError(null);
      const detail = await getBacktest(recentRuns[0].id);
      setLatestRunDetail(detail);
    } catch (runErr: any) {
      setLatestRunDetail(null);
      setRunsError(
        runErr?.message || (isZh ? "加载最近回测详情失败" : "Failed to load the latest backtest detail")
      );
    } finally {
      setRunsLoading(false);
    }
  };

  const handleRename = async (event: FormEvent) => {
    event.preventDefault();
    if (!strategy) {
      return;
    }

    const nextName = renameValue.trim();
    if (!nextName) {
      setRenameSuccess(null);
      setRenameError("策略名不能为空");
      if (!isZh) {
        setRenameError("Strategy name cannot be empty");
      }
      return;
    }

    try {
      setRenameSaving(true);
      setRenameError(null);
      setRenameSuccess(null);
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
      setRenameSuccess(isZh ? "策略名称保存成功" : "Strategy name saved successfully");
    } catch (err: any) {
      setRenameSuccess(null);
      setRenameError(err?.message || (isZh ? "改名失败" : "Rename failed"));
    } finally {
      setRenameSaving(false);
    }
  };

  if (!loading && !error && !strategy) {
    return (
      <AppShell
        title={isZh ? "策略详情" : "Strategy Detail"}
        subtitle={
          isZh
            ? "当前没有找到目标策略，可能是链接失效，或者后端里还不存在这个 ID"
            : "The target strategy could not be found. The link may be stale or the backend may not have this ID yet."
        }
        actions={actionLink("/strategies", isZh ? "返回策略库" : "Back To Strategies")}
      >
        <p>{isZh ? "未找到策略。" : "Strategy not found."}</p>
      </AppShell>
    );
  }

  return (
    <AppShell
      title={strategy?.name || "策略详情"}
      subtitle={
        isZh
          ? "把单个策略的定义、运行时标准化结果和执行条件放在一个页面里，后面接回测和运行记录时就能自然扩展"
          : "Keep the strategy definition, normalized runtime payload, and execution readiness in one place so backtests and run history can extend naturally."
      }
      actions={
        <>
          {actionLink("/strategies", isZh ? "返回策略库" : "Back To Strategies")}
          {actionLink(
            strategy ? `/backtests?strategyId=${encodeURIComponent(strategy.id)}` : "/backtests",
            isZh ? "用它回测" : "Backtest It"
          )}
          {actionLink("/strategies/new", isZh ? "创建新策略" : "Create New Strategy", true)}
        </>
      }
    >
      {loading ? <p>{isZh ? "加载中..." : "Loading..."}</p> : null}
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
              label={isZh ? "策略版本" : "Strategy Version"}
              value={`v${strategy.version}`}
              hint={
                isZh
                  ? "你现在的策略是带版本语义的，详情页非常适合继续扩展成版本比较和复制新版本的入口"
                  : "This strategy already carries version semantics, which makes the detail page a natural place for version comparison and cloning later."
              }
              accent="#0f766e"
            />
            <MetricCard
              label={isZh ? "股票池规模" : "Universe Size"}
              value={String(universeSymbols.length)}
              hint={
                isZh
                  ? "当前策略显式配置的手动股票池数量。没有配置时，通常意味着你后续会在运行时决定 universe"
                  : "Number of explicitly configured symbols in the manual universe. If empty, the universe is usually resolved at runtime."
              }
              accent="#2563eb"
            />
            <MetricCard
              label={isZh ? "最大持仓" : "Max Positions"}
              value={String(getStrategyFieldNumber(strategy, "risk", "max_positions") ?? "-")}
              hint={
                isZh
                  ? "这个指标和回测的持仓上限、信号选股过程直接相关，适合放在详情页顶上"
                  : "This metric is directly tied to backtest position limits and signal selection, so it belongs near the top."
              }
              accent="#ca8a04"
            />
            <MetricCard
              label={isZh ? "单票仓位" : "Position Size"}
              value={formatPercent(
                getStrategyFieldNumber(strategy, "risk", "position_size_pct"),
                0
              )}
              hint={
                isZh
                  ? "当前策略的单票 sizing 约束。后面做回测页时，也建议默认从这里自动带出"
                  : "Current single-position sizing constraint. This is also a good candidate for defaulting into backtests later."
              }
              accent="#b45309"
            />
          </section>

          <section style={{ marginBottom: 18 }}>
            {sectionCard(
              isZh ? "概览" : "Overview",
              isZh
                ? "把策略身份、运行约束和执行检查放在同一块横向总览里，首屏就能把这套策略看完整"
                : "Put identity, runtime constraints, and execution checks into one overview so the first screen already explains the strategy clearly.",
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

                  {infoRow(isZh ? "策略 ID" : "Strategy ID", strategy.id)}
                  {infoRow(isZh ? "策略族 Key" : "Strategy Family Key", strategy.strategy_key)}
                  {infoRow(isZh ? "类型" : "Type", String(strategy.strategy_type))}
                  {infoRow(
                    isZh ? "调仓频率" : "Rebalance",
                    getStrategyFieldText(strategy, "execution", "rebalance") || "-"
                  )}
                  {infoRow(
                    isZh ? "运行时机" : "Run Timing",
                    getStrategyFieldText(strategy, "execution", "run_at") || "-"
                  )}
                  {infoRow(
                    isZh ? "时间框架" : "Timeframe",
                    getStrategyFieldText(strategy, "execution", "timeframe") || "-"
                  )}
                  {infoRow(
                    isZh ? "股票池" : "Universe",
                    previewSymbols(universeSymbols, 20, locale)
                  )}
                  {infoRow(isZh ? "创建时间" : "Created At", formatDateTime(strategy.created_at, locale))}
                  {infoRow(isZh ? "更新时间" : "Updated At", formatDateTime(strategy.updated_at, locale))}
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
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>
                      {isZh ? "执行检查" : "Execution Checks"}
                    </div>
                    <div style={{ color: "#475569", lineHeight: 1.6 }}>
                      {isZh
                        ? "这块不是后端校验的替代，而是让你在前端一眼看懂这个策略是否已具备进入引擎的条件。"
                        : "This is not a replacement for backend validation. It is a fast read on whether the strategy is ready for the engine."}
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
                    {isZh ? "引擎可执行" : "Engine Ready"}: {strategy.engine_ready ? (isZh ? "是" : "Yes") : isZh ? "否" : "No"}
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
                    {isZh ? "时间框架检查" : "Timeframe Check"}:{" "}
                    {getStrategyFieldText(strategy, "execution", "timeframe") || (isZh ? "未设置" : "Not set")}
                  </div>
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 18,
                      background: universeSymbols.length > 0 ? "#f8fafc" : "#fff7ed",
                      color: universeSymbols.length > 0 ? "#334155" : "#9a3412",
                    }}
                  >
                    {isZh ? "股票池检查" : "Universe Check"}:{" "}
                    {universeSymbols.length > 0
                      ? isZh
                        ? `已配置 ${universeSymbols.length} 个 symbol`
                        : `${universeSymbols.length} symbols configured`
                      : isZh
                        ? "当前没有手动股票池，回测可能无法直接运行"
                        : "No manual universe is configured, so backtests may not run directly."}
                  </div>
                </div>
              </div>
            )}
          </section>

          <section id="rename" style={{ marginBottom: 18 }}>
            {sectionCard(
              isZh ? "改名" : "Rename",
              isZh
                ? "第一版改名会直接修改这条策略记录的 name，但只允许改成一个全新的名字，不允许并入另一个已存在名字的版本族"
                : "This first rename flow edits the strategy name directly. It must be a brand-new name and cannot merge into another existing version family.",
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
                  <span style={{ color: "#0f172a", fontWeight: 700 }}>
                    {isZh ? "新的策略名" : "New Strategy Name"}
                  </span>
                  <input
                    value={renameValue}
                    onChange={(e) => {
                      setRenameValue(e.target.value);
                      setRenameSuccess(null);
                    }}
                    placeholder={isZh ? "输入新的策略名称" : "Enter a new strategy name"}
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
                  {renameSaving ? (isZh ? "保存中..." : "Saving...") : isZh ? "保存新名称" : "Save New Name"}
                </button>
                {renameSuccess ? (
                  <div
                    style={{
                      gridColumn: "1 / -1",
                      color: "#15803d",
                      fontSize: 14,
                      fontWeight: 600,
                    }}
                  >
                    {renameSuccess}
                  </div>
                ) : null}
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
                    color: "#475569",
                    lineHeight: 1.6,
                    fontSize: 14,
                  }}
                >
                  {isZh
                    ? "当前规则下，改名不会改变 `strategy_id`、`strategy_key`、`version`、已有回测和 allocation 关联，只会更新展示名称。"
                    : "Under the current rules, renaming does not change `strategy_id`, `strategy_key`, `version`, or any existing backtest and allocation links. It only updates the display name."}
                </div>
              </form>
            )}
          </section>

          {sectionCard(
            isZh ? "与之相关的运行与回测" : "Relevant Runs & Backtests",
            isZh ? "" : "",
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
                  {isZh
                    ? "这个策略还没有回测记录。你可以直接点上面的“用它回测”，把最近一次运行结果回挂到这里。"
                    : "This strategy does not have any backtest records yet. Use the backtest action above and the latest run will show up here."}
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
                          {isZh ? "最近一次运行" : "Latest Run"}
                        </div>
                        <div style={{ color: "#475569", lineHeight: 1.6, fontWeight: 400 }}>
                          {formatDateTime(recentRuns[0].finished_at || recentRuns[0].updated_at, locale)}
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
                        <div style={miniLabelStyle}>{isZh ? "总收益" : "Total Return"}</div>
                        <div style={miniValueStyle}>
                          {formatPercent(getMetric(recentRuns[0].summary_metrics, "total_return"), 2)}
                        </div>
                      </div>
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>{isZh ? "最大回撤" : "Max Drawdown"}</div>
                        <div style={miniValueStyle}>
                          {formatPercent(getMetric(recentRuns[0].summary_metrics, "max_drawdown"), 2)}
                        </div>
                      </div>
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>{isZh ? "终点权益" : "Final Equity"}</div>
                        <div style={miniValueStyle}>{formatCurrency(recentRuns[0].final_equity, locale)}</div>
                      </div>
                      <div style={miniCardStyle}>
                        <div style={miniLabelStyle}>{isZh ? "交易数" : "Trade Count"}</div>
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
                        {isZh ? "区间" : "Window"}: {recentRuns[0].window_start || "-"} {isZh ? "到" : "to"} {recentRuns[0].window_end || "-"}
                      </div>
                      <div>
                        {isZh ? "基准" : "Benchmark"}: {recentRuns[0].benchmark_symbol || (isZh ? "未设置" : "Not set")}，{isZh ? "初始资金" : "Initial Cash"}{" "}
                        {formatCurrency(recentRuns[0].initial_cash, locale)}
                      </div>
                      {recentRuns[0].basket_name ? <div>{isZh ? "股票组合" : "Basket"}: {recentRuns[0].basket_name}</div> : null}
                    </div>

                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      {actionLink(`/backtests/${encodeURIComponent(recentRuns[0].id)}`, isZh ? "查看本次回测" : "View This Backtest")}
                      {actionLink(
                        `/backtests?strategyId=${encodeURIComponent(strategy.id)}`,
                        isZh ? "查看全部记录" : "View All Runs",
                        true
                      )}
                    </div>
                  </div>

                  <div style={{ display: "grid", gap: 10 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>
                      {isZh ? "最近运行记录" : "Recent Runs"}
                    </div>
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
                          <span style={{ color: "#475569", fontSize: 13 }}>
                            {formatDateTime(run.finished_at || run.updated_at, locale)}
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
                          <div>{isZh ? "收益" : "Return"} {formatPercent(getMetric(run.summary_metrics, "total_return"), 2)}</div>
                          <div>{isZh ? "回撤" : "Drawdown"} {formatPercent(getMetric(run.summary_metrics, "max_drawdown"), 2)}</div>
                          <div>{isZh ? "终点" : "Final"} {formatCurrency(run.final_equity, locale)}</div>
                        </div>
                      </Link>
                    ))}
                  </div>

                  <div style={{ display: "grid", gap: 12 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 12,
                        flexWrap: "wrap",
                      }}
                    >
                      <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>
                        {isZh ? "最近信号与成交" : "Recent Signals & Transactions"}
                      </div>
                      <button
                        type="button"
                        onClick={handleToggleLatestEvents}
                        style={{
                          padding: "10px 14px",
                          borderRadius: 14,
                          border: "1px solid rgba(15, 118, 110, 0.2)",
                          background: showLatestEvents ? "rgba(240,253,250,0.95)" : "#0f766e",
                          color: showLatestEvents ? "#0f766e" : "#ffffff",
                          fontWeight: 700,
                          cursor: "pointer",
                        }}
                      >
                        {showLatestEvents
                          ? isZh
                            ? "收起详情"
                            : "Collapse"
                          : isZh
                            ? "展开查看"
                            : "Expand"}
                      </button>
                    </div>
                    {!showLatestEvents ? (
                      <div style={emptyRunBlockStyle}>
                        {isZh
                          ? "默认先不加载最近信号与成交，点“展开查看”后再请求最近一次回测详情。"
                          : "Recent signals and transactions stay collapsed by default. Expand this section to fetch the latest backtest detail."}
                      </div>
                    ) : null}
                    {showLatestEvents && runsLoading ? (
                      <div style={emptyRunBlockStyle}>{isZh ? "正在加载最近一次回测详情..." : "Loading the latest backtest detail..."}</div>
                    ) : null}
                    {showLatestEvents && !runsLoading && runsError ? <div style={errorBlockStyle}>{runsError}</div> : null}
                    {showLatestEvents && !runsLoading && !runsError && latestRunDetail ? (
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                          gap: 12,
                        }}
                      >
                        <div style={eventPanelStyle}>
                          <div style={eventPanelTitleStyle}>{isZh ? "最近信号" : "Recent Signals"}</div>
                          {latestSignalPreview.length === 0 ? (
                            <div style={eventEmptyStyle}>{isZh ? "这次运行还没有可展示的 BUY / SELL 信号。" : "This run does not have any visible BUY / SELL signals yet."}</div>
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
                                  {formatDateTime(signal.ts, locale)}{signal.reason ? ` · ${signal.reason}` : ""}
                                </div>
                              </div>
                            ))
                          )}
                        </div>

                        <div style={eventPanelStyle}>
                          <div style={eventPanelTitleStyle}>{isZh ? "最近成交" : "Recent Transactions"}</div>
                          {latestTransactionPreview.length === 0 ? (
                            <div style={eventEmptyStyle}>{isZh ? "这次运行还没有成交记录。" : "This run does not have any transactions yet."}</div>
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
                                  {formatDateTime(txn.ts, locale)} · {txn.qty} @ {formatCurrency(txn.price, locale)}
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
              isZh ? "标准化参数" : "Normalized Params",
              isZh
                ? "这里展示后端返回的标准化策略参数，也就是你真正持久化并拿去驱动执行逻辑的配置"
                : "This shows the normalized strategy params returned by the backend, which are the actual persisted settings driving execution logic.",
              <div style={{ display: "grid", gap: 14 }}>
                <div style={collapsedSummaryStyle}>
                  <div style={collapsedSummaryTextStyle}>
                    {jsonSummary(strategy.params, locale)}
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowParamsJson((current) => !current)}
                    style={collapseButtonStyle}
                  >
                    {showParamsJson ? (isZh ? "收起详情" : "Hide Details") : isZh ? "展开详情" : "Show Details"}
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
              isZh
                ? "这里展示 `/runtime` 返回的运行时 payload。以后接引擎、回测和调仓时，前端就可以明确看到后端实际消费的那份结构"
                : "This shows the payload returned by `/runtime`, so the frontend can see the exact structure consumed by the backend.",
              <div style={{ display: "grid", gap: 14 }}>
                <div style={collapsedSummaryStyle}>
                  <div style={collapsedSummaryTextStyle}>
                    {jsonSummary(runtime, locale)}
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowRuntimeJson((current) => !current)}
                    style={collapseButtonStyle}
                  >
                    {showRuntimeJson ? (isZh ? "收起详情" : "Hide Details") : isZh ? "展开详情" : "Show Details"}
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
  color: "#0f172a",
};

const miniLabelStyle: CSSProperties = {
  color: "#475569",
  fontSize: 12,
  fontWeight: 700,
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
  color: "#0f172a",
  display: "grid",
  gap: 10,
};

const eventPanelTitleStyle: CSSProperties = {
  color: "#0f172a",
  fontSize: 15,
  fontWeight: 800,
};

const eventEmptyStyle: CSSProperties = {
  color: "#475569",
  lineHeight: 1.7,
};

const eventRowStyle: CSSProperties = {
  display: "grid",
  gap: 6,
  paddingTop: 10,
  borderTop: "1px solid rgba(226, 232, 240, 0.8)",
};

const eventMetaStyle: CSSProperties = {
  color: "#475569",
  fontSize: 13,
  lineHeight: 1.6,
};

const collapsedSummaryStyle: CSSProperties = {
  padding: 16,
  borderRadius: 18,
  border: "1px solid rgba(226, 232, 240, 0.9)",
  background: "rgba(248,250,252,0.82)",
  color: "#0f172a",
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
