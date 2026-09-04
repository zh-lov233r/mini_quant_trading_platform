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
  - Create from any strategy card or detail page: the wizard preloads the source and locks its type, then saves a uniquely named Draft with an independent `strategy_key` starting at `v1`; backtests, allocations, run history, and positions are not copied
  - Expose a strategy catalog and normalized runtime payloads; the shared C++ descriptor registry is the single source for defaults, JSON Schema, required features, history windows, validation, and algorithm revisions
  - Current strategy types include `trend`, `mean_reversion`, `momentum_breakout`, `island_reversal`, `double_bottom`, `head_shoulders_bottom`, `rounded_bottom`, `v_reversal`, `support_resistance`, and `custom`
  - The five bottom-reversal categories use cumulative 20% / 50% / 100% staged entries; see [Bottom-reversal strategies](docs/bottom-reversal-strategies.md)
  - Engine-ready execution currently supports `trend`, `mean_reversion`, `momentum_breakout`, `island_reversal`, `double_bottom`, `head_shoulders_bottom`, `rounded_bottom`, `v_reversal`, and `support_resistance`
  - All nine engine-ready strategies execute only through the shared C++ kernel; `custom` remains stored-only and is not an executable DSL
  - `momentum_breakout` uses existing forward-adjusted-when-available daily close, SMA20, 20-day return, and volume features; day-T close signals fill at the next valid session (T+1) open

- Market data and feature engineering
  - Maintain instruments, EOD bars, adjusted prices, and daily features
  - Backfill historical and missing market data from Massive
  - Idempotently import Shanghai, Shenzhen, and Beijing A-share daily bars, adjustment factors, broad-market indices, and backtest features from Tushare into PostgreSQL; see [Tushare A-share data](docs/tushare-a-share-data.md)
  - Run the daily catch-up inside an exclusive maintenance window that drains backtests/research, invalidates derived caches before writes, and stays blocked after failure

- Backtesting
  - Generate signals from strategy parameters plus `daily_features`
  - Queue manual and research runs in PostgreSQL and execute them with an independent worker
  - Choose `summary`, `trades`, or `full` persistence; manual runs default to `full`
  - Plan the read-only correctness/screening funnel with `make benchmark-backtests BENCHMARK_ARGS="plan"`; write benchmarks require explicit `--apply` and the safety gates in the performance guide
  - Resolve stable instrument identity and reuse a structurally keyed, read-only v4 columnar PreparedDataset for manual, research, and verification runs; warm hits open directly and corrupt caches rebuild atomically
  - Load summary, downsampled equity, signals, and transactions through incremental APIs
  - Execute every engine-ready run with the in-process C++20 kernel and persist typed results with transaction-scoped psycopg3 `COPY`; Python retains queueing, database, progress/cancellation, and result orchestration
  - Rank same-strategy BUY signals by a frozen day-T strength score before next-valid-session (T+1) open fills; see [Signal strength](docs/signal-strength.md)

- Paper trading
  - Support multiple Alpaca paper accounts
  - Support multiple strategy portfolios under one paper account
  - Support strategy allocation, capital base, fractional trading, and auto-run flags
  - Support single-strategy and multi-strategy paper trading
  - Convert Paper history into an in-memory PreparedDataset v3 and call native `evaluate_day(dataset_day, strategy, portfolio_state)`, sharing the same rules and canonical metadata as backtests; broker queries, idempotent orders, sleeve isolation, and next-valid-session-open quote validation remain in Python
  - Support real paper-order submission to Alpaca

- Daily scheduler
  - Automatically starts the paper-trading scheduler when the backend boots
  - Runs only after `daily_features` are fully materialized for the target trade date
  - Runs only active allocations with `auto_run_enabled=true`
  - Can run in dry-run mode or submit real Alpaca paper orders

- Agent-assisted strategy research
  - Uses AgentOps workflows to propose draft strategies, run bounded research experiments, and prepare Draft PRs for native C++ strategy modules, descriptors, golden differentials, and wheel validation
  - Includes engine-ready `support_resistance` research with frozen support-bounce zones, channel/risk filters, and qualifying repeated entries after exit in the same zone
  - Separates immutable stock phases from four-regime classification; close breaks or structural conflicts reset the whole phase, and independent effectiveness studies use detector revision 13
  - Persists experiment specifications, deterministic trial expansions, progress, token usage, termination evidence, and robustness reports
  - Supports automatic stop policies based on elapsed time, workflow token usage, or a target metric
  - Keeps broker, portfolio activation, and order-submission tools outside the Agent service API

## Tech Stack

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
  - Tushare A-share daily bars, adjustment factors, Shanghai Composite, and Shenzhen Component

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
- `/backtest-tasks`
- `/backtests/[runId]`
- `/stock-baskets`
- `/strategy-allocations`
- `/paper-trading`
- `/paper-trading/portfolios/[portfolioId]`
- `/research`
- `/research/[experimentId]`
- `/agent-runs/[runId]`

The 14 active workbench pages use a wide-screen, compact-density shell. Primary navigation lives in a collapsible left sidebar, while the remaining width always belongs to the main workspace; there is no fixed right context rail. Page-specific configuration, creation, identity, and risk details open through clearly labeled, keyboard-accessible dialogs, which become full-screen below 768px. Important progress, validation, broker warnings, and engine-ready status remain visible in the main page. All short enum and pagination selectors use one dark, cyan-accented Radix option panel instead of an operating-system menu, with consistent keyboard, hover, focus, invalid, disabled, and mobile states. Strategy, basket, and portfolio entity selectors in Backtests and Paper Trading remain searchable and keyboard navigable while retaining the existing request values. The dashboard omits the duplicated risk/action and daily TODO cards; new backtests start from the upper-right workbench action, while strategy-library and strategy-detail backtest links open the same dialog with the current engine-ready strategy preselected. The result list supports strategy or basket search plus strategy-category and run-status filters. It shows 10 runs per page by default with selectable page sizes and centered previous/next controls, and each result card reuses its strategy-library category color and label. Terminal manual runs can be deleted individually after confirmation; the dialog closes immediately, deletion continues in the background, and a viewport-fixed centered notification appears only after success or failure before fading out automatically. Queued and running runs remain protected, while research and verification runs are managed only from their owning experiment. Strategy creation uses those same colors for category cards and selected states. Backtest detail loads market-appropriate comparison curves independently from the compact summary and equity payloads: A-share runs use Shanghai Composite and Shenzhen Component, while other runs use SPY and QQQ. It removes the raw summary-metric list and limits latest positions to quantity, average cost, closing price, and market value. Dense tables support sorting, filtering, column visibility, resizing, and explicit client/server pagination; they preserve semantic tables for smaller results and virtualize only result sets of 200 rows or more. Detail-page columns collapse to a single column on narrow screens, and compact metric cards stack when their container becomes too narrow so labels, monetary values, and technical fields remain fully readable. Development and production builds use separate Next.js output directories so a verification build cannot invalidate the active development server.

Position lifecycles use New York trading dates. Open rows distinguish period-end valuation from actual sell fills. SR audit data loads directly for the visible candle window, independently of initial signal pagination; identical completed requests are reused and candles remain interactive while loading. The lifecycle chart does not draw regime backgrounds or entry channels.

Backtest detail uses three sibling sections: Backtest Overview, Equity Curve, and Backtest Review Workbench. Each section has a single outer surface; the four primary metrics share one row, while metadata, curve statistics, lifecycle details, and ending positions use spacing and separators instead of nested cards. Latest Snapshot remains collapsed initially. The overview stays readable while the equity curve loads, fails, or has too few points. Metrics wrap on phones, and wide tables scroll within their own area.

Backtest detail groups per-symbol PnL, signal-strength ranking, lifecycles, transactions, and latest positions into one review workbench with tab navigation and opens on Lifecycles first. Its content area scrolls independently while preserving each module's existing filters, sorting, pagination, and expansion controls; only clicking the lifecycle summary row (or pressing Enter / Space while it is focused) toggles expansion. Detail text, chart interactions, whitespace and dragging never collapse it. Changing the pre-entry or post-exit display range keeps the existing candlestick chart and workbench scroll position in place until the refreshed data replaces it. Lifecycle charts place every colored event dot and buy/sell arrow in the price pane gutters, with dashed leaders to the corresponding candles so neither candles nor volume bars are obscured; adjacent labels automatically switch sides or lanes to avoid overlap.

SR lifecycle charts default to zones explicitly linked to the trade: its frozen entry zone and channel boundaries, plus matched exit evidence (exit-regime boundaries only for downtrend exits). “Show all zones” reveals the current window's remaining zones without guessing missing associations. Faint dashed segments are explicitly retrospective fits from the earliest member Pivot to confirmation; solid segments are immutable active geometry and stop at phase end. Geometry is clipped to the chosen window and never stitched across phases. Hover highlights one zone and its fixed Pivots/recorded touches; click pins details, Escape or blank space clears them, and the zone selector supports keyboard/touch and ambiguous-edge switching. Off-window Pivots are listed by date only. Hover neither fetches data nor recreates the chart or resets zoom. See [SR strategy and safe cleanup](docs/support-resistance-strategy.md) for timing, repeated trading, cache revisions, and exact-ID backup/restore requirements.

Backtest deletion uses the same accessible workspace dialog as the rest of the platform, with an explicit retained-data warning and separate cancel/danger actions instead of the browser's native confirmation box.

Workspace motion uses the existing CSS Modules and Radix components, with 120ms interaction feedback, 180ms surface transitions, and 240ms content entrances. Clickable strategy and backtest cards lift slightly on mouse hover or keyboard focus and highlight in their strategy category color. The four overview KPI cards fade in at 30ms intervals, while skeletons gently pulse only during loading. Polling, search, and pagination do not replay whole-page entrances; financial values appear directly, and charts and virtual table rows receive no entrance transforms. Progress bars interpolate only actual server percentages and settle immediately in terminal states; centered action notices are still removed after 3200ms. Dialogs restore focus and page scrolling after their exit animation, retaining full-screen layouts on phones. The system's Reduce Motion preference disables motion and smooth back-to-top scrolling while keeping all controls and status information available. This is a frontend presentation change with no API, database, or broker-operation changes.

Saved baskets support editing names, descriptions, symbols and status (`PUT /api/stock-baskets/{basket_id}`) while preserving identity and historical backtest snapshots. The Edit button is at each basket card’s bottom-left. Creation and editing use short ticker/company search, single-ticker addition, market/industry/market-cap screening and a 20-row selected list; the complete ticker textarea no longer exists. Bulk matches require explicit confirmation and append with deduplication. Closing without saving discards the draft; description input does not rerender the memoized selector. The automatically maintained `All Common Stock` basket is read-only; create a separate basket to customize it. See [Stock basket screening](docs/stock-basket-screening.md) for data units, manual refresh and approved schema rollout. Strategy creation and cloning configure trading logic only and do not select a stock scope. Every new manual backtest must choose a saved basket; the selected membership is copied into the run snapshot. The backtest workbench filters results and basket choices by A-share / US market; result market is resolved from the run snapshot, not the current basket name. Task-center total duration runs from execution start through persistence completion, excluding queue wait; missing timestamps display a dash rather than an estimate. The market viewer entry sits at the sidebar bottom (icon-only when collapsed, bottom-left on mobile), with its panel opening from the workspace left edge.

Percentage parameters display and accept percentages (enter 10 for 10%, 0.5 for 0.5%) while persistence retains fractional ratios. Return metrics retain necessary decimal precision; ATR, volume multiples, bps and raw JSON remain unconverted. Overview strategy evidence/backtest activity and research category choices reuse strategy-library colors. SR Pivot annotations appear only for the hovered or pinned zone and represent retrospective fitting evidence, not signals available on the extremum date.

The display changes do not alter execution timing or broker state. Stock screening additionally requires the explicitly approved `instrument_market_caps` table; other display changes require no schema update. There is no Alembic workflow and no database reset is performed.

## Backend API Modules

The main route groups currently include:

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

The `/api/agent/*` routes require a Bearer service token and expose only controlled draft-strategy and research-experiment operations. They do not expose broker orders or portfolio activation.

Backtests are not executed by the Web process. Full-platform commands supervise a lightweight manager and restart it two seconds after an unexpected exit; the manager starts a spawn-based process worker only while durable queued jobs exist. `BACKTEST_WORKER_CONCURRENCY` defaults to `2` (`1|2`), while `BACKTEST_INTRA_RUN_THREADS` defaults to `4` (`1..16`) and is CPU-capped; trading days remain serial, with same-day instruments evaluated by a reusable native thread pool only above the documented threshold. Both settings require a backend/manager restart after changes. `GET /api/backtests/worker-status` reports automation health, process capacity, and configured/effective per-run threads, while the task center displays “processes × threads/run.” The list page and task center show structured phase, percentage, and finalization item progress; backtest details omit the top execution-progress panel, and Latest Snapshot within Backtest Overview starts collapsed and can be expanded by clicking its heading or using the keyboard. The `/backtest-tasks` workbench combines manual runs, research trials, and verification jobs without replacing either queue; its paired research/backtest health cards distinguish work that has not entered the durable queue from work paused after enqueue. Failed manual and verification runs can be retried there as new queued runs while retaining the failed record. Terminal manual runs can also be deleted individually; research and verification evidence remains experiment-owned and can only be deleted from the experiment page. See [Backtest performance and worker operations](docs/backtest-performance.md).

Health endpoints:

- `/`
- `/healthz`
- `/readyz` (full-platform readiness; requires a live backtest manager leader)

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
.venv/bin/pip install -e backend/native
```

The second command builds the local C++20/pybind11 strategy-kernel wheel. Docker builds the same wheel in a compiler-only builder stage; the runtime image contains no compiler toolchain.

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
TUSHARE_TOKEN=

ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

Notes:

- `DATABASE_URL` / `SQLALCHEMY_DATABASE_URL` are used by the backend
- `TUSHARE_TOKEN` is read only by the A-share import command and must never be committed
- Alpaca features can use `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
- Paper accounts can also map credentials to custom env var names such as `ALPACA_API_KEY_MAIN`

### 4. Initialize the database

Before the first startup, you can manually run:

```bash
.venv/bin/python backend/utils/create_db.py
```

This script runs all `create_*.sql` files in `backend/utils/` and creates the required schema.

### 5. Start the development environment

`make dev` starts backend, frontend, and the on-demand backtest manager. The full-platform target forces both paper scheduling and order submission off:

```bash
make dev
```

Start the complete local platform:

```bash
make dev
```

Start backend only (partial stack; no automatic queue consumption, and set paper safety variables explicitly if needed):

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
- `backtest-worker-manager`: lightweight queue manager; worker child exists only while jobs are eligible
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
- The frontend waits for a healthy manager leader; an idle manager is healthy without a worker child
- The backend and manager force paper scheduling and order submission off
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
make backtest-worker-manager
make backfill-daily
make import-a-share A_SHARE_ARGS="plan --start-date 2024-01-01 --end-date 2024-12-31"
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

- `import_tushare_a_share.py`
  - Import Shanghai, Shenzhen, and Beijing A-share identities, unadjusted daily bars, forward/backward-adjusted OHLC, broad-market indices, and `daily_features`
  - Synchronize the `All A Shares (Tushare)` basket for all nine engine-ready strategies in the Backtests workbench
  - A-share results compare against Shanghai Composite and Shenzhen Component instead of SPY and QQQ

- `check_market_data_quality.py`
  - Read-only checks for price/feature gaps, invalid VWAP/short-interest values, ticker-event consistency, duplicate identities, symbol-history overlaps, stale instruments, and partial latest sessions

Apply `backend/utils/create_stock_enrichment.sql` before the first enrichment run. It is additive and idempotent, but this repository has no Alembic migration workflow; back up and verify the target database before applying it. The schema adds SIC snapshot columns, `stock_short_interest`, `security_ticker_events`, and per-instrument vendor sync state.

Massive VWAP is stored unadjusted (`adjusted=false`). The current plan boundary begins on 2016-08-29; older null VWAP remains an expected warning. SIC is a snapshot, not point-in-time industry history. Short interest is keyed by settlement date and is not treated as known on every daily bar because the endpoint does not provide a reliable publication timestamp. Ticker Events is experimental: raw events are always auditable, while incomplete chains, FIGI/exchange mismatches, ticker reuse, and interval conflicts remain `unresolved` and never trigger a guessed repair.

The Tushare A-share flow requires a `plan` run before an explicit `apply`. Apply runs use the same exclusive maintenance window as the daily Massive pipeline; they do not start the backend, paper scheduler, or any broker operation. See [Tushare A-share data](docs/tushare-a-share-data.md) for commands, field semantics, recovery, and backtest limitations.

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

The installed macOS LaunchAgent runs daily at 20:15 local time and writes to `logs/daily-market-backfill.log` and `logs/daily-market-backfill.err.log`. Inspect its status with `launchctl print "gui/$(id -u)/com.quant.daily-market-backfill"`. Its schedule and installed paths are unchanged by the backfill scripts. A write run enters the singleton `draining` state, rejects new backtest/research work, waits for existing work, takes the exclusive database advisory lock, invalidates derived caches, and then updates source tables. Every child receives the same maintenance-owner token and both Paper scheduler controls remain explicitly disabled. A failed pipeline or quality gate leaves the state `failed` and blocks strategy work until a later successful rerun.

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

## Dashboard: research and operations

`/dashboard` loads a local read-only summary through one `GET /api/dashboard/overview` request. It refreshes every 30 seconds while visible and supports manual refresh. A failed refresh preserves the last successful data and timestamp; changing language does not refetch business data. The endpoint never initializes missing records, creates tasks, runs schedulers, or contacts brokers.

- System observations distinguish healthy, degraded, failed, unknown, and disabled. Maintenance `ready` only means the execution gate is open; it does not establish latest-session data completeness. Broker connectivity is always unchecked. Scheduler status observes the loop in the current Web process, separately from historical strategy execution results.
- Running/queued backtests come from the durable queue. Awaiting research dispatch counts only queued trials with no linked backtest run. Counts are not limited by list pagination or the latest 50 runs. Alerts group related problems, sort by severity, and link to existing workspaces for all actions.
- Evidence covers the 10 most recently updated strategies and selects the latest completed manual backtest with the same strategy ID and version, excluding research and verification parameter overrides. Since configuration can change in place, rules, risk, and execution parameters are compared as well. Metadata is excluded; each run resolves its own universe, so consult its details for universe and window. Mismatched configurations hide metrics and show a change label. Return, Sharpe, drawdown, and trade count reuse existing metrics without recalculation or rankings across different scopes.
- Experiments, evaluated candidates (with a Pareto rank), candidates with completed full verification, promoted strategies, and strategies configured for Paper are counted separately. Units differ and may overlap. Completed verification and historical promotion lineage are not general effectiveness or profitability conclusions.
- Paper covers active paper accounts and active portfolios, showing up to 10 portfolios. Auto-run eligibility follows scheduler selection: active account, portfolio, strategy and allocation, with allocation auto-run enabled; non-executable configurations are alerted separately. Latest strategy runs distinguish dry-run from order-submission mode and do not represent whole-portfolio results. Real-time orders, account equity, daily PnL and equity charts are deferred, starting with a separate single-account integration.
- Recent activity merges bounded sources, returning up to 20 events and displaying 10; a run is never duplicated as a job. Empty counts are `0`, unavailable values are `null` and display as `—`. Alerts and activity use stable codes with localized presentation.

The backend uses a fixed number of batch and window queries, without per-account overview calls or transaction, signal, or equity details. Current strategy parameters are still read and validated for engine readiness, so their cost grows with strategy count; fixed query counts do not imply constant latency. Database/program errors fail the request; malformed JSON affects only the associated evidence. No database schema, trading, or backtest semantics change, and no migration is required. Roll back through code versions rather than a parallel Dashboard implementation.

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
