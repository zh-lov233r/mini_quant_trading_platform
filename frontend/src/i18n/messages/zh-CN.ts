export const zhCNMessages = {
  common: {
    appName: "量化策略工作台",
    language: "语言",
    chinese: "中文",
    english: "English",
  },
  nav: {
    dashboard: "总览",
    strategies: "策略库",
    stockBaskets: "股票池",
    newStrategy: "创建策略",
    backtests: "回测",
    paperTrading: "Paper Trading",
  },
  paperTrading: {
    title: "Paper Trading 工作台",
    loading: "加载中...",
    loadOverviewFailed: "加载账户概览失败",
    loadWorkspaceFailed: "加载 Paper Trading 工作台失败",
    actions: {
      accounts: "账户与子组合",
      allocations: "策略组合调整",
      execution: "Paper Trading 执行",
    },
    tabs: {
      accounts: {
        label: "账户与子组合",
        description: "创建和查看 paper account 与 strategy portfolio",
      },
      allocations: {
        label: "策略组合调整",
        description: "给子组合配置策略、资金占比和虚拟本金",
      },
      execution: {
        label: "Paper Trading 执行",
        description: "运行单策略或多策略调度，并查看最新结果",
      },
    },
    metrics: {
      paperAccounts: {
        label: "Paper Accounts",
        hint: "每个 account 对应一套 Alpaca paper 凭证和一个独立的执行通道",
      },
      portfolios: {
        label: "当前账户子组合",
        hint: "这里统计当前选中 paper account 下的 active strategy portfolios",
      },
      activeAllocations: {
        label: "当前账户 Active Allocations",
        hint: "只有这些 active allocation 会真正参与多策略调度",
      },
      runnableStrategies: {
        label: "可运行策略",
        hint: "active 且 engine-ready 的策略数量，是当前能被 paper trading 直接消费的策略池",
      },
    },
    accounts: {
      title: "账户与子组合",
      subtitle: "先创建 paper trading account，再在账户下面创建 strategy portfolio",
      createAccountTitle: "创建 Paper Account",
      createAccountSubtitle: "用环境变量名绑定 Alpaca 凭证",
      createPortfolioTitle: "创建策略子组合",
      createPortfolioSubtitle:
        "子组合是挂在某个 paper account 下的虚拟 sleeve。后续 allocation 和运行都会按它来组织",
      overviewTitle: "账户概览",
      overviewSubtitle:
        "这里会显示当前账户下有几个子组合、每个子组合里有多少策略，以及最近一次运行情况",
      noPortfolioYet: "该账户下还没有子组合。先创建一个 strategy portfolio",
      emptyOverview: "先选择一个 paper account，系统会在这里展示该账户的子组合和策略概览",
      loadingOverview: "加载账户概览中...",
      form: {
        accountName: "账户名",
        accountNameDescription:
          "给这套 paper trading 凭证一个容易识别的名字，例如 us-paper-main",
        accountNamePlaceholder: "例如 us-paper-main",
        apiKeyEnv: "API Key 环境变量",
        apiKeyEnvDescription:
          "后端会从这个环境变量里读取 Alpaca API key，比如 ALPACA_API_KEY_MAIN",
        secretKeyEnv: "Secret Key 环境变量",
        secretKeyEnvDescription:
          "后端会从这个环境变量里读取 Alpaca secret key，比如 ALPACA_SECRET_KEY_MAIN",
        baseUrl: "Base URL",
        baseUrlDescription: "默认是 Alpaca paper endpoint；只有接别的环境时才需要改",
        timeout: "请求超时",
        timeoutDescription: "单位是秒，用来控制账户查询和下单请求的超时时间",
        notes: "备注",
        notesDescription:
          "可选。建议写清楚这套账户是测试用、演示用，还是某一组策略专用",
        currentAccount: "当前账户",
        currentAccountDescription:
          "先选一个 paper account，再往这个账户下创建 strategy portfolio",
        portfolioName: "子组合名",
        portfolioNameDescription:
          "第一版要求全局唯一。建议带上账户前缀，例如 us-main-growth、us-main-default",
        portfolioNamePlaceholder: "例如 us-main-growth",
        portfolioDescription: "说明",
        portfolioDescriptionDescription:
          "写明这个子组合主要承载哪类策略或风险风格，比如 rotation、growth、mean-reversion",
        initialStrategies: "初始化策略",
        initialStrategiesDescription:
          "创建 portfolio 时就把这些策略放进去。当前会按等权自动生成 active allocations，后面你仍然可以在下方继续调整",
      },
      buttons: {
        createAccount: "创建 Paper Account",
        creatingAccount: "创建中...",
        createPortfolio: "创建策略子组合",
        creatingPortfolio: "创建中...",
      },
      summary: {
        baseUrl: "base url",
        credentials: "credentials",
        portfolioCount: "子组合数",
        activeStrategyCount: "active 策略数",
        strategiesCount: "个策略",
        noRunYet: "还没有运行",
        noDescription: "暂无子组合描述",
        allocations: "allocations",
        latestRun: "latest run",
        latestEquity: "latest equity",
        strategyStatus: "strategy status",
      },
    },
    allocations: {
      title: "策略分配",
      subtitle: "只展示当前账户下的 portfolio 和分配情况。每个 portfolio 都可以展开单独配置",
      currentAccountTitle: "当前账户分配",
      empty: "当前账户还没有可配置的 portfolio。先创建子组合，再回来配置 allocation。",
      noAllocationYet: "这个 portfolio 还没有 allocation，展开后可以直接新增。",
      expand: "展开",
      collapse: "收起",
      newAllocation: "新增分配",
      strategy: "策略",
      strategyDescription:
        "选择要放进这个 portfolio 的策略。已存在的组合会走 upsert 覆盖保存",
      strategyEditingDescription:
        "当前正在调整已有分配。策略本身固定不变，如果要配新策略请点右上角的新增分配",
      strategyPlaceholder: "选择策略",
      allocationPct: "资金占比 / allocation_pct",
      allocationPctDescription:
        "表示这个策略在该 portfolio 下可以使用多少虚拟资金，范围 0 到 1。比如 0.25 表示 25%",
      capitalBase: "固定本金 / capital_base",
      capitalBaseDescription:
        "可选。留空时系统会按账户净值乘以 allocation_pct 计算虚拟本金；填写后则优先用这个固定金额",
      capitalBasePlaceholder: "可选固定 capital_base，例如 50000",
      status: "状态",
      statusDescription:
        "只有 active 的 allocation 会被多策略调度器真正读取; draft 和 archived 适合预配置或暂时停用",
      allowFractional: "允许 fractional shares",
      allowFractionalDescription:
        "打开后仓位计算允许小数股；关闭后会把下单数量向下取整，更接近只支持整股的执行方式",
      notes: "备注",
      notesDescription:
        "可选。建议写清楚这个 allocation 的用途，例如属于哪个子资金池、目标风险暴露，或者是否为临时实验配置",
      save: "保存分配",
      saving: "保存中...",
      noStrategyDescription: "暂无策略描述",
      defaultCapitalBase: "跟随账户净值 * allocation_pct",
      capitalBaseLabel: "capital base",
      notesLabel: "notes",
      countAllocations: "条分配",
      countActive: "条 active",
    },
    execution: {
      title: "Paper Trading 执行",
      subtitle:
        "执行时只需要选子组合。系统会自动根据子组合找到对应的 paper account，再用那套 Alpaca 凭证去查询账户和发单。",
      singleTitle: "单策略运行",
      singleSubtitle:
        "适合先验证某个策略在某个 portfolio 下的 sleeve 账本、风控和下单逻辑。",
      multiTitle: "多策略调度",
      multiSubtitle:
        "这个入口会按 portfolio 下的 active allocation 顺序，把多个策略跑在同一个 Alpaca account 上。",
      strategy: "策略",
      strategyDescription: "选择一个 active 且 engine-ready 的策略",
      strategyPlaceholder: "选择策略",
      tradeDate: "交易日期",
      singleTradeDateDescription:
        "使用这一天的日线特征数据生成信号。当前系统是日频策略，所以这里填的是交易日，不是分钟级时间戳。",
      multiTradeDateDescription:
        "所有被调度的策略都会使用同一天的日线特征快照来生成信号和执行本轮 paper trading。",
      latestTradeDateSuffix: "当前最新可用交易日是 {date}",
      portfolio: "子组合 / Portfolio",
      singlePortfolioDescription:
        "决定这个策略要挂到哪个虚拟子组合下运行。系统会先根据这个 portfolio 找到对应的 paper account，再去 Alpaca 查询和发单",
      multiPortfolioDescription:
        "多策略调度会读取这个 portfolio 下所有 active allocation，并自动找到它所属的 paper account 后执行",
      basketOverride: "股票池覆盖",
      basketOverrideDescription:
        "可选。留空时使用策略自身配置的 universe；选了 basket 后，会临时用这个股票池覆盖本次运行的 universe",
      basketDefaultOption: "使用策略原始 universe",
      submitOrders: "提交真实 paper 订单",
      singleSubmitOrdersDescription:
        "关闭时只做 dry run，验证信号、sleeve 账本和风控；打开后会真正向 Alpaca paper account 发单",
      multiSubmitOrdersDescription:
        "关闭时只跑模拟调度，不真正下单；打开后会把每个策略的订单都提交到该子组合所属的 Alpaca paper account",
      submitOrdersLabel: "真正提交到 Alpaca paper account",
      continueOnError: "失败后的处理方式",
      continueOnErrorDescription:
        "打开后即使某个策略执行失败，调度器也会继续跑 portfolio 里剩下的策略；关闭则遇到首个错误就停止",
      continueOnErrorLabel: "某个策略失败后继续跑后续策略",
      runSingle: "运行单策略 Paper Trading",
      runningSingle: "执行中...",
      runMulti: "运行多策略 Paper Trading",
      runningMulti: "调度中...",
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
        "当前是编辑模式。点击别的 allocation 可以切换到那条分配，点新增分配会切回新建模式",
      createModeDescription:
        "这里保存的 allocation 会直接绑定到当前 portfolio，并被后续多策略调度读取",
    },
    validation: {
      accountNameRequired: "请输入账户名称",
      accountRequired: "请先选择一个 paper account",
      portfolioNameRequired: "请输入子组合名称",
      portfolioStrategiesRequired: "请至少选择一个要初始化到子组合里的策略",
      strategyRequired: "请选择一个策略",
    },
    errors: {
      createAccountFailed: "创建 paper account 失败",
      createPortfolioFailed: "创建策略子组合失败",
      saveAllocationFailed: "保存策略分配失败",
      singleRunFailed: "发起单策略 paper trading 失败",
      multiRunFailed: "发起多策略 paper trading 失败",
    },
  },
} as const;
