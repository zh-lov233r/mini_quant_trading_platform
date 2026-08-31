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

Support/Resistance zone versions and run audit events use transaction-scoped SQLAlchemy Core inserts in batches of 5,000. Batches never commit independently: a failure rolls back the run's full detail set. Cache hits skip shared zone-version writes but still persist the exact run-scoped event set requested by `full`; event types and payloads are not pruned.

Candidate promotion first creates an OOS/base-cost `full` verification job. The job rechecks the data fingerprint and compares numeric summary metrics at relative and absolute tolerance `1e-10`. A mismatch or fingerprint change records failed verification and blocks promotion. The verification run ID is written to candidate metrics and promoted strategy lineage.

## On-demand durable worker

Apply the additive schema before routing traffic:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker-manager
```

The API atomically creates a `strategy_runs` row and `backtest_jobs` row, then returns HTTP 201 even if automation is unavailable. The lightweight manager remains resident, polls every two seconds, recovers expired leases, and holds a PostgreSQL advisory lock so only one leader may spawn the default global concurrency-one worker. A leader starts `backtest_worker --once --concurrency 1` when eligible queued work exists; the worker drains the queue and exits. Unexpected child exits use 1/2/5/10/30-second capped backoff.

Manager rows in `backtest_worker_managers` heartbeat every five seconds. A leader heartbeat older than 15 seconds makes automation unavailable. An idle platform is ready with a healthy manager and no child worker. `make dev`, `make dev-agent-safe`, `make dev-agent-all`, and Docker Compose supervise the manager and force both paper scheduler settings off. `make dev-backend`, `make dev-frontend`, and direct `uvicorn` are partial-stack entry points and do not guarantee automatic queue consumption. `make backtest-worker` remains an explicit diagnostic/operator command.

Workers claim with `FOR UPDATE SKIP LOCKED`, heartbeat their lease, check cancellation each trade day, clear only the current run's incomplete details before retry, and mark linked research trials terminal when retries are exhausted.

## Progress semantics

Progress phases are `queued` (0%), `preparing` (0%), `running` (completed trading days mapped to 0-85%), `finalizing` (85-99%), and terminal `completed`, `failed`, or `cancelled`. Finalization reports `zone_versions`, `run_events`, `backtest_details`, or `committing`; item-based stages include `completed_items` and `total_items`. Completion reaches 100% only after the final database commit. Failed and cancelled jobs preserve their last percentage; a retry increments `attempt` and resets progress to zero. Running updates are persisted at most once every five seconds, repeated updates within one finalizing stage at most once per second, and stage changes immediately. Legacy progress documents are normalized by the API without a data migration.

## Incremental API

- `GET /api/backtests/{id}/summary`
- `GET /api/backtests/worker-status`
- `GET /api/backtests/{id}/equity?max_points=1500&shape=chart`
- `GET /api/backtests/{id}/comparison-curves?max_points=1500`
- `GET /api/backtests/{id}/signals?limit=100&cursor=...&symbol=...`
- `GET /api/backtests/{id}/transactions?limit=100&cursor=...&symbol=...`
- `POST /api/backtests/{id}/cancel`

`shape` defaults to `snapshot` for backward compatibility and retains the original full-row/Python downsampling path as a rollback option. `shape=chart` returns only `ts`, `equity`, `drawdown`, and nullable benchmark values; it never transfers positions or the complete metrics document. For this compact shape, PostgreSQL applies the existing deterministic first/last plus bucket min/max selection before returning rows, including odd, even, and very small `max_points` values. The comparison endpoint returns cached SPY/QQQ points and reconstructs a missing legacy curve from snapshots and adjusted historical bars without committing database changes; it applies the same endpoint-preserving point cap. Snapshot persistence and the default `max_points=1500` are unchanged.

The legacy detail endpoint remains for one migration cycle. The list endpoint returns scalar metrics only. Signal ordering is `(ts, symbol, id)` ascending; transaction ordering is `(ts, id)` descending. Cursors are opaque.

## Chart loading and rendering

The list page polls active runs every four seconds and shows a shared accessible percentage bar. The detail page polls only summary and worker status while a run is queued or running. It does not request equity, comparison curves, signals, or transactions until status is `completed`; those payloads then load independently with separate error states. A comparison-curve failure leaves the strategy curve available and shows a non-blocking warning. Failed and cancelled runs stop polling and preserve their terminal progress. When manager automation is unavailable, both pages warn that queueing remains available but execution is paused.

After completion, the first 100 newest transactions make the detail tables available immediately. The page then follows the opaque transaction cursor in batches of up to 500 until the overview chart and symbol P&L cover the full run. Progress is shown as loaded/total; a failed tail page keeps the partial markers visible and can resume from its failed cursor. Equity-chart signal and fill markers retain their shapes, colors, filters, counts, and hover details, but do not render repeated `BUY`/`SELL` text on the plot. The transaction table initially renders the latest 10 rows and adds 10 at a time. Position lifecycles initially use the latest 100 transactions and render 12 rows; loading more expands the calculation window by 100 transactions when needed and renders 12 more lifecycle rows. Because that lifecycle scope is intentionally partial, the UI always reports its loaded transaction count against the run total.

Equity, lifecycle, and global stock charts use the pinned `lightweight-charts@5.2.1` client-only module. It is dynamically loaded only when a chart is visible and does not enter the shared first-load chunk. Native canvas interaction handles panning, wheel and touch zoom, crosshairs, and resize. Canvas primitives preserve island gaps, Double Bottom annotations, Support/Resistance regions, connectors, and collision-aware labels. Chart instances and attached primitives are destroyed when their panel closes or changes. The built-in TradingView attribution logo remains enabled.

Rendering changes do not alter backtest calculations, T+1 execution, costs, corporate actions, positions, metrics, snapshot persistence, paper trading, or worker/scheduler behavior.

## Database rollout and recovery

The project has no Alembic workflow. Before apply, resolve and record the exact target database, take a database-native backup, review current constraints/indexes, and run the SQL with `ON_ERROR_STOP`. The script adds `backtest_jobs`, `backtest_worker_managers`, nullable `instrument_id` columns, foreign keys, and the queue/manager indexes. It deliberately does not add speculative signal/transaction cursor indexes. It does not rewrite historical detail; legacy runs are interpreted as `full`.

If rollout fails, stop new job producers and the backtest worker, then route new work to v1. Retain additive objects during application rollback. If schema removal is required later, first prove no application version references the objects and restore from backup on any data-integrity issue; do not use a destructive rollback during the normal release.

## Telemetry and acceptance

`summary_metrics.performance` records SQL loading, Python dataset construction, history maintenance, signals, execution, detail/summary persistence, total time, row counts, output counts, and peak RSS. Support/Resistance persistence additionally records zone/event rows and milliseconds, total persistence milliseconds, and whether the immutable cache was reused. Logs contain the same structured mapping. The full-universe acceptance gate requires identical normalized zone/event content, at least 50% lower Support/Resistance finalization time, and peak RSS no higher than the recorded 16.1 GB baseline. Do not claim this gate from unit tests alone. Run the 100/500/3,640-symbol and 1-year/5-year/full-history matrix before enabling v2 for manual traffic. Index or LAG query changes require real `EXPLAIN ANALYZE` evidence and at least 20% improvement; neither is assumed from synthetic tests.

The NumPy prepared-dataset/memmap layer is dependency-pinned but remains rollout-gated until cold/hot cache, fingerprint invalidation, concurrent open, corruption, and cleanup acceptance is complete. Numba is not a dependency.

For chart releases, record the production-build shared and backtest-detail first-load sizes and verify that the Lightweight Charts chunk is separate. Use fixed fixtures for 1,500/5,000 equity points, 200 events, and 500 candles/100 markers; in one production Chromium, measure five runs and require median data-ready-to-chart-ready time at or below 100 ms, average pan/zoom at or above 55 FPS, and no chart main-thread task longer than 50 ms.
