import type { CSSProperties, FormEvent, MouseEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createPaperAccount,
  createStrategyPortfolio,
  getPaperAccountOverview,
  listPaperAccounts,
  listStrategyPortfolios,
  renameStrategyPortfolio,
} from "@/api/paper-accounts";
import {
  createMultiStrategyPaperTradingRun,
  createPaperTradingRun,
  getLatestPaperTradingTradeDate,
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
  StrategyPortfolioRename,
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
import { useI18n } from "@/i18n/provider";

function toDateInputValue(dt: Date): string {
  return dt.toISOString().slice(0, 10);
}

const TODAY_INPUT_VALUE = toDateInputValue(new Date());

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

export default function PaperTradingPage() {
  const { locale, messages, t } = useI18n();
  const copy = messages.paperTrading;
  const isZh = locale === "zh-CN";
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
  const [renamingPortfolio, setRenamingPortfolio] = useState(false);
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
  const [editingPortfolioId, setEditingPortfolioId] = useState<string | null>(null);
  const [editingPortfolioName, setEditingPortfolioName] = useState("");
  const [editingPortfolioError, setEditingPortfolioError] = useState<string | null>(null);
  const [portfolioRenameSuccess, setPortfolioRenameSuccess] = useState<{
    id: string;
    message: string;
  } | null>(null);

  const [allocationStrategyId, setAllocationStrategyId] = useState("");
  const [allocationPortfolioName, setAllocationPortfolioName] = useState("default");
  const [allocationPct, setAllocationPct] = useState(0.25);
  const [capitalBase, setCapitalBase] = useState("");
  const [allowFractional, setAllowFractional] = useState(true);
  const [allocationStatus, setAllocationStatus] = useState("active");
  const [notes, setNotes] = useState("");
  const [expandedAllocationPortfolioName, setExpandedAllocationPortfolioName] =
    useState<string | null>(null);
  const [editingAllocationId, setEditingAllocationId] = useState<string | null>(null);

  const [strategyId, setStrategyId] = useState("");
  const [singlePortfolioName, setSinglePortfolioName] = useState("default");
  const [singleTradeDate, setSingleTradeDate] = useState(TODAY_INPUT_VALUE);
  const [basketId, setBasketId] = useState("");
  const [singleSubmitOrders, setSingleSubmitOrders] = useState(false);

  const [multiPortfolioName, setMultiPortfolioName] = useState("default");
  const [multiTradeDate, setMultiTradeDate] = useState(TODAY_INPUT_VALUE);
  const [multiSubmitOrders, setMultiSubmitOrders] = useState(false);
  const [continueOnError, setContinueOnError] = useState(false);
  const [latestAvailableTradeDate, setLatestAvailableTradeDate] = useState<string | null>(null);

  const workspaceTabs: Array<{
    key: WorkspaceTab;
    label: string;
    description: string;
  }> = [
    {
      key: "accounts",
      label: copy.tabs.accounts.label,
      description: copy.tabs.accounts.description,
    },
    {
      key: "allocations",
      label: copy.tabs.allocations.label,
      description: copy.tabs.allocations.description,
    },
    {
      key: "execution",
      label: copy.tabs.execution.label,
      description: copy.tabs.execution.description,
    },
  ];

  const formatNumber = (value: number) => value.toLocaleString(locale);
  const allocationCountLabel = (count: number) =>
    isZh ? `${count} ${copy.allocations.countAllocations}` : `${count} ${count === 1 ? "allocation" : copy.allocations.countAllocations}`;
  const activeAllocationCountLabel = (count: number) =>
    isZh ? `${count} ${copy.allocations.countActive}` : `${count} ${copy.allocations.countActive}`;
  const strategyCountLabel = (count: number) =>
    isZh ? `${count} ${copy.accounts.summary.strategiesCount}` : `${count} ${count === 1 ? "strategy" : copy.accounts.summary.strategiesCount}`;
  const singleTradeDateDescription = latestAvailableTradeDate
    ? `${copy.execution.singleTradeDateDescription} ${t(
        "paperTrading.execution.latestTradeDateSuffix",
        {
          date: latestAvailableTradeDate,
        }
      )}`
    : copy.execution.singleTradeDateDescription;
  const multiTradeDateDescription = latestAvailableTradeDate
    ? `${copy.execution.multiTradeDateDescription} ${t(
        "paperTrading.execution.latestTradeDateSuffix",
        {
          date: latestAvailableTradeDate,
        }
      )}`
    : copy.execution.multiTradeDateDescription;

  const refreshOverview = useCallback(
    async (accountId: string) => {
      if (!accountId) {
        setAccountOverview(null);
        return;
      }
      try {
        setOverviewLoading(true);
        const payload = await getPaperAccountOverview(accountId);
        setAccountOverview(payload);
      } catch (err: any) {
        setError(err?.message || copy.loadOverviewFailed);
      } finally {
        setOverviewLoading(false);
      }
    },
    [copy.loadOverviewFailed]
  );

  const refreshWorkspace = async (preferredAccountId?: string) => {
    const [strategyItems, basketItems, allocationItems, accountItems, portfolioItems, latestTradeDatePayload] =
      await Promise.all([
        listStrategies(),
        listStockBaskets(),
        listStrategyAllocations(),
        listPaperAccounts(),
        listStrategyPortfolios(),
        getLatestPaperTradingTradeDate(),
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
    setLatestAvailableTradeDate(latestTradeDatePayload.latest_trade_date || null);
    setStrategyId((current) => current || preferredStrategy?.id || strategyItems[0]?.id || "");
    setAllocationStrategyId(
      (current) => current || preferredStrategy?.id || strategyItems[0]?.id || ""
    );
    setSelectedAccountId(nextAccountId);
    if (latestTradeDatePayload.latest_trade_date) {
      setSingleTradeDate((current) =>
        current === TODAY_INPUT_VALUE ? latestTradeDatePayload.latest_trade_date || current : current
      );
      setMultiTradeDate((current) =>
        current === TODAY_INPUT_VALUE ? latestTradeDatePayload.latest_trade_date || current : current
      );
    }
  };

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      listStrategies(),
      listStockBaskets(),
      listStrategyAllocations(),
      listPaperAccounts(),
      listStrategyPortfolios(),
      getLatestPaperTradingTradeDate(),
    ])
      .then(
        ([
          strategyItems,
          basketItems,
          allocationItems,
          accountItems,
          portfolioItems,
          latestTradeDatePayload,
        ]) => {
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
        setLatestAvailableTradeDate(latestTradeDatePayload.latest_trade_date || null);
        setStrategyId(preferredStrategy?.id || strategyItems[0]?.id || "");
        setAllocationStrategyId(preferredStrategy?.id || strategyItems[0]?.id || "");
        setSelectedAccountId(nextAccountId);
        if (latestTradeDatePayload.latest_trade_date) {
          setSingleTradeDate(latestTradeDatePayload.latest_trade_date);
          setMultiTradeDate(latestTradeDatePayload.latest_trade_date);
        }
      })
      .catch((err: any) => {
        if (!cancelled) {
          setError(err?.message || copy.loadWorkspaceFailed);
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
  }, [copy.loadWorkspaceFailed]);

  useEffect(() => {
    if (!selectedAccountId) {
      return;
    }
    void refreshOverview(selectedAccountId);
  }, [refreshOverview, selectedAccountId]);

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
  const activeAllocationPortfolioNamesForSelectedAccount = useMemo(() => {
    return new Set(
      allocations
        .filter(
          (item) => item.paper_account_id === selectedAccountId && item.status === "active"
        )
        .map((item) => item.portfolio_name)
    );
  }, [allocations, selectedAccountId]);

  useEffect(() => {
    const nextPortfolioName =
      portfoliosForSelectedAccount.find((item) =>
        activeAllocationPortfolioNamesForSelectedAccount.has(item.name)
      )?.name ||
      portfoliosForSelectedAccount[0]?.name ||
      "default";
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
  }, [activeAllocationPortfolioNamesForSelectedAccount, portfoliosForSelectedAccount]);

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

  const allocationGroupsForSelectedAccount = useMemo(() => {
    return portfoliosForSelectedAccount
      .map((portfolio) => {
        const items = allocations
          .filter(
            (item) =>
              item.paper_account_id === selectedAccountId &&
              item.portfolio_name === portfolio.name
          )
          .sort((a, b) => {
            return (a.strategy_name || a.strategy_id).localeCompare(
              b.strategy_name || b.strategy_id
            );
          });

        return {
          portfolio,
          items,
          activeCount: items.filter((item) => item.status === "active").length,
          allocationPctTotal: items
            .filter((item) => item.status === "active")
            .reduce((sum, item) => sum + item.allocation_pct, 0),
        };
      })
      .sort((a, b) => a.portfolio.name.localeCompare(b.portfolio.name));
  }, [allocations, portfoliosForSelectedAccount, selectedAccountId]);
  const editingAllocation = useMemo(
    () => allocations.find((item) => item.id === editingAllocationId) || null,
    [allocations, editingAllocationId]
  );

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
      setAccountError(copy.validation.accountNameRequired);
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
      setAccountError(err?.message || copy.errors.createAccountFailed);
    } finally {
      setCreatingAccount(false);
    }
  };

  const handlePortfolioSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setPortfolioError(null);

    if (!selectedAccountId) {
      setPortfolioError(copy.validation.accountRequired);
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
      setPortfolioError(copy.validation.portfolioNameRequired);
      return;
    }
    if (payload.strategy_ids.length === 0) {
      setPortfolioError(copy.validation.portfolioStrategiesRequired);
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
      setPortfolioError(err?.message || copy.errors.createPortfolioFailed);
    } finally {
      setCreatingPortfolio(false);
    }
  };

  const handlePortfolioRenameStart = (portfolio: StrategyPortfolioOut) => {
    setEditingPortfolioId(portfolio.id);
    setEditingPortfolioName(portfolio.name);
    setEditingPortfolioError(null);
    setPortfolioRenameSuccess(null);
  };

  const handlePortfolioRenameCancel = () => {
    setEditingPortfolioId(null);
    setEditingPortfolioName("");
    setEditingPortfolioError(null);
  };

  const handlePortfolioRenameSubmit = async (
    event: FormEvent,
    portfolio: StrategyPortfolioOut
  ) => {
    event.preventDefault();
    const nextName = editingPortfolioName.trim();
    if (!nextName) {
      setEditingPortfolioError(isZh ? "portfolio 名称不能为空" : "Portfolio name cannot be empty");
      return;
    }
    if (nextName === portfolio.name) {
      handlePortfolioRenameCancel();
      return;
    }

    try {
      setRenamingPortfolio(true);
      setEditingPortfolioError(null);
      setPortfolioRenameSuccess(null);
      const payload: StrategyPortfolioRename = { name: nextName };
      const saved = await renameStrategyPortfolio(portfolio.id, payload);
      await refreshWorkspace(selectedAccountId || saved.paper_account_id);
      await refreshOverview(saved.paper_account_id);
      setEditingPortfolioId(null);
      setEditingPortfolioName("");
      setPortfolioRenameSuccess({
        id: saved.id,
        message: isZh ? "portfolio 改名成功" : "Portfolio renamed successfully",
      });
      setSinglePortfolioName((current) => (current === portfolio.name ? saved.name : current));
      setMultiPortfolioName((current) => (current === portfolio.name ? saved.name : current));
      setAllocationPortfolioName((current) => (current === portfolio.name ? saved.name : current));
      setExpandedAllocationPortfolioName((current) => (current === portfolio.name ? saved.name : current));
    } catch (err: any) {
      setEditingPortfolioError(err?.message || (isZh ? "portfolio 改名失败" : "Failed to rename portfolio"));
    } finally {
      setRenamingPortfolio(false);
    }
  };

  const seedAllocationPct = portfolioStrategyIds.length > 0 ? 1 / portfolioStrategyIds.length : 0;
  const selectedStrategySummary =
    portfolioStrategyIds.length > 0
      ? isZh
        ? `已选 ${portfolioStrategyIds.length} 个策略，初始等权约为 ${formatPercent(seedAllocationPct, 0)} / strategy`
        : `${portfolioStrategyIds.length} strategies selected, starting near ${formatPercent(seedAllocationPct, 0)} per strategy`
      : isZh
        ? "已选 0 个策略"
        : "0 strategies selected";

  const resetAllocationEditor = (portfolioName?: string) => {
    setEditingAllocationId(null);
    setAllocationPortfolioName(portfolioName || portfoliosForSelectedAccount[0]?.name || "default");
    setAllocationPct(0.25);
    setCapitalBase("");
    setAllowFractional(true);
    setAllocationStatus("active");
    setNotes("");
  };

  const loadAllocationIntoEditor = (item: StrategyAllocationOut) => {
    setEditingAllocationId(item.id);
    setAllocationStrategyId(item.strategy_id);
    setAllocationPortfolioName(item.portfolio_name);
    setAllocationPct(item.allocation_pct);
    setCapitalBase(
      typeof item.capital_base === "number" ? String(item.capital_base) : ""
    );
    setAllowFractional(item.allow_fractional);
    setAllocationStatus(item.status);
    setNotes(item.notes || "");
  };

  const handleAllocationSubmit = async (
    event: FormEvent,
    portfolioNameOverride?: string
  ) => {
    event.preventDefault();
    setAllocationError(null);

    if (!allocationStrategyId) {
      setAllocationError(copy.validation.strategyRequired);
      return;
    }

    const payload: StrategyAllocationUpsert = {
      strategy_id: allocationStrategyId,
      portfolio_name: portfolioNameOverride || allocationPortfolioName.trim() || "default",
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
      resetAllocationEditor(payload.portfolio_name);
      setAllocationStrategyId((current) => current || eligibleStrategies[0]?.id || "");
      setExpandedAllocationPortfolioName(payload.portfolio_name);
      setSinglePortfolioName(payload.portfolio_name);
      setMultiPortfolioName(payload.portfolio_name);
    } catch (err: any) {
      setAllocationError(err?.message || copy.errors.saveAllocationFailed);
    } finally {
      setSubmittingAllocation(false);
    }
  };

  const handleAllocationEditorToggle = (portfolioName: string) => {
    setAllocationError(null);
    setExpandedAllocationPortfolioName((current) => {
      if (current === portfolioName) {
        setEditingAllocationId(null);
        return null;
      }
      resetAllocationEditor(portfolioName);
      setAllocationStrategyId((currentStrategyId) => currentStrategyId || eligibleStrategies[0]?.id || "");
      return portfolioName;
    });
  };

  const handleAllocationItemClick = (item: StrategyAllocationOut) => {
    setAllocationError(null);
    if (editingAllocationId === item.id) {
      setEditingAllocationId(null);
      setExpandedAllocationPortfolioName(null);
      return;
    }
    setExpandedAllocationPortfolioName(item.portfolio_name);
    loadAllocationIntoEditor(item);
  };

  const handleAllocationPortfolioOpen = (portfolioName: string) => {
    if (
      expandedAllocationPortfolioName === portfolioName &&
      editingAllocationId === null
    ) {
      setExpandedAllocationPortfolioName(null);
      return;
    }
    handleNewAllocationClick(portfolioName);
  };

  const handleAllocationPortfolioCardClick = (
    event: MouseEvent<HTMLElement>,
    portfolioName: string
  ) => {
    const target = event.target;
    if (
      target instanceof Element &&
      target.closest(
        "button, input, select, textarea, a, label, [data-allocation-editor='true']"
      )
    ) {
      return;
    }
    handleAllocationPortfolioOpen(portfolioName);
  };

  const handleNewAllocationClick = (portfolioName: string) => {
    setAllocationError(null);
    setExpandedAllocationPortfolioName(portfolioName);
    resetAllocationEditor(portfolioName);
    setAllocationStrategyId(eligibleStrategies[0]?.id || "");
  };

  const handleSingleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSingleError(null);

    if (!strategyId) {
      setSingleError(copy.validation.strategyRequired);
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
      setSingleError(err?.message || copy.errors.singleRunFailed);
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
      setMultiError(err?.message || copy.errors.multiRunFailed);
    } finally {
      setSubmittingMulti(false);
    }
  };

  return (
    <AppShell
      title={copy.title}
      subtitle=""
      actions={
        <>
          <button
            type="button"
            onClick={() => setActiveTab("accounts")}
            style={actionButtonStyle(activeTab === "accounts")}
          >
            {copy.actions.accounts}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("allocations")}
            style={actionButtonStyle(activeTab === "allocations")}
          >
            {copy.actions.allocations}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("execution")}
            style={actionButtonStyle(activeTab === "execution")}
          >
            {copy.actions.execution}
          </button>
        </>
      }
    >
      {loading ? <p>{copy.loading}</p> : null}
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
              label={copy.metrics.paperAccounts.label}
              value={String(stats.accounts)}
              hint={copy.metrics.paperAccounts.hint}
              accent="#0f766e"
            />
            <MetricCard
              label={copy.metrics.portfolios.label}
              value={String(stats.portfolios)}
              hint={copy.metrics.portfolios.hint}
              accent="#2563eb"
            />
            <MetricCard
              label={copy.metrics.activeAllocations.label}
              value={String(stats.activeAllocations)}
              hint={copy.metrics.activeAllocations.hint}
              accent="#ca8a04"
            />
            <MetricCard
              label={copy.metrics.runnableStrategies.label}
              value={String(stats.activeStrategies)}
              hint={copy.metrics.runnableStrategies.hint}
              accent="#b45309"
            />
          </section>

          <section style={tabPanelShellStyle}>
            <div style={tabListStyle}>
              {workspaceTabs.map((tab) => (
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
                <h2 style={sectionTitleStyle}>{copy.accounts.title}</h2>
                <p style={subtitleStyle}>{copy.accounts.subtitle}</p>
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
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {copy.accounts.createAccountTitle}
                  </h3>
                  <p style={subtitleStyle}>{copy.accounts.createAccountSubtitle}</p>
                </div>

                <form onSubmit={handleAccountSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    copy.accounts.form.accountName,
                    copy.accounts.form.accountNameDescription,
                    <input
                      value={accountName}
                      onChange={(e) => setAccountName(e.target.value)}
                      placeholder={copy.accounts.form.accountNamePlaceholder}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.accounts.form.apiKeyEnv,
                    copy.accounts.form.apiKeyEnvDescription,
                    <input
                      value={apiKeyEnv}
                      onChange={(e) => setApiKeyEnv(e.target.value)}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.accounts.form.secretKeyEnv,
                    copy.accounts.form.secretKeyEnvDescription,
                    <input
                      value={secretKeyEnv}
                      onChange={(e) => setSecretKeyEnv(e.target.value)}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.accounts.form.baseUrl,
                    copy.accounts.form.baseUrlDescription,
                    <input
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.accounts.form.timeout,
                    copy.accounts.form.timeoutDescription,
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
                    copy.accounts.form.notes,
                    copy.accounts.form.notesDescription,
                    <textarea
                      value={accountNotes}
                      onChange={(e) => setAccountNotes(e.target.value)}
                      rows={3}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                  )}
                  {accountError ? <div style={errorTextStyle}>{accountError}</div> : null}
                  <button type="submit" disabled={creatingAccount} style={buttonStyle}>
                    {creatingAccount
                      ? copy.accounts.buttons.creatingAccount
                      : copy.accounts.buttons.createAccount}
                  </button>
                </form>
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {copy.accounts.createPortfolioTitle}
                  </h3>
                  <p style={subtitleStyle}>{copy.accounts.createPortfolioSubtitle}</p>
                </div>

                {fieldBlock(
                  copy.accounts.form.currentAccount,
                  copy.accounts.form.currentAccountDescription,
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
                    copy.accounts.form.portfolioName,
                    copy.accounts.form.portfolioNameDescription,
                    <input
                      value={portfolioName}
                      onChange={(e) => setPortfolioName(e.target.value)}
                      placeholder={copy.accounts.form.portfolioNamePlaceholder}
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.accounts.form.portfolioDescription,
                    copy.accounts.form.portfolioDescriptionDescription,
                    <textarea
                      value={portfolioDescription}
                      onChange={(e) => setPortfolioDescription(e.target.value)}
                      rows={3}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                  )}
                  {fieldBlock(
                    copy.accounts.form.initialStrategies,
                    copy.accounts.form.initialStrategiesDescription,
                    <div style={selectionPanelStyle}>
                      <div style={selectionHintStyle}>{selectedStrategySummary}</div>
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
                                {item.name}{" "}
                                {item.engine_ready
                                  ? ""
                                  : isZh
                                    ? "(仅存档)"
                                    : "(stored-only)"}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {portfolioError ? <div style={errorTextStyle}>{portfolioError}</div> : null}
                  <button type="submit" disabled={creatingPortfolio} style={buttonStyle}>
                    {creatingPortfolio
                      ? copy.accounts.buttons.creatingPortfolio
                      : copy.accounts.buttons.createPortfolio}
                  </button>
                </form>
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {copy.accounts.overviewTitle}
                  </h3>
                  <p style={subtitleStyle}>{copy.accounts.overviewSubtitle}</p>
                </div>

                {overviewLoading ? <p>{copy.accounts.loadingOverview}</p> : null}
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
                          <strong>{copy.accounts.summary.baseUrl}:</strong>{" "}
                          {accountOverview.account.base_url}
                        </div>
                        <div>
                          <strong>{copy.accounts.summary.credentials}:</strong>{" "}
                          {accountOverview.account.api_key_env} /{" "}
                          {accountOverview.account.secret_key_env}
                        </div>
                        <div>
                          <strong>{copy.accounts.summary.portfolioCount}:</strong>{" "}
                          {accountOverview.portfolio_count}
                        </div>
                        <div>
                          <strong>{copy.accounts.summary.activeStrategyCount}:</strong>{" "}
                          {accountOverview.active_strategy_count}
                        </div>
                      </div>
                    </div>

                    {accountOverview.portfolios.length === 0 ? (
                      <div style={emptyStyle}>{copy.accounts.noPortfolioYet}</div>
                    ) : (
                      <div style={{ display: "grid", gap: 12, maxHeight: 720, overflowY: "auto", paddingRight: 4 }}>
                        {accountOverview.portfolios.map((portfolio) => (
                          <article key={portfolio.id} style={listCardStyle}>
                            <div style={listHeaderStyle}>
                              <div>
                                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 6 }}>
                                  <h3 style={{ margin: 0, fontSize: 20 }}>{portfolio.name}</h3>
                                  {portfolio.name !== "default" ? (
                                    <button
                                      type="button"
                                      onClick={() => handlePortfolioRenameStart(portfolio)}
                                      style={secondaryInlineButtonStyle}
                                    >
                                      {isZh ? "改名" : "Rename"}
                                    </button>
                                  ) : (
                                    <span style={mutedInlineHintStyle}>
                                      {isZh ? "默认组合不可改名" : "Default portfolio cannot be renamed"}
                                    </span>
                                  )}
                                </div>
                                <div style={badgeRowStyle}>
                                  <Badge tone={portfolio.status === "active" ? "success" : "warning"}>
                                    {portfolio.status}
                                  </Badge>
                                  <Badge>
                                    {strategyCountLabel(portfolio.allocated_strategy_count)}
                                  </Badge>
                                  <Badge>{formatPercent(portfolio.active_allocation_pct_total, 0)}</Badge>
                                </div>
                              </div>
                              <div style={metaTextStyle}>
                                {portfolio.latest_run_requested_at
                                  ? formatDateTime(portfolio.latest_run_requested_at, locale)
                                  : copy.accounts.summary.noRunYet}
                              </div>
                            </div>
                            <p style={bodyTextStyle}>
                              {portfolio.description || copy.accounts.summary.noDescription}
                            </p>
                            {editingPortfolioId === portfolio.id ? (
                              <form
                                onSubmit={(event) => handlePortfolioRenameSubmit(event, portfolio)}
                                style={{
                                  display: "grid",
                                  gridTemplateColumns: "minmax(220px, 1fr) auto auto",
                                  gap: 10,
                                  alignItems: "end",
                                  marginBottom: 12,
                                }}
                              >
                                <label style={{ display: "grid", gap: 6 }}>
                                  <span style={fieldLabelStyle}>
                                    {isZh ? "新的 portfolio 名称" : "New Portfolio Name"}
                                  </span>
                                  <input
                                    value={editingPortfolioName}
                                    onChange={(event) => {
                                      setEditingPortfolioName(event.target.value);
                                      setEditingPortfolioError(null);
                                      setPortfolioRenameSuccess(null);
                                    }}
                                    placeholder={isZh ? "输入新的 portfolio 名称" : "Enter a new portfolio name"}
                                    style={inputStyle}
                                  />
                                </label>
                                <button
                                  type="submit"
                                  disabled={renamingPortfolio}
                                  style={buttonStyle}
                                >
                                  {renamingPortfolio
                                    ? isZh
                                      ? "保存中..."
                                      : "Saving..."
                                    : isZh
                                      ? "保存改名"
                                      : "Save"}
                                </button>
                                <button
                                  type="button"
                                  onClick={handlePortfolioRenameCancel}
                                  disabled={renamingPortfolio}
                                  style={secondaryButtonStyle(false)}
                                >
                                  {isZh ? "取消" : "Cancel"}
                                </button>
                                {editingPortfolioError ? (
                                  <div style={{ gridColumn: "1 / -1", ...errorTextStyle }}>
                                    {editingPortfolioError}
                                  </div>
                                ) : null}
                              </form>
                            ) : null}
                            {portfolioRenameSuccess?.id === portfolio.id ? (
                              <div
                                style={{
                                  marginBottom: 12,
                                  color: "#15803d",
                                  fontWeight: 600,
                                }}
                              >
                                {portfolioRenameSuccess.message}
                              </div>
                            ) : null}
                            <div style={detailGridStyle}>
                              <div>
                                <strong>{copy.accounts.summary.allocations}:</strong>{" "}
                                {portfolio.active_allocation_count}/
                                {portfolio.allocation_count}
                              </div>
                              <div>
                                <strong>{copy.accounts.summary.latestRun}:</strong>{" "}
                                {portfolio.latest_run_status || "-"}
                              </div>
                              <div>
                                <strong>{copy.accounts.summary.latestEquity}:</strong>{" "}
                                {typeof portfolio.latest_run_equity === "number"
                                  ? formatNumber(portfolio.latest_run_equity)
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
                                      <strong>{copy.accounts.summary.strategyStatus}:</strong>{" "}
                                      {item.strategy_status}
                                    </div>
                                    <div>
                                      <strong>{copy.accounts.summary.latestRun}:</strong>{" "}
                                      {item.latest_run_status || "-"}
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
                  <div style={emptyStyle}>{copy.accounts.emptyOverview}</div>
                )}
              </section>
            </div>
          </section>
          ) : null}

          {activeTab === "allocations" ? (
          <section id="allocations" style={sectionStyle}>
            <div style={sectionHeaderStyle}>
              <div>
                <h2 style={sectionTitleStyle}>{copy.allocations.title}</h2>
                <p style={subtitleStyle}>{copy.allocations.subtitle}</p>
              </div>
            </div>

            <section style={cardStyle}>
              <div style={{ marginBottom: 14 }}>
                <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>
                  {copy.allocations.currentAccountTitle}
                </h3>
              </div>

              {allocationGroupsForSelectedAccount.length === 0 ? (
                <div style={emptyStyle}>{copy.allocations.empty}</div>
              ) : (
                <div style={{ display: "grid", gap: 14 }}>
                  {allocationGroupsForSelectedAccount.map((group) => (
                    <article
                      key={group.portfolio.name}
                      style={{ ...listCardStyle, cursor: "pointer" }}
                      onClick={(event) =>
                        handleAllocationPortfolioCardClick(event, group.portfolio.name)
                      }
                    >
                      <div style={listHeaderStyle}>
                        <button
                          type="button"
                          onClick={() => handleAllocationPortfolioOpen(group.portfolio.name)}
                          style={portfolioCardButtonStyle()}
                        >
                          <h3 style={{ margin: "0 0 6px", fontSize: 20 }}>
                            {group.portfolio.name}
                          </h3>
                          <div style={badgeRowStyle}>
                            <Badge tone="info">{group.portfolio.paper_account_name || "-"}</Badge>
                            <Badge>{allocationCountLabel(group.items.length)}</Badge>
                            <Badge>{activeAllocationCountLabel(group.activeCount)}</Badge>
                            <Badge>{formatPercent(group.allocationPctTotal, 0)}</Badge>
                          </div>
                        </button>
                        <button
                          type="button"
                          onClick={() => handleAllocationEditorToggle(group.portfolio.name)}
                          style={secondaryButtonStyle(
                            expandedAllocationPortfolioName === group.portfolio.name
                          )}
                        >
                          {expandedAllocationPortfolioName === group.portfolio.name
                            ? copy.allocations.collapse
                            : copy.allocations.expand}
                        </button>
                      </div>

                      {group.items.length === 0 ? (
                        <div style={emptyStyle}>{copy.allocations.noAllocationYet}</div>
                      ) : (
                        <div style={{ display: "grid", gap: 12 }}>
                          {group.items.map((item) => {
                            const strategy =
                              strategies.find((entry) => entry.id === item.strategy_id) || null;
                            return (
                              <button
                                key={item.id}
                                type="button"
                                onClick={() => handleAllocationItemClick(item)}
                                style={allocationCardButtonStyle(
                                  editingAllocationId === item.id
                                )}
                              >
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
                                    {formatDateTime(item.updated_at || item.created_at, locale)}
                                  </div>
                                </div>
                                <p style={bodyTextStyle}>
                                  {strategy
                                    ? getStrategyDescription(strategy)
                                    : copy.allocations.noStrategyDescription}
                                </p>
                                <div style={detailGridStyle}>
                                  <div>
                                    <strong>{copy.allocations.capitalBaseLabel}:</strong>{" "}
                                    {typeof item.capital_base === "number"
                                      ? formatNumber(item.capital_base)
                                      : copy.allocations.defaultCapitalBase}
                                  </div>
                                  <div>
                                    <strong>{copy.allocations.notesLabel}:</strong>{" "}
                                    {item.notes || "-"}
                                  </div>
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      )}

                      {expandedAllocationPortfolioName === group.portfolio.name ? (
                        <div data-allocation-editor="true" style={inlineEditorStyle}>
                          <div style={listHeaderStyle}>
                            <div>
                              <h4 style={{ margin: "0 0 6px", fontSize: 18 }}>
                                {editingAllocation?.portfolio_name === group.portfolio.name
                                  ? isZh
                                    ? `调整 ${editingAllocation.strategy_name || editingAllocation.strategy_id}`
                                    : `Edit ${editingAllocation.strategy_name || editingAllocation.strategy_id}`
                                  : isZh
                                    ? `配置 ${group.portfolio.name} 的策略分配`
                                    : `Configure strategy allocations for ${group.portfolio.name}`}
                              </h4>
                              <p style={subtitleStyle}>
                                {editingAllocation?.portfolio_name === group.portfolio.name
                                  ? copy.editor.editModeDescription
                                  : copy.editor.createModeDescription}
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() => handleNewAllocationClick(group.portfolio.name)}
                              style={secondaryButtonStyle(false)}
                            >
                              {copy.allocations.newAllocation}
                            </button>
                          </div>

                          <form
                            onSubmit={(event) => handleAllocationSubmit(event, group.portfolio.name)}
                            style={{ display: "grid", gap: 12 }}
                          >
                            {fieldBlock(
                              copy.allocations.strategy,
                              editingAllocation?.portfolio_name === group.portfolio.name
                                ? copy.allocations.strategyEditingDescription
                                : copy.allocations.strategyDescription,
                              <select
                                value={allocationStrategyId}
                                onChange={(e) => setAllocationStrategyId(e.target.value)}
                                disabled={editingAllocation?.portfolio_name === group.portfolio.name}
                                style={inputStyle}
                              >
                                <option value="">{copy.allocations.strategyPlaceholder}</option>
                                {activeStrategies.map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {item.name}{" "}
                                    {item.engine_ready
                                      ? ""
                                      : isZh
                                        ? "(仅存档)"
                                        : "(stored-only)"}
                                  </option>
                                ))}
                              </select>
                            )}
                            {fieldBlock(
                              copy.allocations.allocationPct,
                              copy.allocations.allocationPctDescription,
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
                              copy.allocations.capitalBase,
                              copy.allocations.capitalBaseDescription,
                              <input
                                value={capitalBase}
                                onChange={(e) => setCapitalBase(e.target.value)}
                                type="number"
                                min="0"
                                step="1000"
                                placeholder={copy.allocations.capitalBasePlaceholder}
                                style={inputStyle}
                              />
                            )}
                            {fieldBlock(
                              copy.allocations.status,
                              copy.allocations.statusDescription,
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
                              copy.allocations.allowFractional,
                              copy.allocations.allowFractionalDescription,
                              <label style={checkboxRowStyle}>
                                <input
                                  type="checkbox"
                                  checked={allowFractional}
                                  onChange={(e) => setAllowFractional(e.target.checked)}
                                />
                                {copy.allocations.allowFractional}
                              </label>
                            )}
                            {fieldBlock(
                              copy.allocations.notes,
                              copy.allocations.notesDescription,
                              <textarea
                                value={notes}
                                onChange={(e) => setNotes(e.target.value)}
                                rows={4}
                                style={{ ...inputStyle, resize: "vertical" }}
                              />
                            )}
                            {allocationError ? (
                              <div style={errorTextStyle}>{allocationError}</div>
                            ) : null}
                            <button
                              type="submit"
                              disabled={submittingAllocation}
                              style={buttonStyle}
                            >
                              {submittingAllocation
                                ? copy.allocations.saving
                                : copy.allocations.save}
                            </button>
                          </form>
                        </div>
                      ) : null}
                    </article>
                  ))}
                </div>
              )}
            </section>
          </section>
          ) : null}

          {activeTab === "execution" ? (
          <section id="execution" style={sectionStyle}>
            <div style={sectionHeaderStyle}>
              <div>
                <h2 style={sectionTitleStyle}>{copy.execution.title}</h2>
                <p style={subtitleStyle}>{copy.execution.subtitle}</p>
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
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {copy.execution.singleTitle}
                  </h3>
                  <p style={subtitleStyle}>{copy.execution.singleSubtitle}</p>
                </div>

                <form onSubmit={handleSingleSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    copy.execution.strategy,
                    copy.execution.strategyDescription,
                    <select
                      value={strategyId}
                      onChange={(e) => setStrategyId(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">{copy.execution.strategyPlaceholder}</option>
                      {eligibleStrategies.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    copy.execution.tradeDate,
                    singleTradeDateDescription,
                    <input
                      value={singleTradeDate}
                      onChange={(e) => setSingleTradeDate(e.target.value)}
                      type="date"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.execution.portfolio,
                    copy.execution.singlePortfolioDescription,
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
                    copy.execution.basketOverride,
                    copy.execution.basketOverrideDescription,
                    <select
                      value={basketId}
                      onChange={(e) => setBasketId(e.target.value)}
                      style={inputStyle}
                    >
                      <option value="">{copy.execution.basketDefaultOption}</option>
                      {activeBaskets.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name} ({item.symbol_count})
                        </option>
                      ))}
                    </select>
                  )}
                  {fieldBlock(
                    copy.execution.submitOrders,
                    copy.execution.singleSubmitOrdersDescription,
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={singleSubmitOrders}
                        onChange={(e) => setSingleSubmitOrders(e.target.checked)}
                      />
                      {copy.execution.submitOrdersLabel}
                    </label>
                  )}
                  {singleError ? <div style={errorTextStyle}>{singleError}</div> : null}
                  <button type="submit" disabled={submittingSingle} style={buttonStyle}>
                    {submittingSingle
                      ? copy.execution.runningSingle
                      : copy.execution.runSingle}
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
                        <strong>{copy.execution.results.signals}:</strong>{" "}
                        {latestSingleRun.signal_count}
                      </div>
                      <div>
                        <strong>{copy.execution.results.orders}:</strong>{" "}
                        {latestSingleRun.order_count}
                      </div>
                      <div>
                        <strong>{copy.execution.results.submitted}:</strong>{" "}
                        {latestSingleRun.submitted_order_count}
                      </div>
                      <div>
                        <strong>{copy.execution.results.finalEquity}:</strong>{" "}
                        {formatNumber(latestSingleRun.final_equity)}
                      </div>
                    </div>
                  </div>
                ) : null}
              </section>

              <section style={cardStyle}>
                <div style={{ marginBottom: 14 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {copy.execution.multiTitle}
                  </h3>
                  <p style={subtitleStyle}>{copy.execution.multiSubtitle}</p>
                </div>

                <form onSubmit={handleMultiSubmit} style={{ display: "grid", gap: 12 }}>
                  {fieldBlock(
                    copy.execution.portfolio,
                    copy.execution.multiPortfolioDescription,
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
                    copy.execution.tradeDate,
                    multiTradeDateDescription,
                    <input
                      value={multiTradeDate}
                      onChange={(e) => setMultiTradeDate(e.target.value)}
                      type="date"
                      style={inputStyle}
                    />
                  )}
                  {fieldBlock(
                    copy.execution.submitOrders,
                    copy.execution.multiSubmitOrdersDescription,
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={multiSubmitOrders}
                        onChange={(e) => setMultiSubmitOrders(e.target.checked)}
                      />
                      {copy.execution.submitOrdersLabel}
                    </label>
                  )}
                  {fieldBlock(
                    copy.execution.continueOnError,
                    copy.execution.continueOnErrorDescription,
                    <label style={checkboxRowStyle}>
                      <input
                        type="checkbox"
                        checked={continueOnError}
                        onChange={(e) => setContinueOnError(e.target.checked)}
                      />
                      {copy.execution.continueOnErrorLabel}
                    </label>
                  )}
                  {multiError ? <div style={errorTextStyle}>{multiError}</div> : null}
                  <button type="submit" disabled={submittingMulti} style={buttonStyle}>
                    {submittingMulti
                      ? copy.execution.runningMulti
                      : copy.execution.runMulti}
                  </button>
                </form>

                {latestMultiRun ? (
                  <div style={resultCardStyle}>
                    <div style={badgeRowStyle}>
                      <Badge tone="info">{latestMultiRun.portfolio_name}</Badge>
                      <Badge tone={latestMultiRun.failed_runs > 0 ? "warning" : "success"}>
                        {latestMultiRun.completed_runs}/{latestMultiRun.total_runs}{" "}
                        {copy.execution.results.completed}
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
                              <strong>{copy.execution.results.strategy}:</strong>{" "}
                              {item.strategy_id}
                            </div>
                            <div>
                              <strong>{copy.execution.results.orders}:</strong>{" "}
                              {item.order_count}
                            </div>
                            <div>
                              <strong>{copy.execution.results.submitted}:</strong>{" "}
                              {item.submitted_order_count}
                            </div>
                            <div>
                              <strong>{copy.execution.results.equity}:</strong>{" "}
                              {formatNumber(item.final_equity)}
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

function secondaryButtonStyle(active: boolean): CSSProperties {
  return {
    padding: "10px 14px",
    borderRadius: 12,
    border: active ? "1px solid rgba(15, 118, 110, 0.35)" : "1px solid rgba(148, 163, 184, 0.28)",
    background: active ? "rgba(15, 118, 110, 0.12)" : "rgba(255,255,255,0.9)",
    color: "#0f172a",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
}

const secondaryInlineButtonStyle: CSSProperties = {
  padding: "6px 10px",
  borderRadius: 999,
  border: "1px solid rgba(148, 163, 184, 0.28)",
  background: "rgba(255,255,255,0.92)",
  color: "#0f172a",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const mutedInlineHintStyle: CSSProperties = {
  color: "#64748b",
  fontSize: 13,
  fontWeight: 600,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

function allocationCardButtonStyle(active: boolean): CSSProperties {
  return {
    ...miniStrategyCardStyle,
    width: "100%",
    textAlign: "left",
    cursor: "pointer",
    boxShadow: active ? "0 0 0 2px rgba(15, 118, 110, 0.18)" : "none",
    border: active
      ? "1px solid rgba(15, 118, 110, 0.35)"
      : miniStrategyCardStyle.border,
  };
}

function portfolioCardButtonStyle(): CSSProperties {
  return {
    display: "grid",
    gap: 6,
    flex: 1,
    width: "100%",
    padding: 0,
    border: "none",
    background: "transparent",
    color: "#0f172a",
    textAlign: "left",
    cursor: "pointer",
    boxShadow: "none",
    outline: "none",
    borderRadius: 12,
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
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
  color: "#0f172a",
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
  color: "#0f172a",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
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
  color: "#0f172a",
};

const fieldLabelStyle: CSSProperties = {
  color: "#0f172a",
  fontWeight: 700,
  lineHeight: 1.2,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const fieldDescriptionStyle: CSSProperties = {
  color: "#475569",
  lineHeight: 1.6,
  fontSize: 13,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  color: "#475569",
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
  color: "#0f172a",
};

const miniStrategyCardStyle: CSSProperties = {
  padding: 12,
  borderRadius: 14,
  background: "rgba(255,255,255,0.9)",
  border: "1px solid rgba(226, 232, 240, 0.85)",
  color: "#0f172a",
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
  color: "#475569",
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
  color: "#0f172a",
  display: "grid",
  gap: 10,
};

const nestedResultStyle: CSSProperties = {
  padding: 12,
  borderRadius: 14,
  background: "rgba(255,255,255,0.88)",
  border: "1px solid rgba(226, 232, 240, 0.9)",
  color: "#0f172a",
  display: "grid",
  gap: 8,
};

const inlineEditorStyle: CSSProperties = {
  marginTop: 16,
  padding: 16,
  borderRadius: 18,
  border: "1px solid rgba(148, 163, 184, 0.2)",
  background: "rgba(248, 250, 252, 0.92)",
  display: "grid",
  gap: 12,
};

const resultGridStyle: CSSProperties = {
  display: "grid",
  gap: 8,
  color: "#0f172a",
  fontSize: 14,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};
