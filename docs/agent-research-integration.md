# Quant and AgentOps Local Integration

[中文](agent-research-integration.zh-CN.md) | [Documentation index](README.md)

The Quant frontend is the primary entry point for strategy development and research experiments. Ordinary Quant features can run from this repository alone. `/research`, `/agent-runs/*`, and the new-strategy Draft PR flow also require the adjacent AgentOps Control Plane repository.

## Safety Boundary

Joint development must disable the paper scheduler and order submission:

```env
PAPER_TRADING_SCHEDULER_ENABLED=false
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
```

`/api/agent/*` requires a Bearer service token and exposes only draft-strategy and research-experiment validation, creation, cancellation, and experiment usage updates. Broker, portfolio activation, and order tools are not registered. Never put the token in workflow inputs, artifacts, URLs, or logs.

GitHub delivery defaults to mock mode. Real delivery is Draft-PR-only, requires an explicit delivery mode and approval, and does not merge or deploy code.

## New-Algorithm Native Contract

The new-algorithm workflow targets the current single-engine architecture. A Draft PR must add a C++20 strategy module and native descriptor covering defaults, strict JSON Schema, required features, history window, and algorithm revision. It must include only a test/reference Python implementation or frozen golden evidence, a complete `1e-10` Python/native differential, native wheel build and smoke checks, and proof that backtest and Paper daily signals use the same native rules. Adding a strategy requires rebuilding and redeploying the wheel. AgentOps never merges, deploys, starts a backtest, activates scheduling, or submits an order.

## First Database Upgrade

Quant does not use Alembic. Resolve the exact local Quant database before applying the additive SQL file:

```bash
.venv/bin/python backend/utils/preflight_adaptive_research_rollout.py
psql "$DATABASE_URL" -f backend/utils/create_zzzzz_research_experiments.sql
```

The SQL adds the research experiment, round, candidate, and trial structures, including the nullable historical `candidate_id` link. It does not delete existing data. Before rollout, run a read-only preflight and block deployment if any legacy experiment is `queued`, `running`, or `cancel_requested`; production-like rollout still requires an exact target, backup, recovery, and compatibility plan.

AgentOps uses Alembic. Revision `0007` persists external tool runs, `0008` adds workflow token accounting, and `0009` adds structured approval resolution payloads for final Pareto candidate selection. `make dev-agent-all` applies AgentOps migrations but does not silently target or mutate an unknown Quant database.

## Recommended One-Command Startup

After both repositories have their dependencies installed, run this from the Quant repository:

```bash
make dev-agent-all
```

On macOS, `start-agent-platform.command` starts the same topology. The launcher:

1. Finds a sibling Coding Agent repository, or uses `CODING_AGENT_REPO=/absolute/path`.
2. Starts AgentOps PostgreSQL and applies Alembic migrations.
3. Starts the Control Plane on `:8100`, creates or updates the Quant Project, and publishes the engine-category research and new-algorithm Draft PR workflows.
4. Starts the Quant backend on `:8000`, the research worker, and the frontend on `:3000`.
5. Generates an ephemeral shared service token without writing it to disk or logs.
6. Forces both paper scheduler switches to `false`.

Pressing `Ctrl+C` stops the application processes started by the launcher. AgentOps PostgreSQL and existing data remain. Missing dependencies are reported with installation commands; the launcher does not modify lockfiles.

## Manual Startup

1. In AgentOps, start PostgreSQL on `:15432`, apply Alembic migrations, and start the Control Plane on `:8100`.
2. Bootstrap the Quant workflow templates and record the printed project ID.
3. Start Quant PostgreSQL on `:5432`.
4. Start the Quant backend, research worker, and frontend with the same non-empty service token:

```bash
QUANT_AGENT_SERVICE_TOKEN='local-only-token' \
AGENTOPS_PROJECT_ID='<bootstrap-project-id>' \
make dev-agent-safe
```

AgentOps requires:

```env
QUANT_AGENT_INTEGRATION_ENABLED=true
QUANT_API_BASE_URL=http://localhost:8000
QUANT_AGENT_SERVICE_TOKEN=local-only-token
```

The Quant frontend receives:

```env
NEXT_PUBLIC_AGENTOPS_API_BASE_URL=http://localhost:8100
NEXT_PUBLIC_AGENTOPS_PROJECT_ID=<project-id>
```

The AgentOps Web Console on `:3100` is an optional advanced surface. The Quant research flow does not require it.

## Stop Policies and Token Synchronization

The published Quant research workflow requires `stopPolicy` with at least one time, token, or target-metric condition. AgentOps validates the policy before starting the run, stores `tokenBudget` and cumulative `totalTokens` on `WorkflowRun`, and copies the requested policy exactly into the Quant experiment specification.

The engine-category workflow accepts `support_resistance` without changing the selected type. The planner may vary existing scalar mode switches and numeric `signal.*` / `risk.*` leaves, while Quant rejects any candidate that disables all three entry modes. This does not add portfolio, scheduler, or order permissions.

The same workflow treats `head_shoulders_bottom`, `rounded_bottom`, and `v_reversal` as locked engine-ready categories. The planner may study existing pattern thresholds and staged targets, but Quant rejects targets that are not strictly increasing or whose third stage is not 100%; generated strategies remain Draft.

While the workflow is `waiting_external`, AgentOps periodically sends cumulative usage to the authenticated experiment usage endpoint. Quant records that usage and re-evaluates all stop conditions. A triggered stop records termination evidence, cancels queued trials, and lets already-running synchronous work finish safely. See [Research experiments](research-experiments.md) for exact fields and semantics.

## Recovery and Limits

- AgentOps persists external tool work as `ExternalToolRun`; Control Plane restart resumes `waiting_external` polling.
- Experiment polling tolerates a bounded number of connection or HTTP 503 failures, configured in AgentOps by `QUANT_TOOL_POLL_MAX_ERRORS`.
- The Quant worker uses PostgreSQL `FOR UPDATE SKIP LOCKED`, defaults to concurrency 2, and permits at most 5 rounds and 100 actual backtests (defaults: 3/48).
- Worker restart requeues orphaned trials and reuses their cleaned `StrategyRun` evidence instead of creating duplicate backtests.
- The exclusive market-data maintenance gate rejects new studies and promotions, drains the complete experiment, invalidates derived caches, and only then permits source writes. A failed maintenance stays blocked until a successful rerun.
- Cancellation stops new trial claims; already-running synchronous backtests reach a safe boundary before `cancelled`.
- Policy-stopped and cancelled experiments do not resume queued work after restart.
- A new code strategy remains in a Draft PR until it is reviewed, merged, rebuilt into the native wheel, deployed, and present in the engine-ready catalog.
- Robustness reports are research evidence, not proof of profitability or live-trading safety.
