# Quant Trading System

[English](README.md) | [中文](README.zh-CN.md)

一个面向股票量化研究与交易执行的全栈项目，覆盖了策略定义、特征数据准备、回测、paper trading、组合分配，以及基于 Alpaca 的定时自动下单链路。

当前仓库由两部分组成：

- `backend`：FastAPI + SQLAlchemy + PostgreSQL，负责策略、回测、市场数据、paper account、组合分配和调度执行
- `frontend`：Next.js，提供策略管理、回测查看、篮子管理、组合配置和 paper trading 页面

## 当前支持的核心功能

- 策略管理
  - 创建、查看、更新、归档策略
  - 获取策略 catalog 和 normalized runtime payload
  - 当前策略类型包含 `trend`、`mean_reversion`、`island_reversal`、`double_bottom`、`custom`
  - 当前 engine-ready 的执行型策略以 `trend`、`mean_reversion`、`island_reversal`、`double_bottom` 为主

- 市场数据与特征工程
  - 维护 instruments、EOD bars、adjusted prices、daily features
  - 支持通过 Massive 补历史行情、补缺失行情、回刷特征
  - 内置每日市场数据 catch-up 脚本

- 回测
  - 基于策略参数和 `daily_features` 生成信号
  - 持久化 `StrategyRun`、`Signal`、`Transaction`、`PortfolioSnapshot`
  - 前端可查看回测列表和详情

- Paper trading
  - 支持多个 Alpaca paper account
  - 支持一个 account 下挂多个 strategy portfolio
  - 支持 strategy allocation、capital base、是否允许碎股、是否参与 auto-run
  - 支持单策略和多策略 paper trading
  - 支持向 Alpaca 提交真实 paper order

- 每日 scheduler
  - backend 启动时自动拉起 paper trading scheduler
  - scheduler 只会在 `daily_features` 对目标 trade date 完整落库后才执行
  - scheduler 只会跑 `auto_run_enabled=true` 的 active portfolio allocation
  - 可以配置为 dry run，也可以配置为直接提交 Alpaca paper orders

## 技术栈

- Backend
  - FastAPI
  - SQLAlchemy 2.x
  - PostgreSQL
  - Requests / Psycopg

- Frontend
  - Next.js 15
  - React 18
  - TypeScript
  - Axios

- Broker / Data
  - Alpaca paper trading API
  - Massive market data

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
├── data/                 # 本地数据文件
├── logs/                 # 回填和定时任务日志
├── docker-compose.yml
├── Makefile
├── README.md
├── README.en.md
└── README.zh-CN.md
```

## 前端页面

当前前端页面主要包括：

- `/dashboard`
- `/strategies`
- `/strategies/new`
- `/strategies/[strategyId]`
- `/backtests`
- `/backtests/[runId]`
- `/stock-baskets`
- `/strategy-allocations`
- `/paper-trading`

## 后端 API 模块

当前主要路由模块包括：

- `/api/strategies`
- `/api/backtests`
- `/api/market-data`
- `/api/stock-baskets`
- `/api/strategy-allocations`
- `/api/paper-accounts`
- `/api/strategy-portfolios`
- `/api/paper-trading`

应用健康检查：

- `/`
- `/healthz`

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
```

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

ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

说明：

- `DATABASE_URL` / `SQLALCHEMY_DATABASE_URL` 供 backend 使用
- Alpaca 相关功能可用 `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
- paper account 也支持把凭证映射到自定义环境变量名，例如 `ALPACA_API_KEY_MAIN`

### 4. 初始化数据库

首次启动前可以手动跑一次：

```bash
.venv/bin/python backend/utils/create_db.py
```

这个脚本会顺序执行 `backend/utils/` 下的 `create_*.sql` 文件，创建项目需要的表结构。

### 5. 启动开发环境

同时启动前后端：

```bash
make dev
```

只启动 backend：

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

如果你要用 Massive 或 Alpaca，再补充：

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
- `./data` 会挂载到容器 `/app/data`
- `./logs` 会挂载到容器 `/app/logs`
- 修改 `NEXT_PUBLIC_API_BASE_URL` 后需要重新 build frontend 镜像

## 常用命令

```bash
make help
make dev
make dev-backend
make dev-frontend
make backfill-daily
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
  - 顺序执行缺失 EOD 检查、公司行为同步、复权价格刷新、`daily_features` 刷新

- `backfill_missing_eod_from_massive.py`
  - 用 Massive 补缺失的日线行情

- `backfill_adjusted_prices.py`
  - 刷新复权 OHLC

- `backfill_daily_features.py`
  - 基于 `eod_bars` 计算并回写 `daily_features`

通过 Makefile 触发每日回填：

```bash
make backfill-daily
```

如果需要传额外参数：

```bash
make backfill-daily BACKFILL_ARGS="--start-date 2026-04-01 --end-date 2026-04-10"
```

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

当前 scheduler 的逻辑是：

1. 轮询当前纽约时间
2. 查找 `<= 今天` 的最新 ready trade date
3. ready trade date 的定义是：
   - 该日期在 `eod_bars` 中存在数据
   - 且该日期的每一条 `eod_bars` 都已经有对应的 `daily_features`
4. 只有在特征完整落库后，scheduler 才允许执行
5. 到达 `PAPER_TRADING_SCHEDULER_RUN_TIME_NY` 后，才会真正跑 portfolio
6. 同一 `portfolio + trade_date + trigger=scheduler` 只会执行一次

这样做的目的是避免：

- `daily_features` 只落了一部分就抢跑
- 同一天重复下单
- 数据晚到时直接错过原本应该执行的 trade date

### Scheduler 相关环境变量

```env
PAPER_TRADING_SCHEDULER_ENABLED=true
PAPER_TRADING_SCHEDULER_RUN_TIME_NY=23:30
PAPER_TRADING_SCHEDULER_POLL_SECONDS=60
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
PAPER_TRADING_SCHEDULER_CONTINUE_ON_ERROR=true
```

建议：

- 初次联调时先保持 `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`
- 确认信号和组合分配正常后，再切到 `true`

### Alpaca 说明

- 这里的自动下单面向 Alpaca paper account
- 真实提交订单后，会在 Alpaca paper 账户里留下真实的 paper position / order state
- 如果你在联调期间做过测试下单，记得清理 paper 持仓和挂单，避免影响后续策略判断

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

这套模型把“策略定义”“策略执行结果”“组合分配”“券商账户映射”拆开了，便于：

- 一套策略挂多个 portfolio
- 一个 paper account 管多个 portfolio
- 同时支持回测和 paper trading

## README 之外值得先看的文件

- [backend/src/main.py](backend/src/main.py)
- [backend/src/services/paper_trading_service.py](backend/src/services/paper_trading_service.py)
- [backend/src/services/paper_trading_scheduler.py](backend/src/services/paper_trading_scheduler.py)
- [backend/src/services/strategy_engine.py](backend/src/services/strategy_engine.py)
- [backend/src/services/strategy_registry.py](backend/src/services/strategy_registry.py)
- [frontend/src/pages/paper-trading.tsx](frontend/src/pages/paper-trading.tsx)

## 当前 README 的定位

这份 README 重点覆盖：

- 这个仓库现在能做什么
- 项目结构如何对应功能
- 本地开发 / Docker 如何启动
- 数据回填与 scheduler 如何衔接

如果后面你希望，我可以继续把它往两种方向再补一层：

- 面向新同事的“上手流程版”
- 面向部署的“运维与生产配置版”
