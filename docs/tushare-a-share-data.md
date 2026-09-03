# Tushare A-Share Data

[中文](tushare-a-share-data.zh-CN.md)

This flow writes Tushare Shanghai, Shenzhen, and Beijing A-share identities, unadjusted daily bars, and adjustment factors into the existing PostgreSQL market-data tables. It then reuses the existing `daily_features` calculation and shared native nine-strategy backtest kernel. It is research/backtest-only: it does not start the backend or paper scheduler, create strategy allocations, or call Alpaca.

## Credentials and permissions

Keep the Tushare Pro token only in the root `.env`:

```env
TUSHARE_TOKEN=...
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
```

Git ignores `.env`. Never place the token in a command line, source file, log, or commit. Current Tushare documentation says `stock_basic` and `adj_factor` commonly require at least 2,000 points. If permission or rate limits stop a run, already committed dates remain intact; fix the cause and idempotently rerun the same range.

## Safe execution

First inspect the target database, current Tushare row counts, date range, and universe without network or writes:

```bash
make import-a-share A_SHARE_ARGS="plan --start-date 2016-01-01 --end-date 2026-09-02"
```

Then import explicitly:

```bash
make import-a-share A_SHARE_ARGS="apply --start-date 2016-01-01 --end-date 2026-09-02"
```

For a one-symbol smoke test or a narrow repair, repeat `--ts-code` as needed:

```bash
make import-a-share A_SHARE_ARGS="apply --start-date 2026-09-01 --end-date 2026-09-02 --ts-code 000001.SZ"
```

The importer iterates Shanghai Stock Exchange open dates and commits EOD data per session. After a network or permission failure, rerun the same command; do not delete history. Requests are spaced by at least 0.2 seconds by default, configurable with `--request-interval-seconds`. `--skip-features` is diagnostic only and leaves the imported range not ready for backtesting.

## Data semantics

- Symbols retain their Tushare suffix, such as `000001.SZ`, `600000.SH`, and `920000.BJ`. Exchanges are stored as `XSHE`, `XSHG`, and `XBSE`; currency is `CNY`; locale is `cn`.
- `daily.vol` is reported in board lots and is multiplied by 100 into shares. `daily.amount` is reported in thousands of CNY and is used to derive unadjusted VWAP.
- Each timestamp is 15:00 Asia/Shanghai on the trading date, converted to UTC. The existing `dt_ny` column has a legacy name, but this timestamp still maps to the same calendar date in New York, so the stored trading-date key matches Tushare `trade_date`.
- Forward adjustment follows Tushare's documented formula: `raw price × date factor / latest factor`; backward adjustment is `raw price × date factor`. Factor refreshes change the data fingerprint and invalidate the PreparedDataset cache.
- The importer rejects non-finite/non-positive prices, inverted OHLC, negative volume/amount, missing adjustment factors, and a response at the 6,000-row limit that may have been truncated.
- `backfill_adjusted_prices.py` excludes `vendor='tushare'`, preserving provider-supplied factors.

## Nine-strategy backtesting

Each completed import synchronizes the active `All A Shares (Tushare)` basket. In `/backtests`, choose any engine-ready strategy and select this basket to override the strategy universe. All nine types use the same native dataset and backtest entrypoint:

`trend`, `mean_reversion`, `momentum_breakout`, `island_reversal`, `double_bottom`, `head_shoulders_bottom`, `rounded_bottom`, `v_reversal`, and `support_resistance`.

Backtests preserve day-T close signals, next-valid-session open fills, SELL-first ordering, shared cash, and deterministic ranking. Clear the default `SPY` benchmark when a US comparison is not useful, or use an imported A-share symbol as a simple reference.

This change provides A-share data and strategy-execution compatibility, not a complete China-exchange microstructure simulator. Board-lot purchases, price-limit fill rejection, suspended-order queues, and stamp-duty differences are not automatically modeled. Commission and slippage inputs can provide conservative cost stress, but results are not evidence of live-trading safety or profitability.

## Verification

After import, verify equal Tushare EOD/feature counts, non-null adjusted fields, and the focused market-code regression across all nine strategies:

```bash
PYTHONPATH=backend .venv/bin/python -m unittest \
  backend.tests.test_import_tushare_a_share \
  backend.tests.test_native_nine_strategy_golden.NativeNineStrategyGoldenTests.test_all_nine_accept_a_share_symbols_and_exchanges
```
