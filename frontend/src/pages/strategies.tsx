import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";

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
import { useI18n } from "@/i18n/provider";
import type { StrategyCatalogItem, StrategyOut } from "@/types/strategy";
import {
  formatDateTime,
  getStrategyDescription,
  getStrategyFieldNumber,
  getStrategyFieldText,
  getTypeLabel,
  getUniverseSummary,
  summarizeStrategies,
} from "@/utils/strategy";

export default function StrategiesPage() {
  const router = useRouter();
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
      }
    >
      {loading && <p>{isZh ? "加载中..." : "Loading..."}</p>}
      {error && <p style={{ color: "#fda4af" }}>{error}</p>}
      {deleteError && <p style={{ color: "#fda4af" }}>{deleteError}</p>}

      {!loading && !error ? (
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
                gridTemplateColumns: "minmax(220px, 2fr) repeat(3, minmax(150px, 1fr))",
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

              <select
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
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="all">{isZh ? "全部状态" : "All Statuses"}</option>
                <option value="draft">draft</option>
                <option value="active">active</option>
                <option value="archived">archived</option>
              </select>

              <select
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
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="all">{isZh ? "全部类型" : "All Types"}</option>
                {catalog.map((item) => (
                  <option key={item.strategy_type} value={item.strategy_type}>
                    {item.label}
                  </option>
                ))}
              </select>

              <select
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
                value={engineFilter}
                onChange={(e) => setEngineFilter(e.target.value)}
              >
                <option value="all">{isZh ? "全部可执行状态" : "All Execution States"}</option>
                <option value="ready">{isZh ? "仅 engine-ready" : "Engine-ready Only"}</option>
                <option value="stored">{isZh ? "仅 stored-only" : "Stored-only Only"}</option>
              </select>
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

                      return (
                        <article
                          key={item.id}
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            height: "100%",
                            padding: 22,
                            borderRadius: 22,
                            border: "1px solid rgba(71, 85, 105, 0.28)",
                            background:
                              "radial-gradient(circle at top right, rgba(45,212,191,0.08), transparent 26%), linear-gradient(140deg, rgba(8,15,24,0.94), rgba(15,23,42,0.9))",
                            color: "#e2e8f0",
                            boxShadow: "0 14px 36px rgba(2, 6, 23, 0.24)",
                            cursor: "pointer",
                          }}
                          role="link"
                          tabIndex={0}
                          onClick={() => router.push(`/strategies/${item.id}`)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              void router.push(`/strategies/${item.id}`);
                            }
                          }}
                        >
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
                                <div
                                  style={{
                                    color: "rgba(148, 163, 184, 0.88)",
                                    fontSize: 14,
                                    minHeight: 20,
                                    fontFamily:
                                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                  }}
                                >
                                  {getTypeLabel(item.strategy_type, catalog)} · v{item.version}
                                </div>
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
                                minHeight: 62,
                                alignContent: "flex-start",
                              }}
                            >
                              <Badge tone={item.engine_ready ? "success" : "warning"}>
                                {item.engine_ready ? "engine-ready" : "stored-only"}
                              </Badge>
                              <Badge>{item.status}</Badge>
                              <Badge tone="info">{item.strategy_type}</Badge>
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
                              <button
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
              </>
            );
          })()}
        </>
      ) : null}
    </AppShell>
  );
}
