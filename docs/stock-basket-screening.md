# Stock basket screening

[中文](stock-basket-screening.zh-CN.md) | [Documentation index](README.md)

## Editing

Open `/stock-baskets`, then Create or the bottom-left Edit button. There is no full-list ticker textarea. Search a local ticker/company name or add one ticker directly; unknown/unverified tickers are labelled and never trigger market-data downloads. Codes are uppercased and deduplicated. Selected stocks support code search, 20-row pagination, individual removal and current-page checkbox removal.

Screening supports current active common stocks from the local A-share and US masters. Choose a market before industry or cap filtering. Tushare industry labels and US SIC labels are independent taxonomies. Cap fields use **CNY 100 million** for A shares and **USD 100 million** for US stocks, with no FX conversion. Blank bounds mean no cap filter; both bounds are inclusive. Missing caps are excluded only when a bound is present; the missing count is computed after market/name/industry filtering, before cap bounds. Dates and retrieval times are shown; unknown dates stay unknown.

Add individually or resolve all matches and confirm the newly resolved count. This appends, never replaces existing members. Save commits the whole draft via the existing basket API; closing discards it. `All Common Stock` remains read-only. Membership is static, not a scheduled rule. Current industry/cap snapshots are **not** historical point-in-time universes and may cause selection/survivorship bias in retrospective research. No backtest timing, execution rule, strategy copy or broker state changes.

## API and storage

Strategy creation (`/strategies/new`, including clones) configures trading logic only and does not load or select a stock scope. Every new manual backtest must choose a saved basket; the backend rejects submissions without either that static basket or an explicit historical dynamic-universe policy. The run records the resolved membership, so later basket edits do not rewrite historical results. Existing strategy-level universe values remain readable as historical configuration but are not the default for new manual backtests.

- `GET /api/stock-screening/stocks`: `query`, `market=US|CN`, `industry`, `min_cap`, `max_cap`, `limit` (20 default, 100 maximum), `offset`. API cap bounds are base currency units, not hundreds of millions. Returns `items`, `total`, `missing_market_cap`, ordered by ticker and instrument ID.
- `GET /api/stock-screening/industries?market=CN`: sorted local industry labels.
- `POST /api/stock-screening/symbols`: read-only resolution of all symbols using the same filters (no pagination); no basket write or vendor call.

Example: `{"market":"CN","industry":"银行","min_cap":10000000000}` selects banks with at least CNY 10 billion. Invalid ranges and cap/industry filters without a market return 422. Missing schema returns 503 with an operator message; manual ticker editing still works. Search is debounced 300 ms; stale requests are aborted/ignored. Only current pages are mounted in the DOM, never thousands of hidden inputs or tags.

`instrument_market_caps` stores one latest snapshot per stable `instrument_id`: positive `NUMERIC(24,4)` amount, currency, source, nullable data date, retrieval timestamp and raw response. Basket `symbols` storage is unchanged. Massive `market_cap` uses the vendor's amount and currency; existing `vendor_payload.ticker_overview` can seed it using the original `sic_asof` retrieval time and an unknown data date. Tushare `daily_basic.total_mv` is in ten-thousand CNY and is multiplied by 10,000. Vendor ticker/FIGI, currency, positive finite amounts and requested dates are checked before writes. Historical/cached reruns cannot replace a newer snapshot; identical payload reruns do not update timestamps.

Sources: [Massive ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview), [Tushare daily_basic](https://tushare.pro/document/2?doc_id=32). Tushare currently requires daily_basic access (documented minimum 2,000 points); availability is not assumed. Missing data, permissions or failed requests preserve existing snapshots and report failures. No estimates are substituted.

## Manual rollout and refresh

No Alembic or startup auto-DDL is used. Confirm the exact host/database, candidate counts and backup path before schema application or enrichment. Commands below are examples from the repository root; replace dates with the intended completed market date. `plan` uses a read-only transaction, never contacts vendors and works before schema installation.

```bash
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py plan --market US --cached
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py plan --market CN --date 2026-09-02
```

After explicit approval for the resolved local database (example `hzy`), create a backup before applying the additive SQL:

```bash
pg_dump --host=localhost --dbname=hzy --schema-only --format=custom --file=stock-screening-schema-before.dump
psql --host=localhost --dbname=hzy --set=ON_ERROR_STOP=1 --file=backend/utils/create_instrument_market_caps.sql
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py apply --market US --cached
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py apply --market CN --date 2026-09-02
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py apply --market US --date 2026-09-02
```

The command reads `.env` without printing credentials. Live US refresh requires `MASSIVE_API_KEY`, CN requires `TUSHARE_TOKEN`. Cached US import needs no vendor credentials. Refresh writes only `instrument_market_caps`, serializes concurrent cap refreshes with a database advisory lock and rechecks master identity under a row lock. It never changes master data, raw market history, basket members, caches or schedulers. No automatic refresh is installed. Rerun the same approved command, optionally with `--symbols AAPL MSFT` or `--symbols 600000.SH`, to retry failures; successful rows survive partial failures. Zero CN rows for a non-trading/unavailable date fail without changing snapshots.

Before later refreshes, back up the snapshot table as well. Recovery is limited to this new table: restore its prior contents from the backup in a reviewed transaction, or leave an initially empty/new table unused and revert the UI/API deployment. Do not reset or restore unrelated market-data tables. Destructive recovery needs separate approval. If a backend restart is needed, explicitly set `PAPER_TRADING_SCHEDULER_ENABLED=false`, `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`, and `RESEARCH_WORKER_ENABLED=false` before startup.

Live US requests default to a 12-second interval (5/minute); use `--request-interval` only to match a verified account quota. Cached imports make no requests. Plan output includes validated reusable-cache counts; refresh reports progress every 100 candidates.

## Verification

Run `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_stock_screening backend.tests.test_stock_baskets_api`, plus frontend `npm test`, `npm run lint` and an isolated `NEXT_DIST_DIR` production build. `make check-data CHECK_DATA_ARGS="--strict --json"` remains read-only. Browser acceptance covers 5,555 symbols, description focus/typing, search, pagination, bulk confirmation, cancellation and save/reopen, keyboard/mobile layout and console errors.

For isolated browser acceptance, run `backend/tests/serve_stock_basket_fixture.py` with `PYTHONPATH=backend` using the repository Python. Build with `NEXT_PUBLIC_API_BASE_URL=http://localhost:18080 NEXT_DIST_DIR=.next-codex-basket-qa npm run build` from `frontend`, then serve with `NEXT_DIST_DIR=.next-codex-basket-qa npx next start -p 3103`. The fixture uses only disposable in-memory SQLite records; it has no real database, vendor calls, workers or broker routes. Stop both processes after testing.
