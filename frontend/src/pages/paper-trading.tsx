import type { CSSProperties, FormEvent } from "react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  createPaperAccount,
  createStrategyPortfolio,
  getPaperAccountOverview,
  listPaperAccounts,
  listStrategyPortfolios,
} from "@/api/paper-accounts";
import {
  createMultiStrategyPaperTradingRun,
  createPaperTradingRun,
} from "@/api/paper-trading";
import { listStockBaskets } from "@/api/stock-baskets";
import {
  listStrategyAllocations,
  upsertStrategyAllocation,
} from "@/api/strategy-allocations";
import { listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import type {
  PaperTradingAccountCreate,
  PaperTradingAccountOut,
  PaperTradingAccountOverviewOut,
  StrategyPortfolioCreate,
  StrategyPortfolioOut,
} from "@/types/paper-account";
import type {
  MultiStrategyPaperTradingRunOut,
  MultiStrategyPaperTradingRunRequest,
  PaperTradingRunOut,
  PaperTradingRunRequest,
} from "@/types/paper-trading";
import type { StockBasketOut } from "@/types/stock-basket";
import type {
  StrategyAllocationOut,
  StrategyAllocationUpsert,
} from "@/types/strategy-allocation";
import type { StrategyOut } from "@/types/strategy";
import {
  formatDateTime,
  formatPercent,
  getStrategyDescription,
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

function toDateInputValue(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

function fieldBlock(
  label: string,
  description: string,
  input: React.ReactNode
) {
  return (
    <label style={fieldBlockStyle}>
      <div style={{ display: "grid", gap: 4 }}>
        <span style={fieldLabelStyle}>{label}</span>
        <span style={fieldDescriptionStyle}>{description}</span>
      </div>
      {input}
    </label>
  );
}

type WorkspaceTab = "accounts" | "allocations" | "execution";

const WORKSPACE_TABS: Array<{
  key: WorkspaceTab;
  label: string;
  description: string;
}> = [
  {
    key: "accounts",
    label: "账户与子组合",
    description: "创建和查看 paper account 与 strategy portfolio。",
  },
  {
    key: "allocations",
    label: "策略组合调整",
    description: "给子组合配置策略、资金占比和虚拟本金。",
  },
  {
    key: "execution",
    label: "Paper Trading 执行",
    description: "运行单策略或多策略调度，并查看最新结果。",
  },
];

export default function PaperTradingPage() {
  const [strategies, setStrategies] = useState<StrategyOut[]>([]);
  const [baskets, setBaskets] = useState<StockBasketOut[]>([]);
  const [allocations, setAllocations] = useState<StrategyAllocationOut[]>([]);
  const [accounts, setAccounts] = useState<PaperTradingAccountOut[]>([]);
  const [portfolios, setPortfolios] = useState<StrategyPortfolioOut[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [accountOverview, setAccountOverview] =
    useState<PaperTradingAccountOverviewOut | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("accounts");

  const [loading, setLoading] = useState(true);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [accountError, setAccountError] = useState<string | null>(null);
  const [portfolioError, setPortfolioError] = useState<string | null>(null);
  const [allocationError, setAllocationError] = useState<string | null>(null);
  const [singleError, setSingleError] = useState<string | null>(null);
  const [multiError, setMultiError] = useState<string | null>(null);

  const [creatingAccount, setCreatingAccount] = useState(false);
  const [creatingPortfolio, setCreatingPortfolio] = useState(false);
  const [submittingAllocation, setSubmittingAllocation] = useState(false);
  const [submittingSingle, setSubmittingSingle] = useState(false);
  const [submittingMulti, setSubmittingMulti] = useState(false);

  const [latestSingleRun, setLatestSingleRun] = useState<PaperTradingRunOut | null>(null);
  const [latestMultiRun, setLatestMultiRun] =
    useState<MultiStrategyPaperTradingRunOut | null>(null);

  const [accountName, setAccountName] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("ALPACA_API_KEY");
  const [secretKeyEnv, setSecretKeyEnv] = useState("ALPACA_SECRET_KEY");
  const [baseUrl, setBaseUrl] = useState("https://paper-api.alpaca.markets");
  const [timeoutSeconds, setTimeoutSeconds] = useState(20);
  const [accountNotes, setAccountNotes] = useState("");

  const [portfolioName, setPortfolioName] = useState("default");
  const [portfolioDescription, setPortfolioDescription] = useState("");
  const [portfolioStrategyIds, setPortfolioStrategyIds] = useState<string[]>([]);

  const [allocationStrategyId, setAllocationStrategyId] = useState("");
  const [allocationPortfolioName, setAllocationPortfolioName] = useState("default");
  const [allocationPct, setAllocationPct] = useState(0.25);
  const [capitalBase, setCapitalBase] = useState("");
  const [allowFractional, setAllowFractional] = useState(true);
  const [allocationStatus, setAllocationStatus] = useState("active");
  const [notes, setNotes] = useState("");

  const [strategyId, setStrategyId] = useState("");
  const [singlePortfolioName, setSinglePortfolioName] = useState("default");
  const [singleTradeDate, setSingleTradeDate] = useState(toDateInputValue(new Date()));
  const [basketId, setBasketId] = useState("");
  const [singleSubmitOrders, setSingleSubmitOrders] = useState(false);

  const [multiPortfolioName, setMultiPortfolioName] = useState("default");
  const [multiTradeDate, setMultiTradeDate] = useState(toDateInputValue(new Date()));
  const [multiSubmitOrders, setMultiSubmitOrders] = useState(false);
  const [continueOnError, setContinueOnError] = useState(false);

  const refreshOverview = async (accountId: string) => {
    if (!accountId) {
      setAccountOverview(null);
      return;
    }
    try {
      setOverviewLoading(true);
      const payload = await getPaperAccountOverview(accountId);
      setAccountOverview(payload);
    } catch (err: any) {
      setError(err?.message || "加载账户概览失败");
    } finally {
      setOverviewLoading(false);
    }
  };

  const refreshWorkspace = async (preferredAccountId?: string) => {
    const [strategyItems, basketItems, allocationItems, accountItems, portfolioItems] =
      await Promise.all([
        listStrategies(),
        listStockBaskets(),
        listStrategyAllocations(),
        listPaperAccounts(),
        listStrategyPortfolios(),
      ]);

    const preferredStrategy = strategyItems.find(
      (item) => item.status === "active" && item.engine_ready
    );
    const nextAccountId =
      preferredAccountId ||
      selectedAccountId ||
      accountItems[0]?.id ||
      "";

    setStrategies(strategyItems);
    setBaskets(basketItems);
    setAllocations(allocationItems);
    setAccounts(accountItems);
    setPortfolios(portfolioItems);
    setStrategyId((current) => current || preferredStrategy?.id || strategyItems[0]?.id || "");
    setAllocationStrategyId(
      (current) => current || preferredStrategy?.id || strategyItems[0]?.id || ""
    );
    setSelectedAccountId(nextAccountId);
  };

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      listStrategies(),
      listStockBaskets(),
      listStrategyAllocations(),
      listPaperAccounts(),
      listStrategyPortfolios(),
    ])
      .then(([strategyItems, basketItems, allocationItems, accountItems, portfolioItems]) => {
        if (cancelled) {
          return;
        }

        const preferredStrategy = strategyItems.find(
          (item) => item.status === "active" && item.engine_ready
        );
        const nextAccountId = accountItems[0]?.id || "";

        setStrategies(strategyItems);
        setBaskets(basketItems);
        setAllocations(allocationItems);
        setAccounts(accountItems);
        setPortfolios(portfolioItems);
        setStrategyId(preferredStrategy?.id || strategyItems[0]?.id || "");
        setAllocationStrategyId(preferredStrategy?.id || strategyItems[0]?.id || "");
        setSelectedAccountId(nextAccountId);
      })
      .catch((err: any) => {
        if (!cancelled) {
          setError(err?.message || "加载 Paper Trading 工作台失败");
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

  useEffect(() => {
    if (!selectedAccountId) {
      return;
    }
    void refreshOverview(selectedAccountId);
  }, [selectedAccountId]);

  const activeStrategies = useMemo(
    () => strategies.filter((item) => item.status === "active"),
    [strategies]
  );
  const eligibleStrategies = useMemo(
    () => activeStrategies.filter((item) => item.engine_ready),
    [activeStrategies]
  );
  const activeBaskets = useMemo(
    () => baskets.filter((item) => item.status === "active"),
    [baskets]
  );
  const activePortfolios = useMemo(
    () => portfolios.filter((item) => item.status === "active"),
    [portfolios]
  );
  const portfoliosForSelectedAccount = useMemo(() => {
    return activePortfolios.filter((item) => item.paper_account_id === selectedAccountId);
  }, [activePortfolios, selectedAccountId]);

  useEffect(() => {
    const nextPortfolioName = portfoliosForSelectedAccount[0]?.name || "default";
    setAllocationPortfolioName((current) =>
      portfoliosForSelectedAccount.some((item) => item.name === current)
        ? current
        : nextPortfolioName
    );
    setSinglePortfolioName((current) =>
      portfoliosForSelectedAccount.some((item) => item.name === current)
        ? current
        : nextPortfolioName
    );
    setMultiPortfolioName((current) =>
      portfoliosForSelectedAccount.some((item) => item.name === current)
        ? current
        : nextPortfolioName
    );
  }, [portfoliosForSelectedAccount]);

  const stats = useMemo(() => {
    const activeAllocations = allocations.filter((item) => item.status === "active");
    const accountAllocations = activeAllocations.filter(
      (item) => item.paper_account_id === selectedAccountId
    );
    return {
      activeStrategies: eligibleStrategies.length,
      accounts: accounts.length,
      portfolios: portfoliosForSelectedAccount.length,
      activeAllocations: accountAllocations.length,
    };
  }, [accounts.length, allocations, eligibleStrategies.length, portfoliosForSelectedAccount.length, selectedAccountId]);

  const groupedAllocationsForSelectedAccount = useMemo(() => {
    const accountAllocations = allocations
      .filter((item) => item.paper_account_id === selectedAccountId)
      .sort((a, b) => {
        const portfolioCompare = a.portfolio_name.localeCompare(b.portfolio_name);
        if (portfolioCompare !== 0) {
          return portfolioCompare;
        }
        return (a.strategy_name || a.strategy_id).localeCompare(
          b.strategy_name || b.strategy_id
        );
      });

    const grouped = new Map<string, StrategyAllocationOut[]>();
    accountAllocations.forEach((item) => {
      const bucket = grouped.get(item.portfolio_name);
      if (bucket) {
        bucket.push(item);
        return;
      }
      grouped.set(item.portfolio_name, [item]);
    });

    return Array.from(grouped.entries()).map(([portfolioName, items]) => ({
      portfolioName,
      items,
      activeCount: items.filter((item) => item.status === "active").length,
      allocationPctTotal: items
        .filter((item) => item.status === "active")
        .reduce((sum, item) => sum + item.allocation_pct, 0),
    }));
  }, [allocations, selectedAccountId]);

  const handleAccountSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setAccountError(null);

    const payload: PaperTradingAccountCreate = {
      name: accountName.trim(),
      api_key_env: apiKeyEnv.trim(),
      secret_key_env: secretKeyEnv.trim(),
      base_url: baseUrl.trim(),
      timeout_seconds: Number(timeoutSeconds),
      notes: accountNotes.trim() || null,
      broker: "alpaca",
      mode: "paper",
      status: "active",
    };

    if (!payload.name) {
      setAccountError("请输入账户名称");
      return;
    }

    try {
      setCreatingAccount(true);
      const saved = await createPaperAccount(payload);
      await refreshWorkspace(saved.id);
      await refreshOverview(saved.id);
      setAccountName("");
      setAccountNotes("");
    } catch (err: any) {
      setAccountError(err?.message || "创建 paper account 失败");
    } finally {
      setCreatingAccount(false);
    }
  };

  const handlePortfolioSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setPortfolioError(null);

    if (!selectedAccountId) {
      setPortfolioError("请先选择一个 paper account");
      return;
    }

    const payload: StrategyPortfolioCreate = {
      paper_account_id: selectedAccountId,
      name: portfolioName.trim(),
      description: portfolioDescription.trim() || null,
      strategy_ids: portfolioStrategyIds,
      status: "active",
    };

    if (!payload.name) {
      setPortfolioError("请输入子组合名称");
      return;
    }
    if (payload.strategy_ids.length === 0) {
      setPortfolioError("请至少选择一个要初始化到子组合里的策略");
      return;
    }

    try {
      setCreatingPortfolio(true);
      const saved = await createStrategyPortfolio(payload);
      await refreshWorkspace(selectedAccountId);
      await refreshOverview(selectedAccountId);
      setPortfolioName("");
      setPortfolioDescription("");
      setPortfolioStrategyIds([]);
      setAllocationPortfolioName(saved.name);
      setSinglePortfolioName(saved.name);
      setMultiPortfolioName(saved.name);
    } catch (err: any) {
      setPortfolioError(err?.message || "创建策略子组合失败");
    } finally {
      setCreatingPortfolio(false);
    }
  };

  const seedAllocationPct = portfolioStrategyIds.length > 0 ? 1 / portfolioStrategyIds.length : 0;

  const handleAllocationSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setAllocationError(null);

    if (!allocationStrategyId) {
      setAllocationError("请选择一个策略");
      return;
    }

    const payload: StrategyAllocationUpsert = {
      strategy_id: allocationStrategyId,
      portfolio_name: allocationPortfolioName.trim() || "default",
      allocation_pct: Number(allocationPct),
      capital_base: capitalBase.trim() ? Number(capitalBase) : null,
      allow_fractional: allowFractional,
      notes: notes.trim() || null,
      status: allocationStatus,
    };

    try {
      setSubmittingAllocation(true);
      await upsertStrategyAllocation(payload);
      await refreshWorkspace(selectedAccountId);
      await refreshOverview(selectedAccountId);
      setNotes("");
      setSinglePortfolioName(payload.portfolio_name);
      setMultiPortfolioName(payload.portfolio_name);
    } catch (err: any) {
      setAllocationError(err?.message || "保存策略分配失败");
    } finally {
      setSubmittingAllocation(false);
    }
  };

  const handleSingleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSingleError(null);

    if (!strategyId) {
      setSingleError("请选择一个策略");
      return;
    }

    const payload: PaperTradingRunRequest = {
      strategy_id: strategyId,
      trade_date: singleTradeDate,
      portfolio_name: singlePortfolioName.trim() || "default",
      basket_id: basketId || null,
      submit_orders: singleSubmitOrders,
    };

    try {
      setSubmittingSingle(true);
      const run = await createPaperTradingRun(payload);
      setLatestSingleRun(run);
      await refreshOverview(selectedAccountId);
    } catch (err: any) {
      setSingleError(err?.message || "发起单策略 paper trading 失败");
    } finally {
      setSubmittingSingle(false);
    }
  };

  const handleMultiSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setMultiError(null);

    const payload: MultiStrategyPaperTradingRunRequest = {
      trade_date: multiTradeDate,
      portfolio_name: multiPortfolioName.trim() || "default",
      submit_orders: multiSubmitOrders,
      continue_on_error: continueOnError,
    };

    try {
      setSubmittingMulti(true);
      const run = await createMultiStrategyPaperTradingRun(payload);
      setLatestMultiRun(run);
      await refreshOverview(selectedAccountId);
    } catch (err: any) {
      setMultiError(err?.message || "发起多策略 paper trading 失败");
    } finally {
      setSubmittingMulti(false);
    }
  };

  return (
    <AppShell
      title="Paper Trading 工作台"
      subtitle="第一版已经把 paper account、策略子组合、allocation 和执行工作台串起来了。你现在可以按账户管理多个虚拟子组合，并直接查看每个子组合下的策略情况。"
      actions={
        <>
          <button
            type="button"
            onClick={() => setActiveTab("accounts")}
            style={actionButtonStyle(activeTab === "accounts")}
          >
            账户与子组合
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("allocations")}
            style={actionButtonStyle(activeTab === "allocations")}
          >
            策略组合调整
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("execution")}
            style={actionButtonStyle(activeTab === "execution")}
          >
            Paper Trading 执行
          </button>
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
              label="Paper Accounts"
              value={String(stats.accounts)}
              hint="每个 account 对应一套 Alpaca paper 凭证和一个独立的执行通道。"
              accent="#0f766e"
            />
            <MetricCard
              label="当前账户子组合"
              value={String(stats.portfolios)}
              hint="这里统计当前选中 paper account 下的 active strategy portfolios。"
              accent="#2563eb"
            />
            <MetricCard
              label="当前账户 Active Allocations"
              value={String(stats.activeAllocations)}
              hint="只有这些 active allocation 会真正参与多策略调度。"
              accent="#ca8a04"
            />
            <MetricCard
              label="可运行策略"
              value={String(stats.activeStrategies)}
              hint="active 且 engine-ready 的策略数量，是当前能被 paper trading 直接消费的策略池。"
              accent="#b45309"
            />
          </section>

          <section style={tabPanelShellStyle}>
            <div style={tabListStyle}>
              {WORKSPACE_TABS.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setActiveTab(tab.key)}
                  style={tabButtonStyle(activeTab === tab.key)}
                >
                  <span style={tabLabelStyle}>{tab.label}</span>
                  <span style={tabDescriptionStyle}>{tab.description}</span>
                </button>
              ))}
            </div>
          </section>

          {activeTab === "accounts" ? (
          <section id="accounts" style={sectionStyle}>
            <div style={sectionHeaderStyle}>
              <div>
                <h2 style={sectionTitleStyle}>账户与子组合</h2>
                <p style={subtitleStyle}>
                  先创建 paper trading account，再在账户下面创建 strategy portfolio。第一版里
                  portfolio 名称需要全局唯一，后面如果你要支持同名子组合，我们再升级成
                  `portfolio_id` 驱动。
                </p>
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(320px, 0.95fr) minmax(320px, 0.95fr) minmax(0, 1.2fr)",
                gap: 18,
                alignItems: "start",
              }}
            >
              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>创建 Paper Account</h3>
                  <p style={subtitleStyle}>
                    用环境变量名来绑定 Alpaca 凭证，这样前端不用直接保存明文 key。
                  </p>
                </div>

                <form onSubmit={handleAccountSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    "账户名",
                    "给这套 paper trading 凭证一个容易识别的名字，例如 us-paper-main。",
                    <input
                      value={accountName}
                      onChange={(e) => setAccountName(e.target.value)}
                      placeholder="例如 us-paper-main"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "API Key 环境变量",
                    "后端会从这个环境变量里读取 Alpaca API key，比如 ALPACA_API_KEY_MAIN。",
                    <input
                      value={apiKeyEnv}
                      onChange={(e) => setApiKeyEnv(e.target.value)}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "Secret Key 环境变量",
                    "后端会从这个环境变量里读取 Alpaca secret key，比如 ALPACA_SECRET_KEY_MAIN。",
                    <input
                      value={secretKeyEnv}
                      onChange={(e) => setSecretKeyEnv(e.target.value)}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "Base URL",
                    "默认是 Alpaca paper endpoint；只有接别的环境时才需要改。",
                    <input
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "请求超时",
                    "单位是秒，用来控制账户查询和下单请求的超时时间。",
                    <input
                      value={timeoutSeconds}
                      onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
                      type="number"
                      min="1"
                      step="1"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "备注",
                    "可选。建议写清楚这套账户是测试用、演示用，还是某一组策略专用。",
                    <textarea
                      value={accountNotes}
                      onChange={(e) => setAccountNotes(e.target.value)}
                      rows={3}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                  )}
                  {accountError ? <div style={errorTextStyle}>{accountError}</div> : null}
                  <button type="submit" disabled={creatingAccount} style={buttonStyle}>
                    {creatingAccount ? "创建中..." : "创建 Paper Account"}
                  </button>
                </form>
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>创建策略子组合</h3>
                  <p style={subtitleStyle}>
                    子组合是挂在某个 paper account 下的虚拟 sleeve。后续 allocation 和运行都会按它来组织。
                  </p>
                </div>

                {fieldBlock(
                  "当前账户",
                  "先选一个 paper account，再往这个账户下创建 strategy portfolio。",
                  <select
                    value={selectedAccountId}
                    onChange={(e) => setSelectedAccountId(e.target.value)}
                    style={inputStyle}
                  >
                    {accounts.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                )}

                <form onSubmit={handlePortfolioSubmit} style={{ display: "grid", gap: 12, marginTop: 12 }}>
                  {fieldBlock(
                    "子组合名",
                    "第一版要求全局唯一。建议带上账户前缀，例如 us-main-growth、us-main-default。",
                    <input
                      value={portfolioName}
                      onChange={(e) => setPortfolioName(e.target.value)}
                      placeholder="例如 us-main-growth"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "说明",
                    "写明这个子组合主要承载哪类策略或风险风格，比如 rotation、growth、mean-reversion。",
                    <textarea
                      value={portfolioDescription}
                      onChange={(e) => setPortfolioDescription(e.target.value)}
                      rows={3}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                  )}
                  {fieldBlock(
                    "初始化策略",
                    "创建 portfolio 时就把这些策略放进去。当前会按等权自动生成 active allocations，后面你仍然可以在下方继续调整。",
                    <div style={selectionPanelStyle}>
                      <div style={selectionHintStyle}>
                        已选 {portfolioStrategyIds.length} 个策略
                        {portfolioStrategyIds.length > 0
                          ? `，初始等权约为 ${formatPercent(seedAllocationPct, 0)} / strategy`
                          : ""}
                      </div>
                      <div style={selectionListStyle}>
                        {activeStrategies.map((item) => {
                          const checked = portfolioStrategyIds.includes(item.id);
                          return (
                            <label key={item.id} style={selectionItemStyle}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={(e) =>
                                  setPortfolioStrategyIds((current) =>
                                    e.target.checked
                                      ? [...current, item.id]
                                      : current.filter((value) => value !== item.id)
                                  )
                                }
                              />
                              <span>
                                {item.name} {item.engine_ready ? "" : "(stored-only)"}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {portfolioError ? <div style={errorTextStyle}>{portfolioError}</div> : null}
                  <button type="submit" disabled={creatingPortfolio} style={buttonStyle}>
                    {creatingPortfolio ? "创建中..." : "创建策略子组合"}
                  </button>
                </form>
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>账户概览</h3>
                  <p style={subtitleStyle}>
                    这里会显示当前账户下有几个子组合、每个子组合里有多少策略，以及最近一次运行情况。
                  </p>
                </div>

                {overviewLoading ? <p>加载账户概览中...</p> : null}
                {accountOverview ? (
                  <div style={{ display: "grid", gap: 14 }}>
                    <div style={nestedResultStyle}>
                      <div style={badgeRowStyle}>
                        <Badge tone="info">{accountOverview.account.name}</Badge>
                        <Badge>{accountOverview.account.mode}</Badge>
                        <Badge tone={accountOverview.account.status === "active" ? "success" : "warning"}>
                          {accountOverview.account.status}
                        </Badge>
                      </div>
                      <div style={detailGridStyle}>
                        <div>
                          <strong>base url:</strong> {accountOverview.account.base_url}
                        </div>
                        <div>
                          <strong>credentials:</strong> {accountOverview.account.api_key_env} /{" "}
                          {accountOverview.account.secret_key_env}
                        </div>
                        <div>
                          <strong>子组合数:</strong> {accountOverview.portfolio_count}
                        </div>
                        <div>
                          <strong>active 策略数:</strong> {accountOverview.active_strategy_count}
                        </div>
                      </div>
                    </div>

                    {accountOverview.portfolios.length === 0 ? (
                      <div style={emptyStyle}>这个账户下还没有子组合。先创建一个 strategy portfolio。</div>
                    ) : (
                      <div style={{ display: "grid", gap: 12, maxHeight: 720, overflowY: "auto", paddingRight: 4 }}>
                        {accountOverview.portfolios.map((portfolio) => (
                          <article key={portfolio.id} style={listCardStyle}>
                            <div style={listHeaderStyle}>
                              <div>
                                <h3 style={{ margin: "0 0 6px", fontSize: 20 }}>{portfolio.name}</h3>
                                <div style={badgeRowStyle}>
                                  <Badge tone={portfolio.status === "active" ? "success" : "warning"}>
                                    {portfolio.status}
                                  </Badge>
                                  <Badge>{portfolio.allocated_strategy_count} 个策略</Badge>
                                  <Badge>{formatPercent(portfolio.active_allocation_pct_total, 0)}</Badge>
                                </div>
                              </div>
                              <div style={metaTextStyle}>
                                {portfolio.latest_run_requested_at
                                  ? formatDateTime(portfolio.latest_run_requested_at)
                                  : "还没有运行"}
                              </div>
                            </div>
                            <p style={bodyTextStyle}>{portfolio.description || "暂无子组合描述"}</p>
                            <div style={detailGridStyle}>
                              <div>
                                <strong>allocations:</strong> {portfolio.active_allocation_count}/
                                {portfolio.allocation_count}
                              </div>
                              <div>
                                <strong>latest run:</strong> {portfolio.latest_run_status || "-"}
                              </div>
                              <div>
                                <strong>latest equity:</strong>{" "}
                                {typeof portfolio.latest_run_equity === "number"
                                  ? portfolio.latest_run_equity.toLocaleString()
                                  : "-"}
                              </div>
                            </div>
                            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
                              {portfolio.strategies.map((item) => (
                                <div key={`${portfolio.id}-${item.strategy_id}`} style={miniStrategyCardStyle}>
                                  <div style={badgeRowStyle}>
                                    <Badge tone="info">{item.strategy_name}</Badge>
                                    <Badge>{item.strategy_type}</Badge>
                                    <Badge>{formatPercent(item.allocation_pct, 0)}</Badge>
                                  </div>
                                  <div style={detailGridStyle}>
                                    <div>
                                      <strong>strategy status:</strong> {item.strategy_status}
                                    </div>
                                    <div>
                                      <strong>latest run:</strong> {item.latest_run_status || "-"}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </article>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div style={emptyStyle}>先选择一个 paper account，系统会在这里展示该账户的子组合和策略概览。</div>
                )}
              </section>
            </div>
          </section>
          ) : null}

          {activeTab === "allocations" ? (
          <section id="allocations" style={sectionStyle}>
            <div style={sectionHeaderStyle}>
              <div>
                <h2 style={sectionTitleStyle}>策略分配</h2>
                <p style={subtitleStyle}>
                  现在 allocation 不再只是一个字符串标签，而是明确挂到某个账户下的 strategy portfolio。
                </p>
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(320px, 0.95fr) minmax(0, 1.15fr)",
                gap: 18,
                alignItems: "start",
              }}
            >
              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>配置策略分配</h3>
                  <p style={subtitleStyle}>
                    选中某个子组合后，把 active 策略按比例分进去。后面运行单策略或多策略时都会按这个 sleeve 来算账。
                  </p>
                </div>

                <form onSubmit={handleAllocationSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    "策略",
                    "选择要放进虚拟子组合里的策略。一般建议优先给 active 且 engine-ready 的策略配置 allocation。",
                    <select
                      value={allocationStrategyId}
                      onChange={(e) => setAllocationStrategyId(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">选择策略</option>
                      {activeStrategies.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name} {item.engine_ready ? "" : "(stored-only)"}
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    "子组合 / Portfolio",
                    "这里只显示当前账户下的 active strategy portfolios。allocation 会直接绑定到你选中的子组合。",
                    <select
                      value={allocationPortfolioName}
                      onChange={(e) => setAllocationPortfolioName(e.target.value)}
                      style={inputStyle}
                    >
                      {portfoliosForSelectedAccount.map((item) => (
                        <option key={item.id} value={item.name}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    "资金占比 / allocation_pct",
                    "表示这个策略在该子组合下可以使用多少虚拟资金，范围 0 到 1。比如 0.25 表示 25%。",
                    <input
                      value={allocationPct}
                      onChange={(e) => setAllocationPct(Number(e.target.value))}
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "固定本金 / capital_base",
                    "可选。留空时系统会按账户净值乘以 allocation_pct 计算虚拟本金；填写后则优先用这个固定金额。",
                    <input
                      value={capitalBase}
                      onChange={(e) => setCapitalBase(e.target.value)}
                      type="number"
                      min="0"
                      step="1000"
                      placeholder="可选固定 capital_base，例如 50000"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "状态",
                    "只有 active 的 allocation 会被多策略调度器真正读取；draft 和 archived 适合预配置或暂时停用。",
                    <select
                      value={allocationStatus}
                      onChange={(e) => setAllocationStatus(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="active">active</option>
                      <option value="draft">draft</option>
                      <option value="archived">archived</option>
                    </select>
                  )}
                  {fieldBlock(
                    "允许 fractional shares",
                    "打开后仓位计算允许小数股；关闭后会把下单数量向下取整，更接近只支持整股的执行方式。",
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={allowFractional}
                        onChange={(e) => setAllowFractional(e.target.checked)}
                      />
                      允许 fractional shares
                    </label>
                  )}
                  {fieldBlock(
                    "备注",
                    "可选。建议写清楚这个 allocation 的用途，例如属于哪个子资金池、目标风险暴露，或者是否为临时实验配置。",
                    <textarea
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      rows={4}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                  )}
                  {allocationError ? <div style={errorTextStyle}>{allocationError}</div> : null}
                  <button type="submit" disabled={submittingAllocation} style={buttonStyle}>
                    {submittingAllocation ? "保存中..." : "保存分配"}
                  </button>
                </form>
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>当前账户分配</h3>
                  <p style={subtitleStyle}>
                    这里展示当前选中 account 下的所有 allocation。你可以用它来确认某个 portfolio 里有哪些策略、每个策略占多少资金。
                  </p>
                </div>

                {groupedAllocationsForSelectedAccount.length === 0 ? (
                  <div style={emptyStyle}>当前账户还没有策略分配。先创建一个 allocation，再往下跑 paper trading。</div>
                ) : (
                  <div style={{ display: "grid", gap: 14 }}>
                    {groupedAllocationsForSelectedAccount.map((group) => (
                      <article key={group.portfolioName} style={listCardStyle}>
                        <div style={listHeaderStyle}>
                          <div>
                            <h3 style={{ margin: "0 0 6px", fontSize: 20 }}>
                              {group.portfolioName}
                            </h3>
                            <div style={badgeRowStyle}>
                              <Badge tone="info">{group.items[0]?.paper_account_name || "-"}</Badge>
                              <Badge>{group.items.length} 条分配</Badge>
                              <Badge>{group.activeCount} 条 active</Badge>
                              <Badge>{formatPercent(group.allocationPctTotal, 0)}</Badge>
                            </div>
                          </div>
                        </div>
                        <div style={{ display: "grid", gap: 12 }}>
                          {group.items.map((item) => {
                            const strategy =
                              strategies.find((entry) => entry.id === item.strategy_id) || null;
                            return (
                              <div key={item.id} style={miniStrategyCardStyle}>
                                <div style={listHeaderStyle}>
                                  <div>
                                    <h4 style={{ margin: "0 0 6px", fontSize: 17 }}>
                                      {item.strategy_name || strategy?.name || item.strategy_id}
                                    </h4>
                                    <div style={badgeRowStyle}>
                                      <Badge
                                        tone={item.status === "active" ? "success" : "warning"}
                                      >
                                        {item.status}
                                      </Badge>
                                      <Badge>{formatPercent(item.allocation_pct, 0)}</Badge>
                                      <Badge>
                                        {item.allow_fractional ? "fractional" : "whole shares"}
                                      </Badge>
                                    </div>
                                  </div>
                                  <div style={metaTextStyle}>
                                    {formatDateTime(item.updated_at || item.created_at)}
                                  </div>
                                </div>
                                <p style={bodyTextStyle}>
                                  {strategy ? getStrategyDescription(strategy) : "暂无策略描述"}
                                </p>
                                <div style={detailGridStyle}>
                                  <div>
                                    <strong>capital base:</strong>{" "}
                                    {typeof item.capital_base === "number"
                                      ? item.capital_base.toLocaleString()
                                      : "跟随账户净值 * allocation_pct"}
                                  </div>
                                  <div>
                                    <strong>notes:</strong> {item.notes || "-"}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </section>
          ) : null}

          {activeTab === "execution" ? (
          <section id="execution" style={sectionStyle}>
            <div style={sectionHeaderStyle}>
              <div>
                <h2 style={sectionTitleStyle}>Paper Trading 执行</h2>
                <p style={subtitleStyle}>
                  执行时只需要选子组合。系统会自动根据子组合找到对应的 paper account，再用那套 Alpaca 凭证去查询账户和发单。
                </p>
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                gap: 18,
                alignItems: "start",
              }}
            >
              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>单策略运行</h3>
                  <p style={subtitleStyle}>
                    适合先验证某个策略在某个 portfolio 下的 sleeve 账本、风控和下单逻辑。
                  </p>
                </div>

                <form onSubmit={handleSingleSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    "策略",
                    "选择一个 active 且 engine-ready 的策略。",
                    <select
                      value={strategyId}
                      onChange={(e) => setStrategyId(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">选择策略</option>
                      {eligibleStrategies.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    "交易日期",
                    "使用这一天的日线特征数据生成信号。当前系统是日频策略，所以这里填的是交易日，不是分钟级时间戳。",
                    <input
                      value={singleTradeDate}
                      onChange={(e) => setSingleTradeDate(e.target.value)}
                      type="date"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "子组合 / Portfolio",
                    "决定这个策略要挂到哪个虚拟子组合下运行。系统会先根据这个 portfolio 找到对应的 paper account，再去 Alpaca 查询和发单。",
                    <select
                      value={singlePortfolioName}
                      onChange={(e) => setSinglePortfolioName(e.target.value)}
                      style={inputStyle}
                    >
                      {portfoliosForSelectedAccount.map((item) => (
                        <option key={item.id} value={item.name}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    "股票池覆盖",
                    "可选。留空时使用策略自身配置的 universe；选了 basket 后，会临时用这个股票池覆盖本次运行的 universe。",
                    <select
                      value={basketId}
                      onChange={(e) => setBasketId(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">使用策略原始 universe</option>
                      {activeBaskets.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name} ({item.symbol_count})
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    "提交真实 paper 订单",
                    "关闭时只做 dry run，验证信号、sleeve 账本和风控；打开后会真正向 Alpaca paper account 发单。",
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={singleSubmitOrders}
                        onChange={(e) => setSingleSubmitOrders(e.target.checked)}
                      />
                      真正提交到 Alpaca paper account
                    </label>
                  )}
                  {singleError ? <div style={errorTextStyle}>{singleError}</div> : null}
                  <button type="submit" disabled={submittingSingle} style={buttonStyle}>
                    {submittingSingle ? "执行中..." : "运行单策略 Paper Trading"}
                  </button>
                </form>

                {latestSingleRun ? (
                  <div style={resultCardStyle}>
                    <div style={badgeRowStyle}>
                      <Badge tone={latestSingleRun.status === "completed" ? "success" : "warning"}>
                        {latestSingleRun.status}
                      </Badge>
                      <Badge tone="info">{latestSingleRun.portfolio_name}</Badge>
                      <Badge>{formatPercent(latestSingleRun.allocation_pct, 0)}</Badge>
                    </div>
                    <div style={resultGridStyle}>
                      <div>
                        <strong>signals:</strong> {latestSingleRun.signal_count}
                      </div>
                      <div>
                        <strong>orders:</strong> {latestSingleRun.order_count}
                      </div>
                      <div>
                        <strong>submitted:</strong> {latestSingleRun.submitted_order_count}
                      </div>
                      <div>
                        <strong>final equity:</strong>{" "}
                        {latestSingleRun.final_equity.toLocaleString()}
                      </div>
                    </div>
                  </div>
                ) : null}
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>多策略调度</h3>
                  <p style={subtitleStyle}>
                    这个入口会按 portfolio 下的 active allocation 顺序，把多个策略跑在同一个 Alpaca account 上。
                  </p>
                </div>

                <form onSubmit={handleMultiSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    "子组合 / Portfolio",
                    "多策略调度会读取这个 portfolio 下所有 active allocation，并自动找到它所属的 paper account 后执行。",
                    <select
                      value={multiPortfolioName}
                      onChange={(e) => setMultiPortfolioName(e.target.value)}
                      style={inputStyle}
                    >
                      {portfoliosForSelectedAccount.map((item) => (
                        <option key={item.id} value={item.name}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    "交易日期",
                    "所有被调度的策略都会使用同一天的日线特征快照来生成信号和执行本轮 paper trading。",
                    <input
                      value={multiTradeDate}
                      onChange={(e) => setMultiTradeDate(e.target.value)}
                      type="date"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    "提交真实 paper 订单",
                    "关闭时只跑模拟调度，不真正下单；打开后会把每个策略的订单都提交到该子组合所属的 Alpaca paper account。",
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={multiSubmitOrders}
                        onChange={(e) => setMultiSubmitOrders(e.target.checked)}
                      />
                      真正提交到 Alpaca paper account
                    </label>
                  )}
                  {fieldBlock(
                    "失败后的处理方式",
                    "打开后即使某个策略执行失败，调度器也会继续跑 portfolio 里剩下的策略；关闭则遇到首个错误就停止。",
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={continueOnError}
                        onChange={(e) => setContinueOnError(e.target.checked)}
                      />
                      某个策略失败后继续跑后续策略
                    </label>
                  )}
                  {multiError ? <div style={errorTextStyle}>{multiError}</div> : null}
                  <button type="submit" disabled={submittingMulti} style={buttonStyle}>
                    {submittingMulti ? "调度中..." : "运行多策略 Paper Trading"}
                  </button>
                </form>

                {latestMultiRun ? (
                  <div style={resultCardStyle}>
                    <div style={badgeRowStyle}>
                      <Badge tone="info">{latestMultiRun.portfolio_name}</Badge>
                      <Badge tone={latestMultiRun.failed_runs > 0 ? "warning" : "success"}>
                        {latestMultiRun.completed_runs}/{latestMultiRun.total_runs} completed
                      </Badge>
                    </div>
                    <div style={{ display: "grid", gap: 10 }}>
                      {latestMultiRun.results.map((item) => (
                        <div key={item.run_id} style={nestedResultStyle}>
                          <div style={badgeRowStyle}>
                            <Badge tone={item.status === "completed" ? "success" : "warning"}>
                              {item.status}
                            </Badge>
                            <Badge>{formatPercent(item.allocation_pct, 0)}</Badge>
                          </div>
                          <div style={resultGridStyle}>
                            <div>
                              <strong>strategy:</strong> {item.strategy_id}
                            </div>
                            <div>
                              <strong>orders:</strong> {item.order_count}
                            </div>
                            <div>
                              <strong>submitted:</strong> {item.submitted_order_count}
                            </div>
                            <div>
                              <strong>equity:</strong> {item.final_equity.toLocaleString()}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </section>
            </div>
          </section>
          ) : null}
        </>
      ) : null}
    </AppShell>
  );
}

function actionButtonStyle(active: boolean): CSSProperties {
  return {
    padding: "11px 16px",
    borderRadius: 14,
    border: active ? "none" : "1px solid rgba(148, 163, 184, 0.28)",
    background: active ? "#0f766e" : "rgba(255,255,255,0.8)",
    color: active ? "#fff" : "#0f172a",
    textDecoration: "none",
    fontWeight: 700,
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
    cursor: "pointer",
  };
}

const sectionStyle: CSSProperties = {
  display: "grid",
  gap: 18,
  marginBottom: 22,
};

const tabPanelShellStyle: CSSProperties = {
  marginBottom: 22,
};

const tabListStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
  gap: 12,
};

const tabLabelStyle: CSSProperties = {
  color: "inherit",
  fontSize: 16,
  fontWeight: 700,
  lineHeight: 1.2,
};

const tabDescriptionStyle: CSSProperties = {
  color: "inherit",
  opacity: 0.8,
  fontSize: 13,
  lineHeight: 1.6,
};

function tabButtonStyle(active: boolean): CSSProperties {
  return {
    display: "grid",
    gap: 6,
    width: "100%",
    padding: "16px 18px",
    borderRadius: 20,
    border: active
      ? "1px solid rgba(15, 118, 110, 0.35)"
      : "1px solid rgba(226, 232, 240, 0.95)",
    background: active
      ? "linear-gradient(135deg, rgba(15,118,110,0.95), rgba(13,148,136,0.88))"
      : "rgba(255,255,255,0.82)",
    color: active ? "#ffffff" : "#0f172a",
    boxShadow: active
      ? "0 18px 40px rgba(15, 118, 110, 0.18)"
      : "0 10px 24px rgba(15, 23, 42, 0.05)",
    textAlign: "left",
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
}

const sectionHeaderStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-end",
  gap: 16,
  flexWrap: "wrap",
};

const sectionTitleStyle: CSSProperties = {
  margin: "0 0 8px",
  fontSize: 28,
};

const cardStyle: CSSProperties = {
  padding: 22,
  borderRadius: 24,
  border: "1px solid rgba(148, 163, 184, 0.18)",
  background: "rgba(255,255,255,0.82)",
  boxShadow: "0 18px 44px rgba(15, 23, 42, 0.06)",
};

const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: 12,
  border: "1px solid #dbe4ee",
  borderRadius: 14,
  fontSize: 14,
  background: "#fff",
};

const buttonStyle: CSSProperties = {
  padding: "12px 16px",
  borderRadius: 14,
  border: "none",
  background: "#0f766e",
  color: "#fff",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const fieldBlockStyle: CSSProperties = {
  display: "grid",
  gap: 8,
  padding: 14,
  borderRadius: 18,
  border: "1px solid rgba(226, 232, 240, 0.95)",
  background: "rgba(248, 250, 252, 0.9)",
};

const fieldLabelStyle: CSSProperties = {
  color: "#0f172a",
  fontWeight: 700,
  lineHeight: 1.2,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const fieldDescriptionStyle: CSSProperties = {
  color: "#64748b",
  lineHeight: 1.6,
  fontSize: 13,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  color: "#64748b",
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const checkboxRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  color: "#334155",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const selectionPanelStyle: CSSProperties = {
  display: "grid",
  gap: 10,
};

const selectionHintStyle: CSSProperties = {
  color: "#475569",
  fontSize: 13,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const selectionListStyle: CSSProperties = {
  display: "grid",
  gap: 8,
  maxHeight: 220,
  overflowY: "auto",
  padding: 10,
  borderRadius: 14,
  border: "1px solid rgba(226, 232, 240, 0.95)",
  background: "rgba(255,255,255,0.9)",
};

const selectionItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  color: "#334155",
  fontSize: 14,
  lineHeight: 1.5,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const emptyStyle: CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: "#f8fafc",
  color: "#475569",
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const listCardStyle: CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: "linear-gradient(135deg, rgba(245,250,255,0.95), rgba(255,255,255,0.96))",
  border: "1px solid rgba(226, 232, 240, 0.9)",
};

const miniStrategyCardStyle: CSSProperties = {
  padding: 12,
  borderRadius: 14,
  background: "rgba(255,255,255,0.9)",
  border: "1px solid rgba(226, 232, 240, 0.85)",
  display: "grid",
  gap: 8,
};

const listHeaderStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  alignItems: "flex-start",
  flexWrap: "wrap",
  marginBottom: 10,
};

const badgeRowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const metaTextStyle: CSSProperties = {
  color: "#64748b",
  fontSize: 13,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const bodyTextStyle: CSSProperties = {
  margin: "0 0 12px",
  color: "#475569",
  lineHeight: 1.7,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const detailGridStyle: CSSProperties = {
  display: "grid",
  gap: 8,
  color: "#334155",
  fontSize: 14,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const errorTextStyle: CSSProperties = {
  color: "crimson",
  fontSize: 14,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const resultCardStyle: CSSProperties = {
  marginTop: 14,
  padding: 14,
  borderRadius: 18,
  background: "rgba(240,253,250,0.92)",
  border: "1px solid rgba(94, 234, 212, 0.45)",
  display: "grid",
  gap: 10,
};

const nestedResultStyle: CSSProperties = {
  padding: 12,
  borderRadius: 14,
  background: "rgba(255,255,255,0.88)",
  border: "1px solid rgba(226, 232, 240, 0.9)",
  display: "grid",
  gap: 8,
};

const resultGridStyle: CSSProperties = {
  display: "grid",
  gap: 8,
  color: "#0f172a",
  fontSize: 14,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};
