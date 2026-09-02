# Backtest Performance and Worker Operations

[中文](backtest-performance.zh-CN.md)

## Compatibility boundary

Backtest v2 changes data loading, stable instrument resolution, detail persistence, and execution orchestration only. Signal rules, day-T close decisions, next-session open fills, sell-before-buy ordering, shared cash, costs, corporate actions, position limits, and exact summary metrics remain compatibility invariants. Paper/live execution remains on its existing path.

`BACKTEST_ENGINE_VERSION` defaults to `v1`. Research summary and verification jobs explicitly request `v2`; switch manual backtests only after correctness and database-scale benchmarks pass. A rollback changes job payloads or the environment back to `v1`; additive tables, nullable columns, and incremental endpoints remain in place.

## Persistence levels

- `summary`: exact metrics plus at most 1,500 deterministic min/max bucket equity points; no run signals, transactions, or full positions.
- `trades`: summary plus transactions; no signals or full daily positions.
- `full`: signals, transactions, daily positions/snapshots, and Support/Resistance run audit events.

Manual requests default to `full`; research trials always request `summary`. `persist_level` and `available_details` tell clients whether a detail was not persisted. Missing detail must never be presented as a real zero count.

The manual form always submits the selected value explicitly and offers bilingual Full audit (`full`), Trade analysis (`trades`), and Fast summary (`summary`) options. Persistence changes stored detail only; signal, fill, equity, and summary calculations are unchanged.

Support/Resistance zone versions and run audit events use transaction-scoped SQLAlchemy Core inserts in batches of 5,000. Batches never commit independently: a failure rolls back the run's full detail set. Cache hits skip shared zone-version writes but still persist the exact run-scoped event set requested by `full`; event types and payloads are not pruned.

Candidate promotion first creates an OOS/base-cost `full` verification job. The job rechecks the data fingerprint and compares numeric summary metrics at relative and absolute tolerance `1e-10`. A mismatch or fingerprint change records failed verification and blocks promotion. The verification run ID is written to candidate metrics and promoted strategy lineage.

## On-demand durable worker

Apply the additive schema before routing traffic:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker-manager
```

The API atomically creates a `strategy_runs` row and `backtest_jobs` row, then returns HTTP 201 even if automation is unavailable. The lightweight manager remains resident, polls every two seconds, recovers expired leases, and holds a PostgreSQL advisory lock so only one leader may start the worker group. A leader starts `backtest_worker --once --concurrency N` when eligible queued work exists; the worker coordinator uses `ProcessPoolExecutor` with the multiprocessing `spawn` context, drains the queue, waits for all active jobs, and exits. Each child imports the application and creates its own SQLAlchemy engine/session state, so database connections are not inherited from the coordinator. A normal task failure is isolated to that job. A broken process pool exits the worker non-zero, allowing the manager's existing 1/2/5/10/30-second capped backoff and expired-lease recovery to take over.

`BACKTEST_WORKER_CONCURRENCY` defaults to `2` and accepts only `1` or `2`; invalid, non-integer, or out-of-range values fail startup. Set it to `1` for an immediate serial rollback. Docker Compose injects the same value into backend and manager, while Make targets inherit it from the shell. Configuration is read at process startup and is not hot-reloaded, so restart both backend and manager after changing it. The status endpoint reports `execution_model=process`, `configured_concurrency`, and `available_slots=max(configured_concurrency-active_jobs, 0)`.

Every backtest consumes one equal execution slot. A single run still processes trading days and symbols serially, so this setting improves throughput for independent runs rather than the latency of one run. Two full-universe or long-window jobs may therefore approach roughly twice the memory pressure of one job; the recorded single-run acceptance baseline is 16.1 GB. Observe peak RSS and switch concurrency to `1` if the host is under memory pressure. Only the advisory-lock leader manager should run the worker group. Do not run `make backtest-worker` or another diagnostic worker alongside automatic manager execution, because it creates additional slots beyond the configured global count.

Manager rows in `backtest_worker_managers` heartbeat every five seconds. A leader heartbeat older than 15 seconds makes automation unavailable. An idle platform is ready with a healthy manager and no child worker. `make dev`, `make dev-agent-safe`, and `make backtest-worker-manager` supervise the manager and restart it two seconds after an unexpected exit; `make dev-agent-all` and Docker Compose provide their own process supervision. All full-stack paths force both paper scheduler settings off. `make dev-backend`, `make dev-frontend`, and direct `uvicorn` are partial-stack entry points and do not guarantee automatic queue consumption. `make backtest-worker` remains an explicit diagnostic/operator command.

Workers claim with `FOR UPDATE SKIP LOCKED`, heartbeat their lease, check cancellation each trade day, clear only the current run's incomplete details before retry, and mark linked research trials terminal when retries are exhausted.

## Unified task center

`/backtest-tasks` is a read-only projection over the existing research scheduler, `strategy_runs`, `backtest_jobs`, and verification candidates. `GET /api/backtests/tasks` returns manual runs, every research trial (including trials not yet submitted to the backtest queue), and candidate verification work without duplicating a trial that already owns a run/job. Active work sorts before terminal history; source/stage filters and 25/50/100-row pagination are applied by the server.

The page intentionally shows two health layers. `GET /api/research/worker-status` explains whether a trial is still waiting for research scheduling; `GET /api/backtests/worker-status` explains whether an already-queued run has execution capacity. The task list polls every four seconds only while its current result page contains a non-terminal task. Cancellation continues to use the existing cooperative backtest boundary, and the task center does not introduce a second queue or change signal, fill, cost, or persistence semantics.

## Progress semantics

Progress phases are `queued` (0%), `preparing` (0%), `running` (completed trading days mapped to 0-85%), `finalizing` (85-99%), and terminal `completed`, `failed`, or `cancelled`. Finalization reports `zone_versions`, `run_events`, `backtest_details`, or `committing`; item-based stages include `completed_items` and `total_items`. Completion reaches 100% only after the final database commit. Failed and cancelled jobs preserve their last percentage; a retry increments `attempt` and resets progress to zero. Running updates are persisted at most once every five seconds, repeated updates within one finalizing stage at most once per second, and stage changes immediately. Legacy progress documents are normalized by the API without a data migration.

## Incremental API

- `GET /api/backtests/{id}/summary`
- `GET /api/backtests/tasks`
- `GET /api/backtests/worker-status`
- `GET /api/backtests/{id}/equity?max_points=1500&shape=chart`
- `GET /api/backtests/{id}/comparison-curves?max_points=1500`
- `GET /api/backtests/{id}/signals?limit=100&cursor=...&symbol=...`
- `GET /api/backtests/{id}/transactions?limit=100&cursor=...&symbol=...`
- `POST /api/backtests/{id}/cancel`

`shape` defaults to `snapshot` for backward compatibility and retains the original full-row/Python downsampling path as a rollback option. `shape=chart` returns only `ts`, `equity`, `drawdown`, and nullable benchmark values; it never transfers positions or the complete metrics document. For this compact shape, PostgreSQL applies the existing deterministic first/last plus bucket min/max selection before returning rows, including odd, even, and very small `max_points` values. The comparison endpoint returns cached SPY/QQQ points and reconstructs a missing legacy curve from snapshots and adjusted historical bars without committing database changes; it applies the same endpoint-preserving point cap. Snapshot persistence and the default `max_points=1500` are unchanged.

The legacy detail endpoint remains for one migration cycle. The list endpoint returns scalar metrics only. Signal ordering is `(ts, symbol, id)` ascending; transaction ordering is `(ts, id)` descending. Cursors are opaque.

## Chart loading and rendering

The list page polls active runs every four seconds and shows a shared accessible percentage bar. The detail page polls only summary and worker status while a run is queued or running. It does not request equity, comparison curves, signals, or transactions until status is `completed`; those payloads then load independently with separate error states. A comparison-curve failure leaves the strategy curve available and shows a non-blocking warning. Failed and cancelled runs stop polling and preserve their terminal progress. When manager automation is unavailable, both pages prioritize the warning that queueing remains available but execution is paused. Otherwise they show active/configured processes, available slots, and queued jobs.

After completion, the first 100 newest transactions make the detail tables available immediately. The page then follows the opaque transaction cursor in batches of up to 500 until the overview chart and symbol P&L cover the full run. Progress is shown as loaded/total; a failed tail page keeps the partial markers visible and can resume from its failed cursor. Equity-chart signal and fill markers retain their shapes, colors, filters, counts, and hover details, but do not render repeated `BUY`/`SELL` text on the plot. The transaction table initially renders the latest 10 rows and adds 10 at a time. Position lifecycles initially use the latest 100 transactions and render 12 rows; loading more expands the calculation window by 100 transactions when needed and renders 12 more lifecycle rows. Because that lifecycle scope is intentionally partial, the UI always reports its loaded transaction count against the run total.

Equity, lifecycle, and global stock charts use the pinned `lightweight-charts@5.2.1` client-only module. It is dynamically loaded only when a chart is visible and does not enter the shared first-load chunk. Native canvas interaction handles panning, wheel and touch zoom, crosshairs, and resize. Canvas primitives preserve island gaps, Double Bottom annotations, Support/Resistance regions, connectors, and collision-aware labels. Chart instances and attached primitives are destroyed when their panel closes or changes. The built-in TradingView attribution logo remains enabled.

Rendering changes do not alter backtest calculations, T+1 execution, costs, corporate actions, positions, metrics, snapshot persistence, paper trading, or worker/scheduler behavior.

## Database rollout and recovery

The project has no Alembic workflow. Before apply, resolve and record the exact target database, take a database-native backup, review current constraints/indexes, and run the SQL with `ON_ERROR_STOP`. The scripts add `backtest_jobs`, `backtest_worker_managers`, nullable `instrument_id` columns, and nullable `experiment_trials.cancel_requested_at`, plus the required foreign keys and queue/manager indexes. The trial cancellation marker requires no historical backfill. The scripts deliberately do not add speculative signal/transaction cursor indexes or rewrite historical detail; legacy runs are interpreted as `full`.

If rollout fails, stop new job producers and the backtest worker, then route new work to v1. Retain additive objects during application rollback. If schema removal is required later, first prove no application version references the objects and restore from backup on any data-integrity issue; do not use a destructive rollback during the normal release.

## Telemetry and acceptance

`summary_metrics.performance` uses non-overlapping phases for SQL execute/fetch, row decode, day grouping, history state, signals, execution, detail construction, detail/summary persistence, response construction, and engine total. It also records rows/days/signals/trades per second, microseconds per input row, phase shares, `unaccounted_ms`, and peak RSS. Worker terminalization adds queue wait, active, and finalization overhead. Support/Resistance subphases are diagnostic dimensions and are not double-counted in `unaccounted_ms`. Logs contain the same structured mapping.

Research trials use the stable key and manifest in `run_manifest.preparedDataset`. The v3 key includes loader revision, source fingerprint, stable instrument set, full date range, feature set, price/corporate-action, symbol-identity, and universe-membership semantics; ordinary strategy parameters are excluded. The first trial atomically builds separate Fortran-order `int64` identity/date and `float64` feature memmaps plus symbol/asset/exchange, date-offset, and corporate-action sidecars under a file lock. Missing numeric features are NaN. Later trials open both buffers read-only. A v2 key never matches v3, and old files are not deleted automatically. Corrupt data or metadata rebuilds under the lock; cache-infrastructure failures record a reason and fall back to the DB loader for the same fingerprint; a row-count change during construction marks the experiment `data_changed`. Manual backtests do not use this cache. Cleanup counts queued/running jobs referencing the key and refuses deletion while an active lease exists.

The native package is built with C++20, `pybind11==3.1.0`, `-O3`, and `-DNDEBUG`; fast-math is intentionally disabled. Local development installs it with `.venv/bin/pip install -e backend/native`. Docker produces a wheel in a Linux builder stage and installs only that wheel into the runtime stage.

The native `run_backtest(dataset, strategy, options, control_callback)` foundation currently covers Trend, Mean Reversion, and Momentum Breakout. It reads the two PreparedDataset v3 NumPy buffers without copying, releases the GIL for the trading loop, and reacquires it only once per completed session for cancellation/progress. Its typed `KernelResult` exposes read-only NumPy views for signals, trades, equity, positions, and per-session dynamic-universe diagnostics. The shared native ledger already preserves T-close/T+1-open timing, SELL-first execution, shared cash, deterministic strength ranking and thresholds, position limits, minimum commissions, adverse slippage, split quantity/cost-basis adjustment, stable instrument identity, missing opens, and delisting write-offs. Point-in-time membership uses the same deterministic exclusion order and per-instrument processed-session history counter as Python, filters BUY signals only, and keeps ineligible existing positions exit-only. The three native strategies now emit canonical signal metadata JSON without per-row Python dictionaries; trade columns carry signal/execution timestamps, reason, entry features/history, position before/after, execution date, slippage, gross notional, and net cash flow. Python/C++ differentials cover those metadata and audit contracts as well as the ledgers and dynamic-universe eligibility. This entry point is not yet used by the production worker: staged-pattern metadata/audit vectors and typed COPY persistence must pass their own differentials before the one-time cutover.

Support/Resistance now uses the native module as the single implementation for detector cache identity, full-Pivot zone identity, `NUMERIC(24,10)` half-up price normalization, deterministic weighted Theil-Sen fitting, frozen-zone projection validation, and cross-session candidate, regime, entry-channel, exit, and audit evolution. The public native catalog and daily evaluator therefore cover all nine engine-ready strategies. Existing Python dataclasses remain the transport and persistence shape while the native state machine mutates them in place; replacing that boundary with typed native state and releasing the GIL remain later kernel-integration work. There is no runtime engine selector or Python algorithm fallback for migrated behavior.

Read-only benchmark preflight:

```bash
make benchmark-backtests BENCHMARK_ARGS="plan"
make benchmark-backtests BENCHMARK_ARGS="screening"
```

`plan`, and correctness/screening/confirmation without `--apply`, report the target database, code/dependency versions, cases, data fingerprints, and estimated run count without creating a `StrategyRun`. Write mode requires an explicit `--apply`, a clean worktree, no queued/running jobs, `RESEARCH_WORKER_ENABLED=false`, both paper-scheduler settings set to `false`, and `BACKTEST_WORKER_CONCURRENCY=1`. Each case performs one warm-up and five measured runs, reports the median and maximum, and retains every run ID. Do not run write mode until `hzy/public`, the fingerprints, and expected run count have been reported and explicitly authorized.

The three stateless candidate kernels are implemented independently and still delegate final event construction to the shared strategy handlers. They are disabled on the default execution path. Screening generates baseline/kernel A/B cases for every strategy and persistence level; a later change may enable one strategy only after its full-universe confirmation improves median engine time by at least 20% with an identical differential result.

The full-universe acceptance gate requires identical normalized zone/event content, at least 50% lower Support/Resistance finalization time, and peak RSS no higher than the recorded 16.1 GB baseline. Do not claim this gate from unit tests alone. Run the 100/500/3,640-symbol and 1-year/5-year/full-history matrix before enabling v2 for manual traffic. Index or LAG query changes require real `EXPLAIN ANALYZE` evidence and at least 20% improvement; neither is assumed from synthetic tests.

Numba is not a dependency. Concurrency remains `1|2`; concurrency 4, SQL/index changes, ring buffers, and further pattern optimization require observed thresholds and a separate change.

For chart releases, record the production-build shared and backtest-detail first-load sizes and verify that the Lightweight Charts chunk is separate. Use fixed fixtures for 1,500/5,000 equity points, 200 events, and 500 candles/100 markers; in one production Chromium, measure five runs and require median data-ready-to-chart-ready time at or below 100 ms, average pan/zoom at or above 55 FPS, and no chart main-thread task longer than 50 ms.
