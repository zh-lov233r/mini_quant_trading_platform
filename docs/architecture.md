# System Architecture

[中文](architecture.zh-CN.md) | [Documentation index](README.md)

## System Boundaries

The system has a FastAPI backend, a Next.js Pages Router frontend, and PostgreSQL persistence. Massive supplies market data. Alpaca is used only for paper-trading accounts and paper orders. The optional AgentOps integration orchestrates draft strategies, bounded research experiments, and Draft-PR-only strategy-code delivery.

```text
Massive -> EOD bars -> adjusted prices -> daily features
                                      -> strategy signals
                                      -> backtest runs and reports
                                      -> paper portfolios -> Alpaca paper orders

AgentOps -> authenticated /api/agent tools -> draft strategies / experiments
        <- read-only research results and bounded execution evidence
```

The backend owns domain behavior and persistence. FastAPI routes stay thin. Frontend HTTP access lives under `frontend/src/api/`, while shared request and response shapes live under `frontend/src/types/`.

## Market Data and Feature Flow

1. Instruments identify securities independently of a display symbol.
2. `eod_bars` stores daily unadjusted market observations.
3. Corporate actions feed adjusted-price calculation.
4. `adjusted_prices` stores deliberately adjusted OHLC values.
5. `daily_features` stores the features consumed by strategy rules.

The daily catch-up pipeline checks missing EOD rows, synchronizes corporate actions, refreshes adjusted prices, and then refreshes features. The scheduler completeness gate requires every target-date EOD row to have a matching daily-feature row before any portfolio execution can start.

Adjusted and unadjusted prices are not interchangeable. A price-source change must be traced through signals, fills, positions, P&L, and benchmark curves.

## Strategy and Backtest Timing

Reusable strategy definitions and parameter normalization live in the strategy registry. Signal generation belongs in the strategy engine so backtests and paper trading share the same rules.

The daily timing model is:

```text
day T data closes -> day T signal is generated -> next valid session (T+1) opens -> fill at that session's open
```

Here `T+1` means the next valid market session, not the next calendar day. The fill reference is that session's opening price; it is never the T+1 close.

A signal must never use data that was unavailable at its signal timestamp. Runs, dates, symbols, signals, and fills must remain deterministically ordered. Costs, slippage, commissions, missing sessions, corporate actions, inactive instruments, and partial or fractional fills are part of execution correctness.

Backtests persist `StrategyRun`, `Signal`, `Transaction`, and `PortfolioSnapshot` evidence. Research experiments create ordinary backtest runs for their trials rather than maintaining a second execution engine.

The `support_resistance` handler adds a causal stateful detector shared with paper signals. It evaluates T against T-1-frozen confirmed-Pivot/ATR zones. Sparse shared materializations store only zone membership, role, or status changes; run links and events remain separate so deleting a run does not delete cache evidence used by another run. Source revision fingerprints prevent adjusted-price or daily-feature corrections from silently reusing stale zones.

## Paper Trading and Scheduler

A `PaperTradingAccount` can own multiple `StrategyPortfolio` records. Each portfolio has one or more `StrategyAllocation` records that refer to strategies and capital settings.

The scheduler:

1. Uses `America/New_York` to resolve the intended US trade date.
2. Waits for the daily-feature completeness gate.
3. Waits until `PAPER_TRADING_SCHEDULER_RUN_TIME_NY`.
4. Selects active portfolios and allocations with `auto_run_enabled=true`.
5. Preserves idempotency for `portfolio + trade_date + trigger` and broker client-order IDs.

Alpaca paper orders still mutate remote order, position, and buying-power state. Local development and Agent integration must set both scheduler enablement and order submission to `false` unless that external action is explicitly intended.

## Research and AgentOps Integration

Quant remains the source of truth for strategies, market data, trials, and backtest results. AgentOps owns workflow definitions, approvals, structured agents, token accounting, external-tool persistence, and delivery evidence.

The authenticated `/api/agent/*` surface is deliberately narrow. It validates or creates draft strategies and research experiments, accepts cancellation and token-usage updates, and never exposes broker orders or portfolio activation. The public `/api/research/*` surface supports the Quant UI and result inspection.

Engine-category research persists `ResearchExperiment -> ExperimentRound -> ExperimentCandidate -> ExperimentTrial -> StrategyRun`. Candidate parameters are normalized and globally deduplicated before deterministic expansion; Pareto ranking uses locked objective directions and stable parameter-hash tie-breaking. Between rounds Quant enters `waiting_agent`, while restart-safe AgentOps proposes only from bounded aggregate evidence. Universe membership and market inputs are fingerprinted so drift becomes `data_changed`. Historical finite-grid rows remain readable. See [Research experiments](research-experiments.md).

Pre-registered support/resistance effectiveness studies add a self-referencing parent experiment above that chain. The parent owns the protocol hash, immutable data fingerprint, child phase IDs, frozen champion, holdout gate, final decision, and artifact manifest. Child experiments retain the ordinary PostgreSQL trial queue. The read path exposes children and generated documents, while report retry remains service-authenticated and never grants portfolio or broker permissions. See [Support/resistance effectiveness study](support-resistance-effectiveness.md).

## Contract and Schema Changes

API work must follow the complete path:

```text
database/model -> service -> FastAPI schema/route -> apps/openapi.yaml
-> frontend type -> frontend API client -> page/component
```

This repository has no Alembic workflow. Schema changes must keep ORM models and creation SQL aligned and must document target database, compatibility, rollout, backup, and recovery risks before an apply operation.
