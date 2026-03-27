import type { CSSProperties, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";

import { createBacktest, listBacktests } from "@/api/backtests";
import { listStockBaskets } from "@/api/stock-baskets";
import { listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import type { BacktestCreate, BacktestRunOut } from "@/types/backtest";
import type { StockBasketOut } from "@/types/stock-basket";
import type { StrategyOut } from "@/types/strategy";
import {
  formatDateTime,
  formatPercent,
  getStrategyDescription,
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

function toDateInputValue(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

function getMetric(summary: Record<string, unknown>, key: string): number | null {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function fieldBlock(
  label: string,
  description: string,
  input: React.ReactNode,
  meta?: string
) {
  return (
    <label
      style={{
        display: "grid",
        gap: 8,
        minWidth: 0,
        width: "100%",
        boxSizing: "border-box",
        padding: 14,
        borderRadius: 18,
        border: "1px solid rgba(226, 232, 240, 0.95)",
        background: "rgba(248, 250, 252, 0.9)",
      }}
    >
      <div
        style={{
          display: "grid",
          gap: 4,
          minWidth: 0,
        }}
      >
        <span
          style={{
            color: "#0f172a",
            fontWeight: 700,
            lineHeight: 1.2,
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {label}
        </span>
        {meta ? (
          <span
            style={{
              color: "#0f766e",
              fontSize: 12,
              fontWeight: 700,
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            }}
          >
            {meta}
          </span>
        ) : null}
      </div>
      <span
        style={{
          color: "#64748b",
          lineHeight: 1.6,
          fontSize: 13,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        {description}
      </span>
      {input}
    </label>
  );
}

export default function BacktestsPage() {
  const router = useRouter();
  const preselectedStrategyId = Array.isArray(router.query.strategyId)
    ? router.query.strategyId[0]
    : router.query.strategyId;

  const [strategies, setStrategies] = useState<StrategyOut[]>([]);
  const [baskets, setBaskets] = useState<StockBasketOut[]>([]);
  const [runs, setRuns] = useState<BacktestRunOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccessRun, setSubmitSuccessRun] = useState<BacktestRunOut | null>(null);

  const [strategyId, setStrategyId] = useState("");
  const [basketId, setBasketId] = useState("");
  const [startDate, setStartDate] = useState(toDateInputValue(new Date("2024-01-01")));
  const [endDate, setEndDate] = useState(toDateInputValue(new Date("2024-12-31")));
  const [initialCash, setInitialCash] = useState(100000);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState("SPY");
  const [commissionBps, setCommissionBps] = useState(1);
  const [commissionMin, setCommissionMin] = useState(1);
  const [slippageBps, setSlippageBps] = useState(5);

  useEffect(() => {
    let cancelled = false;

    Promise.all([listStrategies(), listBacktests(), listStockBaskets()])
      .then(([strategyItems, runItems, basketItems]) => {
        if (cancelled) {
          return;
        }
        setStrategies(strategyItems);
        setRuns(runItems);
        setBaskets(basketItems);
        if (preselectedStrategyId) {
          setStrategyId(preselectedStrategyId);
        } else if (strategyItems.length > 0) {
          const preferred = strategyItems.find(
            (item) => item.engine_ready && item.status === "active"
          );
          setStrategyId(preferred?.id || strategyItems[0].id);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || "加载回测页面失败");
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
  }, [preselectedStrategyId]);

  const eligibleStrategies = useMemo(
    () => strategies.filter((item) => item.engine_ready),
    [strategies]
  );

  const selectedStrategy = useMemo(
    () => strategies.find((item) => item.id === strategyId) || null,
    [strategies, strategyId]
  );
  const activeBaskets = useMemo(
    () => baskets.filter((item) => item.status === "active"),
    [baskets]
  );
  const selectedBasket = useMemo(
    () => baskets.find((item) => item.id === basketId) || null,
    [baskets, basketId]
  );

  const runStats = useMemo(() => {
    const completed = runs.filter((run) => run.status === "completed");
    const failed = runs.filter((run) => run.status === "failed");
    const latestReturn = completed.length
      ? getMetric(completed[0].summary_metrics, "total_return")
      : null;
    return {
      total: runs.length,
      completed: completed.length,
      failed: failed.length,
      latestReturn,
    };
  }, [runs]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    setSubmitSuccessRun(null);

    if (!strategyId) {
      setSubmitError("请选择一个策略");
      return;
    }

    const payload: BacktestCreate = {
      strategy_id: strategyId,
      basket_id: basketId || null,
      start_date: startDate,
      end_date: endDate,
      initial_cash: Number(initialCash),
      benchmark_symbol: benchmarkSymbol.trim() || null,
      commission_bps: Number(commissionBps),
      commission_min: Number(commissionMin),
      slippage_bps: Number(slippageBps),
    };

    try {
      setSubmitting(true);
      const run = await createBacktest(payload);
      setRuns((prev) => [run, ...prev.filter((item) => item.id !== run.id)]);
      setSubmitSuccessRun(run);
    } catch (err: any) {
      setSubmitError(err?.message || "发起回测失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AppShell
      title="回测工作台"
      subtitle="从策略库直接挑选 engine-ready 策略，提交一次完整回测，并把 run、交易、净值快照持续沉淀到后端。"
      actions={
        <>
          {actionLink("/stock-baskets", "管理股票库")}
          {actionLink("/strategies", "查看策略库")}
          {actionLink("/strategies/new", "创建策略")}
          {actionLink("/backtests", "刷新回测页", true)}
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
              label="可回测策略"
              value={String(eligibleStrategies.length)}
              hint={`当前共有 ${summarizeStrategies(strategies).engineReady} 个 engine-ready 策略。回测页默认只建议你挑这部分策略。`}
              accent="#0f766e"
            />
            <MetricCard
              label="累计 Runs"
              value={String(runStats.total)}
              hint="每次回测都会生成一条 strategy_run，并把交易与净值快照挂在这条运行记录下。"
              accent="#2563eb"
            />
            <MetricCard
              label="Completed"
              value={String(runStats.completed)}
              hint="完成态的回测数量。下一步很适合接详情页和权益曲线图。"
              accent="#ca8a04"
            />
            <MetricCard
              label="最近收益"
              value={formatPercent(runStats.latestReturn, 2)}
              hint="最近一次已完成回测的总收益率，方便快速判断最近策略迭代有没有明显改善。"
              accent="#b45309"
            />
          </section>

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(320px, 0.95fr) minmax(0, 1.2fr)",
              gap: 18,
              alignItems: "start",
            }}
          >
            <section
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
                  marginBottom: 16,
                  padding: 18,
                  borderRadius: 20,
                  background:
                    "linear-gradient(135deg, rgba(240,253,250,0.95), rgba(255,247,237,0.95))",
                  border: "1px solid rgba(15, 118, 110, 0.12)",
                }}
              >
                <h2 style={{ margin: "0 0 10px", fontSize: 24 }}>发起回测窗口</h2>
                <p
                  style={{
                    margin: "0 0 12px",
                    color: "#64748b",
                    lineHeight: 1.6,
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  这一步会直接调用后端同步回测接口。当前版本适合先验证策略定义、特征表和结果落库链路。
                </p>
                <div
                  style={{
                    display: "grid",
                    gap: 6,
                    color: "#475569",
                    fontSize: 13,
                    lineHeight: 1.6,
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  <div>1. 先选择一个 `engine-ready` 的策略。</div>
                  <div>2. 设定回测区间、初始资金和对标基准。</div>
                  <div>3. 根据你想要的保守程度调整手续费和滑点假设。</div>
                </div>
              </div>

              <form
                onSubmit={handleSubmit}
                style={{ display: "grid", gap: 16 }}
              >
                <section style={formGroupStyle}>
                  <div>
                    <div style={formGroupTitleStyle}>策略与时间窗口</div>
                    <div style={formGroupTextStyle}>
                      这组参数决定“拿哪一个策略”以及“在什么历史区间里回放它”。
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: 12 }}>
                    {fieldBlock(
                      "策略",
                      "选择本次要回测的策略定义。这里建议优先选择 active 且 engine-ready 的策略，因为它们已经能被后端引擎直接消费。",
                      <select
                        value={strategyId}
                        onChange={(e) => setStrategyId(e.target.value)}
                        style={inputStyle}
                      >
                        <option value="">请选择策略</option>
                        {eligibleStrategies.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name} ({item.strategy_type} v{item.version})
                          </option>
                        ))}
                      </select>,
                      "决定用哪套信号逻辑"
                    )}

                    {fieldBlock(
                      "股票组合",
                      "可选。如果你选了股票库里的某个组合，本次回测会用这个组合覆盖策略原本的手动股票池，这样就不用每次重新输入 symbols。",
                      <select
                        value={basketId}
                        onChange={(e) => setBasketId(e.target.value)}
                        style={inputStyle}
                      >
                        <option value="">不覆盖，沿用策略自带股票池</option>
                        {activeBaskets.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name} ({item.symbol_count} symbols)
                          </option>
                        ))}
                      </select>,
                      "回测时绑定股票组合"
                    )}

                    <div style={responsiveTwoColGridStyle}>
                      {fieldBlock(
                        "开始日期",
                        "回测窗口左边界。策略会从这一天开始读取特征和行情，日期越早，样本越长。",
                        <input
                          type="date"
                          value={startDate}
                          onChange={(e) => setStartDate(e.target.value)}
                          style={inputStyle}
                        />,
                        "样本起点"
                      )}
                      {fieldBlock(
                        "结束日期",
                        "回测窗口右边界。建议覆盖一个完整市场阶段，这样更容易看清策略在上涨、震荡和回撤时的表现。",
                        <input
                          type="date"
                          value={endDate}
                          onChange={(e) => setEndDate(e.target.value)}
                          style={inputStyle}
                        />,
                        "样本终点"
                      )}
                    </div>
                  </div>
                </section>

                <section style={formGroupStyle}>
                  <div>
                    <div style={formGroupTitleStyle}>资金与对标</div>
                    <div style={formGroupTextStyle}>
                      这组参数决定你的账户规模，以及回测结果拿谁做参考。
                    </div>
                  </div>
                  <div style={responsiveTwoColGridStyle}>
                    {fieldBlock(
                      "初始资金",
                      "模拟账户在回测开始时拥有的现金。它会直接影响仓位规模、能不能买得起标的，以及最终收益的绝对值。",
                      <input
                        type="number"
                        min={1}
                        step="1"
                        value={initialCash}
                        onChange={(e) => setInitialCash(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="初始资金"
                      />,
                      "账户本金"
                    )}
                    {fieldBlock(
                      "基准标的",
                      "用于做横向比较的 benchmark，例如 `SPY`。当前它主要是被记录在 run 配置里，后续很适合继续接基准收益曲线。",
                      <input
                        value={benchmarkSymbol}
                        onChange={(e) => setBenchmarkSymbol(e.target.value.toUpperCase())}
                        style={inputStyle}
                        placeholder="基准，如 SPY"
                      />,
                      "表现参照物"
                    )}
                  </div>
                </section>

                <section style={formGroupStyle}>
                  <div>
                    <div style={formGroupTitleStyle}>交易成本假设</div>
                    <div style={formGroupTextStyle}>
                      这组参数不会改变信号逻辑，但会直接影响成交结果和最终净值。设得越保守，结果通常越接近真实交易。
                    </div>
                  </div>
                  <div style={responsiveThreeColGridStyle}>
                    {fieldBlock(
                      "手续费 bps",
                      "按成交金额收取的比例费用，单位是基点，1 bps = 0.01%。如果你希望模拟更便宜或更昂贵的交易环境，就调这个值。",
                      <input
                        type="number"
                        min={0}
                        step="0.1"
                        value={commissionBps}
                        onChange={(e) => setCommissionBps(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="commission bps"
                      />,
                      "比例手续费"
                    )}
                    {fieldBlock(
                      "最低手续费",
                      "即使按比例算出来很低，也至少收取这一笔固定费用。它对小资金、小单量交易的影响会更明显。",
                      <input
                        type="number"
                        min={0}
                        step="0.1"
                        value={commissionMin}
                        onChange={(e) => setCommissionMin(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="commission min"
                      />,
                      "单笔最低收费"
                    )}
                    {fieldBlock(
                      "滑点 bps",
                      "模拟成交价偏离理论价格的幅度。数值越高，表示你越难按理想价格成交，适合用来给回测结果降一点乐观偏差。",
                      <input
                        type="number"
                        min={0}
                        step="0.1"
                        value={slippageBps}
                        onChange={(e) => setSlippageBps(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="slippage bps"
                      />,
                      "成交摩擦"
                    )}
                  </div>
                </section>

                <div
                  style={{
                    padding: 14,
                    borderRadius: 16,
                    background: "#fff7ed",
                    border: "1px solid rgba(249, 115, 22, 0.14)",
                    color: "#7c2d12",
                    lineHeight: 1.6,
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    fontSize: 13,
                  }}
                >
                  当前回测引擎按“当日收盘生成信号，下一交易日收盘成交”的规则执行，所以这组参数更适合先验证策略方向和结果落库，而不是做超精细撮合。
                </div>

                {selectedStrategy ? (
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 16,
                      background: "#f8fafc",
                      color: "#475569",
                      lineHeight: 1.6,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    <div style={{ marginBottom: 6, fontWeight: 700, color: "#0f172a" }}>
                      {selectedStrategy.name}
                    </div>
                    <div>{getStrategyDescription(selectedStrategy)}</div>
                  </div>
                ) : null}

                {selectedBasket ? (
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 16,
                      background: "#eff6ff",
                      color: "#475569",
                      lineHeight: 1.6,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    <div style={{ marginBottom: 6, fontWeight: 700, color: "#0f172a" }}>
                      已绑定股票组合: {selectedBasket.name}
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      {selectedBasket.description?.trim() || "这次回测会使用该组合覆盖策略原有的股票池。"}
                    </div>
                    <div style={{ color: "#1d4ed8", fontSize: 13 }}>
                      {selectedBasket.symbol_count} 只股票: {selectedBasket.symbols.slice(0, 8).join(", ")}
                      {selectedBasket.symbol_count > 8 ? ` +${selectedBasket.symbol_count - 8}` : ""}
                    </div>
                  </div>
                ) : null}

                <button
                  type="submit"
                  disabled={submitting}
                  style={{
                    padding: "12px 16px",
                    borderRadius: 14,
                    border: "none",
                    background: "#0f766e",
                    color: "#fff",
                    fontWeight: 700,
                    cursor: "pointer",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  {submitting ? "回测中..." : "开始回测"}
                </button>
              </form>

              {submitError ? <p style={{ color: "crimson", marginTop: 12 }}>{submitError}</p> : null}
              {submitSuccessRun ? (
                <div
                  style={{
                    marginTop: 12,
                    padding: 14,
                    borderRadius: 14,
                    background: "#ecfdf5",
                    color: "#166534",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    lineHeight: 1.6,
                  }}
                >
                  <div>回测已完成，结果已经写入后端。</div>
                  <Link
                    href={`/backtests/${encodeURIComponent(submitSuccessRun.id)}`}
                    style={{
                      color: "#0f766e",
                      textDecoration: "none",
                      fontWeight: 700,
                    }}
                  >
                    查看本次回测结果
                  </Link>
                </div>
              ) : null}
            </section>

            <section
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
                  flexWrap: "wrap",
                  alignItems: "center",
                  marginBottom: 14,
                }}
              >
                <div>
                  <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>最近回测</h2>
                  <p
                    style={{
                      margin: 0,
                      color: "#64748b",
                      lineHeight: 1.6,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    先用 run 列表确认回测是否真的落库完成，后面再补详细图表和单次 run 详情。
                  </p>
                </div>
              </div>

              {runs.length === 0 ? (
                <div
                  style={{
                    padding: 18,
                    borderRadius: 18,
                    background: "#f8fafc",
                    color: "#475569",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  还没有回测记录。先从一个 active 且 engine-ready 的策略开始。
                </div>
              ) : (
                <div style={{ display: "grid", gap: 14 }}>
                  {runs.map((run) => {
                    const totalReturn = getMetric(run.summary_metrics, "total_return");
                    const maxDrawdown = getMetric(run.summary_metrics, "max_drawdown");
                    return (
                      <Link
                        key={run.id}
                        href={`/backtests/${encodeURIComponent(run.id)}`}
                        style={{
                          textDecoration: "none",
                          color: "inherit",
                        }}
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
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                                color: "#64748b",
                                fontSize: 13,
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
                              marginBottom: 10,
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

                          {run.error_message ? (
                            <div
                              style={{
                                color: "crimson",
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {run.error_message}
                            </div>
                          ) : (
                            <div
                              style={{
                                color: "#0f766e",
                                fontWeight: 700,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              查看详情
                            </div>
                          )}
                        </article>
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>
          </section>
        </>
      ) : null}
    </AppShell>
  );
}

const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: 12,
  borderRadius: 14,
  border: "1px solid #dbe4ee",
  background: "#fff",
  fontSize: 14,
  color: "#0f172a",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const formGroupStyle: CSSProperties = {
  display: "grid",
  gap: 12,
};

const formGroupTitleStyle: CSSProperties = {
  marginBottom: 4,
  color: "#0f172a",
  fontSize: 16,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const formGroupTextStyle: CSSProperties = {
  color: "#64748b",
  lineHeight: 1.6,
  fontSize: 13,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const responsiveTwoColGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 12,
  alignItems: "start",
};

const responsiveThreeColGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 12,
  alignItems: "start",
};
