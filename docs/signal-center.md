# Signal Center

[中文](signal-center.zh-CN.md) · [Documentation index](README.md)

## Workflows

Open `/signals`, choose one market, a stock basket and multiple saved strategy instances. An explicit historical session is optional when a valid market completion marker exists. Scanning freezes the basket's instrument identities, strategy versions, normalized parameters and algorithm revisions. The page confirms that a manual scan was submitted, then leaves processing to the worker. It does not show scan history, progress, results, or a manual report action. A manual scan never creates a report or sends email automatically.

A daily task stores its name, market, basket, fixed strategy versions, report language, recipients and retention sessions. New tasks are disabled. Enabling authorizes automatic scanning, publication, email and configured retention. The operator switches below must also be enabled. Pausing prevents new scans; queued/running work finishes using its frozen configuration. Editing affects future runs. Recovery selects only the latest valid completed session; the unique task/session key prevents repeat reports after restarts or data revisions. No cron configuration or historical catch-up is provided.

Reports show successful empty results, incomplete coverage and failures separately. Scores rank observations only inside one strategy instance. The report page supports strategy/symbol/filter views, URL selection, previous/next navigation, and immutable chart evidence. Clicking anywhere on an observation row expands its chart; clicking the same row again collapses it. Windows are 60/120/250 sessions, initially 120; older data is bounded by the stored slice. Same-instrument strategy, language and layer changes preserve the chart view. A missing volume stays missing. Expired links return HTTP 410 instead of showing current quotes.

The Signal Center uses a flat section layout with lightweight dividers, searchable basket selection and status badges. Strategy choices reuse the strategy library card shell: category gradient, top accent strip, border, typography and category color, with a compact multi-select layout. Selected cards strengthen their own category color; unsupported strategies remain disabled. The rest of the page avoids nested card surfaces. Manual and scheduled forms have a visible active mode; empty lists explain the next step. Controls support keyboard focus and mobile touch sizes.

`GET /api/signal-scans/{identity}/instruments/{instrument_id}/chart` requires `strategy_run_id` and `signal_id`, accepts `limit` and `before`, and validates their scan/instrument ownership. It returns the same chart shape as report charts, with `scan_id` and a null `report_id`; report charts include both IDs. Unfinished scans return 409; purged scan content returns 410. Reads hold a shared scan lock against retention cleanup and never fall back to current market data. No database schema changes are required.

## Detection and input boundaries

The nine built-in strategies use `quant_kernel.observe_market` and the same native predicates/state machines as daily evaluation and backtests. `custom` is rejected. No fake positions, broker state or hypothetical fills are passed into detection. Crossover/explicit confirmation anchors are discrete events; other matching predicates are daily conditions. Execution behavior remains T-close signals and next-valid-session open fills in the existing execution engine.

Scans read PostgreSQL in batches under the market-data read lock. PreparedDataset v5 arrays are reused across strategy instances for each instrument. Pattern warm-up uses native configuration; SR cold replays all available input through the selected session, with independent state per instance. Optional SR market filters require actual benchmark close/SMA data. Required-field, OHLC geometry and as-of-session checks distinguish missing/invalid data from a valid zero-signal result. Forward-adjusted OHLC is preferred per field, with the existing unadjusted fallback explicitly recorded.

The scan manifest records frozen membership and actual input boundaries per instrument. Confirmation timestamps use trusted market-session close times; when those are unavailable, confirmation precision is the session date rather than the vendor bar timestamp. Only instruments with observations get chart assets: the latest **up to 1,000 actual available sessions**, indicators and native structural evidence. This is a reproducible report/chart slice, not a complete archival copy of every scan input. Historical basket membership is not inferred. Prior-report comparisons require the same instrument membership, strategy identities, versions, parameters and revisions, with complete coverage; otherwise the document states why comparison is unavailable.

## Readiness and calendars

`signal_data_ready` is a committed market/session completion record containing a version and coverage counts. Full US daily maintenance publishes it only after bars, corporate actions, adjustments, features and quality checks. Full A-share imports publish after adjustment/features and validation. Partial imports, skipped features/quality checks and failed flows cannot publish full-market readiness. Maintenance invalidates old markers before changes. The scheduler additionally checks the maintenance gate and each scan validates its selected scope. Waiting is never a successful empty scan.

`signal_market_sessions` persists dates, opening/closing timestamps, source and retrieval time. A-share imports use [Tushare trade_cal](https://tushare.pro/document/2?doc_id=26). US maintenance persists forward calendar coverage from [Massive upcoming holidays](https://massive.com/docs/rest/stocks/market-operations/market-holidays), including early closes; it never uses that forward endpoint to invent missing historical dates. Calendar errors leave reports retained until trusted coverage is available. Vendor calls are not made by Dashboard or chart reads.

## Publication, delivery and retention

A fixed ReportDocument is immutable after publication. HTML, JSON, complete CSV and PDF render from it. CSV includes filtered observations and structural evidence. Creating a new report creates a new identity. Retrying a failed export fills missing formats from the same document without rescanning. The body commits independently before email is queued. HTML, JSON, CSV and PDF use the separate `signal_report_render_jobs` queue. Report details expose export progress through `render_status`, `render_error` and `render_errors`. Rendering tries at most three times, then supports manual retries of missing formats. A render failure never unpublishes the body or rescans.

Report assets live outside quote/cache directories in `SIGNAL_REPORT_STORAGE_DIR`, shared by API and worker. JSON/gzip snapshots and exports are content-addressed, atomically written and checked before serving. Back up this directory together with the signal database tables.

SMTP uses STARTTLS with certificate validation and environment-only credentials. Mail contains a summary, report link and retention notice, not chart history. Reports and deliveries have separate states. Confirmed temporary rejection can retry up to three attempts. A timeout/disconnect after DATA begins is `unknown`; restart recovery preserves that ambiguity and never automatically resends. Only confirmed `failed` deliveries expose retry. Automated tests mock SMTP.

Scheduled reports default to **three complete market trading sessions**. Counting starts at the next session whose opening is strictly after publication; expiry is that third session's close. Monday after-close publication normally expires Thursday after close. Weekends, holidays, early closes, timezones and delayed publication use persisted calendar timestamps. Missing dates yield `calendar_incomplete` and delay cleanup. Manual reports have no default expiry.

Cleanup first publishes an expired tombstone, then removes exports and exclusive observations/snapshots in retryable steps. Shared data referenced by manual or other active reports remains. Running jobs, active delivery and locked asset reads are protected. Task identity, session, result counts, report expiry state and delivery summaries remain for audit/idempotency. Already downloaded bytes can finish streaming.

Maintenance must specify US or CN and invalidates readiness only for that market; partial imports preserve the other market. The global draining gate still blocks new scans during maintenance.

## Schema and deployment

When tables are missing, Signal endpoints return 503 with `signal_schema_missing`; `/readyz` returns 503 and missing table names. Dashboard preserves the other sections and explicitly marks Signal Center as not installed instead of showing successful empty results. Maintenance checks required tables before changing its gate.

There is no Alembic workflow. This feature adds nine `signal_*` tables; it does not reset or migrate user market data. **Preflight the exact target and obtain authorization before applying to an existing database.** Back up existing signal tables and asset storage before rollout. Stop API/worker/ingestion during schema deployment; retain market-data backups. Rollback means stop the new worker and restore a matching code/database/assets backup, not dropping user tables blindly.

```bash
# Read-only target/table counts. DATABASE_URL comes from the operator environment.
make signal-schema
# Review generated additive DDL without connecting.
make signal-schema SIGNAL_SCHEMA_ARGS="--sql"
# Only after target review and authorization:
make signal-schema SIGNAL_SCHEMA_ARGS="--apply"
.venv/bin/pip install --no-build-isolation --no-deps -e backend/native
# Start the dedicated worker (also supervised by make dev/dev-agent-safe/dev-agent-all).
make signal-worker
# Cleanup is dry-run unless --apply is supplied.
make signal-cleanup
```

Reviewed SQL: [`create_signal_center.sql`](../backend/utils/create_signal_center.sql). `make signal-cleanup SIGNAL_CLEANUP_ARGS="--apply"` performs the configured expiry deletion and requires the operator's intended cleanup scope. Keep automatic cleanup disabled during development.

| Environment variable | Default / purpose |
| --- | --- |
| `SIGNAL_SCAN_SCHEDULER_ENABLED` | `false`; allow ready-session task scheduling |
| `SIGNAL_REPORT_DELIVERY_ENABLED` | `false`; allow SMTP, also required when enabling a task |
| `SIGNAL_REPORT_RETENTION_ENABLED` | `false`; allow worker cleanup |
| `SIGNAL_REPORT_STORAGE_DIR` | repository `data/signal-reports`; persistent shared directory |
| `SIGNAL_REPORT_PUBLIC_URL` | `http://localhost:3000`; externally reachable report base URL |
| `SIGNAL_SMTP_HOST`, `SIGNAL_SMTP_FROM` | required for email |
| `SIGNAL_SMTP_PORT` | `587`, STARTTLS |
| `SIGNAL_SMTP_USER`, `SIGNAL_SMTP_PASSWORD` | optional server authentication, environment only |

Docker runs `signal-worker` using the same persistent `/app/data/signal-reports` mount as the backend. Local Make supervision restarts the worker after failure. PostgreSQL provides SKIP LOCKED claims, 120-second leases, 20-second heartbeats and at most three attempts. Each pass claims at most one scan, publication, email and export job, giving every stage a turn even when scans keep arriving. All four job stages recover independently. In safe development startup, disable Paper submission/scheduling and all three automatic Signal switches; manual scan/report jobs still execute. No Redis or Celery is needed.

## API and validation

[OpenAPI](../apps/openapi.yaml) is authoritative. Routes cover `/api/signal-scan-plans` (create/list/update/enable), `/api/signal-scans` (enqueue/status/observations/manual report) and `/api/signal-reports` (list/detail/observations/chart/export/render retry/delivery/retry). Enqueue calls accept `Idempotency-Key`. Observation paging uses `limit`, `offset`, `strategy_run_id`, `instrument_id`, `q`, `passes=true|false|all`, and `group=strategy|instrument`. Chart queries require the report, instrument, strategy subtask and signal to belong together.

`GET /api/dashboard/overview` adds `signal_reports`: latest five published, unexpired summaries, delivery counts and bounded aggregate state. Waiting data, failed/incomplete scans, report failures and mail exceptions also appear in existing alerts. Dashboard reads never run scans, render, send mail, clean assets or load chart history.

```bash
PYTHONPATH=backend:. .venv/bin/python -m unittest backend.tests.test_signal_center
# Supply only a separately initialized disposable PostgreSQL test database:
SIGNAL_TEST_DATABASE_URL=postgresql+psycopg://127.0.0.1:55441/quant_signal_test \
  PYTHONPATH=backend:. .venv/bin/python -m unittest backend.tests.test_signal_center_postgres
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend
npm test
npm run lint
NEXT_DIST_DIR=.next-signal-center-build npm run build
```

The PostgreSQL test expects the current base schema plus market bar/features tables and writes synthetic data. Do not point it at an existing user database. Browser acceptance should verify manual submission feedback without scan-history requests, strategy-card category/selection states, report deep links, fast selection, zoom/layer/language preservation, mobile/keyboard use and expired links with mail/trading/cleanup disabled.
