# Research Experiments

[中文](research-experiments.zh-CN.md) | [Documentation index](README.md)

Category research starts from an existing engine handler, not an existing Strategy row. AgentOps generates a concrete engine-ready draft and a bounded adaptive Pareto plan; Quant owns deterministic validation, trials, fingerprints, ranking, lineage, and reports. Historical finite-grid experiments remain readable but can no longer be created.

`support_resistance` participates as an existing engine category. Its three boolean mode switches and numeric `signal.*` / `risk.*` leaves are scalar search paths; validation rejects a candidate that disables all three modes. Every trial uses the same T-1-frozen zone detector and versioned cache semantics described in the [strategy guide](support-resistance-strategy.md).

## User and API Flow

The Quant UI uses:

- `/strategies/new` as the creation hub. Its Agent-assisted path links to `/research?mode=category|algorithm&source=strategy-create`; the query selects the research mode and preserves a return path without changing workflow inputs.
- `/research` to list experiments and start the AgentOps research workflow.
- `/research/[experimentId]` to inspect progress, trials, stop evidence, and reports.
- `/agent-runs/[runId]` to inspect the corresponding AgentOps workflow run.

Read-only Quant routes are under `/api/research/experiments`. The service-authenticated Agent routes are:

- `POST /api/agent/research/category-studies/validate`
- `POST /api/agent/research/category-studies`
- `POST /api/agent/research/experiments/{experimentId}/rounds`
- `POST /api/agent/research/experiments/{experimentId}/controller-failure`
- `POST /api/agent/research/experiments/{experimentId}/candidates/promote`
- `POST /api/agent/research/experiments/{experimentId}/cancel`
- `POST /api/agent/research/experiments/{experimentId}/usage`

The Agent routes require `Authorization: Bearer <QUANT_AGENT_SERVICE_TOKEN>`. Never place the token in workflow inputs, artifacts, URLs, or logs.

The manual path in `/strategies/new` is separate from Agent research. It validates through `POST /api/strategies/validate`, then persists only a `draft`. Neither path exposes portfolio activation, allocations, scheduling, or broker-order controls.

## Adaptive Experiment Specification

The user selects an engine category such as `trend` or `mean_reversion`. The planner cannot change that type. Quant merges catalog defaults with scalar `signal.*` and `risk.*` overrides, rejects `execution.*` and `universe.*`, verifies feature support, and immediately creates a visible `draft`. Experiment rejection or cancellation before experiment creation archives an unreferenced auto-draft; once an experiment exists, the draft is retained as lineage.

The adaptive specification selects exactly one universe source, locks 2–4 Pareto objectives, and supplies 1–5 first-round candidates. Defaults are 3 rounds and 48 actual backtests; hard limits are 5 rounds and 100 actual backtests. Trial accounting includes candidate × in/out-of-sample × cost scenario.

Example:

```json
{
  "name": "Momentum robustness check",
  "hypothesis": "The signal remains positive after costs out of sample.",
  "strategyType": "momentum_breakout",
  "symbols": ["AAPL", "MSFT"],
  "inSample": {"startDate": "2024-01-02", "endDate": "2024-12-31"},
  "outOfSample": {"startDate": "2025-01-02", "endDate": "2025-12-31"},
  "searchPolicy": {
    "maxRounds": 3,
    "maxTrials": 48,
    "objectives": [
      {"metric": "oos_total_return", "direction": "maximize"},
      {"metric": "oos_max_drawdown", "direction": "minimize"}
    ]
  },
  "initialCandidates": [
    {"overrides": {"risk.position_size_pct": 0.08}, "rationale": "Lower concentration"}
  ],
  "costScenarios": [
    {"name": "base", "commissionBps": 1, "commissionMin": 0, "slippageBps": 2},
    {"name": "stress", "commissionBps": 3, "commissionMin": 0, "slippageBps": 8}
  ],
  "initialCash": 100000,
  "benchmarkSymbol": "SPY",
  "stopPolicy": {"maxDurationSeconds": 3600, "tokenBudget": 200000}
}
```

After each round, Quant aggregates only bounded metrics and performs deterministic non-dominated sorting. Missing objective values stay in evidence but do not enter the frontier; ties use the parameter hash. AgentOps receives aggregate metrics, frontier, failures, and remaining budget—not equity curves or trades—and may submit another distinct round.

## Trial Lifecycle and Reproducibility

Each combination of sample kind, cost scenario, and parameter values produces a stable trial key. The worker claims queued trials with PostgreSQL locking, creates or reuses the associated `StrategyRun`, runs the normal backtest engine, and stores metrics and the backtest run ID.

Before a trial runs, Quant recomputes the fingerprint over the relevant features, adjusted and unadjusted prices, and corporate actions. A mismatch stops reproducible execution with experiment status `data_changed`. The report must not silently combine results produced from different data snapshots.

Experiment states also include `waiting_agent` between rounds. Stop reasons include round/trial/token/time/target limits, no valid or novel candidates, data drift, cancellation, and controller failure. Trial failures remain evidence.

Final approval lists Pareto ranks 1–2. The user may save up to five candidates as ordinary visible drafts or approve an empty selection. Promotion is idempotent and records Experiment, Candidate, WorkflowRun, and Backtest lineage; it never activates or allocates a strategy.

## Automatic Stop Policies

AgentOps Quant research workflows require `stopPolicy`. Direct Quant API callers may omit it, but automated experiments should always be bounded. A policy must contain at least one of:

- `maxDurationSeconds`: integer from 60 through 604800.
- `tokenBudget`: integer from 1000 through 10000000, evaluated against the AgentOps workflow's cumulative `totalTokens`.
- `targetMetric`: a condition over `total_return`, `sharpe`, `max_drawdown`, or `excess_return`, using `gte` or `lte`, an in-sample or out-of-sample result, and a named cost scenario.

The conditions use OR semantics: any matching condition stops further trial intake. When several conditions are observed together, all appear in `runManifest.termination.triggeredConditions`; the primary reason follows deterministic priority `target_reached`, `token_budget_reached`, then `time_limit_reached`.

On a policy stop, Quant:

1. Writes `earlyStopped`, the primary reason, all triggered conditions, and `stoppedAt` into termination evidence.
2. Cancels queued trials with `errorCode=policy_stopped`.
3. Stops claiming new work.
4. Allows already-running synchronous backtests to reach their safe boundary and then finalizes progress and the report.

An early-stopped report is bounded research evidence, not proof of strategy quality or profitability.

## Token Usage

AgentOps accumulates input, cached-input, output, reasoning-output, and total tokens on the `WorkflowRun`. While waiting for Quant, it sends cumulative totals to `/api/agent/research/experiments/{experimentId}/usage` with the owning `workflowRunId`.

Quant rejects usage updates from a different workflow run, stores the latest totals in the run manifest, and immediately re-evaluates `tokenBudget`. Token budgets cover AgentOps model usage; they are not estimates of CPU time, database cost, market-data cost, or broker usage.

## Cancellation, Restart, and Reports

User cancellation first enters `cancel_requested`, prevents new trial claims, and lets already-running synchronous work finish safely before reaching `cancelled`. AgentOps cancellation propagates to a created or awaited Quant experiment.

On worker restart, orphaned trials are recovered without duplicating backtest evidence. Policy-stopped or cancelled experiments do not resume queued work. AgentOps persists external tool runs and resumes `waiting_external` polling after a Control Plane restart.

Use these read endpoints for inspection:

- `GET /api/research/experiments`
- `GET /api/research/experiments/{experimentId}`
- `GET /api/research/experiments/{experimentId}/trials`
- `GET /api/research/experiments/{experimentId}/rounds`
- `GET /api/research/experiments/{experimentId}/candidates`
- `GET /api/research/experiments/{experimentId}/report`

Reports contain progress, successful and failed trial evidence, robustness comparisons, termination details, and token usage where available. They describe research execution only and must not be presented as live-trading safety or expected profitability.

The pre-registered `support_resistance_effectiveness_v2` workflow independently validates `pivot-slope-atr-v2` through a parent/child specialization with a point-in-time liquid universe, a sealed final holdout, an absolute 200-backtest budget, same-cost cache replay, and bilingual JSON/Markdown/PDF artifacts. Its child and artifact endpoints remain read-only to the UI. See [Support/resistance effectiveness study](support-resistance-effectiveness.md) for the full protocol and database rollout boundary.
