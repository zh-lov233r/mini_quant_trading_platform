import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  deleteStrategy,
  extractStrategyDeleteConflictMessage,
  getStrategyCatalog,
  isStrategyDeleteCloseRequired,
  listStrategies,
} from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import { SelectControl } from "@/components/workspace/SelectControl";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogStack as ContextStack, DialogStat as ContextStat, DialogStats as ContextStats, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import motion from "@/styles/Motion.module.css";
import type { StrategyCatalogItem, StrategyOut, StrategyType } from "@/types/strategy";
import {
  formatDateTime,
  getStrategyDescription,
  getStrategyCategoryPresentation,
  getStrategyFieldNumber,
  getStrategyFieldText,
  getUniverseSummary,
  summarizeStrategies,
} from "@/utils/strategy";

export default function StrategiesPage() {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [items, setItems] = useState<StrategyOut[]>([]);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingStrategyId, setDeletingStrategyId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [engineFilter, setEngineFilter] = useState("all");
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyOut | null>(null);

  const categoryEntries = useMemo(() => {
    const counts = new Map<StrategyType, number>();
    items.forEach((item) => {
      counts.set(item.strategy_type, (counts.get(item.strategy_type) || 0) + 1);
    });

    return catalog.map(({ strategy_type: strategyType }) => {
      return {
        strategyType,
        count: counts.get(strategyType) || 0,
        presentation: getStrategyCategoryPresentation(strategyType, locale),
      };
    });
  }, [catalog, items, locale]);

  const visibleCategoryEntries = categoryEntries.filter((entry) => entry.count > 0);

  useEffect(() => {
    let cancelled = false;

    Promise.all([listStrategies(), getStrategyCatalog()])
      .then(([strategies, strategyCatalog]) => {
        if (cancelled) {
          return;
        }
        setItems(strategies);
        setCatalog(strategyCatalog);
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || (isZh ? "加载策略失败" : "Failed to load strategies"));
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
  }, [isZh]);

  const handleDelete = async (item: StrategyOut) => {
    const confirmed = window.confirm(
      isZh
        ? `确认删除策略 "${item.name}" 吗？与它相关的回测、回测快照、signals、transactions、allocations 以及其它 strategy runs 也会一起删除。这个操作不能撤销。`
        : `Delete strategy "${item.name}"? Its related backtests, backtest snapshots, signals, transactions, allocations, and other strategy runs will be deleted as well. This action cannot be undone.`
    );
    if (!confirmed) {
      return;
    }

    try {
      setDeletingStrategyId(item.id);
      setDeleteError(null);
      await deleteStrategy(item.id);
      setItems((current) => current.filter((candidate) => candidate.id !== item.id));
    } catch (err: any) {
      if (isStrategyDeleteCloseRequired(err?.detail)) {
        const conflictMessage = extractStrategyDeleteConflictMessage(err?.detail);
        const closeConfirmed = window.confirm(
          isZh
            ? `检测到 Alpaca 上还有这条策略的持仓：${conflictMessage}\n\n如果继续删除，系统会先尝试市价平仓，再删除策略。是否继续？`
            : `Alpaca still has open positions for this strategy: ${conflictMessage}\n\nIf you continue, the system will try to flatten them first and then delete the strategy. Continue?`
        );
        if (!closeConfirmed) {
          return;
        }

        try {
          await deleteStrategy(item.id, { closePositions: true });
          setItems((current) => current.filter((candidate) => candidate.id !== item.id));
          return;
        } catch (retryErr: any) {
          setDeleteError(
            extractStrategyDeleteConflictMessage(retryErr?.detail) ||
              retryErr?.message ||
              (isZh ? "删除策略失败" : "Failed to delete the strategy")
          );
          return;
        }
      }

      setDeleteError(
        extractStrategyDeleteConflictMessage(err?.detail) ||
          err?.message ||
          (isZh ? "删除策略失败" : "Failed to delete the strategy")
      );
    } finally {
      setDeletingStrategyId(null);
    }
  };

  return (
    <AppShell
      title={isZh ? "策略库" : "Strategy Library"}
      subtitle={
        isZh
          ? "把策略当成长期资产来管理。先过滤、比较、确认哪些定义已经真正具备进入回测和执行链路的条件"
          : "Manage strategies as long-lived assets. Filter, compare, and confirm which definitions are truly ready to enter the backtest and execution pipeline."
      }
      actions={
        <>
          <Link
            href="/strategies/new"
            style={{
              padding: "11px 16px",
              borderRadius: 14,
              background: "#0f766e",
              color: "#fff",
              textDecoration: "none",
              fontWeight: 700,
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            }}
          >
            {isZh ? "新建策略" : "New Strategy"}
          </Link>
          <WorkspaceDialog triggerLabel={isZh ? "筛选概览" : "Filter Summary"} title={isZh ? "筛选与概览" : "Filters & Summary"}>
            <ContextStack>
              <ContextGroup title={isZh ? "策略库存" : "Strategy Inventory"}><ContextStats><ContextStat label={isZh ? "全部策略" : "All strategies"} value={summarizeStrategies(items).total} /><ContextStat label="Draft" value={summarizeStrategies(items).drafts} /><ContextStat label="Active" value={summarizeStrategies(items).active} /><ContextStat label="Engine ready" value={summarizeStrategies(items).engineReady} /></ContextStats></ContextGroup>
              <ContextGroup title={isZh ? "当前筛选" : "Current Filters"}><ContextStats><ContextStat label={isZh ? "搜索" : "Search"} value={search || (isZh ? "全部" : "All")} /><ContextStat label={isZh ? "类别" : "Category"} value={typeFilter} /><ContextStat label={isZh ? "状态" : "Status"} value={statusFilter} /><ContextStat label="Engine" value={engineFilter} /></ContextStats></ContextGroup>
              <ContextGroup title={isZh ? "快速入口" : "Quick Links"}><ContextLinks><ContextLink href="/strategies/new">{isZh ? "新建策略" : "New strategy"}</ContextLink><ContextLink href="/research">{isZh ? "Agent 研究" : "Agent research"}</ContextLink><ContextLink href="/backtests">{isZh ? "回测工作台" : "Backtests"}</ContextLink></ContextLinks></ContextGroup>
            </ContextStack>
          </WorkspaceDialog>
        </>
      }
    >
      {loading && <p>{isZh ? "加载中..." : "Loading..."}</p>}
      {error && <p style={{ color: "#fda4af" }}>{error}</p>}
      {deleteError && <p style={{ color: "#fda4af" }}>{deleteError}</p>}

      {!loading && !error ? (
        <div className={motion.enter}>
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 16,
              marginBottom: 18,
            }}
          >
            <MetricCard
              label={isZh ? "全部策略" : "All Strategies"}
              value={String(summarizeStrategies(items).total)}
              hint={
                isZh
                  ? "策略对象总量。这个数越大，策略库页面越需要强筛选和强比较能力"
                  : "Total number of strategy objects. As this grows, strong filtering and comparison become more important."
              }
              accent="#0f766e"
            />
            <MetricCard
              label="Draft"
              value={String(summarizeStrategies(items).drafts)}
              hint={
                isZh
                  ? "还在定义和试错阶段的策略数量，适合继续打磨参数表单与预览能力"
                  : "Strategies still in definition and experimentation. A good signal for where forms and previews still need polish."
              }
              accent="#6b7280"
            />
            <MetricCard
              label="Active"
              value={String(summarizeStrategies(items).active)}
              hint={
                isZh
                  ? "已经进入主要观察范围的策略。后面接回测与 run 列表时，它们会是主入口"
                  : "Strategies already in the main observation set. They will become the main entry points once backtests and run lists are wired in."
              }
              accent="#2563eb"
            />
            <MetricCard
              label="Engine Ready"
              value={String(summarizeStrategies(items).engineReady)}
              hint={
                isZh
                  ? "真正能被引擎直接消费的策略数量，这个数字很适合放在策略库页顶上盯住"
                  : "Strategies that can be consumed directly by the engine. This is a great top-line number to monitor."
              }
              accent="#ca8a04"
            />
          </section>

          <section
            aria-labelledby="strategy-category-heading"
            style={{
              marginBottom: 18,
              padding: 18,
              borderRadius: 24,
              border: "1px solid rgba(71, 85, 105, 0.3)",
              background: "linear-gradient(135deg, rgba(8,15,24,0.92), rgba(15,23,42,0.82))",
              color: "#e2e8f0",
              boxShadow: "0 18px 44px rgba(2, 6, 23, 0.2)",
            }}
          >
            <div style={{ marginBottom: 14 }}>
              <h2 id="strategy-category-heading" style={{ margin: "0 0 5px", fontSize: 19 }}>
                {isZh ? "策略大类" : "Strategy Categories"}
              </h2>
              <p
                style={{
                  margin: 0,
                  color: "rgba(148, 163, 184, 0.9)",
                  fontSize: 14,
                  lineHeight: 1.6,
                  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
              >
                {isZh
                  ? "用大类快速切换策略视角；数量始终表示策略库中的完整库存。"
                  : "Switch strategy views by category; counts always reflect the full library inventory."}
              </p>
            </div>

            <div
              role="group"
              aria-label={isZh ? "按策略大类筛选" : "Filter by strategy category"}
              style={{ display: "flex", flexWrap: "wrap", gap: 10 }}
            >
              <button
                type="button"
                className={`${motion.control} strategy-category-filter`}
                aria-pressed={typeFilter === "all"}
                onClick={() => setTypeFilter("all")}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 9,
                  padding: "10px 13px",
                  borderRadius: 14,
                  border: typeFilter === "all"
                    ? "1px solid rgba(226, 232, 240, 0.84)"
                    : "1px solid rgba(71, 85, 105, 0.42)",
                  background: typeFilter === "all"
                    ? "rgba(226, 232, 240, 0.14)"
                    : "rgba(8, 15, 24, 0.68)",
                  color: "#f8fafc",
                  cursor: "pointer",
                  fontWeight: 750,
                  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
              >
                <span>{isZh ? "全部策略" : "All Strategies"}</span>
                <span
                  style={{
                    minWidth: 24,
                    padding: "3px 7px",
                    borderRadius: 999,
                    background: "rgba(148, 163, 184, 0.22)",
                    color: "#e2e8f0",
                    fontSize: 12,
                    textAlign: "center",
                  }}
                >
                  {items.length}
                </span>
              </button>

              {visibleCategoryEntries.map((entry) => {
                const selected = typeFilter === entry.strategyType;
                const { accent, accentRgb, label } = entry.presentation;
                return (
                  <button
                    key={entry.strategyType}
                    type="button"
                    className={`${motion.control} strategy-category-filter`}
                    aria-pressed={selected}
                    onClick={() => setTypeFilter(entry.strategyType)}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 9,
                      padding: "10px 13px",
                      borderRadius: 14,
                      border: selected
                        ? `1px solid rgba(${accentRgb}, 0.92)`
                        : `1px solid rgba(${accentRgb}, 0.38)`,
                      background: selected
                        ? `rgba(${accentRgb}, 0.2)`
                        : `rgba(${accentRgb}, 0.08)`,
                      color: selected ? "#f8fafc" : "#e2e8f0",
                      cursor: "pointer",
                      fontWeight: 750,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    <span
                      aria-hidden="true"
                      style={{ width: 8, height: 8, borderRadius: 999, background: accent }}
                    />
                    <span>{label}</span>
                    <span
                      style={{
                        minWidth: 24,
                        padding: "3px 7px",
                        borderRadius: 999,
                        background: `rgba(${accentRgb}, 0.18)`,
                        color: accent,
                        fontSize: 12,
                        textAlign: "center",
                      }}
                    >
                      {entry.count}
                    </span>
                  </button>
                );
              })}
            </div>

            <style jsx>{`
              .strategy-category-filter:focus-visible {
                outline: 3px solid rgba(248, 250, 252, 0.92);
                outline-offset: 3px;
              }
            `}</style>
          </section>

          <section
            style={{
              marginBottom: 18,
              padding: 18,
              borderRadius: 24,
              border: "1px solid rgba(71, 85, 105, 0.3)",
              background: "linear-gradient(180deg, rgba(8,15,24,0.9), rgba(15,23,42,0.86))",
              color: "#e2e8f0",
              boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
            }}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: 12,
              }}
            >
              <input
                style={{
                  padding: 12,
                  borderRadius: 14,
                  border: "1px solid rgba(71, 85, 105, 0.34)",
                  background: "rgba(8, 15, 24, 0.82)",
                  fontSize: 14,
                  color: "#e2e8f0",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={
                  isZh
                    ? "搜索策略名、描述或股票池"
                    : "Search strategy name, description, or universe"
                }
              />

              <SelectControl
                aria-label={isZh ? "策略状态" : "Strategy status"}
                value={statusFilter}
                onValueChange={setStatusFilter}
                options={[
                  { value: "all", label: isZh ? "全部状态" : "All Statuses" },
                  { value: "draft", label: "draft" },
                  { value: "active", label: "active" },
                  { value: "archived", label: "archived" },
                ]}
              />

              <SelectControl
                aria-label={isZh ? "策略大类" : "Strategy category"}
                value={typeFilter}
                onValueChange={setTypeFilter}
                options={[
                  { value: "all", label: isZh ? "全部类型" : "All Types" },
                  ...categoryEntries.map((entry) => ({
                    value: entry.strategyType,
                    label: entry.presentation.label,
                    accent: entry.presentation.accent,
                  })),
                ]}
              />

              <SelectControl
                aria-label={isZh ? "引擎状态" : "Engine status"}
                value={engineFilter}
                onValueChange={setEngineFilter}
                options={[
                  { value: "all", label: isZh ? "全部可执行状态" : "All Execution States" },
                  { value: "ready", label: isZh ? "仅 engine-ready" : "Engine-ready Only" },
                  { value: "stored", label: isZh ? "仅 stored-only" : "Stored-only Only" },
                ]}
              />
            </div>
          </section>

          {(() => {
            const keyword = search.trim().toLowerCase();
            const filtered = items.filter((item) => {
              if (statusFilter !== "all" && item.status !== statusFilter) {
                return false;
              }
              if (typeFilter !== "all" && item.strategy_type !== typeFilter) {
                return false;
              }
              if (engineFilter === "ready" && !item.engine_ready) {
                return false;
              }
              if (engineFilter === "stored" && item.engine_ready) {
                return false;
              }

              if (!keyword) {
                return true;
              }

              const haystack = [
                item.name,
                getStrategyDescription(item),
                item.strategy_type,
                getUniverseSummary(item),
              ]
                .join(" ")
                .toLowerCase();

              return haystack.includes(keyword);
            });

            if (items.length === 0) {
              return (
                <div
                  style={{
                    padding: 24,
                    borderRadius: 20,
                    border: "1px solid rgba(71, 85, 105, 0.3)",
                    background: "rgba(8, 15, 24, 0.82)",
                    color: "#e2e8f0",
                  }}
                >
                  {isZh ? "暂无策略，先去创建一个吧。" : "No strategies yet. Create one first."}
                </div>
              );
            }

            return (
              <>
                <div
                  style={{
                    marginBottom: 14,
                    color: "rgba(148, 163, 184, 0.88)",
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  {isZh ? "当前显示 " : "Showing "}
                  <strong>{filtered.length}</strong> / {items.length}
                  {isZh ? " 个策略" : " strategies"}
                </div>

                {filtered.length === 0 ? (
                  <div
                  style={{
                    padding: 24,
                    borderRadius: 20,
                    border: "1px solid rgba(71, 85, 105, 0.3)",
                    background: "rgba(8, 15, 24, 0.82)",
                    color: "#e2e8f0",
                  }}
                >
                    {isZh
                      ? "没有符合当前筛选条件的策略，可以放宽一下状态、类型或搜索词。"
                      : "No strategies match the current filters. Try relaxing status, type, or the search term."}
                  </div>
                ) : (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                      gap: 16,
                    }}
                  >
                    {filtered.map((item) => {
                      const maxPositions = getStrategyFieldNumber(
                        item,
                        "risk",
                        "max_positions"
                      );
                      const positionSizePct = getStrategyFieldNumber(
                        item,
                        "risk",
                        "position_size_pct"
                      );
                      const rebalance = getStrategyFieldText(
                        item,
                        "execution",
                        "rebalance"
                      );
                      const runAt = getStrategyFieldText(item, "execution", "run_at");
                      const categoryPresentation = getStrategyCategoryPresentation(
                        item.strategy_type,
                        locale
                      );

                      return (
                        <article
                          key={item.id}
                          className={motion.card}
                          style={{
                            ...{ "--workspace-card-accent": categoryPresentation.accentRgb },
                            display: "flex",
                            flexDirection: "column",
                            height: "100%",
                            padding: 22,
                            borderRadius: 22,
                            border: `1px solid rgba(${categoryPresentation.accentRgb}, 0.36)`,
                            background:
                              `radial-gradient(circle at top right, rgba(${categoryPresentation.accentRgb}, 0.16), transparent 34%), linear-gradient(140deg, rgba(8,15,24,0.96), rgba(15,23,42,0.9))`,
                            color: "#e2e8f0",
                            cursor: "pointer",
                            position: "relative",
                            overflow: "hidden",
                          }}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelectedStrategy(item)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedStrategy(item);
                            }
                          }}
                        >
                          <div
                            aria-hidden="true"
                            style={{
                              position: "absolute",
                              inset: "0 0 auto",
                              height: 4,
                              background: categoryPresentation.accent,
                            }}
                          />
                          <div
                            style={{
                              display: "flex",
                              flexDirection: "column",
                              flex: "1 1 auto",
                              minHeight: 0,
                            }}
                          >
                            <div
                              style={{
                                display: "flex",
                                justifyContent: "space-between",
                                gap: 12,
                                alignItems: "flex-start",
                                marginBottom: 12,
                                flexWrap: "wrap",
                                minHeight: 76,
                              }}
                            >
                              <div style={{ minWidth: 0, flex: "1 1 220px" }}>
                                <div
                                  style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 9,
                                    marginBottom: 10,
                                    flexWrap: "wrap",
                                  }}
                                >
                                  <span
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: 7,
                                      color: categoryPresentation.accent,
                                      fontSize: 16,
                                      fontWeight: 800,
                                      letterSpacing: "0.01em",
                                      fontFamily:
                                        "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                    }}
                                  >
                                    <span
                                      aria-hidden="true"
                                      style={{
                                        width: 9,
                                        height: 9,
                                        borderRadius: 999,
                                        background: categoryPresentation.accent,
                                        boxShadow: `0 0 0 4px rgba(${categoryPresentation.accentRgb}, 0.13)`,
                                      }}
                                    />
                                    {categoryPresentation.label}
                                  </span>
                                  <span
                                    style={{
                                      color: "rgba(148, 163, 184, 0.92)",
                                      fontSize: 12,
                                      fontFamily:
                                        "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                                    }}
                                  >
                                    {item.strategy_type} · v{item.version}
                                  </span>
                                </div>
                                <h2
                                  style={{
                                    margin: "0 0 6px",
                                    fontSize: 22,
                                    lineHeight: 1.2,
                                    minHeight: 52,
                                    wordBreak: "break-word",
                                  }}
                                >
                                  {item.name}
                                </h2>
                              </div>
                              <div
                                style={{
                                  color: "rgba(148, 163, 184, 0.88)",
                                  fontSize: 13,
                                  textAlign: "right",
                                  fontFamily:
                                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                }}
                              >
                                {formatDateTime(item.created_at, locale)}
                              </div>
                            </div>

                            <div
                              style={{
                                display: "flex",
                                gap: 8,
                                flexWrap: "wrap",
                                marginBottom: 12,
                                minHeight: 38,
                                alignContent: "flex-start",
                              }}
                            >
                              <Badge tone={item.engine_ready ? "success" : "warning"}>
                                {item.engine_ready ? "engine-ready" : "stored-only"}
                              </Badge>
                              <Badge>{item.status}</Badge>
                            </div>

                            <p
                              style={{
                                minHeight: 72,
                                margin: "0 0 16px",
                                color: "rgba(148, 163, 184, 0.88)",
                                lineHeight: 1.7,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {getStrategyDescription(item)}
                            </p>

                            <div
                              style={{
                                display: "grid",
                                gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                                gap: 10,
                                marginBottom: 16,
                                fontSize: 14,
                                color: "#cbd5e1",
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              <div>{isZh ? "股票池" : "Universe"}: {getUniverseSummary(item)}</div>
                              <div>{isZh ? "最大持仓" : "Max Positions"}: {maxPositions ?? "-"}</div>
                              <div>
                                {isZh ? "单票仓位" : "Position Size"}:
                                {typeof positionSizePct === "number"
                                  ? ` ${(positionSizePct * 100).toFixed(0)}%`
                                  : " -"}
                              </div>
                              <div>{isZh ? "调仓频率" : "Rebalance"}: {rebalance || "-"}</div>
                              <div>{isZh ? "运行时机" : "Run Timing"}: {runAt || "-"}</div>
                              <div>ID: {item.id.slice(0, 8)}...</div>
                            </div>
                          </div>

                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: 12,
                              alignItems: "center",
                              flexWrap: "wrap",
                              marginTop: "auto",
                              paddingTop: 14,
                              borderTop: "1px solid rgba(71, 85, 105, 0.3)",
                            }}
                          >
                            <span
                              style={{
                                color: "rgba(148, 163, 184, 0.88)",
                                fontSize: 13,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {isZh ? "更新时间" : "Updated"} {formatDateTime(item.updated_at, locale)}
                            </span>
                            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                              <Link
                                href={`/strategies/${item.id}`}
                                onClick={(event) => event.stopPropagation()}
                                style={{ color: "#bae6fd", textDecoration: "none", fontSize: 14, fontWeight: 700, whiteSpace: "nowrap" }}
                              >
                                {isZh ? "查看详情" : "View Details"}
                              </Link>
                              <Link
                                href={`/strategies/${item.id}/edit`}
                                onClick={(event) => event.stopPropagation()}
                                style={{
                                  color: "#5eead4",
                                  textDecoration: "none",
                                  fontSize: 14,
                                  fontWeight: 700,
                                  whiteSpace: "nowrap",
                                  fontFamily:
                                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                }}
                              >
                                {isZh ? "编辑策略" : "Edit Strategy"}
                              </Link>
                              <Link
                                href={`/strategies/new?cloneFrom=${encodeURIComponent(item.id)}`}
                                onClick={(event) => event.stopPropagation()}
                                style={{ color: "#a5f3fc", textDecoration: "none", fontSize: 14, fontWeight: 700, whiteSpace: "nowrap" }}
                              >
                                {isZh ? "基于此新建" : "Create From"}
                              </Link>
                              <button
                                className={motion.control}
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void handleDelete(item);
                                }}
                                disabled={deletingStrategyId === item.id}
                                style={{
                                  padding: 0,
                                  border: "none",
                                  background: "transparent",
                                  color: "#fda4af",
                                  textDecoration: "none",
                                  fontSize: 14,
                                  fontWeight: 700,
                                  whiteSpace: "nowrap",
                                  fontFamily:
                                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                  cursor: deletingStrategyId === item.id ? "not-allowed" : "pointer",
                                  opacity: deletingStrategyId === item.id ? 0.7 : 1,
                                }}
                              >
                                {deletingStrategyId === item.id
                                  ? (isZh ? "删除中..." : "Deleting...")
                                  : isZh
                                    ? "删除策略"
                                    : "Delete"}
                              </button>
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                )}
                <WorkspaceDialog
                  open={selectedStrategy != null}
                  onOpenChange={(open) => { if (!open) setSelectedStrategy(null); }}
                  title={selectedStrategy?.name || (isZh ? "策略预览" : "Strategy Preview")}
                >
                  {selectedStrategy ? (
                    <ContextStack>
                      <ContextGroup title={isZh ? "策略身份" : "Strategy Identity"}>
                        <ContextStats>
                          <ContextStat label={isZh ? "技术类型" : "Technical type"} value={selectedStrategy.strategy_type} />
                          <ContextStat label={isZh ? "版本" : "Version"} value={`v${selectedStrategy.version}`} />
                          <ContextStat label={isZh ? "状态" : "Status"} value={selectedStrategy.status} />
                          <ContextStat label="Engine ready" value={selectedStrategy.engine_ready ? (isZh ? "是" : "Yes") : (isZh ? "否" : "No")} />
                          <ContextStat label={isZh ? "股票池" : "Universe"} value={getUniverseSummary(selectedStrategy)} />
                        </ContextStats>
                      </ContextGroup>
                      <ContextGroup title={isZh ? "策略说明" : "Description"}>{getStrategyDescription(selectedStrategy)}</ContextGroup>
                      <ContextLinks>
                        <ContextLink href={`/strategies/${selectedStrategy.id}`}>{isZh ? "打开策略详情" : "Open strategy detail"}</ContextLink>
                        <ContextLink href={`/strategies/${selectedStrategy.id}/edit`}>{isZh ? "编辑策略" : "Edit strategy"}</ContextLink>
                        <ContextLink href={`/strategies/new?cloneFrom=${encodeURIComponent(selectedStrategy.id)}`}>{isZh ? "基于此策略新建" : "Create from this strategy"}</ContextLink>
                        <ContextLink href={`/backtests?strategyId=${selectedStrategy.id}`}>{isZh ? "使用此策略回测" : "Backtest this strategy"}</ContextLink>
                      </ContextLinks>
                    </ContextStack>
                  ) : null}
                </WorkspaceDialog>
              </>
            );
          })()}
        </div>
      ) : null}
    </AppShell>
  );
}
