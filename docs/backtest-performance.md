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

Candidate promotion first creates an OOS/base-cost `full` verification job. The job rechecks the data fingerprint and compares numeric summary metrics at relative and absolute tolerance `1e-10`. A mismatch or fingerprint change records failed verification and blocks promotion. The verification run ID is written to candidate metrics and promoted strategy lineage.

## Durable worker

Apply the additive schema before routing traffic:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzzzz_backtest_performance.sql
make backtest-worker BACKTEST_WORKER_ARGS="--concurrency 2"
```

The API atomically creates a `strategy_runs` row and `backtest_jobs` row, then returns HTTP 201. Workers claim with `FOR UPDATE SKIP LOCKED`, heartbeat their lease, check cancellation each trade day, clear only the current run's incomplete details before retry, and mark linked research trials terminal when retries are exhausted. The Web application does not start this worker. The Make target explicitly disables the paper scheduler and paper-order submission.

## Incremental API

- `GET /api/backtests/{id}/summary`
- `GET /api/backtests/{id}/equity?max_points=1500`
- `GET /api/backtests/{id}/signals?limit=100&cursor=...&symbol=...`
- `GET /api/backtests/{id}/transactions?limit=100&cursor=...&symbol=...`
- `POST /api/backtests/{id}/cancel`

The legacy detail endpoint remains for one migration cycle. The list endpoint returns scalar metrics only. Signal ordering is `(ts, symbol, id)` ascending; transaction ordering is `(ts, id)` descending. Cursors are opaque.

## Database rollout and recovery

The project has no Alembic workflow. Before apply, resolve and record the exact target database, take a database-native backup, review current constraints/indexes, and run the SQL with `ON_ERROR_STOP`. The script only adds `backtest_jobs`, nullable `instrument_id` columns, foreign keys, and the queue claim/lease indexes. It deliberately does not add speculative signal/transaction cursor indexes. It does not rewrite historical detail; legacy runs are interpreted as `full`.

If rollout fails, stop new job producers and the backtest worker, then route new work to v1. Retain additive objects during application rollback. If schema removal is required later, first prove no application version references the objects and restore from backup on any data-integrity issue; do not use a destructive rollback during the normal release.

## Telemetry and acceptance

`summary_metrics.performance` records SQL loading, Python dataset construction, history maintenance, signals, execution, detail/summary persistence, total time, row counts, output counts, and peak RSS. Logs contain the same structured mapping. Run the 100/500/3,640-symbol and 1-year/5-year/full-history matrix before enabling v2 for manual traffic. Index or LAG query changes require real `EXPLAIN ANALYZE` evidence and at least 20% improvement; neither is assumed from synthetic tests.

The NumPy prepared-dataset/memmap layer is dependency-pinned but remains rollout-gated until cold/hot cache, fingerprint invalidation, concurrent open, corruption, and cleanup acceptance is complete. Numba is not a dependency.
