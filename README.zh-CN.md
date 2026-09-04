# Quant Trading System

[English](README.md) | [中文](README.zh-CN.md)

一个面向股票量化研究与交易执行的全栈项目，覆盖了策略定义、特征数据准备、回测、paper trading、组合分配，以及基于 Alpaca 的定时自动下单链路。

当前仓库由两部分组成：

- `backend`：FastAPI + SQLAlchemy + PostgreSQL，负责策略、回测、市场数据、paper account、组合分配和调度执行
- `frontend`：Next.js，提供策略管理、回测查看、篮子管理、组合配置和 paper trading 页面

## 当前支持的核心功能

- 策略管理
  - 创建、查看、更新、归档策略
  - 从 `/strategies/new` 引导中心开始：可用五步向导手工配置已有引擎策略，也可转入 Agent 的已有大类研究或新算法研究
  - 手工创建在落库前完成校验和标准化，并且始终保存为 `draft`；不会激活 portfolio、创建 allocation、启动调度或提交订单
  - 可从策略列表或详情页“基于此策略新建”：向导预填并锁定原策略类型，保存为名称唯一、独立 `strategy_key`、从 `v1` 开始的 Draft；不会复制回测、allocation、运行记录或持仓
  - 获取策略 catalog 和 normalized runtime payload；共享 C++ descriptor registry 是默认值、JSON Schema、所需特征、历史窗口、校验和算法 revision 的唯一来源
  - 当前策略类型包含 `trend`、`mean_reversion`、`momentum_breakout`、`island_reversal`、`double_bottom`、`head_shoulders_bottom`、`rounded_bottom`、`v_reversal`、`support_resistance`、`custom`
  - 五类底部反转策略使用 20% / 50% / 100% 累计目标分批建仓；详见 [底部反转策略](docs/bottom-reversal-strategies.zh-CN.md)
  - 当前 engine-ready 的执行型策略包含 `trend`、`mean_reversion`、`momentum_breakout`、`island_reversal`、`double_bottom`、`head_shoulders_bottom`、`rounded_bottom`、`v_reversal`、`support_resistance`
  - 九个 engine-ready 策略全部只由共享 C++ 内核执行；`custom` 继续 stored-only，不是可执行 DSL
  - `momentum_breakout` 只使用现有优先前复权的日线收盘价、SMA20、20 日收益和成交量特征；T 日收盘信号在下一有效交易日（T+1）开盘成交

- 市场数据与特征工程
  - 维护 instruments、EOD bars、adjusted prices、daily features
  - 支持通过 Massive 补历史行情、补缺失行情、回刷特征
  - 支持通过 Tushare 将沪深北 A 股日线、复权因子、大盘指数和回测特征幂等导入 PostgreSQL；详见 [Tushare A 股数据](docs/tushare-a-share-data.zh-CN.md)
  - 每日 catch-up 在排他维护窗口中运行：先排空回测/研究，在写入前失效派生缓存，失败后继续阻塞

- 回测
  - 基于策略参数和 `daily_features` 生成信号
  - 在 PostgreSQL 中排队手动与研究回测，并由独立 worker 执行
  - 支持 `summary`、`trades`、`full` 三种持久化级别；手动回测默认 `full`
  - 使用 `make benchmark-backtests BENCHMARK_ARGS="plan"` 只读规划 correctness/screening 漏斗；写入基准必须显式增加 `--apply` 并满足性能指南的安全门禁
  - 手动、研究和验证回测统一解析稳定 instrument identity，并通过队列深度为 1 的生产者—消费者流水线处理“日历年 × 完整股票池”的 v5 PreparedDataset 块；热命中直接打开，损坏缓存会原子重建
  - 通过增量接口加载摘要、下采样权益、signals 和 transactions
  - 所有 engine-ready 运行统一经过有状态的进程内 C++20 `BacktestSession`；typed 普通明细、共享支撑/压力生命周期事件、运行决策事件、链接和最终摘要在同一数据库事务中发布
  - 按 T 日冻结的信号强度对同策略 BUY 排名，再于下一有效交易日（T+1）开盘尝试成交；详见[信号强度](docs/signal-strength.zh-CN.md)

- Paper trading
  - 支持多个 Alpaca paper account
  - 支持一个 account 下挂多个 strategy portfolio
  - 支持 strategy allocation、capital base、是否允许碎股、是否参与 auto-run
  - 支持单策略和多策略 paper trading
  - 将 Paper 历史转换为内存 PreparedDataset v5，并调用原生 `evaluate_day(dataset_day, strategy, portfolio_state)`，与回测共享规则和 canonical metadata；券商查询、幂等订单、策略资金隔离和下一有效交易日开盘实时报价校验继续留在 Python
  - 支持向 Alpaca 提交真实 paper order

- 每日 scheduler
  - backend 启动时自动拉起 paper trading scheduler
  - scheduler 只会在 `daily_features` 对目标 trade date 完整落库后才执行
  - scheduler 只会跑 `auto_run_enabled=true` 的 active portfolio allocation
  - 可以配置为 dry run，也可以配置为直接提交 Alpaca paper orders

- Agent 辅助策略研究
  - 通过 AgentOps 工作流生成策略草案、执行有界研究实验，并为 C++ 策略模块、descriptor、golden 差分和 wheel 验证准备 Draft PR
  - 支持 engine-ready 的 `support_resistance` 研究：冻结支撑区反弹入场，保留通道与风险筛选；平仓后可在同一区域再次满足条件入场
  - 股票阶段身份与四状态分类独立；收盘破坏或区域冲突结束整个旧阶段，独立有效性研究采用检测器 revision 14
  - 持久化实验规格、确定性的 trial 展开、进度、token 用量、终止证据和稳健性报告
  - 支持按运行时长、工作流 token 用量或目标指标自动停止
  - Agent service API 不开放券商下单、组合激活或订单提交工具

## 技术栈

- Backend
  - FastAPI
  - SQLAlchemy 2.x
  - PostgreSQL
  - C++20 / pybind11 / NumPy buffer protocol
  - Requests / Psycopg 3

- Frontend
  - Next.js 15
  - React 18
  - TypeScript
  - Axios

- Broker / Data
  - Alpaca paper trading API
  - Massive market data
  - Tushare A 股日线、复权因子、上证指数与深证成指

## 项目结构

```text
.
├── backend/
│   ├── src/
│   │   ├── api/          # FastAPI routers
│   │   ├── core/         # DB / config / app-level wiring
│   │   ├── models/       # ORM tables
│   │   └── services/     # 策略引擎、回测、paper trading、scheduler 等核心逻辑
│   ├── tests/            # 当前仓库里的后端单元测试
│   ├── utils/            # 建表、回填、特征刷新、数据修复脚本
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/components/   # 公共 UI 组件
│   ├── src/pages/        # Next.js 页面
│   ├── package.json
│   └── Dockerfile
├── apps/openapi.yaml     # 项目 API 规格草案/对照文档
├── docs/                 # 架构、研究与联调指南
├── data/                 # 本地数据文件
├── logs/                 # 回填和定时任务日志
├── docker-compose.yml
├── Makefile
├── README.md
└── README.zh-CN.md
```

## 前端页面

当前前端页面主要包括：

- `/dashboard`
- `/strategies`：提供按策略大类导航、数量统计和类型色彩识别
- `/strategies/new`
- `/strategies/[strategyId]`
- `/backtests`
- `/backtest-tasks`
- `/backtests/[runId]`
- `/stock-baskets`
- `/strategy-allocations`
- `/paper-trading`
- `/paper-trading/portfolios/[portfolioId]`
- `/research`
- `/research/[experimentId]`
- `/agent-runs/[runId]`

14 个正式工作台页面统一使用宽屏紧凑布局。主导航位于可收缩左侧栏；可手动收放的宽屏下，主内容视口宽度保持不变，卡片和表格不会随侧栏移动而多显示或少显示内容。不再保留固定右侧上下文栏。页面相关的配置、创建、身份和风险详情通过带明确标签、支持键盘操作的弹窗按需打开；低于 768px 时弹窗切换为全屏。重要进度、校验结果、券商警告和 engine-ready 状态仍直接显示在主区。全平台的短枚举与分页选择统一使用深色、青色强调的 Radix 自定义选项面板，不再调用操作系统菜单，并共享键盘、hover、focus、错误、禁用和移动端状态；回测与 Paper Trading 中的策略、股票组合和 Portfolio 实体选择器继续支持搜索和完整键盘导航，同时保留原有请求值。总览不再显示重复的“风险与待办”和“今日待办”卡片；回测工作台可从页面右上角发起新回测，策略库和策略详情中的“用它回测”入口则会直接打开同一回测窗口并预选当前 engine-ready 策略。结果列表支持按策略名或股票组合搜索，并按策略大类和运行状态筛选，默认每页显示 10 条、支持切换页容量，上一页/下一页固定居中，每张结果卡片复用策略库的大类颜色与标签。终态手动回测可在二次确认后逐条删除；确认后弹窗立即关闭，删除在后台继续，仅在成功或失败后于视口中间偏上显示固定通知，随后自动淡出。排队中和运行中的回测保持保护，研究与验证回测只能在所属实验中管理。创建策略的大类卡片和选中状态也使用同一套颜色。回测详情独立加载与市场匹配的对比曲线：A 股回测使用上证指数与深证成指，其他回测使用 SPY 与 QQQ；该接口不扩大紧凑 summary/equity payload。详情页同时取消原始摘要指标列表，最新持仓只保留数量、成本价、收盘价和市值。密集表格支持排序、筛选、列显示、列宽调整以及明确的客户端/服务端分页；较小结果集保留语义化表格，达到 200 行后才启用可视区域虚拟化。窄屏下详情页双栏会切换为单栏，紧凑指标卡也会在卡片宽度不足时上下排列，以完整保留标签、金额和技术字段。开发服务与 production build 使用不同的 Next.js 输出目录，验证构建不会破坏正在运行的开发服务。

持仓生命周期按纽约交易日显示事件；未平仓行明确区分期末估值与真实卖出成交。SR 审计数据按可见 K 线窗口加载，不依赖首批信号分页；相同已完成请求复用，加载期间 K 线仍可交互。生命周期图不再绘制行情阶段背景或入场通道。

回测详情按“回测概览 → 权益曲线 → 回测复盘工作台”分为三个并列区块，每个区块只保留一层外框。四项核心指标合并为一排，回测信息、曲线统计、生命周期详情和期末持仓通过留白与分隔线组织，不再嵌套小卡片。“最新快照”仍默认收起；权益曲线加载、出错或数据不足时，概览仍可阅读。手机上指标自动换行，宽表格在自身区域滚动。

回测详情把个股盈亏、信号强度排名、生命周期、交易明细和最新持仓收纳进同一个复盘工作台，通过标签页切换，并默认首先显示“生命周期”。生命周期标签页直接显示整合后的指标、筛选和数量工具栏，去掉重复标题和说明。窗口内容区独立滚动，并保留各模块原有的筛选、排序、分页和展开交互；只有点击生命周期摘要行（或聚焦该行后按 Enter / 空格）才会展开或收起；详情文字、图表、空白区及拖拽都不会触发折叠。切换买入前或卖出后显示范围时会保留现有蜡烛图和窗口滚动位置，待新数据返回后原位更新。生命周期图把所有颜色的事件圆点和买卖箭头放在价格区上下留白，并用虚线连接对应 K 线，避免遮挡蜡烛和成交量；相邻标记文字会自动换边或错层，避免相互覆盖。

SR 生命周期默认只显示本笔交易明确关联的区域：入场冻结区域、通道边界及匹配卖出证据（仅下跌趋势退出纳入该退出状态边界）。勾选“显示全部区域”查看当前窗口其他区域，缺少关联时不按价格猜测。浅色虚线明确表示从最早成员 Pivot 到确认日的“事后拟合”；实线为不可修改的有效几何，在阶段结束时截止。区域裁剪到所选窗口，不跨阶段拼接。悬停突出单一区域及其固定 Pivot/真实触碰，点击固定，Escape 或空白清除；区域选择器支持键盘、触屏及边缘多候选切换。窗口外 Pivot 只列日期。悬停不请求数据、不重建图表、不重置缩放。时序、同区重复交易、缓存 revision 与精确 ID 备份恢复规则见 [SR 策略与安全清理](docs/support-resistance-strategy.zh-CN.md)。

回测删除统一使用平台内的无障碍工作区弹窗，明确说明保留的数据，并分开提供取消与危险确认操作，不再调用浏览器原生确认框。

工作台动效使用现有 CSS Modules 和 Radix：操作反馈、浮层开合与内容进入分别采用 120ms、180ms 和 240ms。可点击的策略与回测卡片在鼠标悬停或键盘聚焦时轻微上浮，并沿用策略大类颜色高亮；总览四张指标卡以 30ms 间隔淡入，骨架屏仅在加载时轻微明暗变化。轮询、搜索和分页不会重播整页动画，金融数值直接显示，图表与虚拟表格行不做进场变形。进度条只衔接服务端实际百分比，终态立即停稳；中间偏上的操作通知仍在 3200ms 后自动移除。弹窗退出动画结束后恢复焦点与页面滚动，手机保留全屏布局。系统开启“减少动态效果”时停用动效与平滑返回顶部，操作和状态信息仍完整可用。本次仅调整前端呈现，无 API、数据库或券商操作变化。

股票库支持编辑已保存组合的名称、说明、股票代码和状态（`PUT /api/stock-baskets/{basket_id}`），保留组合 ID；历史回测快照不变。编辑按钮位于每张组合卡片左下角。创建与编辑均使用短 ticker / 公司名搜索、单个代码添加、市场 / 行业 / 市值筛选和每页 20 只的已选列表，不再存在完整 ticker 长文本框。全部匹配需确认后追加并去重；关闭而不保存会丢弃草稿，说明输入不会重渲染已记忆化的选择器。系统自动维护的 `All Common Stock` 不可编辑，请另建独立组合。数据单位、手动更新和授权建表见[股票池筛选](docs/stock-basket-screening.zh-CN.md)。创建和克隆策略只配置交易逻辑，不再选择股票范围；定义与确认步骤不再显示重复的“股票范围”和“初始状态”卡片，提交仍固定保存为 Draft。每次新建手工回测必须选择已保存股票池，并把当次成员固化到运行快照。“发起回测”窗口会对齐同一行卡片内的控件；选择股票组合时，可编辑的基准标的会随之更新为 A 股的 `000001.SH` 或美股的 `SPY`。回测工作台支持 A 股 / 美股的结果筛选和组合选择筛选；结果市场来自运行快照，与当前组合名称无关。任务中心的“总耗时”使用开始执行到完成持久化的时间，不包含排队；缺失时间戳显示“—”或“-”，不估算。行情入口位于左侧栏底部，收起导航时显示图标，手机上位于左下角；行情窗默认从工作区左侧打开。

百分比参数以百分数展示和输入（输入 10 表示 10%，0.5 表示 0.5%），保存时仍为比例小数；数字和百分比字段编辑时可以暂时留空，清空后不会立刻被替换为 0，而是在校验时提示。收益指标保留必要的小数精度，ATR、成交量倍数、bps 和原始 JSON 不换算。总览的策略证据与回测活动、研究创建的大类选项复用策略库颜色。SR Pivot 标记仅在悬停或固定区域时显示，表示事后拟合依据，不表示极值当日已知的交易信号。

展示改动不改变执行时序或券商状态。股票筛选另需明确授权创建 `instrument_market_caps` 表，其余展示改动无需更新 schema。仓库没有 Alembic 工作流，也不执行数据库重置。

## 后端 API 模块

当前主要路由模块包括：

- `/api/strategies`
- `/api/backtests`
- `/api/backtests/tasks`
- `/api/research/worker-status`
- `/api/market-data`
- `/api/stock-baskets`
- `/api/strategy-allocations`
- `/api/paper-accounts`
- `/api/strategy-portfolios`
- `/api/paper-trading`
- `/api/research`
- `/api/agent`

`/api/agent/*` 路由要求 Bearer service token，只提供受控的策略草案和研究实验操作，不开放券商订单或组合激活能力。

Web 进程不执行 CPU 回测。完整平台命令会监管轻量 manager；manager 意外退出后 2 秒自动重启，并且只在存在 durable queued 任务时启动基于 `spawn` 的多进程 worker。`BACKTEST_WORKER_CONCURRENCY` 默认为 `2`（`1|2`）；`BACKTEST_INTRA_RUN_THREADS` 默认为 `4`（`1..16`）并受可用 CPU 自动限额。交易日之间保持串行，只有超过文档阈值时才由可复用原生线程池并行评估同日标的。两个配置修改后都需重启 backend 与 manager。`GET /api/backtests/worker-status` 返回自动执行健康状态、进程容量及配置/有效 run 内线程数，任务中心显示“进程数 × 每个 run 线程数”；列表页和任务中心显示结构化阶段、百分比和收尾条目进度；回测详情不再显示顶部执行进度面板，回测概览中的“最新快照”默认收起，可点击标题或用键盘展开。`/backtest-tasks` 工作台在不替换两层队列的前提下统一展示手动回测、研究 trial 和 verification job；并列的研究/回测健康卡可区分“尚未进入 durable queue”与“入队后暂停”。失败的普通回测和验证回测可在这里创建新的排队重试，同时保留原失败记录。普通回测的终态任务也可逐条删除；研究和验证证据仍归实验所有，只能从实验页删除。详见[回测性能与 worker 运维](docs/backtest-performance.zh-CN.md)。

应用健康检查：

- `/`
- `/healthz`
- `/readyz`（完整平台 readiness；要求存在健康的 backtest manager leader）

项目还维护了一份 API 规格文件：[apps/openapi.yaml](apps/openapi.yaml)

## 本地开发

### 前置依赖

建议准备：

- Python 3.12 左右的环境
- Node.js 18+
- PostgreSQL 16

### 1. 准备 Python 虚拟环境

`Makefile` 默认使用仓库根目录下的 `.venv/bin/python`，所以本地开发建议按这个结构准备：

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pip install -e backend/native
```

第二条命令会构建本地 C++20/pybind11 策略内核 wheel。Docker 使用仅含编译器的 builder stage 生成同一 wheel，runtime 镜像不包含编译工具链。

### 2. 安装前端依赖

```bash
cd frontend
npm install
```

### 3. 配置环境变量

项目会自动读取根目录 `.env`。

本地最常见的变量包括：

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
FRONTEND_ORIGIN=http://localhost:3000

MASSIVE_API_KEY=
TUSHARE_TOKEN=

ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

说明：

- `DATABASE_URL` / `SQLALCHEMY_DATABASE_URL` 供 backend 使用
- `TUSHARE_TOKEN` 只由 A 股导入命令读取，不得提交到 Git
- Alpaca 相关功能可用 `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
- paper account 也支持把凭证映射到自定义环境变量名，例如 `ALPACA_API_KEY_MAIN`

### 4. 初始化数据库

首次启动前可以手动跑一次：

```bash
.venv/bin/python backend/utils/create_db.py
```

这个脚本会顺序执行 `backend/utils/` 下的 `create_*.sql` 文件，创建项目需要的表结构。

### 5. 启动开发环境

`make dev` 会启动 backend、frontend 和按需 backtest manager。该完整平台入口会强制关闭 paper 调度与订单提交：

```bash
make dev
```

启动完整本地平台：

```bash
make dev
```

只启动 backend（部分启动方式，不自动消费队列；需要时仍须显式设置 paper 安全变量）：

```bash
make dev-backend
```

只启动 frontend：

```bash
make dev-frontend
```

默认地址：

- frontend: `http://localhost:3000`
- backend: `http://localhost:8000`

## Docker

这个仓库可以直接用 Docker Compose 启动完整本地环境：

- `frontend`: Next.js，默认 `http://localhost:3000`
- `backend`: FastAPI，默认 `http://localhost:8000`
- `backtest-worker-manager`: 轻量队列 manager；仅在有 eligible 任务时存在 worker 子进程
- `db`: PostgreSQL 16，默认 `localhost:5432`

### 1. 准备 Docker 环境变量

```bash
cp .env.docker.example .env.docker
```

推荐把 Docker 配置和本地开发 `.env` 分开维护。

至少确认这些变量存在或接受默认值：

```env
POSTGRES_DB=quant
POSTGRES_USER=quant
POSTGRES_PASSWORD=quantpass
POSTGRES_PORT=5432
FRONTEND_ORIGIN=http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

如果要用 Massive 或 Alpaca，再补充：

```env
MASSIVE_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

### 2. 启动

```bash
make docker-up
```

或者：

```bash
docker compose --env-file .env.docker up --build -d
```

### 3. 查看日志

```bash
make docker-logs
```

### 4. 停止

```bash
make docker-down
```

### 5. Docker 运行说明

- backend 容器启动时会先执行 `python utils/create_db.py`
- frontend 会等待健康的 manager leader；空队列时 manager 健康且无需 worker 子进程
- backend 与 manager 都强制关闭 paper 调度和订单提交
- `./data` 会挂载到容器 `/app/data`
- `./logs` 会挂载到容器 `/app/logs`
- 修改 `NEXT_PUBLIC_API_BASE_URL` 后需要重新 build frontend 镜像

## 常用命令

```bash
make help
make dev
make dev-agent-all
make dev-agent-safe
make dev-backend
make dev-frontend
make backtest-worker-manager
make backfill-daily
make import-a-share A_SHARE_ARGS="plan --start-date 2024-01-01 --end-date 2024-12-31"
make check-data
make docker-build
make docker-up
make docker-down
make docker-logs
```

## 数据准备与回填

`backend/utils/` 里放着项目的数据脚本。常用的有：

- `create_db.py`
  - 初始化数据库表结构

- `run_daily_market_backfill.py`
  - 每日市场数据 catch-up 总入口
  - 顺序执行证券主数据 → SIC/代码事件 → EOD 缺口 → VWAP → 公司行动 → 复权价格 → short interest → `daily_features` → 只读完整性门禁

- `backfill_missing_eod_from_massive.py`
  - 用 Massive 补缺失的日线行情

- `backfill_vwap_from_massive.py`
  - 使用 point-in-time 代码映射，仅填充为空的未复权 `eod_bars.vwap`，绝不覆盖 OHLCV

- `backfill_sic_from_massive.py`
  - 保存当前（或退市日最终）SIC 快照及命名空间隔离的 Massive ticker-overview 原始响应

- `backfill_short_interest_from_massive.py`
  - 保存 Massive/FINRA 双周结算事实，不向日频特征 forward-fill

- `backfill_ticker_events_from_massive.py`
  - 保存 experimental 代码变更事件，只应用完整验证且无冲突的代码区间

- `backfill_adjusted_prices.py`
  - 刷新复权 OHLC

- `backfill_daily_features.py`
  - 基于 `eod_bars` 计算并回写 `daily_features`

- `import_tushare_a_share.py`
  - 导入沪深北 A 股证券主数据、未复权日线、前/后复权 OHLC、大盘指数和 `daily_features`
  - 同步 `All A Shares (Tushare)` 股票组合，供九个 engine-ready 策略从回测工作台选择
  - A 股结果使用上证指数和深证成指对比，不再显示 SPY 与 QQQ

- `check_market_data_quality.py`
  - 只读检查价格/特征缺口、非法 VWAP/short interest、代码事件一致性、重复证券身份、代码历史重叠、数据陈旧和最新交易日是否完整

首次运行增强数据同步前，应先应用 `backend/utils/create_stock_enrichment.sql`。该 SQL 为增量且可幂等执行，但仓库没有 Alembic 迁移流程；应用前需要备份并核对目标数据库。它会新增 SIC 快照字段、`stock_short_interest`、`security_ticker_events` 和按证券记录的供应商同步状态。

Massive VWAP 以未复权口径保存（`adjusted=false`）。当前套餐历史边界从 2016-08-29 开始，更早的空 VWAP 属于预期 warning。SIC 是快照而非 point-in-time 行业历史。Short interest 以结算日为键；由于接口没有可靠发布日期，不能视为每个日线交易日当时已知。Ticker Events 仍是 experimental：原始事件始终可审计；不完整事件链、FIGI/交易所不一致、ticker 复用和区间冲突保持 `unresolved`，绝不猜测修复。

Tushare A 股导入必须先运行 `plan`，再显式运行 `apply`；apply 与每日 Massive 流水线复用同一个排他维护窗口，且不启动 backend、paper scheduler 或任何券商操作。完整命令、字段口径、恢复方式和回测限制见 [Tushare A 股数据](docs/tushare-a-share-data.zh-CN.md)。

通过 Makefile 触发每日回填：

```bash
make backfill-daily
```

如果需要传额外参数：

```bash
make backfill-daily BACKFILL_ARGS="--start-date 2026-04-01 --end-date 2026-04-10"
```

在不写数据库的情况下预览日期范围和供应商覆盖情况：

```bash
make backfill-daily BACKFILL_ARGS="--dry-run"
```

全量刷新 SIC 和 ticker events，或按数据集跳过：

```bash
make backfill-daily BACKFILL_ARGS="--full-reference-refresh --dry-run"
make backfill-daily BACKFILL_ARGS="--skip-sic --skip-ticker-events --skip-vwap --skip-short-interest"
```

dry-run 会读取所选增强数据集的供应商覆盖，但不会写事实表或修改身份区间；证券主数据同步会被跳过，因为其独立脚本尚无 dry-run 模式。所有写入均可幂等重跑。失败后的恢复方式是修复错误并重跑同一日期范围，不删除或重建历史；ticker-event 修复前后的区间快照保存在 `security_ticker_events` 中。

单独运行完整性门禁。关键错误始终返回非零状态；warning 默认只记录，传入 `--strict` 后才会导致失败：

```bash
make check-data
make check-data CHECK_DATA_ARGS="--strict --json"
```

特殊维护运行可用 `--skip-quality-check` 跳过最终门禁，或用 `--strict-quality-check` 让流水线中的 warning 也阻断任务。正常安装任务保持默认的“仅关键失败阻断”策略。

已安装的 macOS LaunchAgent 每天按本地时间 20:15 运行，并将日志写入 `logs/daily-market-backfill.log` 和 `logs/daily-market-backfill.err.log`。可用 `launchctl print "gui/$(id -u)/com.quant.daily-market-backfill"` 检查状态；补数脚本不会修改其日程或安装路径。写入运行先进入单例 `draining` 状态，拒绝新回测/研究并等待现有工作，取得数据库排他 advisory lock、失效派生缓存后才写源表。每个子进程收到同一维护所有者令牌，两项 Paper scheduler 配置始终显式关闭。流水线或质量门禁失败会把状态留在 `failed`，持续阻止策略工作，直到后续重跑成功。

## Paper Trading 与 Scheduler

### Paper trading 运行方式

项目支持两种 paper trading 触发方式：

- 手动触发
  - 通过 `/api/paper-trading/run`
  - 或 `/api/paper-trading/run-multi`

- 定时触发
  - backend 启动时自动启动 scheduler
  - scheduler 会扫描所有 active paper account 下的 active portfolio
  - 只执行 `auto_run_enabled=true` 的 strategy allocation

### Scheduler 当前执行逻辑
1. 轮询当前纽约时间
2. 查找 `<= 今天` 的最新 ready trade date
3. ready trade date 的定义是：
   - 该日期在 `eod_bars` 中存在数据
   - 且该日期的每一条 `eod_bars` 都已经有对应的 `daily_features`
4. 只有在特征完整落库后，scheduler 才允许执行
5. 到达 `PAPER_TRADING_SCHEDULER_RUN_TIME_NY` 后，才会真正跑 portfolio
6. 同一 `portfolio + trade_date + trigger=scheduler` 只会执行一次

### Scheduler 相关环境变量

```env
PAPER_TRADING_SCHEDULER_ENABLED=true
PAPER_TRADING_SCHEDULER_RUN_TIME_NY=23:30
PAPER_TRADING_SCHEDULER_POLL_SECONDS=60
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=true
PAPER_TRADING_SCHEDULER_CONTINUE_ON_ERROR=true
```

建议：

- 初次联调时先保持 `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`
- 确认信号和组合分配正常后，再切到 `true`

### Alpaca 说明

- 这里的自动下单面向 Alpaca paper account
- 真实提交订单后，会在 Alpaca paper 账户里留下真实的 paper position / order state
- 如果在联调期间做过测试下单，记得清理 paper 持仓和挂单，避免影响后续策略判断
- 应用当前默认同时启用 scheduler 和 scheduler 订单提交；如果没有明确下单意图，必须将两项都覆盖为 `false`

## 数据模型概览

项目目前的核心关系是：

```text
Strategy
  -> StrategyRun
    -> Signal
    -> Transaction
    -> PortfolioSnapshot

PaperTradingAccount
  -> StrategyPortfolio
    -> StrategyAllocation
      -> Strategy
```

这套模型将“策略定义”“策略执行结果”“组合分配”“券商账户映射”拆开，便于：

- 一套策略挂多个 portfolio
- 一个 paper account 管多个 portfolio
- 同时支持回测和 paper trading

## Dashboard：研究与运行总览

`/dashboard` 使用一个 `GET /api/dashboard/overview` 请求加载本地只读摘要，页面可见时每 30 秒刷新，也可手动刷新。刷新失败保留上次成功数据及时间；切换语言不会重新请求业务数据。接口不会初始化缺失记录、创建任务、运行调度或访问券商。

- 系统状态区分正常、受限、异常、未知和已关闭。数据维护 `ready` 仅表示门禁放行，不证明最新交易日行情完整；券商连接始终标为未检查。调度状态观察当前 Web 进程的循环，历史策略运行结果单独展示。
- 回测运行/排队数来自 durable queue；等待研究调度仅统计尚未关联回测 run 的 queued trial。统计不受列表分页或最近 50 条限制。待处理事项按严重程度排序，同类问题汇总，所有处理入口跳转既有正式页面。
- 策略证据最多展示最近更新的 10 个策略，使用同一 ID、版本的最新 completed 手动回测，排除研究和 verification 参数覆盖运行。由于配置可原地修改，还比较规则、风险和执行参数；metadata 不参与比较，股票池由每次运行独立解析，以详情中的股票池和窗口为准。参数不匹配时隐藏指标并标明配置变更。收益、Sharpe、回撤与交易次数直接读取既有指标，不重算，也不按不同范围排名。
- 研究实验、已评估候选（已有 Pareto 排名）、完整验证完成候选、已晋升策略、配置 Paper 的策略分别计数，单位不同且可能重叠。验证完成和历史晋升来源均不是通用有效性或盈利证明。
- Paper 仅统计 active paper 账户和 active 组合，最多展示 10 个组合。自动运行资格沿用调度选择条件：active 账户、组合、策略和 allocation，且 allocation 开启 auto-run；不可执行配置另行告警。最近策略运行保留 dry-run/提交订单模式区分，不代表整个组合结果。首页不提供实时挂单、账户权益、当日盈亏或权益曲线，后续优先单独接入真实单账户数据。
- 最近活动合并各来源最近记录，API 最多 20 条、页面显示 10 条；同一 run 不再以 job 重复呈现。空集合计数为 `0`，无法获得的数值为 `null`，界面显示 `—`。告警与活动使用稳定代码和本地化文本。

后端使用固定数量的批量查询与窗口查询，不逐账户调用 overview，不读取交易、信号或权益明细。需要校验当前策略参数的 engine-ready 状态，因此参数读取与校验成本仍随策略数量增长；查询次数固定不等同于耗时恒定。数据库或程序故障会暴露为请求失败，异常 JSON 仅影响对应证据。数据库结构、交易与回测语义均未改变，无需迁移。回滚使用代码版本，不保留第二套 Dashboard。

## Agent 研究工作台

`/strategies/new` 会先区分手工创建和 Agent 辅助研究。手工路径读取 catalog 默认值，以人类可读百分比、核心/高级参数分层和只读校验接口完成五步创建，校验通过后只保存 Draft。Agent 路径跳转到 `/research?mode=category|algorithm&source=strategy-create`，预选对应研究模式并提供返回创建中心的入口。

`/research` 只保留两个入口。“已有引擎大类研究”让用户选择 catalog handler，自动创建经过校验的 draft，并在实验审批后执行最多 5 轮 / 100 个实际回测的自适应 Pareto 研究；“新算法研究”只交付 Draft PR，不自动合并、部署或回测。旧有限网格实验继续只读，但创建流程已下线。详见[研究实验](docs/research-experiments.zh-CN.md)。

## 文档

请从[文档索引](docs/README.zh-CN.md)开始。当前维护的指南包括：

- [系统架构](docs/architecture.zh-CN.md)
- [研究实验](docs/research-experiments.zh-CN.md)
- [支撑/压力区策略有效性研究](docs/support-resistance-effectiveness.zh-CN.md)
- [支撑线与压力线策略](docs/support-resistance-strategy.zh-CN.md)
- [Quant 与 AgentOps 本地联调](docs/agent-research-integration.zh-CN.md)

`docs/` 下带日期的交付和验证报告属于历史证据，不纳入当前文档导航，也不能替代上述长期指南。
