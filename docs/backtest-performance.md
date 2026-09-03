# Backtest Performance and Worker Operations

[中文](backtest-performance.zh-CN.md)

## Native execution boundary

All nine engine-ready daily long strategies use one in-process C++20 kernel for both full backtests and Paper Trading signal evaluation. Signal rules, day-T close decisions, next-valid-session (T+1) open fills, sell-before-buy ordering, shared cash, costs, corporate actions, dynamic-universe handling, position limits, and deterministic ordering remain invariants. Python owns data access, the durable queue, database transactions, progress/cancellation, broker effects, and result persistence. `custom` remains stored-only.

There is no runtime engine selector, job-level engine version, or Python execution fallback. New run summaries record `kernel.version=cpp-v1`, ABI, build ID, PreparedDataset schema `v3`, and the strategy algorithm revision. Historical records remain readable. Operational rollback means redeploying the previously validated application/wheel build, not selecting an old engine inside the current process.

## Persistence levels

- `summary`: exact metrics plus at most 1,500 deterministic min/max bucket equity points; no run signals, transactions, or full positions.
- `trades`: summary plus transactions; no signals or full daily positions.
- `full`: signals, transactions, daily positions/snapshots, and Support/Resistance run audit events.

Manual requests default to `full`; research trials always request `summary`. `persist_level` and `available_details` tell clients whether a detail was not persisted. Missing detail must never be presented as a real zero count.

The manual form always submits the selected value explicitly and offers bilingual Full audit (`full`), Trade analysis (`trades`), and Fast summary (`summary`) options. Persistence changes stored detail only; signal, fill, equity, and summary calculations are unchanged.

Signals, transactions, snapshots, Support/Resistance zone/regime versions, and run audit events use the current SQLAlchemy Session's `postgresql+psycopg` connection for `COPY FROM STDIN` in batches of 5,000. Every typed column set, enum, JSON payload, instrument reference, finite numeric, database numeric bound, and Support/Resistance geometry is validated before the first detail write; cancellation is checked again before every COPY batch. Batches never commit independently: validation, COPY, cancellation, or materialization failure rolls back the run's full detail set. Cache hits skip shared zone/regime writes but still persist the exact run-scoped event set requested by `full`; event types and payloads are not pruned.

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

Rendering changes do not alter backtest calculations, next-valid-session (T+1) open execution, costs, corporate actions, positions, metrics, snapshot persistence, paper trading, or worker/scheduler behavior.

## Database rollout and recovery

The project has no Alembic workflow. Before apply, resolve and record the exact target database, take a database-native backup, review current constraints/indexes, and run the SQL with `ON_ERROR_STOP`. The scripts add `backtest_jobs`, `backtest_worker_managers`, nullable `instrument_id` columns, and nullable `experiment_trials.cancel_requested_at`, plus the required foreign keys and queue/manager indexes. The trial cancellation marker requires no historical backfill. The scripts deliberately do not add speculative signal/transaction cursor indexes or rewrite historical detail; legacy runs are interpreted as `full`.

If rollout fails, stop new job producers and the backtest worker, then redeploy the last validated application and native wheel. Retain additive objects during application rollback. If schema removal is required later, first prove no application version references the objects and restore from backup on any data-integrity issue; do not use a destructive rollback during the normal release.

## Telemetry and acceptance

`summary_metrics.performance` uses non-overlapping phases for SQL execute/fetch, row decode, day grouping, history state, signals, execution, detail construction, detail/summary persistence, response construction, and engine total. It also records rows/days/signals/trades per second, microseconds per input row, phase shares, `unaccounted_ms`, and peak RSS. Worker terminalization adds queue wait, active, and finalization overhead. Support/Resistance subphases are diagnostic dimensions and are not double-counted in `unaccounted_ms`. Logs contain the same structured mapping.

Manual, research, and verification backtests use the stable key and manifest in `run_manifest.preparedDataset`. The v3 key includes loader revision, source fingerprint, stable instrument set, full date range, feature set, price/corporate-action, symbol-identity, and universe-membership semantics; ordinary strategy parameters are excluded. The first run atomically builds separate Fortran-order `int64` identity/date and `float64` feature memmaps plus symbol/asset/exchange, date-offset, corporate-action, and dynamic-universe sidecars under a file lock. Missing numeric features are NaN. Later runs open both buffers read-only. A v2 key never matches v3, and old files are not deleted automatically. Corrupt data or metadata rebuilds under the lock; a source row-count/fingerprint change marks the work `data_changed`, while an unrecoverable cache build fails the run instead of falling back to a Python trading-day loop. Cleanup counts queued/running jobs referencing the key and refuses deletion while an active lease exists.

The native package is built with C++20, `pybind11==3.1.0`, `-O3`, and `-DNDEBUG`; fast-math is intentionally disabled. Local development installs it with `.venv/bin/pip install -e backend/native`. Docker produces a wheel in a Linux builder stage and installs only that wheel into the runtime stage.

The native `run_backtest(dataset, strategy, options, control_callback)` covers Trend, Mean Reversion, Momentum Breakout, Island Reversal, Double Bottom, Head-and-Shoulders Bottom, Rounded Bottom, V Reversal, and Support/Resistance. It reads PreparedDataset v3 NumPy buffers without copying and invokes the Python control callback exactly once per completed formal session; rows before `start_date` warm bounded histories, pattern/S/R state, and dynamic-universe counters without signals, trades, equity rows, or callbacks. Its typed `KernelResult` exposes read-only NumPy views plus canonical JSON columns for signals, trades, equity/positions, universe diagnostics, and Support/Resistance audit vectors. The shared native ledger preserves T-close/T+1-open timing, SELL-first execution, shared cash, deterministic strength ranking and thresholds, position limits, staged targets, minimum commissions, adverse slippage, corporate actions, stable identity, missing opens, dynamic-universe exit-only behavior, and delisting write-offs.

Paper signal generation converts the bounded history and current portfolio state into an in-memory v3 columnar view, then calls `evaluate_day(dataset_day, strategy, portfolio_state)`. Regression tests require its action, order, reason, score, and canonical metadata to equal the matching native backtest session.

Frozen golden fixtures preserve the pre-cutover oracle and the pre-STL full-ledger result for all nine strategies. `native_nine_strategy_golden.json` fingerprints every typed signals/trades/equity/positions/audit vector for a deterministic `20 symbols × 120 sessions` matrix and records the exact base commit and recovery-diff digest used to rebuild that oracle. Native tests also cover per-day ordering, reasons, metadata, staged audit, and Support/Resistance lifecycle evidence with `1e-10` numeric tolerance. Paper tests compare the native daily evaluator against the same-day backtest signal contract with all Alpaca calls mocked. These checks establish semantic regression coverage, not the database-scale performance gate.

Support/Resistance uses the Python-header-free `support_resistance_core.hpp/.cpp` STL state machine for cache identity, full-Pivot zone identity, `1e-10` half-up price normalization, deterministic weighted Theil-Sen fitting, frozen geometry, four-state regime evolution, pending outcomes, posterior evidence, entry channels, exits, and typed audit output. `support_resistance_kernel.cpp` only converts Python boundary DTOs before releasing the GIL and converts completed typed results afterward. Python queries and locks the cache tables, hydrates native inputs, and adapts final typed vectors to persistence rows; it does not implement an alternative detector or trading state machine. During `run_backtest`, S/R warmup and formal-session computation remain GIL-free; the sole calculation-time acquisition is the once-per-formal-session control callback.

Read-only benchmark preflight:

```bash
make benchmark-backtests BENCHMARK_ARGS="plan"
```

`plan` reports the target database, native ABI/build, cases, compact PostgreSQL source-revision fingerprints, service state, and the complete write-authorization scope without creating a `StrategyRun`. The fixed full matrix comprises one benchmark-only Draft Strategy, 105 frozen-Python baseline runs, and 159 native correctness/performance runs (264 `StrategyRun` rows total); `currentlyRunnableNativeRunCount` separately exposes missing strategy fixtures instead of silently lowering that scope. The compact fingerprint hashes server-side row/action/identity revisions so preflight does not materialize millions of JSON rows in Python. Formal cache construction still computes the strict row-level fingerprint. Correctness, screening, and confirmation require explicit `--apply`, a clean worktree, no queued/running jobs, `RESEARCH_WORKER_ENABLED=false`, both paper-scheduler settings set to `false`, and `BACKTEST_WORKER_CONCURRENCY=1`. Screening and confirmation additionally require `--baseline /path/to/frozen-python-report.json`; the command computes every speedup and RSS gate and blocks acceptance when a baseline case is absent. Each measured case performs one warm-up and five formal runs and retains every run ID. Do not run write mode until the exact database, fingerprints, service state, and expected run count have been reported and explicitly authorized.

Screening covers all nine strategies at 500 symbols by one year in warm `summary` mode and requires a median native speedup of at least 5x against the frozen Python baseline. Confirmation covers Trend, Double Bottom, and Support/Resistance at 3,640 symbols by five years: cold `summary` at least 3x, warm `summary` at least 5x, warm `full` at least 2x, and peak RSS no higher than baseline. Cold includes database reading and v3 cache construction; warm starts from the matching fingerprinted cache. If cold misses 3x, the next implementation step is PostgreSQL COPY streaming into the columnar cache; the threshold is not lowered.

Do not claim the performance gate from unit tests or synthetic smoke results. A candidate is deployment-ready only after the separately authorized database-scale matrix passes every threshold. Index or SQL-query changes still require real `EXPLAIN ANALYZE` evidence; no improvement is inferred from synthetic tests.

Numba is not a dependency. Concurrency remains `1|2`; concurrency 4, SQL/index changes, ring buffers, and further pattern optimization require observed thresholds and a separate change.

For chart releases, record the production-build shared and backtest-detail first-load sizes and verify that the Lightweight Charts chunk is separate. Use fixed fixtures for 1,500/5,000 equity points, 200 events, and 500 candles/100 markers; in one production Chromium, measure five runs and require median data-ready-to-chart-ready time at or below 100 ms, average pan/zoom at or above 55 FPS, and no chart main-thread task longer than 50 ms.
