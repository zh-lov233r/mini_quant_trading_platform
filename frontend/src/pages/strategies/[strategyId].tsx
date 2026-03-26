import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";

import {
  getStrategy,
  getStrategyCatalog,
  getStrategyRuntime,
} from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!router.isReady || !strategyId) {
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      getStrategy(strategyId),
      getStrategyRuntime(strategyId),
      getStrategyCatalog(),
    ])
      .then(([strategyData, runtimeData, catalogData]) => {
        if (cancelled) {
          return;
        }
        setStrategy(strategyData);
        setRuntime(runtimeData);
        setCatalog(catalogData);
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || "加载策略详情失败");
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
  }, [router.isReady, strategyId]);

  const universeSymbols = useMemo(
    () => (strategy ? getUniverseSymbols(strategy) : []),
    [strategy]
  );

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

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.1fr) minmax(320px, 0.9fr)",
              gap: 18,
              marginBottom: 18,
              alignItems: "start",
            }}
          >
            {sectionCard(
              "概览",
              "先用一块清晰的元信息区域，把策略的业务身份、当前状态和运行约束读清楚。",
              <>
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
                  universeSymbols.length > 0 ? universeSymbols.join(", ") : "运行时选择或空"
                )}
                {infoRow("创建时间", formatDateTime(strategy.created_at))}
                {infoRow("更新时间", formatDateTime(strategy.updated_at))}
              </>
            )}

            <div style={{ display: "grid", gap: 18 }}>
              {sectionCard(
                "执行检查",
                "这块不是后端校验的替代，而是让你在前端一眼看懂这个策略是否已具备进入引擎的条件。",
                <div
                  style={{
                    display: "grid",
                    gap: 12,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
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
              )}

              {sectionCard(
                "后续模块",
                "你的数据库已经有 StrategyRun / Signal / Transaction / PortfolioSnapshot 这条链路了，后面最自然的扩展就是把这些挂到这个页面。",
                <div
                  style={{
                    display: "grid",
                    gap: 10,
                    color: "#475569",
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  <div>下一步建议 1: 挂接最近回测摘要</div>
                  <div>下一步建议 2: 挂接运行记录列表</div>
                  <div>下一步建议 3: 展示最近信号和成交</div>
                </div>
              )}
            </div>
          </section>

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gap: 18,
              alignItems: "start",
            }}
          >
            {sectionCard(
              "标准化参数",
              "这里展示后端返回的标准化策略参数，也就是你真正持久化并拿去驱动执行逻辑的配置。",
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
            )}

            {sectionCard(
              "Runtime Payload",
              "这里展示 `/runtime` 返回的运行时 payload。以后接引擎、回测和调仓时，前端就可以明确看到后端实际消费的那份结构。",
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
            )}
          </section>
        </>
      ) : null}
    </AppShell>
  );
}
