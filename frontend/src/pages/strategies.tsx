import Link from "next/link";
import { useEffect, useState } from "react";

import { getStrategyCatalog, listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
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
  const [items, setItems] = useState<StrategyOut[]>([]);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
          setError(err.message || "加载策略失败");
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
  }, []);

  return (
    <AppShell
      title="策略库"
      subtitle="把策略当成长期资产来管理。先过滤、比较、确认哪些定义已经真正具备进入回测和执行链路的条件。"
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
          新建策略
        </Link>
      }
    >
      {loading && <p>加载中...</p>}
      {error && <p style={{ color: "crimson" }}>{error}</p>}

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
              label="全部策略"
              value={String(summarizeStrategies(items).total)}
              hint="策略对象总量。这个数越大，策略库页面越需要强筛选和强比较能力。"
              accent="#0f766e"
            />
            <MetricCard
              label="Draft"
              value={String(summarizeStrategies(items).drafts)}
              hint="还在定义和试错阶段的策略数量，适合继续打磨参数表单与预览能力。"
              accent="#6b7280"
            />
            <MetricCard
              label="Active"
              value={String(summarizeStrategies(items).active)}
              hint="已经进入主要观察范围的策略。后面接回测与 run 列表时，它们会是主入口。"
              accent="#2563eb"
            />
            <MetricCard
              label="Engine Ready"
              value={String(summarizeStrategies(items).engineReady)}
              hint="真正能被引擎直接消费的策略数量，这个数字很适合放在策略库页顶上盯住。"
              accent="#ca8a04"
            />
          </section>

          <section
            style={{
              marginBottom: 18,
              padding: 18,
              borderRadius: 24,
              border: "1px solid rgba(148, 163, 184, 0.18)",
              background: "rgba(255,255,255,0.82)",
              boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
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
                  border: "1px solid #dbe4ee",
                  background: "#fff",
                  fontSize: 14,
                  color: "#0f172a",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索策略名、描述或股票池"
              />

              <select
                style={{
                  padding: 12,
                  borderRadius: 14,
                  border: "1px solid #dbe4ee",
                  background: "#fff",
                  fontSize: 14,
                  color: "#0f172a",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="all">全部状态</option>
                <option value="draft">draft</option>
                <option value="active">active</option>
                <option value="archived">archived</option>
              </select>

              <select
                style={{
                  padding: 12,
                  borderRadius: 14,
                  border: "1px solid #dbe4ee",
                  background: "#fff",
                  fontSize: 14,
                  color: "#0f172a",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="all">全部类型</option>
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
                  border: "1px solid #dbe4ee",
                  background: "#fff",
                  fontSize: 14,
                  color: "#0f172a",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
                value={engineFilter}
                onChange={(e) => setEngineFilter(e.target.value)}
              >
                <option value="all">全部可执行状态</option>
                <option value="ready">仅 engine-ready</option>
                <option value="stored">仅 stored-only</option>
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
                    border: "1px solid rgba(148, 163, 184, 0.18)",
                    background: "rgba(255,255,255,0.82)",
                  }}
                >
                  暂无策略，先去创建一个吧。
                </div>
              );
            }

            return (
              <>
                <div
                  style={{
                    marginBottom: 14,
                    color: "#475569",
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  当前显示 <strong>{filtered.length}</strong> / {items.length} 个策略
                </div>

                {filtered.length === 0 ? (
                  <div
                    style={{
                      padding: 24,
                      borderRadius: 20,
                      border: "1px solid rgba(148, 163, 184, 0.18)",
                      background: "rgba(255,255,255,0.82)",
                    }}
                  >
                    没有符合当前筛选条件的策略，可以放宽一下状态、类型或搜索词。
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
                            padding: 22,
                            borderRadius: 22,
                            border: "1px solid rgba(148, 163, 184, 0.16)",
                            background:
                              "linear-gradient(140deg, rgba(255,248,237,0.96), rgba(255,255,255,0.98))",
                            boxShadow: "0 14px 36px rgba(15, 23, 42, 0.06)",
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
                            }}
                          >
                            <div>
                              <h2 style={{ margin: "0 0 6px", fontSize: 22 }}>{item.name}</h2>
                              <div
                                style={{
                                  color: "#64748b",
                                  fontSize: 14,
                                  fontFamily:
                                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                }}
                              >
                                {getTypeLabel(item.strategy_type, catalog)} · v{item.version}
                              </div>
                            </div>
                            <div
                              style={{
                                color: "#64748b",
                                fontSize: 13,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {formatDateTime(item.created_at)}
                            </div>
                          </div>

                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
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
                              color: "#475569",
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
                              color: "#334155",
                              fontFamily:
                                "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                            }}
                          >
                            <div>股票池: {getUniverseSummary(item)}</div>
                            <div>最大持仓: {maxPositions ?? "-"}</div>
                            <div>
                              单票仓位:
                              {typeof positionSizePct === "number"
                                ? ` ${(positionSizePct * 100).toFixed(0)}%`
                                : " -"}
                            </div>
                            <div>调仓频率: {rebalance || "-"}</div>
                            <div>运行时机: {runAt || "-"}</div>
                            <div>ID: {item.id.slice(0, 8)}...</div>
                          </div>

                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: 12,
                              alignItems: "center",
                              flexWrap: "wrap",
                              paddingTop: 14,
                              borderTop: "1px solid rgba(226, 232, 240, 0.9)",
                            }}
                          >
                            <span
                              style={{
                                color: "#64748b",
                                fontSize: 13,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              更新时间 {formatDateTime(item.updated_at)}
                            </span>
                              <span
                                style={{
                                  color: "#0f766e",
                                  fontSize: 14,
                                  fontWeight: 700,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                              >
                                下一步建议：补策略详情页
                              </span>
                              <Link
                                href={`/strategies/${item.id}`}
                                style={{
                                  color: "#0f172a",
                                  textDecoration: "none",
                                  fontSize: 14,
                                  fontWeight: 700,
                                  fontFamily:
                                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                }}
                              >
                                查看详情
                              </Link>
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
