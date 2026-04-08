import type { CSSProperties, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useRef, useState } from "react";

import { createBacktest, listBacktests } from "@/api/backtests";
import { listStockBaskets } from "@/api/stock-baskets";
import { listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import { useI18n } from "@/i18n/provider";
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
        border: filled ? "none" : "1px solid rgba(148, 163, 184, 0.16)",
        background: filled ? "#078cad" : "rgba(15, 23, 42, 0.72)",
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

function toDateInputValue(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

const BACKTEST_FORM_DRAFT_STORAGE_KEY = "backtests-page-form-draft-v1";

type BacktestFormDraft = {
  strategyId: string;
  basketId: string;
  startDate: string;
  endDate: string;
  initialCash: number;
  benchmarkSymbol: string;
  commissionBps: number;
  commissionMin: number;
  slippageBps: number;
};

function readBacktestFormDraft(): Partial<BacktestFormDraft> | null {
  if (typeof window === "undefined") {
    return null;
  }

  const raw = window.localStorage.getItem(BACKTEST_FORM_DRAFT_STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const draft: Partial<BacktestFormDraft> = {};

    if (typeof parsed.strategyId === "string") {
      draft.strategyId = parsed.strategyId;
    }
    if (typeof parsed.basketId === "string") {
      draft.basketId = parsed.basketId;
    }
    if (typeof parsed.startDate === "string") {
      draft.startDate = parsed.startDate;
    }
    if (typeof parsed.endDate === "string") {
      draft.endDate = parsed.endDate;
    }
    if (typeof parsed.initialCash === "number" && Number.isFinite(parsed.initialCash)) {
      draft.initialCash = parsed.initialCash;
    }
    if (typeof parsed.benchmarkSymbol === "string") {
      draft.benchmarkSymbol = parsed.benchmarkSymbol;
    }
    if (typeof parsed.commissionBps === "number" && Number.isFinite(parsed.commissionBps)) {
      draft.commissionBps = parsed.commissionBps;
    }
    if (typeof parsed.commissionMin === "number" && Number.isFinite(parsed.commissionMin)) {
      draft.commissionMin = parsed.commissionMin;
    }
    if (typeof parsed.slippageBps === "number" && Number.isFinite(parsed.slippageBps)) {
      draft.slippageBps = parsed.slippageBps;
    }
    return draft;
  } catch {
    return null;
  }
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
        border: "1px solid rgba(71, 85, 105, 0.28)",
        background: "rgba(15, 23, 42, 0.76)",
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
            color: "#f8fafc",
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
          color: "rgba(148, 163, 184, 0.88)",
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
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const restoredStrategyIdRef = useRef<string | null>(null);
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
  const [draftHydrated, setDraftHydrated] = useState(false);

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
    const draft = readBacktestFormDraft();
    if (draft) {
      if (typeof draft.strategyId === "string") {
        restoredStrategyIdRef.current = draft.strategyId;
        setStrategyId(draft.strategyId);
      }
      if (typeof draft.basketId === "string") {
        setBasketId(draft.basketId);
      }
      if (typeof draft.startDate === "string") {
        setStartDate(draft.startDate);
      }
      if (typeof draft.endDate === "string") {
        setEndDate(draft.endDate);
      }
      if (typeof draft.initialCash === "number") {
        setInitialCash(draft.initialCash);
      }
      if (typeof draft.benchmarkSymbol === "string") {
        setBenchmarkSymbol(draft.benchmarkSymbol);
      }
      if (typeof draft.commissionBps === "number") {
        setCommissionBps(draft.commissionBps);
      }
      if (typeof draft.commissionMin === "number") {
        setCommissionMin(draft.commissionMin);
      }
      if (typeof draft.slippageBps === "number") {
        setSlippageBps(draft.slippageBps);
      }
    }
    setDraftHydrated(true);
  }, []);

  useEffect(() => {
    if (!draftHydrated) {
      return undefined;
    }

    let cancelled = false;

    Promise.all([listStrategies(), listBacktests(), listStockBaskets()])
      .then(([strategyItems, runItems, basketItems]) => {
        if (cancelled) {
          return;
        }
        setStrategies(strategyItems);
        setRuns(runItems);
        setBaskets(basketItems);
        const eligibleStrategyItems = strategyItems.filter((item) => item.engine_ready);
        setStrategyId((current) => {
          if (
            preselectedStrategyId
            && eligibleStrategyItems.some((item) => item.id === preselectedStrategyId)
          ) {
            return preselectedStrategyId;
          }
          if (current && eligibleStrategyItems.some((item) => item.id === current)) {
            return current;
          }

          const restoredStrategyId = restoredStrategyIdRef.current;
          if (
            restoredStrategyId
            && eligibleStrategyItems.some((item) => item.id === restoredStrategyId)
          ) {
            return restoredStrategyId;
          }

          const preferred = strategyItems.find(
            (item) => item.engine_ready && item.status === "active"
          );
          return preferred?.id || eligibleStrategyItems[0]?.id || strategyItems[0]?.id || "";
        });
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || (isZh ? "加载回测页面失败" : "Failed to load the backtest page"));
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
  }, [draftHydrated, isZh, preselectedStrategyId]);

  useEffect(() => {
    if (!draftHydrated || typeof window === "undefined") {
      return;
    }

    const draft: BacktestFormDraft = {
      strategyId,
      basketId,
      startDate,
      endDate,
      initialCash,
      benchmarkSymbol,
      commissionBps,
      commissionMin,
      slippageBps,
    };
    window.localStorage.setItem(BACKTEST_FORM_DRAFT_STORAGE_KEY, JSON.stringify(draft));
  }, [
    basketId,
    benchmarkSymbol,
    commissionBps,
    commissionMin,
    draftHydrated,
    endDate,
    initialCash,
    slippageBps,
    startDate,
    strategyId,
  ]);

  useEffect(() => {
    if (loading || !runs.some((run) => run.status === "queued" || run.status === "running")) {
      return;
    }

    let cancelled = false;
    const timer = window.setInterval(() => {
      listBacktests()
        .then((items) => {
          if (!cancelled) {
            setRuns(items);
            setSubmitSuccessRun((current) =>
              current ? items.find((item) => item.id === current.id) || current : null
            );
          }
        })
        .catch(() => {
          // Ignore one-off polling failures and keep the current UI stable.
        });
    }, 4000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [loading, runs]);

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
      setSubmitError(isZh ? "请选择一个策略" : "Please select a strategy");
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
      setSubmitError(err?.message || (isZh ? "发起回测失败" : "Failed to start the backtest"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AppShell
      title={isZh ? "回测工作台" : "Backtest Workspace"}
      subtitle={
        isZh
          ? "从策略库直接挑选 engine-ready 策略，提交一次完整回测，并把 run、交易、净值快照沉淀到后端"
          : "Select an engine-ready strategy from the library, submit a full backtest, and persist runs, transactions, and equity snapshots to the backend."
      }
      actions={
        <>
          {actionLink("/stock-baskets", isZh ? "管理股票库" : "Manage Baskets")}
          {actionLink("/strategies", isZh ? "查看策略库" : "View Strategies")}
          {actionLink("/strategies/new", isZh ? "创建策略" : "Create Strategy")}
          {actionLink("/backtests", isZh ? "刷新回测页" : "Refresh Backtests", true)}
        </>
      }
    >
      {loading ? <p>{isZh ? "加载中..." : "Loading..."}</p> : null}
      {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}

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
              label={isZh ? "可回测策略" : "Backtestable Strategies"}
              value={String(eligibleStrategies.length)}
              hint={
                isZh
                  ? `当前共有 ${summarizeStrategies(strategies).engineReady} 个 engine-ready 策略。回测页默认只建议你挑这部分策略`
                  : `${summarizeStrategies(strategies).engineReady} engine-ready strategies are currently available. The backtest page prioritizes this set by default.`
              }
              accent="#0f766e"
            />
            <MetricCard
              label={isZh ? "累计 Runs" : "Total Runs"}
              value={String(runStats.total)}
              hint={
                isZh
                  ? "每次回测都会生成一条 strategy_run，并把交易与净值快照挂在这条运行记录下"
                  : "Every backtest generates one strategy_run and attaches transactions and equity snapshots to it."
              }
              accent="#2563eb"
            />
            <MetricCard
              label="Completed"
              value={String(runStats.completed)}
              hint={
                isZh
                  ? "完成态的回测数量。下一步很适合接详情页和权益曲线图"
                  : "Number of completed backtests. A strong top-line indicator before drilling into detail pages and equity curves."
              }
              accent="#ca8a04"
            />
            <MetricCard
              label={isZh ? "最近收益" : "Latest Return"}
              value={formatPercent(runStats.latestReturn, 2)}
              hint={
                isZh
                  ? "最近一次已完成回测的总收益率，方便快速判断最近策略迭代有没有明显改善"
                  : "Total return of the most recent completed backtest, useful for quickly checking whether recent strategy iterations improved."
              }
              accent="#b45309"
            />
          </section>

          <section
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: 18,
              alignItems: "start",
            }}
          >
            <section
              style={{
                padding: 22,
                borderRadius: 24,
                border: "1px solid rgba(148, 163, 184, 0.18)",
                background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
                color: "#e2e8f0",
                boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
              }}
            >
              <div
                style={{
                  marginBottom: 16,
                  padding: 18,
                  borderRadius: 20,
                  background:
                    "radial-gradient(circle at top right, rgba(34,197,94,0.08), transparent 32%), linear-gradient(135deg, rgba(8,15,24,0.92), rgba(15,23,42,0.86))",
                  border: "1px solid rgba(71, 85, 105, 0.28)",
                  boxShadow: "inset 0 1px 0 rgba(148, 163, 184, 0.06)",
                }}
              >
                <h2 style={{ margin: "0 0 10px", fontSize: 24, color: "#f8fafc" }}>
                  {isZh ? "发起回测" : "Start Backtest"}
                </h2>
                <p
                  style={{
                    margin: "0 0 12px",
                    color: "rgba(148, 163, 184, 0.88)",
                    lineHeight: 1.6,
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  {isZh
                    ? "这一步会先创建一条 queued run，再由后端在后台继续执行回测"
                    : "This step first creates a queued run, then the backend continues executing the backtest in the background."}
                </p>
                <div
                  style={{
                    display: "grid",
                    gap: 6,
                    color: "rgba(148, 163, 184, 0.88)",
                    fontSize: 13,
                    lineHeight: 1.6,
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  <div>{isZh ? "1. 选择一个 `engine-ready` 的策略" : "1. Choose an `engine-ready` strategy"}</div>
                  <div>{isZh ? "2. 设定回测区间、初始资金和对标基准" : "2. Set the backtest window, starting cash, and benchmark"}</div>
                  <div>{isZh ? "3. 提交后在后台继续运行" : "3. Submit and let it continue in the background"}</div>
                </div>
              </div>

              <form
                onSubmit={handleSubmit}
                style={{ display: "grid", gap: 16 }}
              >
                <section style={formGroupStyle}>
                  <div>
                    <div style={formGroupTitleStyle}>{isZh ? "策略与时间窗口" : "Strategy & Time Window"}</div>
                  </div>
                  <div style={{ display: "grid", gap: 12 }}>
                    {fieldBlock(
                      isZh ? "策略" : "Strategy",
                      isZh
                        ? "选择本次要回测的策略定义。优先选择 active 且 engine-ready 的策略，因为它们已经能被后端引擎直接消费"
                        : "Choose the strategy definition for this run. Prefer active, engine-ready strategies because the backend engine can consume them directly.",
                      <select
                        value={strategyId}
                        onChange={(e) => setStrategyId(e.target.value)}
                        style={inputStyle}
                      >
                        <option value="">{isZh ? "请选择策略" : "Select a strategy"}</option>
                        {eligibleStrategies.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name} ({item.strategy_type} v{item.version})
                          </option>
                        ))}
                      </select>
                    )}

                    {fieldBlock(
                      isZh ? "股票组合" : "Basket",
                      isZh
                        ? "可选。选择已创建的股票池或使用默认组合"
                        : "Optional. Choose an existing basket or keep the strategy's default universe.",
                      <select
                        value={basketId}
                        onChange={(e) => setBasketId(e.target.value)}
                        style={inputStyle}
                      >
                        <option value="">{isZh ? "不覆盖，沿用策略自带股票池" : "Do not override, use the strategy universe"}</option>
                        {activeBaskets.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name} ({item.symbol_count} symbols)
                          </option>
                        ))}
                      </select>
                    )}

                    <div style={responsiveTwoColGridStyle}>
                      {fieldBlock(
                        isZh ? "开始日期" : "Start Date",
                        isZh
                          ? "回测窗口左边界。策略会从这一天开始读取特征和行情，日期越早，样本越长"
                          : "Left boundary of the backtest window. The strategy starts reading features and market data from this date.",
                        <input
                          type="date"
                          value={startDate}
                          onChange={(e) => setStartDate(e.target.value)}
                          style={inputStyle}
                        />,
                        isZh ? "样本起点" : "Sample Start"
                      )}
                      {fieldBlock(
                        isZh ? "结束日期" : "End Date",
                        isZh
                          ? "回测窗口右边界。建议覆盖一个完整市场阶段，这样更容易看清策略在上涨、震荡和回撤时的表现"
                          : "Right boundary of the backtest window. Covering a full market phase usually gives a clearer picture across rallies, ranges, and drawdowns.",
                        <input
                          type="date"
                          value={endDate}
                          onChange={(e) => setEndDate(e.target.value)}
                          style={inputStyle}
                        />,
                        isZh ? "样本终点" : "Sample End"
                      )}
                    </div>
                  </div>
                </section>

                <section style={formGroupStyle}>
                  <div>
                    <div style={formGroupTitleStyle}>{isZh ? "资金与对标" : "Capital & Benchmark"}</div>
                    <div style={formGroupTextStyle}>
                      {isZh
                        ? "这组参数决定你的账户规模，以及回测结果拿谁做参考"
                        : "These parameters define account size and what benchmark the results are compared against."}
                    </div>
                  </div>
                  <div style={responsiveTwoColGridStyle}>
                    {fieldBlock(
                      isZh ? "初始资金" : "Initial Cash",
                      isZh
                        ? "模拟账户在回测开始时拥有的现金。它会直接影响仓位规模、能不能买得起标的，以及最终收益的绝对值"
                        : "Cash available at the start of the simulation. It directly affects position sizing, affordability, and absolute PnL.",
                      <input
                        type="number"
                        min={1}
                        step="1"
                        value={initialCash}
                        onChange={(e) => setInitialCash(Number(e.target.value))}
                        style={inputStyle}
                        placeholder={isZh ? "初始资金" : "Initial cash"}
                      />,
                      isZh ? "账户本金" : "Account Capital"
                    )}
                    {fieldBlock(
                      isZh ? "基准标的" : "Benchmark Symbol",
                      isZh
                        ? "用于做横向比较的 benchmark，例如 `SPY`。当前它主要是被记录在 run 配置里，后续很适合继续接基准收益曲线"
                        : "Benchmark used for comparison, for example `SPY`. Right now it is mainly recorded into the run config and is ready for future benchmark curve work.",
                      <input
                        value={benchmarkSymbol}
                        onChange={(e) => setBenchmarkSymbol(e.target.value.toUpperCase())}
                        style={inputStyle}
                        placeholder={isZh ? "基准，如 SPY" : "Benchmark, for example SPY"}
                      />,
                      isZh ? "表现参照物" : "Reference"
                    )}
                  </div>
                </section>

                <section style={formGroupStyle}>
                  <div>
                    <div style={formGroupTitleStyle}>{isZh ? "交易成本假设" : "Trading Cost Assumptions"}</div>
                    <div style={formGroupTextStyle}>
                      {isZh
                        ? "这组参数不会改变信号逻辑，但会直接影响成交结果和最终净值。设得越保守，结果通常越接近真实交易"
                        : "These settings do not change signal logic, but they directly affect fills and final equity. More conservative values usually get you closer to live trading."}
                    </div>
                  </div>
                  <div style={responsiveThreeColGridStyle}>
                    {fieldBlock(
                      isZh ? "手续费 bps" : "Commission Bps",
                      isZh
                        ? "按成交金额收取的比例费用，单位是基点，1 bps = 0.01%。如果你希望模拟更便宜或更昂贵的交易环境，就调这个值"
                        : "Proportional fee charged on filled notional, measured in basis points where 1 bps = 0.01%.",
                      <input
                        type="number"
                        min={0}
                        step="0.1"
                        value={commissionBps}
                        onChange={(e) => setCommissionBps(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="commission bps"
                      />,
                      isZh ? "比例手续费" : "Proportional Fee"
                    )}
                    {fieldBlock(
                      isZh ? "最低手续费" : "Minimum Commission",
                      isZh
                        ? "即使按比例算出来很低，也至少收取这一笔固定费用。它对小资金、小单量交易的影响会更明显"
                        : "A fixed minimum fee per trade even when the proportional fee is tiny. It matters more for small accounts and small order sizes.",
                      <input
                        type="number"
                        min={0}
                        step="0.1"
                        value={commissionMin}
                        onChange={(e) => setCommissionMin(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="commission min"
                      />,
                      isZh ? "单笔最低收费" : "Per-Trade Minimum"
                    )}
                    {fieldBlock(
                      isZh ? "滑点 bps" : "Slippage Bps",
                      isZh
                        ? "模拟成交价偏离理论价格的幅度。数值越高，表示你越难按理想价格成交，适合用来给回测结果降一点乐观偏差"
                        : "Simulated distance between ideal and actual execution price. Higher values make the backtest less optimistic.",
                      <input
                        type="number"
                        min={0}
                        step="0.1"
                        value={slippageBps}
                        onChange={(e) => setSlippageBps(Number(e.target.value))}
                        style={inputStyle}
                        placeholder="slippage bps"
                      />,
                      isZh ? "成交摩擦" : "Execution Friction"
                    )}
                  </div>
                </section>

                <div
                  style={{
                    padding: 14,
                    borderRadius: 16,
                    background: "rgba(120, 53, 15, 0.18)",
                    border: "1px solid rgba(249, 115, 22, 0.14)",
                    color: "#fed7aa",
                    lineHeight: 1.6,
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    fontSize: 13,
                  }}
                >
                  {isZh
                    ? "当前回测引擎按“当日收盘生成信号，下一交易日收盘成交”的规则执行，所以这组参数更适合先验证策略方向和结果落库，而不是做超精细撮合"
                    : "The current backtest engine generates signals at today's close and fills at the next trading day's close, so these settings are better for validating strategy direction and persistence than ultra-fine execution modeling."}
                </div>

                {selectedStrategy ? (
                  <div
                    style={{
                      padding: 14,
                      borderRadius: 16,
                      background: "rgba(15, 23, 42, 0.76)",
                      color: "rgba(148, 163, 184, 0.88)",
                      lineHeight: 1.6,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    <div style={{ marginBottom: 6, fontWeight: 700, color: "#f8fafc" }}>
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
                      background: "rgba(30, 64, 175, 0.18)",
                      color: "#bfdbfe",
                      lineHeight: 1.6,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    <div style={{ marginBottom: 6, fontWeight: 700, color: "#f8fafc" }}>
                      {isZh ? "已绑定股票组合" : "Selected basket"}: {selectedBasket.name}
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      {selectedBasket.description?.trim() ||
                        (isZh
                          ? "这次回测会使用该组合覆盖策略原有的股票池"
                          : "This backtest will override the strategy's original universe with this basket.")}
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
                      {submitting ? (isZh ? "提交中..." : "Submitting...") : isZh ? "开始回测" : "Start Backtest"}
                </button>
              </form>

              {submitError ? <p style={{ color: "#fda4af", marginTop: 12 }}>{submitError}</p> : null}
              {submitSuccessRun ? (
                <div
                  style={{
                    marginTop: 12,
                    padding: 14,
                    borderRadius: 14,
                    background: "rgba(20, 83, 45, 0.22)",
                    color: "#bbf7d0",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    lineHeight: 1.6,
                  }}
                >
                  <div>
                    {isZh ? "回测任务已提交到后台，当前状态是 " : "The backtest has been submitted. Current status: "}
                    <strong>{submitSuccessRun.status}</strong>
                    {isZh
                      ? ""
                      : ". You can leave this page and the run will continue in the background."}
                  </div>
                  <Link
                    href={`/backtests/${encodeURIComponent(submitSuccessRun.id)}`}
                    style={{
                      color: "#5eead4",
                      textDecoration: "none",
                      fontWeight: 700,
                    }}
                  >
                    {isZh ? "查看本次回测结果" : "View This Backtest"}
                  </Link>
                </div>
              ) : null}
            </section>

            <section
              style={{
                padding: 22,
                borderRadius: 24,
                border: "1px solid rgba(148, 163, 184, 0.18)",
                background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
                color: "#e2e8f0",
                boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
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
                  <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {isZh ? "最近回测" : "Recent Backtests"}
                  </h2>
                  <p
                    style={{
                      margin: 0,
                      color: "rgba(148, 163, 184, 0.88)",
                      lineHeight: 1.6,
                      fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    {isZh
                      ? "先用 run 列表确认回测是否真的落库完成，后面再补详细图表和单次 run 详情"
                      : "Use the run list first to confirm that backtests completed and were persisted, then drill into charts and single-run details."}
                  </p>
                </div>
              </div>

              {runs.length === 0 ? (
                <div
                  style={{
                    padding: 18,
                    borderRadius: 18,
                    background: "rgba(15, 23, 42, 0.76)",
                    color: "rgba(148, 163, 184, 0.88)",
                    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  {isZh
                    ? "还没有回测记录。先从一个 active 且 engine-ready 的策略开始"
                    : "No backtests yet. Start with one active, engine-ready strategy."}
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
                            border: "1px solid rgba(71, 85, 105, 0.28)",
                            background:
                              "radial-gradient(circle at top right, rgba(59,130,246,0.08), transparent 24%), rgba(8, 15, 24, 0.88)",
                            color: "#e2e8f0",
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
                                color: "rgba(148, 163, 184, 0.88)",
                                fontSize: 13,
                              }}
                            >
                              {formatDateTime(run.finished_at || run.requested_at, locale)}
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
                              <div style={{ color: "#64748b", fontSize: 12, fontWeight: 700 }}>
                                {isZh ? "窗口" : "Window"}
                              </div>
                              <div>
                                {run.window_start} {"->"} {run.window_end}
                              </div>
                            </div>
                            <div>
                              <div style={{ color: "#64748b", fontSize: 12, fontWeight: 700 }}>
                                {isZh ? "总收益" : "Total Return"}
                              </div>
                              <div>{formatPercent(totalReturn, 2)}</div>
                            </div>
                            <div>
                              <div style={{ color: "#64748b", fontSize: 12, fontWeight: 700 }}>
                                {isZh ? "最大回撤" : "Max Drawdown"}
                              </div>
                              <div>{formatPercent(maxDrawdown, 2)}</div>
                            </div>
                            <div>
                              <div style={{ color: "#64748b", fontSize: 12, fontWeight: 700 }}>
                                {isZh ? "期末权益" : "Final Equity"}
                              </div>
                              <div>
                                {typeof run.final_equity === "number"
                                  ? run.final_equity.toLocaleString(locale, {
                                      maximumFractionDigits: 2,
                                    })
                                  : "-"}
                              </div>
                            </div>
                          </div>

                          {run.error_message ? (
                            <div
                              style={{
                                color: "#fda4af",
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {run.error_message}
                            </div>
                          ) : (
                            <div
                              style={{
                                color: "#5eead4",
                                fontWeight: 700,
                                fontFamily:
                                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                              }}
                            >
                              {isZh ? "查看详情" : "View Details"}
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
  border: "1px solid rgba(71, 85, 105, 0.34)",
  background: "rgba(8, 15, 24, 0.82)",
  fontSize: 14,
  color: "#e2e8f0",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const formGroupStyle: CSSProperties = {
  display: "grid",
  gap: 12,
};

const formGroupTitleStyle: CSSProperties = {
  marginBottom: 4,
  color: "#f8fafc",
  fontSize: 16,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const formGroupTextStyle: CSSProperties = {
  color: "rgba(148, 163, 184, 0.88)",
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
