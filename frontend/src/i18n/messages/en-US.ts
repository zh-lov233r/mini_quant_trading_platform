import { zhCNMessages } from "./zh-CN";

export const enUSMessages: typeof zhCNMessages = {
  common: {
    appName: "Quant Strategy Workspace",
    language: "Language",
    chinese: "中文",
    english: "English",
  },
  nav: {
    dashboard: "Dashboard",
    strategies: "Strategies",
    stockBaskets: "Stock Baskets",
    newStrategy: "New Strategy",
    backtests: "Backtests",
    paperTrading: "Paper Trading",
  },
  paperTrading: {
    title: "Paper Trading Workspace",
    loading: "Loading...",
    loadOverviewFailed: "Failed to load account overview",
    loadWorkspaceFailed: "Failed to load the paper trading workspace",
    actions: {
      accounts: "Accounts & Portfolios",
      allocations: "Strategy Allocations",
      execution: "Paper Trading Execution",
    },
    tabs: {
      accounts: {
        label: "Accounts & Portfolios",
        description: "Create and review paper accounts plus strategy portfolios",
      },
      allocations: {
        label: "Strategy Allocations",
        description: "Configure strategies, sizing, and virtual capital per portfolio",
      },
      execution: {
        label: "Paper Trading Execution",
        description: "Run single or multi-strategy paper trading and inspect the latest results",
      },
    },
    metrics: {
      paperAccounts: {
        label: "Paper Accounts",
        hint: "Each account maps to one Alpaca paper credential set and one isolated execution lane",
      },
      portfolios: {
        label: "Portfolios In Current Account",
        hint: "This counts active strategy portfolios under the selected paper account",
      },
      activeAllocations: {
        label: "Active Allocations In Current Account",
        hint: "Only active allocations are picked up by multi-strategy execution",
      },
      runnableStrategies: {
        label: "Runnable Strategies",
        hint: "Active, engine-ready strategies that can be consumed directly by paper trading",
      },
    },
    accounts: {
      title: "Accounts & Portfolios",
      subtitle: "Create a paper trading account first, then create strategy portfolios under that account",
      createAccountTitle: "Create Paper Account",
      createAccountSubtitle: "Bind Alpaca credentials through environment variable names",
      createPortfolioTitle: "Create Strategy Portfolio",
      createPortfolioSubtitle:
        "A portfolio is a virtual sleeve under one paper account. Allocations and execution are organized around it.",
      overviewTitle: "Account Overview",
      overviewSubtitle:
        "See how many portfolios live under the current account, how many strategies each one has, and the latest run status.",
      noPortfolioYet: "This account does not have any portfolios yet. Create a strategy portfolio first.",
      emptyOverview:
        "Select a paper account first, then this panel will show the portfolios and strategies under it.",
      loadingOverview: "Loading account overview...",
      form: {
        accountName: "Account Name",
        accountNameDescription:
          "Give this paper trading credential set a recognizable name, for example us-paper-main",
        accountNamePlaceholder: "For example us-paper-main",
        apiKeyEnv: "API Key Environment Variable",
        apiKeyEnvDescription:
          "The backend reads the Alpaca API key from this environment variable, for example ALPACA_API_KEY_MAIN",
        secretKeyEnv: "Secret Key Environment Variable",
        secretKeyEnvDescription:
          "The backend reads the Alpaca secret key from this environment variable, for example ALPACA_SECRET_KEY_MAIN",
        baseUrl: "Base URL",
        baseUrlDescription:
          "Defaults to the Alpaca paper endpoint; only change it when targeting a different environment",
        timeout: "Request Timeout",
        timeoutDescription:
          "Measured in seconds and used for account lookups plus order submission timeouts",
        notes: "Notes",
        notesDescription:
          "Optional. Use this to clarify whether the account is for testing, demos, or a dedicated strategy group",
        currentAccount: "Current Account",
        currentAccountDescription:
          "Pick a paper account first, then create a strategy portfolio under that account",
        portfolioName: "Portfolio Name",
        portfolioNameDescription:
          "The first version expects this to be globally unique. Prefixing with the account name works well, for example us-main-growth",
        portfolioNamePlaceholder: "For example us-main-growth",
        portfolioDescription: "Description",
        portfolioDescriptionDescription:
          "Describe the style or risk bucket carried by this portfolio, such as rotation, growth, or mean reversion",
        initialStrategies: "Seed Strategies",
        initialStrategiesDescription:
          "Add strategies while creating the portfolio. The system will seed equal-weight active allocations, and you can refine them below later.",
      },
      buttons: {
        createAccount: "Create Paper Account",
        creatingAccount: "Creating...",
        createPortfolio: "Create Strategy Portfolio",
        creatingPortfolio: "Creating...",
      },
      summary: {
        baseUrl: "base url",
        credentials: "credentials",
        portfolioCount: "portfolio count",
        activeStrategyCount: "active strategies",
        strategiesCount: "strategies",
        noRunYet: "No runs yet",
        noDescription: "No portfolio description yet",
        allocations: "allocations",
        latestRun: "latest run",
        latestEquity: "latest equity",
        strategyStatus: "strategy status",
      },
    },
    allocations: {
      title: "Strategy Allocations",
      subtitle:
        "Only portfolios under the current account are shown here. Each portfolio can be expanded and configured on its own.",
      currentAccountTitle: "Current Account Allocations",
      empty: "There are no configurable portfolios under the current account yet. Create a portfolio first, then come back to set allocations.",
      noAllocationYet: "This portfolio does not have any allocations yet. Expand it to add one directly.",
      expand: "Expand",
      collapse: "Collapse",
      newAllocation: "New Allocation",
      strategy: "Strategy",
      strategyDescription:
        "Choose the strategy to place into this portfolio. Existing combinations are updated through upsert.",
      strategyEditingDescription:
        "You are editing an existing allocation. The strategy itself is fixed; use the button at the top right to create a new one.",
      strategyPlaceholder: "Select a strategy",
      allocationPct: "Capital Share / allocation_pct",
      allocationPctDescription:
        "How much virtual capital this strategy can use inside the portfolio, between 0 and 1. For example, 0.25 means 25%.",
      capitalBase: "Fixed Capital / capital_base",
      capitalBaseDescription:
        "Optional. Leave empty to use account equity times allocation_pct; provide a number to pin the sleeve to a fixed amount instead.",
      capitalBasePlaceholder: "Optional fixed capital_base, for example 50000",
      status: "Status",
      statusDescription:
        "Only active allocations are picked up by the multi-strategy scheduler; draft and archived are useful for preconfiguration or temporary pauses.",
      allowFractional: "Allow Fractional Shares",
      allowFractionalDescription:
        "When enabled, sizing can use fractional shares. When disabled, share counts are rounded down to whole shares.",
      notes: "Notes",
      notesDescription:
        "Optional. Capture why this allocation exists, what risk bucket it belongs to, or whether it is a temporary experiment.",
      save: "Save Allocation",
      saving: "Saving...",
      noStrategyDescription: "No strategy description yet",
      defaultCapitalBase: "Follow account equity * allocation_pct",
      capitalBaseLabel: "capital base",
      notesLabel: "notes",
      countAllocations: "allocations",
      countActive: "active",
    },
    execution: {
      title: "Paper Trading Execution",
      subtitle:
        "Execution only needs a portfolio. The system resolves the linked paper account automatically, then uses its Alpaca credentials to query the account and submit orders.",
      singleTitle: "Single Strategy Run",
      singleSubtitle:
        "Best for validating the sleeve ledger, risk controls, and order logic of one strategy inside one portfolio.",
      multiTitle: "Multi-Strategy Run",
      multiSubtitle:
        "This entry point runs strategies in the order of active allocations under the same portfolio on one Alpaca account.",
      strategy: "Strategy",
      strategyDescription: "Select an active, engine-ready strategy",
      strategyPlaceholder: "Select a strategy",
      tradeDate: "Trade Date",
      singleTradeDateDescription:
        "Signals are generated from the daily feature snapshot of this date. The current system is daily-frequency, so this is a trading day rather than a minute timestamp.",
      multiTradeDateDescription:
        "Every dispatched strategy will use the daily feature snapshot from the same date for this execution cycle.",
      latestTradeDateSuffix: "Latest available trade date: {date}",
      portfolio: "Portfolio",
      singlePortfolioDescription:
        "This decides which virtual sleeve the strategy runs against. The system resolves the paper account from the portfolio before querying Alpaca or submitting orders.",
      multiPortfolioDescription:
        "Multi-strategy execution reads every active allocation under this portfolio and resolves the owning paper account automatically.",
      basketOverride: "Basket Override",
      basketOverrideDescription:
        "Optional. Leave blank to use the strategy's own universe. Choosing a basket overrides the universe only for this run.",
      basketDefaultOption: "Use the strategy's original universe",
      submitOrders: "Submit Real Paper Orders",
      singleSubmitOrdersDescription:
        "When disabled, this is a dry run only for signals, sleeve bookkeeping, and risk checks. When enabled, orders are actually sent to the Alpaca paper account.",
      multiSubmitOrdersDescription:
        "When disabled, the scheduler runs in simulation only. When enabled, each strategy's orders are submitted to the portfolio's Alpaca paper account.",
      submitOrdersLabel: "Actually submit to the Alpaca paper account",
      continueOnError: "Failure Handling",
      continueOnErrorDescription:
        "When enabled, the scheduler keeps running later strategies even if one fails. When disabled, it stops at the first error.",
      continueOnErrorLabel: "Continue with later strategies after one fails",
      runSingle: "Run Single-Strategy Paper Trading",
      runningSingle: "Running...",
      runMulti: "Run Multi-Strategy Paper Trading",
      runningMulti: "Dispatching...",
      results: {
        signals: "signals",
        orders: "orders",
        submitted: "submitted",
        finalEquity: "final equity",
        completed: "completed",
        strategy: "strategy",
        equity: "equity",
      },
    },
    editor: {
      editModeDescription:
        "You are editing an existing allocation. Click another allocation to switch to it, or click New Allocation to return to create mode.",
      createModeDescription:
        "Allocations saved here are bound directly to this portfolio and will be read by later multi-strategy execution.",
    },
    validation: {
      accountNameRequired: "Please enter an account name",
      accountRequired: "Please select a paper account first",
      portfolioNameRequired: "Please enter a portfolio name",
      portfolioStrategiesRequired: "Please select at least one strategy to seed into the portfolio",
      strategyRequired: "Please select a strategy",
    },
    errors: {
      createAccountFailed: "Failed to create the paper account",
      createPortfolioFailed: "Failed to create the strategy portfolio",
      saveAllocationFailed: "Failed to save the strategy allocation",
      singleRunFailed: "Failed to start the single-strategy paper trading run",
      multiRunFailed: "Failed to start the multi-strategy paper trading run",
    },
  },
} as const;
