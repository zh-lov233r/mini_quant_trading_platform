import type { CSSProperties, FormEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createPaperAccount,
  createStrategyPortfolio,
  deletePaperAccount,
  deleteStrategyPortfolio,
  getPaperAccountWorkspace,
  listPaperAccounts,
  updatePaperAccount,
} from "@/api/paper-accounts";
import { upsertStrategyAllocation } from "@/api/strategy-allocations";
import {
  createMultiStrategyPaperTradingRun,
  createPaperTradingRun,
  getLatestPaperTradingTradeDate,
} from "@/api/paper-trading";
import { listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import { useI18n } from "@/i18n/provider";
import type {
  BrokerOrderOut,
  BrokerPortfolioHistoryOut,
  BrokerPositionOut,
  PaperAccountTransactionOut,
  PaperTradingAccountCreate,
  PaperTradingAccountOut,
  PaperTradingAccountUpdate,
  PaperTradingWorkspaceOut,
  StrategyPortfolioCreate,
  StrategyPortfolioWorkspaceOut,
} from "@/types/paper-account";
import type {
  MultiStrategyPaperTradingRunOut,
  MultiStrategyPaperTradingRunRequest,
  PaperTradingRunOut,
  PaperTradingRunRequest,
} from "@/types/paper-trading";
import type { StrategyOut } from "@/types/strategy";
import { formatDateTime, formatPercent } from "@/utils/strategy";

function toDateInputValue(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

const TODAY_INPUT_VALUE = toDateInputValue(new Date());
const LAST_SELECTED_PAPER_ACCOUNT_KEY = "paper-trading:last-selected-account-id";

type WorkbenchPage = "account" | "portfolios";

export default function PaperTradingPage() {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const txt = useCallback(
    (zh: string, en: string) => (isZh ? zh : en),
    [isZh]
  );

  const [accounts, setAccounts] = useState<PaperTradingAccountOut[]>([]);
  const [strategies, setStrategies] = useState<StrategyOut[]>([]);
  const [workspace, setWorkspace] = useState<PaperTradingWorkspaceOut | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [activeWorkbenchPage, setActiveWorkbenchPage] =
    useState<WorkbenchPage>("account");
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [creatingAccount, setCreatingAccount] = useState(false);
  const [editingAccount, setEditingAccount] = useState(false);
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [creatingPortfolio, setCreatingPortfolio] = useState(false);
  const [deletingPortfolioId, setDeletingPortfolioId] = useState<string | null>(null);
  const [runningSingle, setRunningSingle] = useState(false);
  const [runningMulti, setRunningMulti] = useState(false);

  const [accountName, setAccountName] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("ALPACA_API_KEY");
  const [secretKeyEnv, setSecretKeyEnv] = useState("ALPACA_SECRET_KEY");
  const [baseUrl, setBaseUrl] = useState("https://paper-api.alpaca.markets");
  const [timeoutSeconds, setTimeoutSeconds] = useState(20);
  const [accountNotes, setAccountNotes] = useState("");

  const [portfolioName, setPortfolioName] = useState("");
  const [portfolioDescription, setPortfolioDescription] = useState("");
  const [portfolioStrategyIds, setPortfolioStrategyIds] = useState<string[]>([]);

  const [selectedPortfolioName, setSelectedPortfolioName] = useState("");
  const [selectedStrategyId, setSelectedStrategyId] = useState("");
  const [singleTradeDate, setSingleTradeDate] = useState(TODAY_INPUT_VALUE);
  const [multiTradeDate, setMultiTradeDate] = useState(TODAY_INPUT_VALUE);
  const [submitSingleOrders, setSubmitSingleOrders] = useState(false);
  const [submitMultiOrders, setSubmitMultiOrders] = useState(false);
  const [continueOnError, setContinueOnError] = useState(false);
  const [latestTradeDate, setLatestTradeDate] = useState<string | null>(null);
  const [latestSingleRun, setLatestSingleRun] = useState<PaperTradingRunOut | null>(null);
  const [latestMultiRun, setLatestMultiRun] =
    useState<MultiStrategyPaperTradingRunOut | null>(null);

  const [accountError, setAccountError] = useState<string | null>(null);
  const [portfolioError, setPortfolioError] = useState<string | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [editingAccountId, setEditingAccountId] = useState<string | null>(null);
  const [allocationDrafts, setAllocationDrafts] = useState<Record<string, string>>({});
  const [allocationErrors, setAllocationErrors] = useState<Record<string, string>>({});
  const [savingPortfolioId, setSavingPortfolioId] = useState<string | null>(null);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);

  const activeStrategies = useMemo(
    () => strategies.filter((item) => item.status === "active" && item.engine_ready),
    [strategies]
  );
  const currentPortfolios = useMemo(
    () => workspace?.portfolios ?? [],
    [workspace?.portfolios]
  );
  const currentAccount = useMemo(
    () => accounts.find((item) => item.id === selectedAccountId) ?? null,
    [accounts, selectedAccountId]
  );
  const runnablePortfolios = useMemo(
    () => currentPortfolios.filter((item) => item.status === "active"),
    [currentPortfolios]
  );
  const isEditingAccount = Boolean(editingAccountId);

  const formatMoney = useCallback(
    (value?: number | null, currency = "USD") => {
      if (typeof value !== "number" || Number.isNaN(value)) {
        return "-";
      }
      return new Intl.NumberFormat(locale, {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(value);
    },
    [locale]
  );

  const formatNumber = useCallback(
    (value?: number | null, digits = 2) => {
      if (typeof value !== "number" || Number.isNaN(value)) {
        return "-";
      }
      return value.toLocaleString(locale, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    },
    [locale]
  );

  const resetAccountForm = useCallback(() => {
    setEditingAccountId(null);
    setAccountName("");
    setApiKeyEnv("ALPACA_API_KEY");
    setSecretKeyEnv("ALPACA_SECRET_KEY");
    setBaseUrl("https://paper-api.alpaca.markets");
    setTimeoutSeconds(20);
    setAccountNotes("");
    setAccountError(null);
  }, []);

  const populateAccountForm = useCallback((account: PaperTradingAccountOut) => {
    setEditingAccountId(account.id);
    setAccountName(account.name);
    setApiKeyEnv(account.api_key_env);
    setSecretKeyEnv(account.secret_key_env);
    setBaseUrl(account.base_url);
    setTimeoutSeconds(Number(account.timeout_seconds || 20));
    setAccountNotes(account.notes || "");
    setAccountError(null);
  }, []);

  const refreshCatalog = useCallback(
    async (preferredAccountId?: string) => {
      const [accountItems, strategyItems, latestTradeDatePayload] = await Promise.all([
        listPaperAccounts(),
        listStrategies(),
        getLatestPaperTradingTradeDate(),
      ]);

      const storedAccountId =
        typeof window !== "undefined"
          ? window.localStorage.getItem(LAST_SELECTED_PAPER_ACCOUNT_KEY) || ""
          : "";
      const nextAccountId =
        preferredAccountId ||
        (selectedAccountId && accountItems.some((item) => item.id === selectedAccountId)
          ? selectedAccountId
          : storedAccountId && accountItems.some((item) => item.id === storedAccountId)
            ? storedAccountId
          : accountItems[0]?.id || "");
      const preferredStrategy = strategyItems.find(
        (item) => item.status === "active" && item.engine_ready
      );

      setAccounts(accountItems);
      setStrategies(strategyItems);
      setSelectedAccountId(nextAccountId);
      setSelectedStrategyId(
        (current) => current || preferredStrategy?.id || strategyItems[0]?.id || ""
      );
      setLatestTradeDate(latestTradeDatePayload.latest_trade_date || null);

      if (latestTradeDatePayload.latest_trade_date) {
        setSingleTradeDate((current) =>
          current === TODAY_INPUT_VALUE ? latestTradeDatePayload.latest_trade_date || current : current
        );
        setMultiTradeDate((current) =>
          current === TODAY_INPUT_VALUE ? latestTradeDatePayload.latest_trade_date || current : current
        );
      }
    },
    [selectedAccountId]
  );

  const refreshWorkspace = useCallback(
    async (accountId: string) => {
      if (!accountId) {
        setWorkspace(null);
        return;
      }

      try {
        setWorkspaceLoading(true);
        setError(null);
        const payload = await getPaperAccountWorkspace(accountId);
        setWorkspace(payload);
      } catch (err: any) {
        setError(
          err?.message ||
            txt(
              "加载 Paper Trading 工作台失败，请检查账户配置或后端日志。",
              "Failed to load the paper trading workspace. Check the account setup or backend logs."
            )
        );
      } finally {
        setWorkspaceLoading(false);
      }
    },
    [txt]
  );

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        await refreshCatalog();
      } catch (err: any) {
        if (!cancelled) {
          setError(
            err?.message ||
              txt(
                "初始化 Paper Trading 工作台失败。",
                "Failed to initialize the paper trading workspace."
              )
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [refreshCatalog, txt]);

  useEffect(() => {
    if (!selectedAccountId) {
      setWorkspace(null);
      return;
    }
    void refreshWorkspace(selectedAccountId);
  }, [refreshWorkspace, selectedAccountId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (!selectedAccountId) {
      window.localStorage.removeItem(LAST_SELECTED_PAPER_ACCOUNT_KEY);
      return;
    }
    window.localStorage.setItem(LAST_SELECTED_PAPER_ACCOUNT_KEY, selectedAccountId);
  }, [selectedAccountId]);

  useEffect(() => {
    if (!accountMenuOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };

    window.addEventListener("mousedown", handlePointerDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
    };
  }, [accountMenuOpen]);

  useEffect(() => {
    setAllocationDrafts({});
    setAllocationErrors({});
    setSavingPortfolioId(null);
  }, [selectedAccountId]);

  useEffect(() => {
    if (!runnablePortfolios.length) {
      setSelectedPortfolioName("");
      return;
    }
    setSelectedPortfolioName((current) =>
      runnablePortfolios.some((item) => item.name === current)
        ? current
        : runnablePortfolios[0]?.name || ""
    );
  }, [runnablePortfolios]);

  useEffect(() => {
    if (!activeStrategies.length) {
      setSelectedStrategyId("");
      return;
    }
    setSelectedStrategyId((current) =>
      activeStrategies.some((item) => item.id === current)
        ? current
        : activeStrategies[0]?.id || ""
    );
  }, [activeStrategies]);

  const handleRefreshClick = async () => {
    await refreshCatalog(selectedAccountId);
    if (selectedAccountId) {
      await refreshWorkspace(selectedAccountId);
    }
  };

  const handleSaveAccount = async (event: FormEvent) => {
    event.preventDefault();
    setAccountError(null);

    const basePayload: PaperTradingAccountUpdate = {
      name: accountName.trim(),
      api_key_env: apiKeyEnv.trim(),
      secret_key_env: secretKeyEnv.trim(),
      base_url: baseUrl.trim(),
      timeout_seconds: Number(timeoutSeconds),
      notes: accountNotes.trim() || null,
      status: "active",
    };

    if (!basePayload.name) {
      setAccountError(txt("请输入账户名称", "Please enter an account name"));
      return;
    }

    try {
      if (isEditingAccount && editingAccountId) {
        setEditingAccount(true);
      } else {
        setCreatingAccount(true);
      }
      const saved = isEditingAccount && editingAccountId
        ? await updatePaperAccount(editingAccountId, basePayload)
        : await createPaperAccount({
            ...basePayload,
            broker: "alpaca",
            mode: "paper",
          } satisfies PaperTradingAccountCreate);
      await refreshCatalog(saved.id);
      await refreshWorkspace(saved.id);
      if (isEditingAccount) {
        populateAccountForm(saved);
      } else {
        resetAccountForm();
      }
    } catch (err: any) {
      setAccountError(
        err?.message ||
          (isEditingAccount
            ? txt("更新 Paper Account 失败", "Failed to update the paper account")
            : txt("创建 Paper Account 失败", "Failed to create the paper account"))
      );
    } finally {
      setCreatingAccount(false);
      setEditingAccount(false);
    }
  };

  const handleDeleteAccount = async () => {
    if (!selectedAccountId) {
      return;
    }

    const account = accounts.find((item) => item.id === selectedAccountId);
    const confirmed = window.confirm(
      txt(
        `确认删除账户 "${account?.name || selectedAccountId}" 吗？它下面的 portfolios 和 allocations 也会一起删除。`,
        `Delete account "${account?.name || selectedAccountId}"? Its portfolios and allocations will be deleted as well.`
      )
    );
    if (!confirmed) {
      return;
    }

    try {
      setDeletingAccount(true);
      await deletePaperAccount(selectedAccountId);
      if (editingAccountId === selectedAccountId) {
        resetAccountForm();
      }
      const remainingAccounts = accounts.filter((item) => item.id !== selectedAccountId);
      const nextAccountId = remainingAccounts[0]?.id || "";
      await refreshCatalog(nextAccountId);
      if (nextAccountId) {
        await refreshWorkspace(nextAccountId);
      } else {
        setWorkspace(null);
      }
    } catch (err: any) {
      setError(
        err?.message ||
          txt("删除 Paper Account 失败", "Failed to delete the paper account")
      );
    } finally {
      setDeletingAccount(false);
    }
  };

  const handleEditAccount = () => {
    if (!currentAccount) {
      return;
    }
    setActiveWorkbenchPage("account");
    populateAccountForm(currentAccount);
  };

  const handleCancelAccountEdit = () => {
    resetAccountForm();
  };

  const handleCreatePortfolio = async (event: FormEvent) => {
    event.preventDefault();
    setPortfolioError(null);

    if (!selectedAccountId) {
      setPortfolioError(
        txt("请先选择一个账户", "Please select an account first")
      );
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
      setPortfolioError(txt("请输入 portfolio 名称", "Please enter a portfolio name"));
      return;
    }

    try {
      setCreatingPortfolio(true);
      const saved = await createStrategyPortfolio(payload);
      await refreshWorkspace(selectedAccountId);
      setPortfolioName("");
      setPortfolioDescription("");
      setPortfolioStrategyIds([]);
      setSelectedPortfolioName(saved.name);
    } catch (err: any) {
      setPortfolioError(
        err?.message ||
          txt("创建 portfolio 失败", "Failed to create the portfolio")
      );
    } finally {
      setCreatingPortfolio(false);
    }
  };

  const handleDeletePortfolio = async (portfolio: StrategyPortfolioWorkspaceOut) => {
    const confirmed = window.confirm(
      txt(
        `确认删除 portfolio "${portfolio.name}" 吗？它下面的 allocations 会一起删除。`,
        `Delete portfolio "${portfolio.name}"? Its allocations will be deleted as well.`
      )
    );
    if (!confirmed) {
      return;
    }

    try {
      setDeletingPortfolioId(portfolio.id);
      await deleteStrategyPortfolio(portfolio.id);
      await refreshWorkspace(selectedAccountId);
    } catch (err: any) {
      setError(
        err?.message ||
          txt("删除 portfolio 失败", "Failed to delete the portfolio")
      );
    } finally {
      setDeletingPortfolioId(null);
    }
  };

  const handleRunSingle = async (event: FormEvent) => {
    event.preventDefault();
    setExecutionError(null);

    if (!selectedPortfolioName) {
      setExecutionError(
        txt("请先选择一个 portfolio", "Please choose a portfolio first")
      );
      return;
    }
    if (!selectedStrategyId) {
      setExecutionError(
        txt("请先选择一个策略", "Please choose a strategy first")
      );
      return;
    }

    const payload: PaperTradingRunRequest = {
      strategy_id: selectedStrategyId,
      trade_date: singleTradeDate,
      portfolio_name: selectedPortfolioName,
      submit_orders: submitSingleOrders,
    };

    try {
      setRunningSingle(true);
      const result = await createPaperTradingRun(payload);
      setLatestSingleRun(result);
      await refreshWorkspace(selectedAccountId);
    } catch (err: any) {
      setExecutionError(
        err?.message ||
          txt("发起单策略运行失败", "Failed to start the single-strategy run")
      );
    } finally {
      setRunningSingle(false);
    }
  };

  const handleRunMulti = async () => {
    setExecutionError(null);

    if (!selectedPortfolioName) {
      setExecutionError(
        txt("请先选择一个 portfolio", "Please choose a portfolio first")
      );
      return;
    }

    const payload: MultiStrategyPaperTradingRunRequest = {
      trade_date: multiTradeDate,
      portfolio_name: selectedPortfolioName,
      submit_orders: submitMultiOrders,
      continue_on_error: continueOnError,
    };

    try {
      setRunningMulti(true);
      const result = await createMultiStrategyPaperTradingRun(payload);
      setLatestMultiRun(result);
      await refreshWorkspace(selectedAccountId);
    } catch (err: any) {
      setExecutionError(
        err?.message ||
          txt("发起多策略调度失败", "Failed to start the multi-strategy run")
      );
    } finally {
      setRunningMulti(false);
    }
  };

  const brokerAccount = workspace?.broker_account;
  const brokerCurrency = brokerAccount?.currency || "USD";
  const portfolioHistory = workspace?.portfolio_history || null;

  const getAllocationDraftKey = useCallback(
    (portfolioId: string, strategyId: string) => `${portfolioId}:${strategyId}`,
    []
  );

  const getAllocationDraftValue = useCallback(
    (portfolioId: string, strategyId: string, allocationPct: number) => {
      const key = getAllocationDraftKey(portfolioId, strategyId);
      return allocationDrafts[key] ?? String(Math.round(allocationPct * 10000) / 100);
    },
    [allocationDrafts, getAllocationDraftKey]
  );

  const handleAllocationDraftChange = (
    portfolioId: string,
    strategyId: string,
    value: string
  ) => {
    const key = getAllocationDraftKey(portfolioId, strategyId);
    setAllocationDrafts((current) => ({
      ...current,
      [key]: value,
    }));
    setAllocationErrors((current) => ({
      ...current,
      [portfolioId]: "",
    }));
  };

  const getPortfolioDraftTotal = useCallback(
    (portfolio: StrategyPortfolioWorkspaceOut) =>
      portfolio.strategies.reduce((sum, item) => {
        const rawValue = getAllocationDraftValue(portfolio.id, item.strategy_id, item.allocation_pct);
        const parsed = Number(rawValue);
        return sum + (Number.isFinite(parsed) ? parsed : 0);
      }, 0),
    [getAllocationDraftValue]
  );

  const handleSavePortfolioAllocations = async (
    portfolio: StrategyPortfolioWorkspaceOut
  ) => {
    const draftTotalPct = getPortfolioDraftTotal(portfolio);
    if (draftTotalPct > 100.0001) {
      setAllocationErrors((current) => ({
        ...current,
        [portfolio.id]: txt(
          `当前总配比为 ${draftTotalPct.toFixed(2)}%，不能超过 100%。`,
          `The current total allocation is ${draftTotalPct.toFixed(2)}%, which cannot exceed 100%.`
        ),
      }));
      return;
    }

    try {
      setSavingPortfolioId(portfolio.id);
      setAllocationErrors((current) => ({
        ...current,
        [portfolio.id]: "",
      }));

      for (const item of portfolio.strategies) {
        const rawValue = getAllocationDraftValue(portfolio.id, item.strategy_id, item.allocation_pct);
        const parsed = Number(rawValue);
        if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
          throw new Error(
            txt(
              `策略 ${item.strategy_name} 的配比必须在 0 到 100 之间。`,
              `Allocation for ${item.strategy_name} must be between 0 and 100.`
            )
          );
        }

        await upsertStrategyAllocation({
          strategy_id: item.strategy_id,
          portfolio_name: portfolio.name,
          allocation_pct: parsed / 100,
          capital_base: item.capital_base ?? null,
          allow_fractional: item.allow_fractional,
          notes: item.notes ?? null,
          status: item.allocation_status || "active",
        });
      }

      setAllocationDrafts((current) => {
        const next = { ...current };
        for (const item of portfolio.strategies) {
          delete next[getAllocationDraftKey(portfolio.id, item.strategy_id)];
        }
        return next;
      });
      await refreshWorkspace(selectedAccountId);
    } catch (err: any) {
      setAllocationErrors((current) => ({
        ...current,
        [portfolio.id]:
          err?.message ||
          txt("保存 portfolio 资金配比失败", "Failed to save portfolio allocations"),
      }));
    } finally {
      setSavingPortfolioId(null);
    }
  };

  return (
    <AppShell
      title={txt("Paper Trading 账户工作台", "Paper Trading Account Workbench")}
      subtitle={txt(
        "围绕 Alpaca paper account 查看账户状态、挂接的 portfolios、收益、持仓、订单和本地交易明细，并支持快速切换账户。",
        "View Alpaca paper account status, attached portfolios, returns, positions, orders, and local trade history from one workspace."
      )}
      actions={
        <button type="button" onClick={() => void handleRefreshClick()} style={headerButtonStyle}>
          {txt("刷新", "Refresh")}
        </button>
      }
    >
      {loading ? <div style={panelStyle}>{txt("加载中...", "Loading...")}</div> : null}
      {error ? <div style={{ ...panelStyle, ...errorPanelStyle }}>{error}</div> : null}

      {!loading && accounts.length === 0 ? (
        <div style={emptyHeroStyle}>
          <div style={{ maxWidth: 720 }}>
            <div style={eyebrowStyle}>{txt("从账户开始", "Start From Accounts")}</div>
            <h2 style={heroTitleStyle}>
              {txt("先接入一套 Alpaca paper account", "Connect an Alpaca paper account first")}
            </h2>
            <p style={heroBodyStyle}>
              {txt(
                "新工作台不再自动创建 default 账户或 default portfolio。你创建的每个账户和 portfolio 都是显式数据，也都可以删除。",
                "The new workspace no longer auto-creates default accounts or portfolios. Every account and portfolio is explicit and deletable."
              )}
            </p>
          </div>

          <form onSubmit={handleSaveAccount} style={formCardStyle}>
            <div style={sectionTitleRowStyle}>
              <h3 style={sectionTitleStyle}>{txt("创建 Paper Account", "Create Paper Account")}</h3>
              <Badge tone="info">Alpaca</Badge>
            </div>
            <label style={fieldStyle}>
              <span style={fieldLabelStyle}>{txt("账户名", "Account Name")}</span>
              <input
                value={accountName}
                onChange={(event) => setAccountName(event.target.value)}
                placeholder={txt("例如 us-paper-main", "For example us-paper-main")}
                style={inputStyle}
              />
            </label>
            <label style={fieldStyle}>
              <span style={fieldLabelStyle}>{txt("API Key 环境变量", "API Key Env")}</span>
              <input
                value={apiKeyEnv}
                onChange={(event) => setApiKeyEnv(event.target.value)}
                placeholder="ALPACA_KEY_1"
                style={inputStyle}
              />
            </label>
            <label style={fieldStyle}>
              <span style={fieldLabelStyle}>{txt("Secret Key 环境变量", "Secret Key Env")}</span>
              <input
                value={secretKeyEnv}
                onChange={(event) => setSecretKeyEnv(event.target.value)}
                placeholder="ALPACA_SECRET_1"
                style={inputStyle}
              />
            </label>
            <label style={fieldStyle}>
              <span style={fieldLabelStyle}>{txt("Base URL", "Base URL")}</span>
              <input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://paper-api.alpaca.markets"
                style={inputStyle}
              />
            </label>
            <label style={fieldStyle}>
              <span style={fieldLabelStyle}>{txt("超时秒数", "Timeout Seconds")}</span>
              <input
                value={timeoutSeconds}
                onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                type="number"
                min="1"
                step="1"
                style={inputStyle}
              />
            </label>
            <label style={fieldStyle}>
              <span style={fieldLabelStyle}>{txt("备注", "Notes")}</span>
              <textarea
                value={accountNotes}
                onChange={(event) => setAccountNotes(event.target.value)}
                rows={3}
                style={{ ...inputStyle, resize: "vertical" }}
              />
            </label>
            {accountError ? <div style={errorTextStyle}>{accountError}</div> : null}
            <button type="submit" disabled={creatingAccount} style={primaryButtonStyle}>
              {creatingAccount
                ? txt("创建中...", "Creating...")
                : txt("创建账户", "Create Account")}
            </button>
          </form>
        </div>
      ) : null}

      {!loading && accounts.length > 0 ? (
        <div style={{ display: "grid", gap: 18 }}>
          <section style={heroPanelStyle}>
            <div style={toolbarRowStyle}>
              <div>
                <div style={eyebrowStyle}>{txt("当前账户", "Current Account")}</div>
                <h2 style={sectionHeroTitleStyle}>{workspace?.account.name || "-"}</h2>
                <p style={heroMetaStyle}>
                  {txt("支持切换账户、查看实时账户状态和本地工作台数据。", "Switch accounts and inspect both live broker data and local workspace records.")}
                </p>
              </div>
              <div style={toolbarActionsStyle}>
                <Badge tone={workspace?.broker_sync.status === "ok" ? "success" : "warning"}>
                  {workspace?.broker_sync.status === "ok"
                    ? txt("Alpaca 已同步", "Alpaca Synced")
                    : txt("Alpaca 同步失败", "Alpaca Sync Failed")}
                </Badge>
                {workspace?.broker_sync.fetched_at ? (
                  <span style={mutedTextStyle}>
                    {txt("最近同步", "Last sync")} {formatDateTime(workspace.broker_sync.fetched_at, locale)}
                  </span>
                ) : null}
                <div style={accountSwitcherWrapStyle} ref={accountMenuRef}>
                  <button
                    type="button"
                    onClick={() => setAccountMenuOpen((current) => !current)}
                    style={accountMenuButtonStyle(accountMenuOpen)}
                  >
                    <span style={accountMenuButtonTextStyle}>
                      {currentAccount?.name || txt("暂无账户", "No accounts yet")}
                    </span>
                    <span style={accountMenuChevronStyle(accountMenuOpen)}>▾</span>
                  </button>
                  {accountMenuOpen ? (
                    <div style={accountMenuListStyle}>
                      {accounts.length === 0 ? (
                        <div style={accountMenuEmptyStyle}>
                          {txt("暂无账户", "No accounts yet")}
                        </div>
                      ) : (
                        accounts.map((item) => (
                          <button
                            key={item.id}
                            type="button"
                            onClick={() => {
                              setSelectedAccountId(item.id);
                              setAccountMenuOpen(false);
                            }}
                            style={accountMenuItemStyle(item.id === selectedAccountId)}
                          >
                            {item.name}
                          </button>
                        ))
                      )}
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={handleEditAccount}
                  style={secondaryButtonStyle}
                >
                  {txt("编辑账户", "Edit Account")}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteAccount()}
                  disabled={deletingAccount}
                  style={dangerButtonStyle}
                >
                  {deletingAccount
                    ? txt("删除中...", "Deleting...")
                    : txt("删除账户", "Delete Account")}
                </button>
              </div>
            </div>
            {workspace?.broker_sync.error ? (
              <div style={warningStripStyle}>{workspace.broker_sync.error}</div>
            ) : null}
          </section>

          <section style={workbenchSwitchBarStyle}>
            <button
              type="button"
              onClick={() => setActiveWorkbenchPage("account")}
              style={workbenchPageButtonStyle(activeWorkbenchPage === "account")}
            >
              {txt("账户概览", "Account Overview")}
            </button>
            <button
              type="button"
              onClick={() => setActiveWorkbenchPage("portfolios")}
              style={workbenchPageButtonStyle(activeWorkbenchPage === "portfolios")}
            >
              {txt("Portfolio 管理", "Portfolio Management")}
            </button>
          </section>

          {activeWorkbenchPage === "account" ? (
            <>
              <section style={metricGridStyle}>
                <MetricCard
                  label={txt("账户权益", "Account Equity")}
                  value={formatMoney(brokerAccount?.equity, brokerCurrency)}
                  hint={txt("来自 Alpaca 实时账户的 equity。", "Live equity from the Alpaca account.")}
                  accent="#0f766e"
                />
                <MetricCard
                  label={txt("可用现金", "Cash")}
                  value={formatMoney(brokerAccount?.cash, brokerCurrency)}
                  hint={txt("可用现金会直接影响后续 paper order 的可执行性。", "Available cash directly affects what can be submitted next.")}
                  accent="#ea580c"
                />
                <MetricCard
                  label={txt("Buying Power", "Buying Power")}
                  value={formatMoney(brokerAccount?.buying_power, brokerCurrency)}
                  hint={txt("当前账户 buying power。", "Current account buying power.")}
                  accent="#0284c7"
                />
                <MetricCard
                  label={txt("当前 portfolios", "Current Portfolios")}
                  value={String(workspace?.stats.portfolio_count || 0)}
                  hint={txt("这是当前账户下挂接的 portfolio 数量。", "Number of portfolios attached to this account.")}
                  accent="#7c3aed"
                />
              </section>

              <section style={panelStyle}>
                <div style={sectionTitleRowStyle}>
                  <div>
                    <h3 style={sectionTitleStyle}>{txt("账户资金变动", "Account Equity History")}</h3>
                    <p style={sectionSubtitleStyle}>
                      {portfolioHistory?.range_label === "SINCE_INCEPTION"
                        ? txt("从账户创建至今的 Alpaca 账户资金变化。", "Alpaca account equity from account creation through now.")
                        : txt("账户创建已超过一年，当前展示最近一年的资金变化。", "The account is older than one year, so this chart shows the most recent year.")}
                    </p>
                  </div>
                  {portfolioHistory ? (
                    <div style={chartSummaryStyle}>
                      <div style={chartHeadlineStyle}>
                        {formatMoney(portfolioHistory.end_value, brokerCurrency)}
                      </div>
                      <div
                        style={{
                          ...chartDeltaStyle,
                          color: portfolioHistory.absolute_change >= 0 ? "#34d399" : "#fb7185",
                        }}
                      >
                        {portfolioHistory.absolute_change >= 0 ? "+" : ""}
                        {formatMoney(portfolioHistory.absolute_change, brokerCurrency)} ·{" "}
                        {formatPercent(portfolioHistory.percent_change ?? null, 2)}
                      </div>
                    </div>
                  ) : null}
                </div>
                {portfolioHistory ? (
                  <AccountBalanceChart
                    history={portfolioHistory}
                    currency={brokerCurrency}
                    locale={locale}
                    titleFormatter={formatMoney}
                    numberFormatter={formatNumber}
                    text={txt}
                  />
                ) : (
                  <div style={emptyBlockStyle}>
                    {txt(
                      "当前还没有可展示的账户资金历史，可能是 Alpaca 尚未返回 portfolio history。",
                      "There is no account equity history to show yet. Alpaca may not have returned portfolio history."
                    )}
                  </div>
                )}
              </section>

              <div style={workspaceGridStyle}>
                <div style={{ display: "grid", gap: 18 }}>
                  <section style={panelStyle}>
                    <div style={sectionTitleRowStyle}>
                      <h3 style={sectionTitleStyle}>{txt("账户明细", "Account Details")}</h3>
                      <Badge tone="info">{brokerAccount?.account_number || "Alpaca"}</Badge>
                    </div>
                    <div style={detailGridStyle}>
                      <DetailItem label={txt("状态", "Status")} value={brokerAccount?.status || "-"} />
                      <DetailItem label={txt("币种", "Currency")} value={brokerAccount?.currency || "-"} />
                      <DetailItem label={txt("Portfolio Value", "Portfolio Value")} value={formatMoney(brokerAccount?.portfolio_value, brokerCurrency)} />
                      <DetailItem label={txt("Long Market Value", "Long Market Value")} value={formatMoney(brokerAccount?.long_market_value, brokerCurrency)} />
                      <DetailItem label={txt("Short Market Value", "Short Market Value")} value={formatMoney(brokerAccount?.short_market_value, brokerCurrency)} />
                      <DetailItem label={txt("Last Equity", "Last Equity")} value={formatMoney(brokerAccount?.last_equity, brokerCurrency)} />
                      <DetailItem label={txt("Day Trade Count", "Day Trade Count")} value={String(brokerAccount?.daytrade_count ?? "-")} />
                      <DetailItem label={txt("Clock", "Clock")} value={workspace?.broker_clock?.is_open ? txt("开市", "Open") : txt("休市", "Closed")} />
                      <DetailItem label={txt("下次开市", "Next Open")} value={formatDateTime(workspace?.broker_clock?.next_open, locale)} />
                      <DetailItem label={txt("下次收市", "Next Close")} value={formatDateTime(workspace?.broker_clock?.next_close, locale)} />
                    </div>
                  </section>

                  <section style={panelStyle}>
                    <div style={sectionTitleRowStyle}>
                      <h3 style={sectionTitleStyle}>{txt("本地交易明细", "Local Transaction History")}</h3>
                      <Badge>{String(workspace?.recent_transactions.length || 0)}</Badge>
                    </div>
                    <DataTable
                      columns={[
                        txt("时间", "Time"),
                        txt("Portfolio", "Portfolio"),
                        txt("策略", "Strategy"),
                        txt("标的", "Symbol"),
                        txt("方向", "Side"),
                        txt("数量", "Qty"),
                        txt("价格", "Price"),
                        txt("净现金流", "Net Cash Flow"),
                      ]}
                      rows={(workspace?.recent_transactions || []).map((item) => [
                        formatDateTime(item.ts, locale),
                        item.portfolio_name || "-",
                        item.strategy_name || item.strategy_id,
                        item.symbol,
                        item.side,
                        formatNumber(item.qty, 4),
                        formatMoney(item.price, brokerCurrency),
                        <span
                          key={`${item.id}-cash-flow`}
                          style={{
                            color: item.net_cash_flow >= 0 ? "#22c55e" : "#f97316",
                            fontWeight: 700,
                          }}
                        >
                          {formatMoney(item.net_cash_flow, brokerCurrency)}
                        </span>,
                      ])}
                      emptyText={txt("还没有本地交易记录。", "No local transactions yet.")}
                    />
                  </section>
                </div>

                <div style={{ display: "grid", gap: 18 }}>
                  <section style={panelStyle}>
                    <div style={sectionTitleRowStyle}>
                      <h3 style={sectionTitleStyle}>{txt("Alpaca 持仓", "Alpaca Positions")}</h3>
                      <Badge>{String(workspace?.positions.length || 0)}</Badge>
                    </div>
                    <DataTable
                      columns={[
                        txt("标的", "Symbol"),
                        txt("方向", "Side"),
                        txt("数量", "Qty"),
                        txt("现价", "Current"),
                        txt("市值", "Market Value"),
                        txt("未实现盈亏", "Unrealized P/L"),
                      ]}
                      rows={(workspace?.positions || []).map((item: BrokerPositionOut) => [
                        item.symbol,
                        item.side || "-",
                        formatNumber(item.qty, 4),
                        formatMoney(item.current_price, brokerCurrency),
                        formatMoney(item.market_value, brokerCurrency),
                        <span
                          key={`${item.symbol}-pl`}
                          style={{
                            color: (item.unrealized_pl || 0) >= 0 ? "#22c55e" : "#f97316",
                            fontWeight: 700,
                          }}
                        >
                          {formatMoney(item.unrealized_pl, brokerCurrency)}
                        </span>,
                      ])}
                      emptyText={txt("当前没有 Alpaca 持仓。", "There are no Alpaca positions right now.")}
                    />
                  </section>

                  <section style={panelStyle}>
                    <div style={sectionTitleRowStyle}>
                      <h3 style={sectionTitleStyle}>{txt("最近订单", "Recent Orders")}</h3>
                      <Badge>{String(workspace?.recent_orders.length || 0)}</Badge>
                    </div>
                    <DataTable
                      columns={[
                        txt("时间", "Time"),
                        txt("标的", "Symbol"),
                        txt("方向", "Side"),
                        txt("状态", "Status"),
                        txt("数量", "Qty"),
                        txt("成交均价", "Fill Avg"),
                      ]}
                      rows={(workspace?.recent_orders || []).map((item: BrokerOrderOut) => [
                        formatDateTime(item.submitted_at, locale),
                        item.symbol || "-",
                        item.side || "-",
                        item.status || "-",
                        formatNumber(item.qty, 4),
                        formatMoney(item.filled_avg_price, brokerCurrency),
                      ])}
                      emptyText={txt("最近没有 Alpaca 订单。", "There are no recent Alpaca orders.")}
                    />
                  </section>

                  <section style={panelStyle}>
                    <div style={sectionTitleRowStyle}>
                      <h3 style={sectionTitleStyle}>{txt("快速执行", "Quick Execution")}</h3>
                      <Badge tone="warning">{txt("可选 dry run", "Dry Run Ready")}</Badge>
                    </div>
                    <form onSubmit={handleRunSingle} style={formGridStyle}>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("Portfolio", "Portfolio")}</span>
                        <select
                          value={selectedPortfolioName}
                          onChange={(event) => setSelectedPortfolioName(event.target.value)}
                          style={inputStyle}
                        >
                          {runnablePortfolios.map((item) => (
                            <option key={item.id} value={item.name}>
                              {item.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("策略", "Strategy")}</span>
                        <select
                          value={selectedStrategyId}
                          onChange={(event) => setSelectedStrategyId(event.target.value)}
                          style={inputStyle}
                        >
                          {activeStrategies.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("单策略日期", "Single Run Date")}</span>
                        <input
                          type="date"
                          value={singleTradeDate}
                          onChange={(event) => setSingleTradeDate(event.target.value)}
                          style={inputStyle}
                        />
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("多策略日期", "Multi Run Date")}</span>
                        <input
                          type="date"
                          value={multiTradeDate}
                          onChange={(event) => setMultiTradeDate(event.target.value)}
                          style={inputStyle}
                        />
                      </label>
                      <label style={checkboxStyle}>
                        <input
                          type="checkbox"
                          checked={submitSingleOrders}
                          onChange={(event) => setSubmitSingleOrders(event.target.checked)}
                        />
                        <span>{txt("单策略真实提交到 Alpaca", "Submit single run orders to Alpaca")}</span>
                      </label>
                      <label style={checkboxStyle}>
                        <input
                          type="checkbox"
                          checked={submitMultiOrders}
                          onChange={(event) => setSubmitMultiOrders(event.target.checked)}
                        />
                        <span>{txt("多策略真实提交到 Alpaca", "Submit multi-run orders to Alpaca")}</span>
                      </label>
                      <label style={checkboxStyle}>
                        <input
                          type="checkbox"
                          checked={continueOnError}
                          onChange={(event) => setContinueOnError(event.target.checked)}
                        />
                        <span>{txt("多策略遇错继续", "Continue multi-run after errors")}</span>
                      </label>
                      {latestTradeDate ? (
                        <div style={mutedTextStyle}>
                          {txt("最新可用交易日", "Latest available trade date")}: {latestTradeDate}
                        </div>
                      ) : null}
                      {executionError ? <div style={errorTextStyle}>{executionError}</div> : null}
                      <div style={buttonRowStyle}>
                        <button type="submit" disabled={runningSingle} style={primaryButtonStyle}>
                          {runningSingle
                            ? txt("执行中...", "Running...")
                            : txt("运行单策略", "Run Single")}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleRunMulti()}
                          disabled={runningMulti}
                          style={secondaryButtonStyle}
                        >
                          {runningMulti
                            ? txt("调度中...", "Dispatching...")
                            : txt("运行多策略", "Run Multi")}
                        </button>
                      </div>
                    </form>

                    {latestSingleRun ? (
                      <div style={resultCardStyle}>
                        <div style={badgeRowStyle}>
                          <Badge tone={latestSingleRun.status === "completed" ? "success" : "warning"}>
                            {latestSingleRun.status}
                          </Badge>
                          <Badge tone="info">{latestSingleRun.portfolio_name}</Badge>
                        </div>
                        <div style={detailGridStyle}>
                          <DetailItem label={txt("信号数", "Signals")} value={String(latestSingleRun.signal_count)} />
                          <DetailItem label={txt("订单数", "Orders")} value={String(latestSingleRun.order_count)} />
                          <DetailItem label={txt("提交数", "Submitted")} value={String(latestSingleRun.submitted_order_count)} />
                          <DetailItem label={txt("最终权益", "Final Equity")} value={formatMoney(latestSingleRun.final_equity, brokerCurrency)} />
                        </div>
                      </div>
                    ) : null}

                    {latestMultiRun ? (
                      <div style={resultCardStyle}>
                        <div style={badgeRowStyle}>
                          <Badge tone={latestMultiRun.failed_runs > 0 ? "warning" : "success"}>
                            {latestMultiRun.completed_runs}/{latestMultiRun.total_runs}
                          </Badge>
                          <Badge tone="info">{latestMultiRun.portfolio_name}</Badge>
                        </div>
                        <div style={mutedTextStyle}>
                          {txt("失败数", "Failed")}: {latestMultiRun.failed_runs}
                        </div>
                      </div>
                    ) : null}
                  </section>

                  <section style={panelStyle}>
                    <div style={sectionTitleRowStyle}>
                      <h3 style={sectionTitleStyle}>
                        {isEditingAccount
                          ? txt("编辑当前账户", "Edit Current Account")
                          : txt("新增账户", "Add Another Account")}
                      </h3>
                      <Badge tone={isEditingAccount ? "info" : "warning"}>
                        {isEditingAccount ? txt("更新配置", "Update Config") : txt("支持切换", "Switchable")}
                      </Badge>
                    </div>
                    <form onSubmit={handleSaveAccount} style={formGridStyle}>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("账户名", "Account Name")}</span>
                        <input
                          value={accountName}
                          onChange={(event) => setAccountName(event.target.value)}
                          placeholder={txt("例如 us-paper-alt", "For example us-paper-alt")}
                          style={inputStyle}
                        />
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("API Key Env", "API Key Env")}</span>
                        <input
                          value={apiKeyEnv}
                          onChange={(event) => setApiKeyEnv(event.target.value)}
                          placeholder="ALPACA_KEY_1"
                          style={inputStyle}
                        />
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("Secret Key Env", "Secret Key Env")}</span>
                        <input
                          value={secretKeyEnv}
                          onChange={(event) => setSecretKeyEnv(event.target.value)}
                          placeholder="ALPACA_SECRET_1"
                          style={inputStyle}
                        />
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("Base URL", "Base URL")}</span>
                        <input
                          value={baseUrl}
                          onChange={(event) => setBaseUrl(event.target.value)}
                          placeholder="https://paper-api.alpaca.markets"
                          style={inputStyle}
                        />
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("超时秒数", "Timeout Seconds")}</span>
                        <input
                          value={timeoutSeconds}
                          onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                          type="number"
                          min="1"
                          step="1"
                          style={inputStyle}
                        />
                      </label>
                      <label style={fieldStyle}>
                        <span style={fieldLabelStyle}>{txt("备注", "Notes")}</span>
                        <textarea
                          value={accountNotes}
                          onChange={(event) => setAccountNotes(event.target.value)}
                          rows={2}
                          style={{ ...inputStyle, resize: "vertical" }}
                        />
                      </label>
                      {accountError ? <div style={errorTextStyle}>{accountError}</div> : null}
                      <div style={buttonRowStyle}>
                        <button
                          type="submit"
                          disabled={creatingAccount || editingAccount}
                          style={primaryButtonStyle}
                        >
                          {isEditingAccount
                            ? editingAccount
                              ? txt("保存中...", "Saving...")
                              : txt("保存修改", "Save Changes")
                            : creatingAccount
                              ? txt("创建中...", "Creating...")
                              : txt("新增账户", "Add Account")}
                        </button>
                        {isEditingAccount ? (
                          <button
                            type="button"
                            onClick={handleCancelAccountEdit}
                            style={secondaryButtonStyle}
                          >
                            {txt("取消编辑", "Cancel")}
                          </button>
                        ) : null}
                      </div>
                    </form>
                  </section>
                </div>
              </div>
            </>
          ) : null}

          {activeWorkbenchPage === "portfolios" ? (
            <div style={portfolioWorkspaceGridStyle}>
              <section style={panelStyle}>
                <div style={sectionTitleRowStyle}>
                  <div>
                    <h3 style={sectionTitleStyle}>{txt("创建 Portfolio", "Create Portfolio")}</h3>
                    <p style={sectionSubtitleStyle}>
                      {txt(
                        "这个子页面专门管理当前账户下的 portfolios。创建、删除和选择执行目标都在这里完成。",
                        "This subpage is dedicated to managing portfolios under the current account. Creation, deletion, and execution-target selection all happen here."
                      )}
                    </p>
                  </div>
                  <Badge tone="info">{workspace?.account.name || "-"}</Badge>
                </div>
                <form onSubmit={handleCreatePortfolio} style={formGridStyle}>
                  <label style={fieldStyle}>
                    <span style={fieldLabelStyle}>{txt("组合名称", "Portfolio Name")}</span>
                    <input
                      value={portfolioName}
                      onChange={(event) => setPortfolioName(event.target.value)}
                      placeholder={txt("例如 us-growth", "For example us-growth")}
                      style={inputStyle}
                    />
                  </label>
                  <label style={fieldStyle}>
                    <span style={fieldLabelStyle}>{txt("说明", "Description")}</span>
                    <textarea
                      value={portfolioDescription}
                      onChange={(event) => setPortfolioDescription(event.target.value)}
                      rows={3}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                  </label>
                  <div style={fieldStyle}>
                    <span style={fieldLabelStyle}>{txt("初始化策略", "Seed Strategies")}</span>
                    <div style={seedPanelStyle}>
                      {activeStrategies.map((item) => {
                        const checked = portfolioStrategyIds.includes(item.id);
                        return (
                          <label key={item.id} style={seedItemStyle}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) =>
                                setPortfolioStrategyIds((current) =>
                                  event.target.checked
                                    ? [...current, item.id]
                                    : current.filter((value) => value !== item.id)
                                )
                              }
                            />
                            <span>{item.name}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                  {portfolioError ? <div style={errorTextStyle}>{portfolioError}</div> : null}
                  <div style={buttonRowStyle}>
                    <button type="submit" disabled={creatingPortfolio} style={primaryButtonStyle}>
                      {creatingPortfolio
                        ? txt("创建中...", "Creating...")
                        : txt("创建 Portfolio", "Create Portfolio")}
                    </button>
                  </div>
                </form>
              </section>

              <section style={panelStyle}>
                <div style={sectionTitleRowStyle}>
                  <div>
                    <h3 style={sectionTitleStyle}>{txt("当前账户的 Portfolios", "Portfolios In This Account")}</h3>
                    <p style={sectionSubtitleStyle}>
                      {txt(
                        "切换账户后，这里的 portfolio 列表和创建目标都会自动切换。",
                        "After you switch accounts, both the portfolio list here and the creation target update automatically."
                      )}
                    </p>
                  </div>
                  <Badge>{String(currentPortfolios.length)}</Badge>
                </div>

                {workspaceLoading ? (
                  <p style={mutedTextStyle}>{txt("正在刷新账户工作台...", "Refreshing workspace...")}</p>
                ) : null}

                {currentPortfolios.length === 0 ? (
                  <div style={emptyBlockStyle}>
                    {txt(
                      "当前账户下还没有 portfolio。先在左侧创建一个。",
                      "There are no portfolios under this account yet. Create one on the left first."
                    )}
                  </div>
                ) : (
                  <div style={portfolioGridStyle}>
                    {currentPortfolios.map((portfolio) => (
                      <article key={portfolio.id} style={portfolioCardStyle}>
                        <div style={sectionTitleRowStyle}>
                          <div>
                            <h4 style={portfolioTitleStyle}>{portfolio.name}</h4>
                            <p style={portfolioBodyStyle}>
                              {portfolio.description || txt("暂无说明", "No description yet")}
                            </p>
                          </div>
                          <div style={buttonRowStyle}>
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedPortfolioName(portfolio.name);
                                setActiveWorkbenchPage("account");
                              }}
                              style={smallSecondaryButtonStyle}
                            >
                              {selectedPortfolioName === portfolio.name
                                ? txt("当前执行组合", "Selected For Runs")
                                : txt("用于执行", "Use For Runs")}
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleDeletePortfolio(portfolio)}
                              disabled={deletingPortfolioId === portfolio.id}
                              style={smallDangerButtonStyle}
                            >
                              {deletingPortfolioId === portfolio.id
                                ? txt("删除中...", "Deleting...")
                                : txt("删除", "Delete")}
                            </button>
                          </div>
                        </div>
                        <div style={badgeRowStyle}>
                          <Badge tone={portfolio.status === "active" ? "success" : "warning"}>
                            {portfolio.status}
                          </Badge>
                          {selectedPortfolioName === portfolio.name ? (
                            <Badge tone="info">{txt("执行目标", "Execution Target")}</Badge>
                          ) : null}
                          <Badge tone="info">{formatPercent(portfolio.active_allocation_pct_total, 0)}</Badge>
                          <Badge>{txt(`${portfolio.active_allocation_count} 条 active allocations`, `${portfolio.active_allocation_count} active allocations`)}</Badge>
                        </div>
                        <div style={detailGridStyle}>
                          <DetailItem
                            label={txt("最新虚拟权益", "Latest Virtual Equity")}
                            value={formatMoney(portfolio.latest_run_equity, brokerCurrency)}
                          />
                          <DetailItem
                            label={txt("最新收益率", "Latest Return")}
                            value={formatPercent(portfolio.latest_run_return_pct ?? null, 2)}
                          />
                          <DetailItem
                            label={txt("本地交易数", "Local Trades")}
                            value={String(portfolio.transaction_count)}
                          />
                          <DetailItem
                            label={txt("净现金流", "Net Cash Flow")}
                            value={formatMoney(portfolio.net_cash_flow, brokerCurrency)}
                          />
                          <DetailItem
                            label={txt("最近运行", "Latest Run")}
                            value={portfolio.latest_run_status || "-"}
                          />
                          <DetailItem
                            label={txt("最近交易", "Latest Trade")}
                            value={formatDateTime(portfolio.latest_transaction_at, locale)}
                          />
                        </div>
                        {portfolio.strategies.length > 0 ? (
                          <div style={strategyListStyle}>
                            <div style={allocationSummaryRowStyle}>
                              <div style={mutedTextStyle}>
                                {txt("当前总配比", "Current Total Allocation")}:{" "}
                                <strong
                                  style={{
                                    color:
                                      getPortfolioDraftTotal(portfolio) > 100.0001
                                        ? "#fda4af"
                                        : "#f8fafc",
                                  }}
                                >
                                  {formatNumber(getPortfolioDraftTotal(portfolio), 2)}%
                                </strong>
                              </div>
                              <button
                                type="button"
                                onClick={() => void handleSavePortfolioAllocations(portfolio)}
                                disabled={savingPortfolioId === portfolio.id}
                                style={smallSecondaryButtonStyle}
                              >
                                {savingPortfolioId === portfolio.id
                                  ? txt("保存中...", "Saving...")
                                  : txt("保存配比", "Save Allocations")}
                              </button>
                            </div>
                            {allocationErrors[portfolio.id] ? (
                              <div style={errorTextStyle}>{allocationErrors[portfolio.id]}</div>
                            ) : null}
                            {portfolio.strategies.map((item) => (
                              <div key={`${portfolio.id}-${item.strategy_id}`} style={strategyRowStyle}>
                                <div style={{ flex: "1 1 220px" }}>
                                  <div style={strategyNameStyle}>{item.strategy_name}</div>
                                  <div style={mutedTextStyle}>
                                    {item.strategy_type} · {item.allocation_status}
                                  </div>
                                </div>
                                <div style={allocationEditorStyle}>
                                  <input
                                    type="number"
                                    min="0"
                                    max="100"
                                    step="0.01"
                                    value={getAllocationDraftValue(
                                      portfolio.id,
                                      item.strategy_id,
                                      item.allocation_pct
                                    )}
                                    onChange={(event) =>
                                      handleAllocationDraftChange(
                                        portfolio.id,
                                        item.strategy_id,
                                        event.target.value
                                      )
                                    }
                                    style={allocationInputStyle}
                                  />
                                  <span style={allocationSuffixStyle}>%</span>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </div>
          ) : null}
        </div>
      ) : null}
    </AppShell>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div style={detailItemStyle}>
      <div style={detailLabelStyle}>{label}</div>
      <div style={detailValueStyle}>{value}</div>
    </div>
  );
}

function DataTable({
  columns,
  rows,
  emptyText,
}: {
  columns: string[];
  rows: Array<Array<string | JSX.Element>>;
  emptyText: string;
}) {
  if (rows.length === 0) {
    return <div style={emptyBlockStyle}>{emptyText}</div>;
  }

  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} style={tableHeadStyle}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => (
                <td key={`${index}-${cellIndex}`} style={tableCellStyle}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AccountBalanceChart({
  history,
  currency,
  locale,
  titleFormatter,
  numberFormatter,
  text,
}: {
  history: BrokerPortfolioHistoryOut;
  currency: string;
  locale: string;
  titleFormatter: (value?: number | null, currency?: string) => string;
  numberFormatter: (value?: number | null, digits?: number) => string;
  text: (zh: string, en: string) => string;
}) {
  const width = 960;
  const height = 260;
  const paddingLeft = 18;
  const paddingRight = 18;
  const paddingTop = 18;
  const paddingBottom = 34;
  const values = history.points.map((item) => item.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const yRange = max - min || Math.max(Math.abs(max) * 0.02, 1);
  const drawableWidth = width - paddingLeft - paddingRight;
  const drawableHeight = height - paddingTop - paddingBottom;

  const points = history.points.map((item, index) => {
    const ratio = history.points.length === 1 ? 0 : index / (history.points.length - 1);
    const x = paddingLeft + ratio * drawableWidth;
    const y =
      paddingTop +
      (1 - (item.equity - min) / yRange) * drawableHeight;
    return { ...item, x, y };
  });

  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ");
  const areaPath = points.length
    ? `${linePath} L ${points[points.length - 1].x.toFixed(2)} ${(height - paddingBottom).toFixed(2)} L ${points[0].x.toFixed(2)} ${(height - paddingBottom).toFixed(2)} Z`
    : "";

  const tickValues = [max, max - yRange / 2, min];

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={chartMetaRowStyle}>
        <div style={mutedTextStyle}>
          {text("起点", "Start")}: {titleFormatter(history.start_value, currency)} ·{" "}
          {formatDateTime(history.start_at, locale)}
        </div>
        <div style={mutedTextStyle}>
          {text("终点", "End")}: {titleFormatter(history.end_value, currency)} ·{" "}
          {formatDateTime(history.end_at, locale)}
        </div>
      </div>
      <div style={chartWrapStyle}>
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: 300, display: "block" }}>
          <defs>
            <linearGradient id="account-balance-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(45, 212, 191, 0.32)" />
              <stop offset="100%" stopColor="rgba(45, 212, 191, 0.02)" />
            </linearGradient>
          </defs>
          {tickValues.map((tick) => {
            const y =
              paddingTop +
              (1 - (tick - min) / yRange) * drawableHeight;
            return (
              <g key={tick}>
                <line
                  x1={paddingLeft}
                  x2={width - paddingRight}
                  y1={y}
                  y2={y}
                  stroke="rgba(148, 163, 184, 0.14)"
                  strokeDasharray="4 6"
                />
                <text
                  x={width - paddingRight}
                  y={y - 6}
                  textAnchor="end"
                  fill="rgba(203, 213, 225, 0.72)"
                  fontSize="12"
                  fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
                >
                  {titleFormatter(tick, currency)}
                </text>
              </g>
            );
          })}
          {areaPath ? <path d={areaPath} fill="url(#account-balance-fill)" /> : null}
          {linePath ? (
            <path
              d={linePath}
              fill="none"
              stroke="#2dd4bf"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
          {points.length ? (
            <>
              <circle cx={points[0].x} cy={points[0].y} r="4" fill="#93c5fd" />
              <circle cx={points[points.length - 1].x} cy={points[points.length - 1].y} r="4.5" fill="#2dd4bf" />
            </>
          ) : null}
        </svg>
      </div>
      <div style={chartFooterStyle}>
        <div style={mutedTextStyle}>
          {history.range_label === "SINCE_INCEPTION"
            ? text("区间: 自创建以来", "Range: Since inception")
            : text("区间: 最近一年", "Range: Last 1 year")}
        </div>
        <div style={mutedTextStyle}>
          {text("样本点", "Points")}: {numberFormatter(history.points.length, 0)}
        </div>
      </div>
    </div>
  );
}

const headerButtonStyle: CSSProperties = {
  padding: "10px 16px",
  borderRadius: 999,
  border: "1px solid rgba(94, 234, 212, 0.28)",
  background: "rgba(15, 118, 110, 0.18)",
  color: "#ccfbf1",
  cursor: "pointer",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const heroPanelStyle: CSSProperties = {
  padding: 22,
  borderRadius: 28,
  border: "1px solid rgba(251, 191, 36, 0.18)",
  background:
    "radial-gradient(circle at top right, rgba(251, 191, 36, 0.18), transparent 28%), linear-gradient(135deg, rgba(15, 23, 42, 0.92), rgba(17, 24, 39, 0.86))",
  boxShadow: "0 24px 60px rgba(2, 6, 23, 0.24)",
};

const panelStyle: CSSProperties = {
  padding: 22,
  borderRadius: 26,
  border: "1px solid rgba(148, 163, 184, 0.14)",
  background: "linear-gradient(180deg, rgba(11, 23, 35, 0.9), rgba(15, 23, 42, 0.86))",
  boxShadow: "0 18px 42px rgba(2, 6, 23, 0.22)",
};

const errorPanelStyle: CSSProperties = {
  borderColor: "rgba(248, 113, 113, 0.24)",
  color: "#fecaca",
};

const emptyHeroStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(320px, 1.1fr) minmax(320px, 0.9fr)",
  gap: 20,
  alignItems: "start",
};

const heroTitleStyle: CSSProperties = {
  margin: "0 0 10px",
  fontSize: 38,
  lineHeight: 1.05,
  color: "#f8fafc",
};

const heroBodyStyle: CSSProperties = {
  margin: 0,
  color: "rgba(226, 232, 240, 0.82)",
  lineHeight: 1.8,
  fontSize: 16,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const eyebrowStyle: CSSProperties = {
  marginBottom: 10,
  color: "#fbbf24",
  fontSize: 12,
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const sectionHeroTitleStyle: CSSProperties = {
  margin: "0 0 8px",
  fontSize: 28,
  color: "#f8fafc",
};

const heroMetaStyle: CSSProperties = {
  margin: 0,
  color: "rgba(226, 232, 240, 0.76)",
  lineHeight: 1.7,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const toolbarRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  flexWrap: "wrap",
  alignItems: "flex-start",
};

const toolbarActionsStyle: CSSProperties = {
  display: "flex",
  gap: 10,
  flexWrap: "wrap",
  alignItems: "center",
  justifyContent: "flex-end",
};

const accountSwitcherWrapStyle: CSSProperties = {
  display: "block",
  minWidth: 240,
  position: "relative",
};

function accountMenuButtonStyle(open: boolean): CSSProperties {
  return {
    width: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    padding: "10px 14px",
    borderRadius: 16,
    border: open
      ? "1px solid rgba(45, 212, 191, 0.28)"
      : "1px solid rgba(148, 163, 184, 0.18)",
    background: "rgba(15, 23, 42, 0.82)",
    color: "#f8fafc",
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
}

const accountMenuButtonTextStyle: CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  textAlign: "left",
  flex: "1 1 auto",
};

function accountMenuChevronStyle(open: boolean): CSSProperties {
  return {
    color: "rgba(203, 213, 225, 0.82)",
    transform: open ? "rotate(180deg)" : "rotate(0deg)",
    transition: "transform 140ms ease",
  };
}

const accountMenuListStyle: CSSProperties = {
  position: "absolute",
  top: "calc(100% + 8px)",
  right: 0,
  minWidth: "100%",
  display: "grid",
  gap: 6,
  padding: 8,
  borderRadius: 18,
  border: "1px solid rgba(71, 85, 105, 0.32)",
  background: "linear-gradient(180deg, rgba(8,15,24,0.96), rgba(15,23,42,0.94))",
  boxShadow: "0 18px 36px rgba(2, 6, 23, 0.34)",
  zIndex: 5,
};

const accountMenuEmptyStyle: CSSProperties = {
  padding: "10px 12px",
  borderRadius: 12,
  color: "rgba(203, 213, 225, 0.72)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

function accountMenuItemStyle(active: boolean): CSSProperties {
  return {
    width: "100%",
    padding: "10px 12px",
    borderRadius: 12,
    border: active
      ? "1px solid rgba(45, 212, 191, 0.24)"
      : "1px solid transparent",
    background: active ? "rgba(15, 118, 110, 0.16)" : "rgba(15, 23, 42, 0.42)",
    color: active ? "#99f6e4" : "#f8fafc",
    textAlign: "left",
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
    fontWeight: active ? 700 : 500,
  };
}

const warningStripStyle: CSSProperties = {
  marginTop: 14,
  padding: "12px 14px",
  borderRadius: 18,
  background: "rgba(127, 29, 29, 0.18)",
  border: "1px solid rgba(248, 113, 113, 0.18)",
  color: "#fecaca",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  lineHeight: 1.6,
};

const metricGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 16,
};

const workspaceGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1.45fr) minmax(360px, 0.95fr)",
  gap: 18,
  alignItems: "start",
};

const sectionTitleRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
  alignItems: "center",
  marginBottom: 16,
};

const sectionTitleStyle: CSSProperties = {
  margin: 0,
  fontSize: 22,
  color: "#f8fafc",
};

const sectionSubtitleStyle: CSSProperties = {
  margin: "6px 0 0",
  color: "rgba(203, 213, 225, 0.72)",
  lineHeight: 1.6,
  fontSize: 14,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const chartSummaryStyle: CSSProperties = {
  display: "grid",
  gap: 6,
  justifyItems: "end",
};

const chartHeadlineStyle: CSSProperties = {
  color: "#f8fafc",
  fontSize: 28,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const chartDeltaStyle: CSSProperties = {
  fontSize: 15,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const chartMetaRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
  alignItems: "center",
};

const chartWrapStyle: CSSProperties = {
  borderRadius: 22,
  border: "1px solid rgba(45, 212, 191, 0.12)",
  background:
    "radial-gradient(circle at top, rgba(45, 212, 191, 0.08), transparent 30%), rgba(8, 15, 24, 0.82)",
  padding: 12,
  overflow: "hidden",
};

const chartFooterStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
  alignItems: "center",
};

const detailGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
  gap: 12,
};

const detailItemStyle: CSSProperties = {
  padding: 14,
  borderRadius: 18,
  background: "rgba(15, 23, 42, 0.76)",
  border: "1px solid rgba(148, 163, 184, 0.12)",
};

const detailLabelStyle: CSSProperties = {
  marginBottom: 6,
  color: "rgba(203, 213, 225, 0.72)",
  fontSize: 12,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const detailValueStyle: CSSProperties = {
  color: "#f8fafc",
  fontSize: 18,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const mutedTextStyle: CSSProperties = {
  color: "rgba(203, 213, 225, 0.74)",
  lineHeight: 1.6,
  fontSize: 14,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const workbenchSwitchBarStyle: CSSProperties = {
  display: "flex",
  gap: 10,
  flexWrap: "wrap",
  alignItems: "center",
};

function workbenchPageButtonStyle(active: boolean): CSSProperties {
  return {
    padding: "11px 16px",
    borderRadius: 999,
    border: active
      ? "1px solid rgba(45, 212, 191, 0.28)"
      : "1px solid rgba(148, 163, 184, 0.18)",
    background: active
      ? "linear-gradient(135deg, rgba(15,118,110,0.22), rgba(8,145,178,0.16))"
      : "rgba(15, 23, 42, 0.72)",
    color: active ? "#99f6e4" : "#e2e8f0",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
}

const portfolioGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 14,
};

const portfolioWorkspaceGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(320px, 0.85fr) minmax(0, 1.15fr)",
  gap: 18,
  alignItems: "start",
};

const portfolioCardStyle: CSSProperties = {
  padding: 18,
  borderRadius: 22,
  border: "1px solid rgba(94, 234, 212, 0.14)",
  background:
    "radial-gradient(circle at top right, rgba(45, 212, 191, 0.12), transparent 25%), rgba(8, 15, 24, 0.88)",
  display: "grid",
  gap: 14,
};

const portfolioTitleStyle: CSSProperties = {
  margin: "0 0 6px",
  fontSize: 20,
  color: "#f8fafc",
};

const portfolioBodyStyle: CSSProperties = {
  margin: 0,
  color: "rgba(203, 213, 225, 0.76)",
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const badgeRowStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const strategyListStyle: CSSProperties = {
  display: "grid",
  gap: 10,
};

const allocationSummaryRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
  alignItems: "center",
};

const strategyRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 10,
  alignItems: "center",
  padding: 12,
  borderRadius: 16,
  background: "rgba(15, 23, 42, 0.62)",
  border: "1px solid rgba(148, 163, 184, 0.1)",
};

const strategyNameStyle: CSSProperties = {
  color: "#f8fafc",
  fontWeight: 700,
  marginBottom: 4,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const allocationEditorStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flex: "0 0 auto",
};

const allocationInputStyle: CSSProperties = {
  width: 96,
  padding: "10px 12px",
  borderRadius: 12,
  border: "1px solid rgba(148, 163, 184, 0.18)",
  background: "rgba(15, 23, 42, 0.84)",
  color: "#f8fafc",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  fontWeight: 700,
  textAlign: "right",
};

const allocationSuffixStyle: CSSProperties = {
  color: "rgba(203, 213, 225, 0.72)",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const formCardStyle: CSSProperties = {
  ...panelStyle,
  display: "grid",
  gap: 12,
};

const formGridStyle: CSSProperties = {
  display: "grid",
  gap: 12,
};

const fieldStyle: CSSProperties = {
  display: "grid",
  gap: 8,
};

const fieldLabelStyle: CSSProperties = {
  color: "#f8fafc",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "12px 14px",
  borderRadius: 16,
  border: "1px solid rgba(148, 163, 184, 0.18)",
  background: "rgba(15, 23, 42, 0.84)",
  color: "#f8fafc",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const checkboxStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  color: "#e2e8f0",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const primaryButtonStyle: CSSProperties = {
  padding: "12px 16px",
  borderRadius: 16,
  border: "none",
  background: "linear-gradient(135deg, #0f766e, #0891b2)",
  color: "#f8fafc",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const secondaryButtonStyle: CSSProperties = {
  padding: "12px 16px",
  borderRadius: 16,
  border: "1px solid rgba(148, 163, 184, 0.18)",
  background: "rgba(15, 23, 42, 0.82)",
  color: "#f8fafc",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const smallSecondaryButtonStyle: CSSProperties = {
  ...secondaryButtonStyle,
  padding: "8px 12px",
  borderRadius: 999,
};

const dangerButtonStyle: CSSProperties = {
  padding: "11px 16px",
  borderRadius: 16,
  border: "1px solid rgba(248, 113, 113, 0.24)",
  background: "rgba(127, 29, 29, 0.22)",
  color: "#fecaca",
  cursor: "pointer",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const smallDangerButtonStyle: CSSProperties = {
  ...dangerButtonStyle,
  padding: "8px 12px",
  borderRadius: 999,
};

const errorTextStyle: CSSProperties = {
  color: "#fda4af",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  lineHeight: 1.6,
};

const emptyBlockStyle: CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: "rgba(15, 23, 42, 0.7)",
  color: "rgba(203, 213, 225, 0.78)",
  lineHeight: 1.7,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const tableWrapStyle: CSSProperties = {
  overflowX: "auto",
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  minWidth: 660,
};

const tableHeadStyle: CSSProperties = {
  textAlign: "left",
  padding: "0 10px 12px",
  color: "rgba(203, 213, 225, 0.72)",
  fontSize: 12,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  borderBottom: "1px solid rgba(148, 163, 184, 0.14)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const tableCellStyle: CSSProperties = {
  padding: "14px 10px",
  borderBottom: "1px solid rgba(148, 163, 184, 0.08)",
  color: "#f8fafc",
  fontSize: 14,
  lineHeight: 1.5,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  verticalAlign: "top",
};

const resultCardStyle: CSSProperties = {
  marginTop: 14,
  padding: 14,
  borderRadius: 18,
  background: "rgba(15, 118, 110, 0.12)",
  border: "1px solid rgba(45, 212, 191, 0.18)",
  display: "grid",
  gap: 10,
};

const buttonRowStyle: CSSProperties = {
  display: "flex",
  gap: 10,
  flexWrap: "wrap",
};

const seedPanelStyle: CSSProperties = {
  maxHeight: 220,
  overflowY: "auto",
  display: "grid",
  gap: 8,
  padding: 12,
  borderRadius: 16,
  border: "1px solid rgba(148, 163, 184, 0.18)",
  background: "rgba(15, 23, 42, 0.72)",
};

const seedItemStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  color: "#e2e8f0",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};
