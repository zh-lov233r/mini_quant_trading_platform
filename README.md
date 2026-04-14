# Quant Trading System

[中文文档](README.zh-CN.md)

A full-stack quant trading system for equity strategy research and execution, covering strategy definition, feature data preparation, backtesting, paper trading, portfolio allocation, and scheduled Alpaca paper-order execution.

The repository currently has two main parts:

- `backend`: FastAPI + SQLAlchemy + PostgreSQL for strategies, backtests, market data, paper accounts, portfolio allocation, and scheduling
- `frontend`: Next.js UI for strategy management, backtest inspection, basket management, portfolio configuration, and paper trading workflows

## Core Features

- Strategy management
  - Create, inspect, update, and archive strategies
  - Expose a strategy catalog and normalized runtime payloads
  - Current strategy types include `trend`, `mean_reversion`, `island_reversal`, `double_bottom`, and `custom`
  - Engine-ready execution currently focuses on `trend`, `mean_reversion`, `island_reversal`, and `double_bottom`

- Market data and feature engineering
  - Maintain instruments, EOD bars, adjusted prices, and daily features
  - Backfill historical and missing market data from Massive
  - Provide a daily market-data catch-up pipeline

- Backtesting
  - Generate signals from strategy parameters plus `daily_features`
  - Persist `StrategyRun`, `Signal`, `Transaction`, and `PortfolioSnapshot`
  - Inspect backtest runs and details from the frontend

- Paper trading
  - Support multiple Alpaca paper accounts
  - Support multiple strategy portfolios under one paper account
  - Support strategy allocation, capital base, fractional trading, and auto-run flags
  - Support single-strategy and multi-strategy paper trading
  - Support real paper-order submission to Alpaca

- Daily scheduler
  - Automatically starts the paper-trading scheduler when the backend boots
  - Runs only after `daily_features` are fully materialized for the target trade date
  - Runs only active allocations with `auto_run_enabled=true`
  - Can run in dry-run mode or submit real Alpaca paper orders

## Tech Stack

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

## Project Structure

```text
.
├── backend/
│   ├── src/
│   │   ├── api/          # FastAPI routers
│   │   ├── core/         # DB, config, and app wiring
│   │   ├── models/       # ORM tables
│   │   └── services/     # Strategy engine, backtests, paper trading, scheduler, etc.
│   ├── tests/            # Backend unit tests currently in the repo
│   ├── utils/            # Schema setup, backfills, feature refresh, repair scripts
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/components/   # Shared UI components
│   ├── src/pages/        # Next.js pages
│   ├── package.json
│   └── Dockerfile
├── apps/openapi.yaml     # API spec / reference document
├── data/                 # Local data files
├── logs/                 # Backfill and scheduled-task logs
├── docker-compose.yml
├── Makefile
├── README.md
└── README.zh-CN.md
```

## Frontend Pages

The frontend currently includes:

- `/dashboard`
- `/strategies`
- `/strategies/new`
- `/strategies/[strategyId]`
- `/backtests`
- `/backtests/[runId]`
- `/stock-baskets`
- `/strategy-allocations`
- `/paper-trading`

## Backend API Modules

The main route groups currently include:

- `/api/strategies`
- `/api/backtests`
- `/api/market-data`
- `/api/stock-baskets`
- `/api/strategy-allocations`
- `/api/paper-accounts`
- `/api/strategy-portfolios`
- `/api/paper-trading`

Health endpoints:

- `/`
- `/healthz`

The repository also includes an API spec file: [apps/openapi.yaml](apps/openapi.yaml)

## Local Development

### Prerequisites

Recommended local dependencies:

- Python 3.12-ish
- Node.js 18+
- PostgreSQL 16

### 1. Create the Python virtual environment

The `Makefile` assumes a root-level `.venv/bin/python`, so local development is easiest if you follow that layout:

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

### 2. Install frontend dependencies

```bash
cd frontend
npm install
```

### 3. Configure environment variables

The project auto-loads the root `.env`.

Typical local variables include:

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
SQLALCHEMY_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
FRONTEND_ORIGIN=http://localhost:3000

MASSIVE_API_KEY=

ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

Notes:

- `DATABASE_URL` / `SQLALCHEMY_DATABASE_URL` are used by the backend
- Alpaca features can use `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
- Paper accounts can also map credentials to custom env var names such as `ALPACA_API_KEY_MAIN`

### 4. Initialize the database

Before the first startup, you can manually run:

```bash
.venv/bin/python backend/utils/create_db.py
```

This script runs all `create_*.sql` files in `backend/utils/` and creates the required schema.

### 5. Start the development environment

Start backend and frontend together:

```bash
make dev
```

Start backend only:

```bash
make dev-backend
```

Start frontend only:

```bash
make dev-frontend
```

Default URLs:

- frontend: `http://localhost:3000`
- backend: `http://localhost:8000`

## Docker

You can also run the full local stack with Docker Compose:

- `frontend`: Next.js, default `http://localhost:3000`
- `backend`: FastAPI, default `http://localhost:8000`
- `db`: PostgreSQL 16, default `localhost:5432`

### 1. Prepare Docker environment variables

```bash
cp .env.docker.example .env.docker
```

It is recommended to keep Docker config separate from the local `.env`.

At minimum, confirm these values exist or use their defaults:

```env
POSTGRES_DB=quant
POSTGRES_USER=quant
POSTGRES_PASSWORD=quantpass
POSTGRES_PORT=5432
FRONTEND_ORIGIN=http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

If you want Massive or Alpaca integration, also set:

```env
MASSIVE_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

### 2. Start

```bash
make docker-up
```

Or directly:

```bash
docker compose --env-file .env.docker up --build -d
```

### 3. View logs

```bash
make docker-logs
```

### 4. Stop

```bash
make docker-down
```

### 5. Docker runtime notes

- The backend container runs `python utils/create_db.py` before starting the app
- `./data` is mounted to `/app/data`
- `./logs` is mounted to `/app/logs`
- If you change `NEXT_PUBLIC_API_BASE_URL`, rebuild the frontend image

## Common Commands

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

## Data Preparation and Backfills

`backend/utils/` contains the project’s data scripts. Common ones include:

- `create_db.py`
  - Initialize the database schema

- `run_daily_market_backfill.py`
  - Main daily market-data catch-up entrypoint
  - Runs missing EOD checks, corporate action sync, adjusted-price refresh, and `daily_features` refresh

- `backfill_missing_eod_from_massive.py`
  - Fill missing daily bars from Massive

- `backfill_adjusted_prices.py`
  - Refresh adjusted OHLC fields

- `backfill_daily_features.py`
  - Recompute and upsert `daily_features` from `eod_bars`

Run the daily backfill flow through Make:

```bash
make backfill-daily
```

Pass extra arguments like this:

```bash
make backfill-daily BACKFILL_ARGS="--start-date 2026-04-01 --end-date 2026-04-10"
```

## Paper Trading and Scheduler

### Paper trading execution modes

The project supports two ways to trigger paper trading:

- Manual
  - `/api/paper-trading/run`
  - `/api/paper-trading/run-multi`

- Scheduled
  - The backend starts the scheduler automatically
  - The scheduler scans active portfolios under active paper accounts
  - It only runs allocations where `auto_run_enabled=true`

### Current scheduler logic

The scheduler currently works like this:

1. Poll the current New York time
2. Find the latest ready trade date that is `<= today`
3. A trade date is ready only if:
   - that date exists in `eod_bars`
   - every `eod_bars` row for that date has a matching `daily_features` row
4. The scheduler only runs after feature coverage is complete
5. It only triggers once `PAPER_TRADING_SCHEDULER_RUN_TIME_NY` has passed
6. The same `portfolio + trade_date + trigger=scheduler` is executed at most once

This is designed to avoid:

- running on partially loaded feature data
- duplicate same-day order submission
- missing the intended trade date when data lands late

### Scheduler environment variables

```env
PAPER_TRADING_SCHEDULER_ENABLED=true
PAPER_TRADING_SCHEDULER_RUN_TIME_NY=23:30
PAPER_TRADING_SCHEDULER_POLL_SECONDS=60
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
PAPER_TRADING_SCHEDULER_CONTINUE_ON_ERROR=true
```

Recommended workflow:

- Start with `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`
- Turn it to `true` only after signals and portfolio allocation look correct

### Alpaca notes

- Automated order submission is aimed at Alpaca paper accounts
- Real paper submissions leave actual paper positions and order state in Alpaca
- If you use smoke tests during integration, clear paper positions and open orders afterward so they do not affect later runs

## Data Model Overview

The main model relationships currently look like this:

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

This separation makes it easier to:

- attach one strategy to multiple portfolios
- manage multiple portfolios under one paper account
- support both backtesting and paper trading in the same system

## Good Files to Read Next

- [backend/src/main.py](backend/src/main.py)
- [backend/src/services/paper_trading_service.py](backend/src/services/paper_trading_service.py)
- [backend/src/services/paper_trading_scheduler.py](backend/src/services/paper_trading_scheduler.py)
- [backend/src/services/strategy_engine.py](backend/src/services/strategy_engine.py)
- [backend/src/services/strategy_registry.py](backend/src/services/strategy_registry.py)
- [frontend/src/pages/paper-trading.tsx](frontend/src/pages/paper-trading.tsx)

## What This README Covers

This README focuses on:

- what the repository can do today
- how the project structure maps to the feature set
- how to start it locally or with Docker
- how data backfills connect to the paper-trading scheduler

If useful, the next step could be a second layer of documentation:

- a teammate onboarding guide
- a deployment / operations guide
