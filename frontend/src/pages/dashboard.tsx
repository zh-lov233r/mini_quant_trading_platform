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
  getTypeLabel,
  getUniverseSummary,
  summarizeStrategies,
} from "@/utils/strategy";

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

export default function DashboardPage() {
  const [items, setItems] = useState<StrategyOut[]>([]);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
          setError(err.message || "加载 dashboard 失败");
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

  const stats = summarizeStrategies(items);
  const recentStrategies = items.slice(0, 5);

  return (
    <AppShell
      title="Dashboard"
      subtitle="先看系统里已经沉淀了什么策略，再决定今天是继续建模、调参，还是开始补回测与执行链路。"
      actions={
        <>
          {actionLink("/strategies/new", "创建策略", true)}
          {actionLink("/strategies", "查看策略库")}
        </>
      }
    >
      {loading ? <p>加载中...</p> : null}
      {error ? <p style={{ color: "crimson" }}>{error}</p> : null}

      {!loading && !error ? (
        <>
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 16,
              marginBottom: 20,
            }}
          >
            <MetricCard
              label="总策略数"
              value={String(stats.total)}
              hint="策略对象已经是你系统里的核心资产。先把策略版本和状态管理清楚，后面的回测与执行就会很顺。"
              accent="#0f766e"
            />
            <MetricCard
              label="Active"
              value={String(stats.active)}
              hint="这些策略最接近被引擎直接消费的状态，适合作为 dashboard 的主关注对象。"
              accent="#2563eb"
            />
            <MetricCard
              label="Engine Ready"
              value={`${stats.engineReady}/${stats.total || 0}`}
              hint="已经具备被后端策略引擎直接加载的策略数量。这个指标最能反映当前系统的可执行程度。"
              accent="#ca8a04"
            />
            <MetricCard
              label="平均股票池"
              value={stats.averageUniverseSize}
              hint={`共有 ${stats.manualUniverse} 个策略显式配置了手动股票池，这个数字能帮你判断前端是否要重点优化 universe 配置体验。`}
              accent="#b45309"
            />
          </section>

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.5fr) minmax(300px, 0.9fr)",
              gap: 18,
              alignItems: "start",
            }}
          >
            <article
              style={{
                padding: 22,
                borderRadius: 24,
                border: "1px solid rgba(148, 163, 184, 0.18)",
                background: "rgba(255,255,255,0.82)",
                boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 12,
                  alignItems: "center",
                  marginBottom: 16,
                  flexWrap: "wrap",
                }}
              >
                <div>
                  <h2 style={{ margin: "0 0 6px", fontSize: 24 }}>最近策略</h2>
                  <p
                    style={{
                      margin: 0,
                      color: "#64748b",
                      lineHeight: 1.6,
                      fontFamily:
                        "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    用这块快速确认最近在调整哪些策略，以及哪些定义已经达到 engine-ready。
                  </p>
                </div>
                <Link
                  href="/strategies"
                  style={{
                    color: "#0f766e",
                    textDecoration: "none",
                    fontWeight: 700,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  全部查看
                </Link>
              </div>

              {recentStrategies.length === 0 ? (
                <div
                  style={{
                    padding: 20,
                    borderRadius: 18,
                    background: "#f8fafc",
                    color: "#475569",
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  还没有策略。建议先从一个 trend 策略开始，把创建、落库、展示链路跑通。
                </div>
              ) : (
                <div style={{ display: "grid", gap: 14 }}>
                  {recentStrategies.map((item) => (
                    <article
                      key={item.id}
                      style={{
                        padding: 18,
                        borderRadius: 18,
                        background:
                          "linear-gradient(135deg, rgba(255,250,240,0.95), rgba(255,255,255,0.95))",
                        border: "1px solid rgba(226, 232, 240, 0.9)",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "flex-start",
                          gap: 12,
                          marginBottom: 10,
                          flexWrap: "wrap",
                        }}
                      >
                        <div>
                          <h3 style={{ margin: "0 0 6px", fontSize: 20 }}>{item.name}</h3>
                          <div
                            style={{
                              display: "flex",
                              gap: 8,
                              flexWrap: "wrap",
                              fontFamily:
                                "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                            }}
                          >
                            <Badge tone="info">
                              {getTypeLabel(item.strategy_type, catalog)}
                            </Badge>
                            <Badge tone={item.engine_ready ? "success" : "warning"}>
                              {item.engine_ready ? "engine-ready" : "stored-only"}
                            </Badge>
                            <Badge>{item.status}</Badge>
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

                      <p
                        style={{
                          margin: "0 0 12px",
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
                          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                          gap: 10,
                          color: "#334155",
                          fontSize: 14,
                          fontFamily:
                            "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                        }}
                      >
                        <div>版本: v{item.version}</div>
                        <div>股票池: {getUniverseSummary(item)}</div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </article>

            <div style={{ display: "grid", gap: 18 }}>
              <article
                style={{
                  padding: 22,
                  borderRadius: 24,
                  border: "1px solid rgba(148, 163, 184, 0.18)",
                  background: "rgba(255,255,255,0.82)",
                  boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
                }}
              >
                <h2 style={{ margin: "0 0 10px", fontSize: 24 }}>策略模板</h2>
                <p
                  style={{
                    margin: "0 0 16px",
                    color: "#64748b",
                    lineHeight: 1.6,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  这里展示后端 registry 已经定义好的策略类型，方便你判断前端后面要重点打磨哪类配置表单。
                </p>

                <div style={{ display: "grid", gap: 12 }}>
                  {catalog.map((item) => {
                    const count = items.filter(
                      (strategy) => strategy.strategy_type === item.strategy_type
                    ).length;

                    return (
                      <div
                        key={item.strategy_type}
                        style={{
                          padding: 14,
                          borderRadius: 18,
                          background: "#f8fafc",
                          border: "1px solid rgba(226, 232, 240, 0.95)",
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            gap: 10,
                            alignItems: "center",
                            marginBottom: 8,
                          }}
                        >
                          <strong style={{ fontSize: 16 }}>{item.label}</strong>
                          <Badge tone={item.engine_ready ? "success" : "warning"}>
                            {count} 个策略
                          </Badge>
                        </div>
                        <p
                          style={{
                            margin: 0,
                            color: "#64748b",
                            lineHeight: 1.6,
                            fontSize: 14,
                            fontFamily:
                              "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                          }}
                        >
                          {item.description}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </article>

              <article
                style={{
                  padding: 22,
                  borderRadius: 24,
                  background: "#102a43",
                  color: "#f8fafc",
                  boxShadow: "0 22px 48px rgba(15, 23, 42, 0.14)",
                }}
              >
                <div
                  style={{
                    marginBottom: 10,
                    fontSize: 12,
                    fontWeight: 700,
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                    color: "#93c5fd",
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  Next Step
                </div>
                <h2 style={{ margin: "0 0 10px", fontSize: 24 }}>下一步最值钱的页面</h2>
                <p
                  style={{
                    margin: "0 0 16px",
                    color: "rgba(241,245,249,0.82)",
                    lineHeight: 1.7,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  当前数据最适合继续补“策略详情”和“回测工作台”。一旦你把 run、signal、transaction、
                  snapshot 这些链路接起来，整个前端会从配置工具升级成真正的量化工作台。
                </p>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <Badge tone="info">先做 Strategy Detail</Badge>
                  <Badge tone="info">再接 Backtests</Badge>
                </div>
              </article>
            </div>
          </section>
        </>
      ) : null}
    </AppShell>
  );
}
