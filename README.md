# Quant Trading System

[中文文档](README.zh-CN.md)

A full-stack quant trading system for equity strategy research and execution, covering strategy definition, feature data preparation, backtesting, paper trading, portfolio allocation, and scheduled Alpaca paper-order execution.

The repository currently has two main parts:

- `backend`: FastAPI + SQLAlchemy + PostgreSQL for strategies, backtests, market data, paper accounts, portfolio allocation, and scheduling
- `frontend`: Next.js UI for strategy management, backtest inspection, basket management, portfolio configuration, and paper trading workflows

## Core Features

- Strategy management
  - Create, inspect, update, and archive strategies
  - Start from a guided `/strategies/new` hub: hand-configure an existing engine strategy in five steps, or hand off to Agent research for an existing category or new algorithm
  - Manual creation validates and normalizes the payload before persistence and always saves a `draft`; it does not activate a portfolio, create an allocation, start scheduling, or submit an order
  - Expose a strategy catalog and normalized runtime payloads
  - Current strategy types include `trend`, `mean_reversion`, `momentum_breakout`, `island_reversal`, `double_bottom`, `support_resistance`, and `custom`
  - Engine-ready execution currently supports `trend`, `mean_reversion`, `momentum_breakout`, `island_reversal`, `double_bottom`, and `support_resistance`
  - `momentum_breakout` uses existing forward-adjusted-when-available daily close, SMA20, 20-day return, and volume features; day-T close signals retain next-session open backtest fills

- Market data and feature engineering
  - Maintain instruments, EOD bars, adjusted prices, and daily features
  - Backfill historical and missing market data from Massive
  - Provide a daily market-data catch-up pipeline

- Backtesting
  - Generate signals from strategy parameters plus `daily_features`
  - Queue manual and research runs in PostgreSQL and execute them with an independent worker
  - Choose `summary`, `trades`, or `full` persistence; manual runs default to `full`
  - Load summary, downsampled equity, signals, and transactions through incremental APIs
  - Keep v1 as the default engine while v2 instrument-identity and batch-persistence rollout is validated

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

- Agent-assisted strategy research
  - Uses AgentOps workflows to propose draft strategies, run bounded research experiments, and prepare Draft PRs for new strategy code
  - Includes engine-ready `support_resistance` research with independent bounce, breakout, and retest mode switches
  - Adds a pre-registered `pivot-atr-v1` effectiveness study with a historical dynamic universe, sealed holdout, 200-backtest ceiling, and bilingual JSON/Markdown/PDF reports
  - Persists experiment specifications, deterministic trial expansions, progress, token usage, termination evidence, and robustness reports
  - Supports automatic stop policies based on elapsed time, workflow token usage, or a target metric
  - Keeps broker, portfolio activation, and order-submission tools outside the Agent service API

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
├── docs/                 # Architecture, research, and integration guides
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
- `/strategies`: category navigation, inventory counts, and type-specific visual accents
- `/strategies/new`
- `/strategies/[strategyId]`
- `/backtests`
- `/backtests/[runId]`
- `/stock-baskets`
- `/strategy-allocations`
- `/paper-trading`
- `/paper-trading/portfolios/[portfolioId]`
- `/research`
- `/research/[experimentId]`
- `/agent-runs/[runId]`

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
- `/api/research`
- `/api/agent`

The `/api/agent/*` routes require a Bearer service token and expose only controlled draft-strategy and research-experiment operations. They do not expose broker orders or portfolio activation.

Backtests are not executed by the Web process. After applying the additive schema, start the dedicated worker separately with `make backtest-worker`. This command explicitly disables paper scheduling and order submission. See [Backtest performance and worker operations](docs/backtest-performance.md).

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

Starting the backend also starts the paper-trading scheduler. For local development, smoke checks, and Agent integration, explicitly disable both scheduling and order submission unless broker-side paper mutations are intended:

```bash
PAPER_TRADING_SCHEDULER_ENABLED=false \
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false \
make dev
```

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
make dev-agent-all
make dev-agent-safe
make dev-backend
make dev-frontend
make backfill-daily
make check-data
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
  - Runs security master → SIC/ticker events → EOD gaps → VWAP → corporate actions → adjusted prices → short interest → `daily_features` → read-only integrity gate

- `backfill_missing_eod_from_massive.py`
  - Fill missing daily bars from Massive

- `backfill_vwap_from_massive.py`
  - Fill only null, unadjusted `eod_bars.vwap` values with point-in-time symbol mapping; OHLCV is never overwritten

- `backfill_sic_from_massive.py`
  - Store the current (or final delisted-date) SIC snapshot and namespaced Massive ticker-overview payload

- `backfill_short_interest_from_massive.py`
  - Store Massive/FINRA biweekly settlement facts without forward-filling them into daily features

- `backfill_ticker_events_from_massive.py`
  - Store experimental ticker-change events and apply only fully validated, conflict-free symbol intervals

- `backfill_adjusted_prices.py`
  - Refresh adjusted OHLC fields

- `backfill_daily_features.py`
  - Recompute and upsert `daily_features` from `eod_bars`

- `check_market_data_quality.py`
  - Read-only checks for price/feature gaps, invalid VWAP/short-interest values, ticker-event consistency, duplicate identities, symbol-history overlaps, stale instruments, and partial latest sessions

Apply `backend/utils/create_stock_enrichment.sql` before the first enrichment run. It is additive and idempotent, but this repository has no Alembic migration workflow; back up and verify the target database before applying it. The schema adds SIC snapshot columns, `stock_short_interest`, `security_ticker_events`, and per-instrument vendor sync state.

Massive VWAP is stored unadjusted (`adjusted=false`). The current plan boundary begins on 2016-08-29; older null VWAP remains an expected warning. SIC is a snapshot, not point-in-time industry history. Short interest is keyed by settlement date and is not treated as known on every daily bar because the endpoint does not provide a reliable publication timestamp. Ticker Events is experimental: raw events are always auditable, while incomplete chains, FIGI/exchange mismatches, ticker reuse, and interval conflicts remain `unresolved` and never trigger a guessed repair.

Run the daily backfill flow through Make:

```bash
make backfill-daily
```

Pass extra arguments like this:

```bash
make backfill-daily BACKFILL_ARGS="--start-date 2026-04-01 --end-date 2026-04-10"
```

Preview the resolved range and vendor coverage without writing to the database:

```bash
make backfill-daily BACKFILL_ARGS="--dry-run"
```

Refresh all SIC and ticker-event references, or selectively skip datasets:

```bash
make backfill-daily BACKFILL_ARGS="--full-reference-refresh --dry-run"
make backfill-daily BACKFILL_ARGS="--skip-sic --skip-ticker-events --skip-vwap --skip-short-interest"
```

The dry run fetches provider coverage for the selected enrichment datasets but does not write facts or identity intervals; security-master sync is skipped because its standalone script has no dry-run mode. All writes are idempotent. Recovery after a failure is to fix the reported cause and rerun the same range—do not delete or rebuild history. Ticker-event repairs retain before/after interval snapshots in `security_ticker_events`.

Run the integrity gate directly. Critical failures always return a non-zero exit status; warnings are informational unless `--strict` is used:

```bash
make check-data
make check-data CHECK_DATA_ARGS="--strict --json"
```

For an exceptional maintenance run, `--skip-quality-check` omits the final gate; `--strict-quality-check` makes pipeline warnings blocking. The normal installed task uses the default critical-failure-only policy.

The installed macOS LaunchAgent runs daily at 20:15 local time and writes to `logs/daily-market-backfill.log` and `logs/daily-market-backfill.err.log`. Inspect its status with `launchctl print "gui/$(id -u)/com.quant.daily-market-backfill"`. Its schedule and installed paths are unchanged by the backfill scripts. Every child maintenance process explicitly receives `PAPER_TRADING_SCHEDULER_ENABLED=false` and `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`. A failed security-master or quality-gate step stops the remaining pipeline and leaves the idempotent catch-up range available for the next run.

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
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=true
PAPER_TRADING_SCHEDULER_CONTINUE_ON_ERROR=true
```

Recommended workflow:

- Start with `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`
- Turn it to `true` only after signals and portfolio allocation look correct

### Alpaca notes

- Automated order submission is aimed at Alpaca paper accounts
- Real paper submissions leave actual paper positions and order state in Alpaca
- If you use smoke tests during integration, clear paper positions and open orders afterward so they do not affect later runs
- The application defaults both scheduler enablement and scheduler order submission to `true`; override both to `false` whenever order submission is not explicitly intended

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

## Agent Research Workspace

`/strategies/new` first separates manual creation from Agent-assisted research. The manual path uses catalog defaults, human-readable percentage inputs, core/advanced parameter sections, and the read-only validation API before saving a Draft. The Agent path links to `/research?mode=category|algorithm&source=strategy-create`, where the requested mode is preselected and the user can return to the creation hub.

`/research` has two entry points. Existing engine category research lets the user choose a catalog handler, then creates a validated draft and runs up to 5 adaptive Pareto rounds / 100 actual backtests after experiment approval. New algorithm research ends at a Draft PR; it does not merge, deploy, or backtest the new handler. Historical finite-grid experiments remain readable, but the old create flow is retired. See [Research experiments](docs/research-experiments.md).

## Documentation

Start with the [documentation index](docs/README.md). The maintained guides cover:

- [System architecture](docs/architecture.md)
- [Research experiments](docs/research-experiments.md)
- [Support/resistance effectiveness study](docs/support-resistance-effectiveness.md)
- [Support and resistance strategy](docs/support-resistance-strategy.md)
- [Quant and AgentOps local integration](docs/agent-research-integration.md)

The dated delivery and validation reports under `docs/` are historical evidence. They are intentionally not part of the maintained documentation navigation and must not be used as substitutes for the guides above.
