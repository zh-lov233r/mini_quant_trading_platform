# Support and Resistance Strategy

[中文](support-resistance-strategy.zh-CN.md) | [Documentation index](README.md)

`support_resistance` is an engine-ready, long-only daily strategy. Backtests, research trials, and paper-signal generation share the same causal confirmed-Pivot plus ATR-clustering detector. Registering or creating the strategy does not create an allocation, activate a portfolio, enable the scheduler, or submit an order.

## Timing and Price Semantics

Session T evaluates only zones frozen after session T-1. The T close is then appended, a Pivot becomes eligible only after its configured right-side bars are complete, and the resulting zone state first becomes visible on T+1. This prevents the current or a future candle from confirming a zone used earlier.

Signals use forward-adjusted OHLC when available and fall back to unadjusted OHLC. Volume, ATR14, and ADV20 come from the matching daily snapshot. A signal generated at the T close fills at the next valid session open. Existing split handling, deterministic symbol/date ordering, and long-only behavior remain unchanged. Lifecycle charts request the same adjusted price semantics.

## Default Detector

- Pivot confirmation: 3 bars left and 3 bars right.
- Detection window: 120 trading sessions.
- Cluster radius: 0.75 ATR; zone half-width: 0.5 ATR.
- Minimum effective Pivots: 2; decay half-life: 60 sessions.
- Active capacity: at most 5 support and 5 resistance zones per symbol.
- Center: time-decayed, Pivot-price weighted median.
- Continuous residence inside a zone counts as one touch.
- A confirmed breakout followed by a successful retest converts resistance to support. A support close below its lower bound converts it to potential resistance. Zones expire when their effective Pivot membership falls below the configured minimum or leaves the window.

## Entry, Scoring, and Exit

All three modes default to enabled, and validation requires at least one:

- `support_bounce`: the prior close is above support, the new bar enters it, and the close recovers above the upper bound plus 0.25 ATR.
- `resistance_breakout`: the close exceeds the upper bound plus 0.5 ATR and volume is at least 1.5 times ADV20.
- `breakout_retest`: within 10 sessions of a breakout, price retests the original resistance, closes above its upper bound, and volume is at most 0.8 times breakout volume.

Every matching mode is retained as a run event. Only one BUY is emitted: highest expanding Beta posterior, then the stable tie order `breakout_retest`, `support_bounce`, `resistance_breakout`. A mode starts at 0.5. Only outcomes resolved before the current session enter its posterior. Success means the 3 ATR target precedes the 1.5 ATR stop inside 20 sessions; neither boundary is censored, and a same-session hit of both boundaries is a loss.

The entry signal freezes the selected zone, all candidate modes, bounds, ATR, target, stop, posterior counts, and score in `Signal.features.support_resistance`. The stop is the strictest long stop among the frozen zone lower bound, 1.5 ATR, and 8% maximum loss. The target is the nearest overhead resistance; entries below 1.5 expected reward/risk are skipped. With no overhead resistance, the target is 3 ATR. Maximum holding time is 40 trading sessions.

## Sparse Persistence and Cache Invalidation

The system does not store a full daily market-wide snapshot. It stores a new zone version only when Pivot membership, role, or status changes, plus run-specific events actually observed by a backtest or paper-signal run:

- `support_resistance_materializations`: immutable cache identity and build status.
- `support_resistance_zone_versions`: sparse zone timelines.
- `support_resistance_run_materializations`: run-to-cache audit link.
- `support_resistance_run_events`: touches, breakouts, retests, candidates, selections, score outcomes, transitions, and invalidations.

The identity contains algorithm version, normalized detector parameters, price semantics, universe hash, coverage range, and a source-data fingerprint. Detector identity includes the Pivot/ATR settings and breakout/retest thresholds that can change zone roles, plus an internal implementation revision so a causal or serialization fix cannot silently reuse output produced by older detector code. The current fingerprint deliberately uses global instrument/symbol-history revisions plus EOD and daily-feature row counts and latest revision timestamps. This may invalidate more caches than necessary, but prevents an identity, adjusted-price, or feature correction from silently reusing older output. The fingerprint is frozen before market rows are loaded and checked again before persistence; a mid-run change fails the run instead of labeling stale output with a new revision. A completed materialization that covers the requested window may be linked by another run. Failed or building materializations are never used as completed cache evidence.

For paper trading, cache materialization and run-event persistence complete before the order-execution loop begins. A build failure marks the strategy run failed and no new paper order is submitted. Registering the handler still does not create an allocation, activate a strategy or portfolio, or enable the scheduler.

Deleting a run cascades its links and run events but retains shared materializations and zone versions. Unreferenced caches are never deleted automatically. The first version materializes on demand and does not prefill ten years of all-market history.

Before a separately authorized prewarm or schema rollout, estimate source rows and sparse versions without writing anything:

```bash
.venv/bin/python backend/utils/plan_support_resistance_materialization.py \
  --symbols AAPL,MSFT --start-date 2024-01-01 --end-date 2025-12-31
```

The requested range must include any warm-up history you want represented. The command opens a read-only transaction and reports the symbols loaded, joined source rows, and detector-derived version/event estimates.

`backend/utils/plan_support_resistance_cache_cleanup.py` is read-only and reports exact unreferenced cache IDs. It intentionally has no apply mode; deletion needs separate explicit authorization.

The read-only audit endpoint is:

```text
GET /api/backtests/{run_id}/support-resistance?symbol=AAPL&zone_key=...&start_date=2025-01-01&end_date=2025-12-31
```

## Database Rollout and Recovery

This repository has no Alembic migration workflow. Do not rely on application startup to create these tables.

1. Resolve and record the exact target database and take a restorable backup.
2. Stop or drain support/resistance backtests and paper-signal runs; keep scheduler/order submission disabled.
3. Review, then explicitly apply `backend/utils/create_zzzzzz_support_resistance.sql` with `ON_ERROR_STOP`.
4. Run the read-only check:

   ```bash
   .venv/bin/python backend/utils/check_support_resistance_integrity.py --json
   ```

5. Deploy backend and frontend code. Do not activate allocations as part of schema rollout.

Rollback the application first. The additive tables may remain for audit and compatibility. If removal is explicitly authorized, back up the four tables and drop dependent run tables before shared zone/materialization tables. Never drop them merely to repair a stale cache. After an EOD, adjusted-price, or `daily_features` correction, rerun the requested backtest: the changed source fingerprint creates a new materialization while referenced old evidence remains intact.

## Validation

Run focused tests first, then the affected repository checks:

```bash
.venv/bin/python -m unittest backend.tests.test_support_resistance_strategy -v
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend && npm run lint && npm run build
```

These results are research evidence only, not profitability or live-trading safety claims.

For the fixed `pivot-atr-v1` effectiveness protocol, point-in-time liquid universe, sealed holdout, and report artifacts, see [Support/resistance effectiveness study](support-resistance-effectiveness.md).
