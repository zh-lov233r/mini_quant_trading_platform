# AGENTS.md

This file defines repository-level working instructions for coding agents. Follow it for all changes in this repository unless a more specific `AGENTS.md` exists in a subdirectory.

## Project overview

This repository is a full-stack equity quant research and paper-trading system.

- `backend/`: FastAPI, SQLAlchemy 2.x, PostgreSQL, backtests, strategy execution, market-data maintenance, paper trading, and scheduling.
- `frontend/`: Next.js 15 Pages Router, React 18, TypeScript, Axios, and bilingual Chinese/English UI.
- `apps/openapi.yaml`: API specification and contract reference.
- `backend/tests/`: backend unit and service tests based on Python `unittest`.
- `backend/utils/`: schema creation, data backfill, reporting, repair, and integrity scripts.
- `data/` and `logs/`: local artifacts; do not treat generated or raw data as source code.

Read `README.zh-CN.md` or `README.md` before changing an unfamiliar workflow. For execution behavior, prefer the implementation and tests over README prose when they disagree, and report the inconsistency.

## Development lifecycle and compatibility policy

This repository is currently a fast-moving development and testing platform, not a production or long-term archival system.

- Prefer one current implementation over compatibility shims, parallel legacy engines, duplicated endpoints, or version-selection branches. When replacing an internal version, remove the obsolete path and update all in-repository consumers in the same coherent change unless the user explicitly requests a staged rollout.
- Backward compatibility with earlier local releases, historical backtest runs, and locally persisted paper-trading records is not required by default. Breaking API, schema, and data-model changes are acceptable when the backend, OpenAPI contract, frontend, tests, and maintained documentation are updated together.
- Historical backtest results and locally persisted paper-trading records are disposable test artifacts and may be recreated. Do not add migrations or compatibility layers solely to preserve those records.
- This policy does not itself authorize a destructive database operation. Before a reset, drop, truncate, or bulk delete, identify the exact local database, affected tables, and expected row impact, then obtain explicit user authorization and use a read-only preflight or dry-run where practical.
- This policy does not make raw/vendor market data, downloaded files, reports, credentials, or production-like databases disposable. Continue to apply the database and data-safety rules below to those assets.
- Alpaca paper-account orders, positions, buying power, and other broker-side state remain external side effects. Local paper-trading records being disposable never authorizes cancelling orders, closing positions, or otherwise mutating the broker account.
- Fast iteration does not relax quant correctness, deterministic execution, test coverage, API synchronization, bilingual documentation, or trading-safety requirements.

## Ponytail project mode

Apply Ponytail `full` mode to coding tasks in this repository: understand and trace the affected flow first, then stop at the first sufficient option—skip speculative work, reuse existing repository code, prefer the standard library or native platform features, reuse installed dependencies, and otherwise write the smallest coherent implementation.

- Ponytail does not override repository requirements for quant correctness, deterministic execution, end-to-end API synchronization, regression tests, bilingual maintained documentation, database safety, or broker-side safety.
- Never simplify away trust-boundary validation, data-loss prevention, security controls, accessibility basics, or behavior explicitly requested by the user.
- Avoid defensive programming for impossible internal states or data already validated at a trust boundary. Do not add redundant guards, fallback defaults, catch-all recovery, or compatibility branches; validate untrusted API, database JSON, market-data, broker, and file inputs at their boundaries, then fail fast on violated internal contracts.
- Keep tests proportional to behavior and risk. Add the smallest focused regression test that would catch the changed behavior; do not duplicate the same assertion across layers or test trivial pass-through code, framework/library behavior, or guarantees already enforced by static types. Expand coverage only for distinct branches, high-risk boundaries, or demonstrated regressions.
- Prefer deletion and direct code over speculative abstractions, compatibility-only layers, new dependencies, single-implementation interfaces, or configuration for values that do not vary.
- A Ponytail review or audit reports over-engineering separately from correctness, security, and performance findings; it does not apply suggested deletions unless the user asks for implementation.

## Important entry points

- Application wiring: `backend/src/main.py`
- Database configuration: `backend/src/core/db.py`
- ORM models: `backend/src/models/tables.py`
- Strategy definitions and signal logic: `backend/src/services/strategy_registry.py` and `backend/src/services/strategy_engine.py`
- Backtesting: `backend/src/services/backtest_engine.py` and `backend/src/api/backtests.py`
- Paper trading: `backend/src/services/paper_trading_service.py`
- Daily scheduler: `backend/src/services/paper_trading_scheduler.py`
- Frontend API clients: `frontend/src/api/`
- Frontend API types: `frontend/src/types/`
- Translations: `frontend/src/i18n/messages/`

## Environment and setup

- Use the repository virtual environment at `.venv/` when it exists.
- Install backend dependencies with `.venv/bin/pip install -r backend/requirements.txt`.
- Install frontend dependencies with `cd frontend && npm install`.
- The application reads environment variables from the repository `.env` where supported.
- Never print, commit, replace, or copy API keys, broker credentials, database passwords, or local `.env` contents.
- Do not silently change dependency versions or regenerate lockfiles unless the task requires it.

## Standard validation

Run the narrowest relevant checks during implementation, then run all checks affected by the final diff.

Backend tests, from the repository root:

```bash
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
```

Backend syntax check:

```bash
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
```

Frontend checks:

```bash
cd frontend
npm run lint
npm run build
```

Read-only market-data integrity check, when database-backed market-data behavior changes:

```bash
make check-data CHECK_DATA_ARGS="--strict --json"
```

Do not claim tests passed if they were skipped, unavailable, or failed because a service was missing. State exactly what ran and what prevented any remaining validation.

## Quant and backtest invariants

Correctness and reproducibility are more important than performance or UI convenience.

- Prevent look-ahead bias. A signal may only use information available at its signal timestamp.
- Preserve the current daily backtest timing model unless the task explicitly changes it: signals are generated from day-T data and fills occur on the next available session using day-(T+1) open data.
- Keep backtest and paper-trading strategy rules aligned. Shared signal behavior belongs in the strategy engine rather than duplicated in API or UI code.
- Treat transaction costs, slippage, commissions, partial fills, splits, reverse splits, stock dividends, missing sessions, and delisted/inactive instruments as correctness concerns.
- Use adjusted and unadjusted prices deliberately. Do not switch a price source without tracing the effect on signals, fills, positions, P&L, and benchmark curves.
- Preserve deterministic ordering for dates, symbols, signals, and fills.
- Normalize ticker symbols consistently and preserve instrument identity across symbol changes.
- Use timezone-aware datetimes. Persist event timestamps in UTC where the existing model does so; derive US trading dates using `America/New_York`.
- Do not weaken the daily-feature completeness gate used by the scheduler.
- Any change to a metric or execution rule must add or update a focused regression test with explicit expected values.

## Trading and external-side-effect safety

Alpaca paper orders still mutate broker-side account, order, position, and buying-power state.

- Never submit an order, enable automatic submission, activate a portfolio, cancel an order, or clear a broker position unless the user explicitly requests that external action.
- Starting the backend also starts the paper-trading scheduler. Its current code defaults both `PAPER_TRADING_SCHEDULER_ENABLED` and `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS` to `true`.
- Before starting the backend or Docker stack in an environment that may contain Alpaca credentials, explicitly set:

```env
PAPER_TRADING_SCHEDULER_ENABLED=false
PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false
```

- Prefer `submit_orders=false` for tests, examples, smoke checks, and manual API calls.
- Preserve scheduler idempotency for `portfolio + trade_date + trigger` and order idempotency/client-order IDs.
- Mock external Massive and Alpaca calls in automated tests. Tests must not depend on live APIs or mutate remote state.
- Never describe paper-trading results as evidence of live-trading safety or profitability.

## Database and data safety

- Treat production-like databases, `data/`, reports, and downloaded market files as user data.
- Do not run schema creation, backfills, repairs, wipes, or SQL that mutates data merely to inspect behavior.
- Use read-only queries and dry-run modes first. Resolve and report the exact target database, date range, symbols, and expected row impact before an apply mode.
- Never run `backend/utils/wipe_schemas.py`, destructive SQL, `docker compose down -v`, or an equivalent destructive command without explicit user authorization.
- Do not edit generated CSV reports or raw market-data artifacts by hand.
- Schema changes must keep ORM models, creation SQL, API schemas, and relevant documentation in sync. The repository does not currently have an Alembic migration workflow. For disposable local test records, prefer an explicitly authorized reset/recreate path over compatibility-only migrations; for any production-like database, call out rollout and backward-compatibility risks explicitly.
- Data repair and backfill operations should be resumable or idempotent and should expose dry-run behavior when practical.

## Backend conventions

- Keep FastAPI route handlers thin. Put reusable domain behavior in `backend/src/services/`.
- Use explicit SQLAlchemy session boundaries. On failures after a write begins, roll back before reusing the session.
- Preserve structured error behavior through the existing exception and API layers.
- Avoid broad `except Exception` blocks or silent `pass` unless cleanup or compatibility genuinely requires them; log or test the intended behavior.
- Add tests beside the existing `backend/tests/test_*.py` suite and keep them deterministic.
- Prefer small helpers with domain names over growing already-large service functions.
- If changing response models or route payloads, update `apps/openapi.yaml`, frontend types, frontend API clients, and consumers in the same task.

## Frontend conventions

- The frontend uses the Next.js Pages Router; do not introduce App Router patterns without an explicit migration task.
- Keep HTTP access in `frontend/src/api/` and shared response/request shapes in `frontend/src/types/`.
- Preserve both `zh-CN` and `en-US` behavior. Add user-facing text through the existing i18n structure when practical, and update both locales together.
- Reuse existing shared components and visual language before adding new dependencies or a second styling system.
- Keep financial calculations in testable pure functions rather than embedding new calculation logic directly in JSX.
- Large pages should be split incrementally by cohesive feature. Preserve behavior first; avoid mixing a major refactor with metric or trading-rule changes.
- For `frontend/src/pages/backtests/[runId].tsx`, prefer extracting chart utilities, P&L/lifecycle calculations, and cohesive panels into focused modules rather than adding more page-local helpers.
- Maintain loading, empty, error, and partial-data states for API-driven views.
- Run both lint and production build after frontend changes; TypeScript success alone is not sufficient.

## API contract discipline

When changing an API flow, trace the complete path:

```text
database/model -> service -> FastAPI schema/route -> apps/openapi.yaml
-> frontend type -> frontend API client -> page/component
```

- Do not fix a contract mismatch with unchecked casts or `any` unless the boundary is genuinely untyped and the reason is documented.
- Keep nullable and optional fields distinct.
- Do not preserve backward compatibility solely for earlier local test versions. Make breaking changes coherently across every in-repository consumer; add a compatibility path only when the user explicitly requests one or a currently supported external consumer requires it.

## Documentation impact checklist

Every new feature must update at least one applicable maintained document in the same change. A dated delivery report, test-run record, or historical design note does not satisfy this requirement.

Assess documentation impact before implementation and again before handoff:

- User-visible behavior, pages, workflows, or operator steps: update the root README or the relevant feature guide.
- API routes, payloads, responses, errors, or schemas: update `apps/openapi.yaml`, examples, frontend types, and the relevant maintained guide.
- Environment variables, commands, dependencies, ports, or startup behavior: update setup or operations documentation.
- Database or persistence changes: document schema rollout, compatibility, target database, backup, recovery, and the absence of an Alembic workflow where relevant.
- Scheduler, market-data, feature, backtest, metric, or trading semantics: document timing, adjusted/unadjusted data choices, safety boundaries, and external side effects.

Maintained documentation is bilingual. English uses `<topic>.md`; Chinese uses `<topic>.zh-CN.md`, with `README.md` and `README.zh-CN.md` at the root. Update both languages in the same change and keep their structure, examples, commands, and cross-links equivalent.

When adding or retiring a maintained guide, update both `docs/README.md` and `docs/README.zh-CN.md`. Do not add dated reports or superseded designs to the maintained indexes.

A pure internal refactor may have no documentation diff only when behavior, contracts, configuration, operations, and user workflows are unchanged. The final handoff must explicitly state that determination and why. Before finishing documentation work, verify relative links, reciprocal language links, commands, routes, configuration names, and structured examples against repository sources of truth.

## Scope and change discipline

- Inspect related code and tests before editing.
- Preserve unrelated user changes and do not reformat unrelated files.
- Prefer the smallest coherent end-to-end change that satisfies the request.
- Do not combine behavior changes, broad refactors, dependency upgrades, and formatting cleanup in one patch unless they are inseparable.
- Update documentation when commands, environment variables, operational behavior, or API contracts change.
- Add a regression test for every bug fix when the behavior can be tested locally.

## Definition of done

A task is complete only when:

1. The requested behavior is implemented across every affected layer.
2. Relevant quant, timing, data, and broker-side invariants are preserved.
3. Focused tests were added or updated for changed behavior.
4. Relevant validation commands pass.
5. API specifications, frontend types, translations, and documentation are synchronized where applicable.
6. Documentation impact was assessed; maintained English and Chinese guides and indexes are synchronized, and relative links were checked.
7. The final handoff states what changed, what was verified, any remaining risk or unverified dependency, and the documentation impact (including a reason when no documentation changed).
