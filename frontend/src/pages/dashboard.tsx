import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { listBacktests } from "@/api/backtests";
import { getPaperAccountOverview, listPaperAccounts } from "@/api/paper-accounts";
import { listStrategyAllocations } from "@/api/strategy-allocations";
import { getStrategyCatalog, listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import type { BacktestRunOut } from "@/types/backtest";
import type { PaperTradingAccountOverviewOut } from "@/types/paper-account";
import type { StrategyAllocationOut } from "@/types/strategy-allocation";
import type { StrategyCatalogItem, StrategyOut } from "@/types/strategy";
import {
  formatDateTime,
  formatPercent,
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

function getMetric(summary: Record<string, unknown>, key: string): number | null {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseDate(value?: string | null): number {
  if (!value) {
    return 0;
  }
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function cardStyle(accent?: string) {
  return {
    padding: 22,
    borderRadius: 24,
    border: `1px solid ${accent || "rgba(148, 163, 184, 0.18)"}`,
    background: "rgba(255,255,255,0.82)",
    boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
  } as const;
}

function sectionTitle(title: string, subtitle: string, href?: string, linkLabel?: string) {
  return (
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
        <h2 style={{ margin: "0 0 6px", fontSize: 24 }}>{title}</h2>
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
      {href && linkLabel ? (
        <Link
          href={href}
          style={{
            color: "#0f766e",
            textDecoration: "none",
            fontWeight: 700,
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {linkLabel}
        </Link>
      ) : null}
    </div>
  );
}

export default function DashboardPage() {
  const [items, setItems] = useState<StrategyOut[]>([]);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [runs, setRuns] = useState<BacktestRunOut[]>([]);
  const [allocations, setAllocations] = useState<StrategyAllocationOut[]>([]);
  const [paperOverviews, setPaperOverviews] = useState<PaperTradingAccountOverviewOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      listStrategies(),
      getStrategyCatalog(),
      listBacktests(),
      listPaperAccounts(),
      listStrategyAllocations(),
    ])
      .then(async ([strategies, strategyCatalog, backtests, paperAccounts, strategyAllocations]) => {
        const overviews = await Promise.all(
          paperAccounts.map((account) => getPaperAccountOverview(account.id))
        );
        if (cancelled) {
          return;
        }
        setItems(strategies);
        setCatalog(strategyCatalog);
        setRuns(backtests);
        setAllocations(strategyAllocations);
        setPaperOverviews(overviews);
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

  const recentStrategies = useMemo(
    () =>
      [...items]
        .sort((left, right) => parseDate(right.updated_at || right.created_at) - parseDate(left.updated_at || left.created_at))
        .slice(0, 4),
    [items]
  );

  const recentRuns = useMemo(
    () =>
      [...runs]
        .sort((left, right) => parseDate(right.finished_at || right.requested_at) - parseDate(left.finished_at || left.requested_at))
        .slice(0, 5),
    [runs]
  );

  const completedRuns = useMemo(
    () => runs.filter((run) => run.status === "completed"),
    [runs]
  );

  const failedRuns = useMemo(
    () => runs.filter((run) => run.status === "failed"),
    [runs]
  );

  const latestCompletedRun = completedRuns[0] || null;

  const paperPortfolios = useMemo(
    () =>
      paperOverviews
        .flatMap((overview) =>
          overview.portfolios.map((portfolio) => ({
            accountName: overview.account.name,
            ...portfolio,
          }))
        )
        .sort(
          (left, right) =>
            parseDate(right.latest_run_requested_at) - parseDate(left.latest_run_requested_at)
        ),
    [paperOverviews]
  );

  const paperStats = useMemo(() => {
    const activePortfolioCount = paperOverviews.reduce(
      (sum, overview) => sum + overview.active_portfolio_count,
      0
    );
    const activeAllocationCount = paperOverviews.reduce(
      (sum, overview) => sum + overview.active_allocation_count,
      0
    );
    const activeStrategyCount = paperOverviews.reduce(
      (sum, overview) => sum + overview.active_strategy_count,
      0
    );
    return {
      accountCount: paperOverviews.length,
      activePortfolioCount,
      activeAllocationCount,
      activeStrategyCount,
    };
  }, [paperOverviews]);

  const allocationRiskItems = useMemo(() => {
    const totals = new Map<string, number>();
    allocations
      .filter((item) => item.status === "active")
      .forEach((item) => {
        totals.set(item.portfolio_name, (totals.get(item.portfolio_name) || 0) + item.allocation_pct);
      });
    return Array.from(totals.entries())
      .filter(([, total]) => total > 1)
      .map(([portfolioName, total]) => ({
        portfolioName,
        total,
      }))
      .sort((left, right) => right.total - left.total);
  }, [allocations]);

  const storedOnlyActive = useMemo(
    () => items.filter((item) => item.status === "active" && !item.engine_ready),
    [items]
  );

  const quietPortfolios = useMemo(
    () => paperPortfolios.filter((item) => !item.latest_run_requested_at).slice(0, 4),
    [paperPortfolios]
  );

  return (
    <AppShell
      title="Dashboard"
      subtitle="把策略、回测和 paper trading 放到同一个总览里，先看系统现在运行得怎么样，再决定今天要优化哪一段链路。"
      actions={
        <>
          {actionLink("/strategies/new", "创建策略", true)}
          {actionLink("/backtests", "开始回测")}
          {actionLink("/paper-trading", "打开 Paper Trading")}
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
              label="Active 策略"
              value={String(stats.active)}
              hint={`其中 ${stats.engineReady} 个已经 engine-ready，${storedOnlyActive.length} 个 active 策略还只是 stored-only。`}
              accent="#0f766e"
            />
            <MetricCard
              label="已完成回测"
              value={String(completedRuns.length)}
              hint={`失败 ${failedRuns.length} 次。这个数字最能反映最近回测链路是否稳定。`}
              accent="#2563eb"
            />
            <MetricCard
              label="Paper Portfolios"
              value={String(paperStats.activePortfolioCount)}
              hint={`覆盖 ${paperStats.accountCount} 个 paper accounts、${paperStats.activeAllocationCount} 条 active allocations。`}
              accent="#ca8a04"
            />
            <MetricCard
              label="最近完成收益"
              value={formatPercent(getMetric(latestCompletedRun?.summary_metrics || {}, "total_return"), 2)}
              hint="最近一次 completed backtest 的总收益率，适合用来快速判断最近策略迭代是否变好。"
              accent="#b45309"
            />
          </section>

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.15fr) minmax(320px, 0.85fr)",
              gap: 18,
              alignItems: "start",
              marginBottom: 18,
            }}
          >
            <article style={cardStyle()}>
              {sectionTitle(
                "最近回测",
                "先看最近的 run 有没有顺利完成，以及收益、回撤和期末权益有没有明显异常。",
                "/backtests",
                "查看全部"
              )}

              {recentRuns.length === 0 ? (
                <div
                  style={{
                    padding: 18,
                    borderRadius: 18,
                    background: "#f8fafc",
                    color: "#475569",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  还没有回测记录。建议先从一个 active 且 engine-ready 的策略开始。
                </div>
              ) : (
                <div style={{ display: "grid", gap: 14 }}>
                  {recentRuns.map((run) => {
                    const totalReturn = getMetric(run.summary_metrics, "total_return");
                    const maxDrawdown = getMetric(run.summary_metrics, "max_drawdown");
                    return (
                      <Link
                        key={run.id}
                        href={`/backtests/${encodeURIComponent(run.id)}`}
                        style={{ textDecoration: "none", color: "inherit" }}
                      >
                        <article
                          style={{
                            padding: 18,
                            borderRadius: 18,
                            border: "1px solid rgba(226, 232, 240, 0.9)",
                            background:
                              "linear-gradient(135deg, rgba(255,250,240,0.92), rgba(255,255,255,0.96))",
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: 12,
                              alignItems: "flex-start",
                              flexWrap: "wrap",
                              marginBottom: 10,
                            }}
                          >
                            <div>
                              <h3 style={{ margin: "0 0 6px", fontSize: 19 }}>
                                {run.strategy_name || run.strategy_id}
                              </h3>
                              <div
                                style={{
                                  display: "flex",
                                  gap: 8,
                                  flexWrap: "wrap",
                                  fontFamily:
                                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                }}
                              >
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
                            </div>
                            <div
                              style={{
                                color: "#64748b",
                                fontSize: 13,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {formatDateTime(run.finished_at || run.requested_at)}
                            </div>
                          </div>

                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                              gap: 12,
                              fontFamily:
                                "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                            }}
                          >
                            <div>
                              <div style={{ color: "#94a3b8", fontSize: 12 }}>窗口</div>
                              <div>
                                {run.window_start} {"->"} {run.window_end}
                              </div>
                            </div>
                            <div>
                              <div style={{ color: "#94a3b8", fontSize: 12 }}>总收益</div>
                              <div>{formatPercent(totalReturn, 2)}</div>
                            </div>
                            <div>
                              <div style={{ color: "#94a3b8", fontSize: 12 }}>最大回撤</div>
                              <div>{formatPercent(maxDrawdown, 2)}</div>
                            </div>
                            <div>
                              <div style={{ color: "#94a3b8", fontSize: 12 }}>期末权益</div>
                              <div>
                                {typeof run.final_equity === "number"
                                  ? run.final_equity.toLocaleString("en-US", {
                                      maximumFractionDigits: 2,
                                    })
                                  : "-"}
                              </div>
                            </div>
                          </div>
                        </article>
                      </Link>
                    );
                  })}
                </div>
              )}
            </article>

            <div style={{ display: "grid", gap: 18 }}>
              <article style={cardStyle("rgba(15, 118, 110, 0.12)")}>
                {sectionTitle(
                  "Paper Trading 状态",
                  "这里看每个 paper account 下的 portfolio 最近有没有运行、是否有活跃 allocations，以及哪些组合还没开始跑。",
                  "/paper-trading",
                  "打开工作台"
                )}

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                    gap: 12,
                    marginBottom: 14,
                  }}
                >
                  <div style={miniPanelStyle}>
                    <div style={miniPanelLabelStyle}>Paper Accounts</div>
                    <div style={miniPanelValueStyle}>{paperStats.accountCount}</div>
                  </div>
                  <div style={miniPanelStyle}>
                    <div style={miniPanelLabelStyle}>Active Portfolios</div>
                    <div style={miniPanelValueStyle}>{paperStats.activePortfolioCount}</div>
                  </div>
                  <div style={miniPanelStyle}>
                    <div style={miniPanelLabelStyle}>Active Strategies</div>
                    <div style={miniPanelValueStyle}>{paperStats.activeStrategyCount}</div>
                  </div>
                </div>

                {paperPortfolios.length === 0 ? (
                  <div style={emptyStateStyle}>还没有 paper trading 账户或 portfolio。先去配置虚拟子组合。</div>
                ) : (
                  <div style={{ display: "grid", gap: 12 }}>
                    {paperPortfolios.slice(0, 4).map((portfolio) => (
                      <div
                        key={`${portfolio.paper_account_id}-${portfolio.id}`}
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
                            flexWrap: "wrap",
                          }}
                        >
                          <strong style={{ fontSize: 16 }}>{portfolio.name}</strong>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <Badge>{portfolio.accountName}</Badge>
                            <Badge tone={portfolio.latest_run_status === "completed" ? "success" : "warning"}>
                              {portfolio.latest_run_status || "未运行"}
                            </Badge>
                          </div>
                        </div>
                        <div
                          style={{
                            display: "grid",
                            gap: 6,
                            color: "#475569",
                            fontSize: 14,
                            fontFamily:
                              "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                          }}
                        >
                          <div>active allocations: {portfolio.active_allocation_count}</div>
                          <div>资金占比合计: {(portfolio.active_allocation_pct_total * 100).toFixed(1)}%</div>
                          <div>最近运行: {formatDateTime(portfolio.latest_run_requested_at)}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </article>

              <article style={cardStyle("rgba(234, 88, 12, 0.14)")}>
                {sectionTitle("风险与待办", "把当前最值得先处理的异常集中到一块，避免每天还要翻各个页面找问题。")}

                <div style={{ display: "grid", gap: 12 }}>
                  <div style={riskItemStyle}>
                    <div style={riskTitleStyle}>active 但未 engine-ready</div>
                    <div style={riskValueStyle}>{storedOnlyActive.length} 个</div>
                    <div style={riskBodyStyle}>
                      {storedOnlyActive.length > 0
                        ? storedOnlyActive
                            .slice(0, 3)
                            .map((item) => item.name)
                            .join(", ")
                        : "当前没有这类策略。"}
                    </div>
                  </div>

                  <div style={riskItemStyle}>
                    <div style={riskTitleStyle}>失败回测</div>
                    <div style={riskValueStyle}>{failedRuns.length} 次</div>
                    <div style={riskBodyStyle}>
                      {failedRuns.length > 0
                        ? "建议优先打开最近失败 run 查看 error_message。"
                        : "最近没有失败回测。"}
                    </div>
                  </div>

                  <div style={riskItemStyle}>
                    <div style={riskTitleStyle}>超配 portfolio</div>
                    <div style={riskValueStyle}>{allocationRiskItems.length} 个</div>
                    <div style={riskBodyStyle}>
                      {allocationRiskItems.length > 0
                        ? allocationRiskItems
                            .slice(0, 2)
                            .map((item) => `${item.portfolioName} ${(item.total * 100).toFixed(1)}%`)
                            .join(" / ")
                        : "所有 active allocation 总和都在 100% 以内。"}
                    </div>
                  </div>

                  <div style={riskItemStyle}>
                    <div style={riskTitleStyle}>尚未运行的组合</div>
                    <div style={riskValueStyle}>{quietPortfolios.length} 个</div>
                    <div style={riskBodyStyle}>
                      {quietPortfolios.length > 0
                        ? quietPortfolios.map((item) => item.name).join(", ")
                        : "最近所有 portfolio 都已经有运行记录。"}
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.1fr) minmax(320px, 0.9fr)",
              gap: 18,
              alignItems: "start",
            }}
          >
            <article style={cardStyle()}>
              {sectionTitle(
                "最近策略",
                "用这块快速确认最近在调整哪些策略，以及哪些定义已经达到 engine-ready。",
                "/strategies",
                "全部查看"
              )}

              {recentStrategies.length === 0 ? (
                <div
                  style={{
                    padding: 20,
                    borderRadius: 18,
                    background: "#f8fafc",
                    color: "#475569",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  还没有策略。建议先从一个 trend 策略开始，把创建、落库、展示链路跑通。
                </div>
              ) : (
                <div style={{ display: "grid", gap: 14 }}>
                  {recentStrategies.map((item) => (
                    <Link
                      key={item.id}
                      href={`/strategies/${item.id}`}
                      style={{
                        display: "block",
                        textDecoration: "none",
                        color: "inherit",
                        padding: 18,
                        borderRadius: 18,
                        background:
                          "linear-gradient(135deg, rgba(255,250,240,0.95), rgba(255,255,255,0.95))",
                        border: "1px solid rgba(226, 232, 240, 0.9)",
                        transition: "transform 160ms ease, box-shadow 160ms ease",
                        boxShadow: "0 10px 24px rgba(15, 23, 42, 0.04)",
                        cursor: "pointer",
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
                            <Badge tone="info">{getTypeLabel(item.strategy_type, catalog)}</Badge>
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
                          {formatDateTime(item.updated_at || item.created_at)}
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
                    </Link>
                  ))}
                </div>
              )}
            </article>

            <div style={{ display: "grid", gap: 18 }}>
              <article style={cardStyle()}>
                {sectionTitle("策略模板", "这里展示后端 registry 已经定义好的策略类型，方便你判断后面该重点打磨哪类配置表单。")}

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
                  Today
                </div>
                <h2 style={{ margin: "0 0 10px", fontSize: 24 }}>今天最值得做的动作</h2>
                <p
                  style={{
                    margin: "0 0 16px",
                    color: "rgba(241,245,249,0.82)",
                    lineHeight: 1.7,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  如果你今天只做三件事，最值钱的通常是：挑一个 engine-ready 策略做回测、检查 paper trading
                  组合分配是否合理，再回到策略页调整参数或命名。
                </p>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <Badge tone="info">先看最近回测</Badge>
                  <Badge tone="info">再看 Paper Trading</Badge>
                  <Badge tone="info">最后回到策略配置</Badge>
                </div>
              </article>
            </div>
          </section>
        </>
      ) : null}
    </AppShell>
  );
}

const miniPanelStyle = {
  padding: 14,
  borderRadius: 18,
  background: "#f8fafc",
  border: "1px solid rgba(226, 232, 240, 0.95)",
} as const;

const miniPanelLabelStyle = {
  marginBottom: 6,
  color: "#94a3b8",
  fontSize: 12,
  fontWeight: 700,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const miniPanelValueStyle = {
  color: "#0f172a",
  fontSize: 28,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const riskItemStyle = {
  padding: 14,
  borderRadius: 18,
  background: "#fff7ed",
  border: "1px solid rgba(251, 146, 60, 0.16)",
} as const;

const riskTitleStyle = {
  marginBottom: 4,
  color: "#9a3412",
  fontSize: 13,
  fontWeight: 700,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const riskValueStyle = {
  marginBottom: 6,
  color: "#7c2d12",
  fontSize: 26,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const riskBodyStyle = {
  color: "#7c2d12",
  lineHeight: 1.6,
  fontSize: 14,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const emptyStateStyle = {
  padding: 18,
  borderRadius: 18,
  background: "#f8fafc",
  color: "#475569",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;
