# Support and Resistance Strategy

[中文](support-resistance-strategy.zh-CN.md) | [Documentation index](README.md)

`support_resistance` is an engine-ready, long-only daily strategy. New execution uses the causal confirmed-Pivot, sloped-ATR-zone, four-regime detector `pivot-slope-regime-v3`. Legacy `pivot-slope-atr-v2` definitions, backtests, and materializations remain readable for audit but cannot start new execution. Registering or creating the strategy does not create an allocation, activate a portfolio, enable the scheduler, or submit an order.

The shared C++ kernel is the only executable implementation. Its descriptor owns defaults, validation, feature/history declarations, and the algorithm revision; native state objects own Pivot membership, zones, regimes, pending outcomes, posterior evidence, entry channels, signals, exits, and audit events. Python only loads and fingerprints data, locks/queries cache tables, adapts typed results for persistence, and performs separately authorized Paper broker operations. Backtests call native `run_backtest`; Paper converts bounded history to an in-memory PreparedDataset v3 and calls `evaluate_day(dataset_day, strategy, portfolio_state)`.

## Timing and Price Semantics

Session T evaluates only zones frozen after session T-1. The T close is then appended, a Pivot becomes eligible only after its configured right-side bars are complete, and the resulting zone state first becomes visible on T+1. This prevents the current or a future candle from confirming a zone used earlier.

Signals use forward-adjusted OHLC when available and fall back to unadjusted OHLC. Volume, ATR14, and ADV20 come from the matching daily snapshot. A signal generated at the T close fills at the next valid session open. Existing split handling, deterministic symbol/date ordering, and long-only behavior remain unchanged. Lifecycle charts request the same adjusted price semantics.

## Default Detector

- Pivot confirmation: 3 bars left and 3 bars right.
- Detection window: 120 trading sessions.
- High and low Pivots are fitted independently by a deterministic two-stage, recency-weighted Theil-Sen estimator.
- A line requires at least 3 inlier Pivots and eligible Pivot pairs span at least 10 sessions.
- First-stage inlier tolerance: 0.75 ATR; maximum absolute slope: 0.25 ATR per session.
- Zone half-width is frozen at 0.5 ATR when a version is created; decay half-life is 60 sessions.
- The highest-quality low-Pivot and high-Pivot boundary are retained independently, so role changes never discard the other side of the channel.
- Quality ordering is deterministic: inlier count, recency weight, ATR-normalized residual, distance to current price, then stable key. There is no horizontal fallback when a line cannot be fitted.
- Continuous residence inside a zone counts as one touch.
- A volume-confirmed breakout is evaluated against the T-1 frozen resistance geometry. After the T-day decision is frozen, any close above a resistance upper bound changes its display role to support; volume confirmation still controls the tradable `resistance_breakout` setup. A newly fitted line below the current close is likewise initialized as support instead of being mislabeled as overhead resistance. A later successful retest confirms the separate `breakout_retest` setup without delaying the role change. A support close below its event-day lower bound converts it to potential resistance. Zones expire when their effective Pivot membership falls below the configured minimum or leaves the window.
- A projected band that becomes non-finite, non-positive, or unordered expires before classification and candidate detection. Its immutable tombstone freezes the last valid geometry and is never projected beyond the invalidation session. Persistence rejects geometry outside the database numeric domain instead of widening precision or silently clipping prices.

## Four mutually exclusive regimes

Every visible market session belongs to exactly one of `uptrend`, `downtrend`, `range`, or `transition`. Classification on T uses only T-1 frozen boundaries, the T close, and Pivots confirmed by T; zones created on T first participate on T+1.

- Uptrend: high/low Pivot structure and both boundaries rise, with the lower boundary intact.
- Downtrend: high/low Pivot structure and both boundaries fall, with the upper boundary intact.
- Range: price remains inside ordered boundaries and the aligned structure is flat, contracting, or expanding.
- Transition: a boundary or Pivot evidence is missing, boundaries are misordered, price leaves the structure, directions conflict, or a boundary is broken.

Direction tolerance is derived from the existing ATR half-width and minimum line span; v3 adds no regime parameter. The first market session is written as `transition` when evidence is insufficient, and a version is appended only when the state changes. Reconstructed time intervals therefore have exact, gap-free, non-overlapping coverage. This constraint applies to time regimes, not to overlap between support/resistance price bands.

## Entry, Scoring, and Exit

All three modes default to enabled, and validation requires at least one:

- `support_bounce`: the prior close is above support, the new bar enters it, and the close recovers above the upper bound plus 0.25 ATR.
- `resistance_breakout`: the close exceeds the upper bound plus 0.5 ATR and volume is at least 1.5 times ADV20; the candidate is audit-only and never directly trades.
- `breakout_retest`: within 10 sessions of a breakout, price retests that session's projected former-resistance bounds, closes above its upper bound, and volume is at most 0.8 times breakout volume.

Every matching mode is retained as a candidate event. Uptrend admits `support_bounce` and `breakout_retest`; range admits only `support_bounce`; downtrend and transition admit no BUY. `resistance_breakout` is always rejected as `direct_breakout_audit_only`. Rejected candidates retain their reason and classification evidence. The expanding Beta posterior is statistical setup evidence only and does not rank candidates.

Only outcomes resolved before T enter Beta evidence. Success means the 3 ATR target precedes the 1.5 ATR stop inside 20 sessions; neither boundary is censored, and a same-session hit of both boundaries is a loss.

The entry signal freezes the selected zone, regime and classification evidence, slope, anchor session, Pivot evidence, all candidate modes, event-day bounds, ATR, target, stop, posterior counts, and signal strength in `Signal.features.support_resistance`. The stop is the strictest long stop among the frozen zone lower bound, 1.5 ATR, and 8% maximum loss. The target is the nearest overhead resistance; entries below 1.5 expected reward/risk are skipped. With no overhead resistance, the target is 3 ATR. Exit priority is stop, target, confirmed downtrend, then 40-session maximum holding. Transition does not force an exit. T-close exits still fill at the next valid session open.

Every BUY must also be inside a role-based inner-edge channel. The nearest active support below the close and nearest active resistance above it must satisfy `support.upper < resistance.lower`; the inclusive entry interval is `support.upper <= price <= resistance.lower`. The signal freezes both zone snapshots, keys, inner edges, slopes, and reason. Backtests project those edges by one session and validate the slippage-adjusted next-valid-session (T+1) open fill; rejection records `execution_rejection` without changing cash, positions, or transactions. SELL, stops, and liquidation never use this gate.

## Sparse Persistence and Cache Invalidation

The system does not store a full daily market-wide snapshot. It stores a new zone version only when Pivot membership, role, or status changes, plus run-specific events actually observed by a backtest or paper-signal run:

- `support_resistance_materializations`: immutable cache identity and build status.
- `support_resistance_zone_versions`: sparse zone timelines.
- `support_resistance_regime_versions`: append-only regime starts with boundary keys, directions, reason code, and full evidence.
- `support_resistance_run_materializations`: run-to-cache audit link.
- `support_resistance_run_events`: touches, breakouts, retests, candidates, selections, channel starts/ends, signal/fill rejections, score outcomes, transitions, and invalidations.

The identity includes the v3 algorithm, detector revision, regime-logic revision, normalized detector parameters, price semantics, universe hash, coverage range, and source-data fingerprint, so it cannot reuse a v2 materialization. Coverage must match exactly to avoid changing projected prices through shifted session ordinals. The fingerprint is frozen before reading market rows and checked again before persistence. A mid-run data change or regime-integrity failure fails the entire build. Zone and regime versions are immutable; future data may only append a version or produce a new materialization.

Before the first detail write, persistence validates all typed column lengths/order, enum and JSON values, stable instrument references, finite numeric/database bounds, exact regime timelines, and projected zone/event geometry. PostgreSQL writes zone versions, regime versions, and run events with the current transaction's psycopg3 `COPY FROM STDIN` connection in 5,000-row batches, checking cancellation before every batch. No batch commits independently; any validation, COPY, cancellation, or materialization failure rolls back the entire run result.

For paper trading, cache materialization and run-event persistence complete first. A support/resistance BUY is stored as `paper_execution=pending`; at the next broker session open, a current-session ask must be inside the projected channel before a regular-hours day limit order is submitted with the resistance inner edge as its cap. The remainder is cancelled when the quote leaves the channel or at 09:35 New York time. A fill below the support inner edge records `channel_fill_violation`, cancels the remainder, and blocks adds until the position is flat, without automatic liquidation. SELL remains first and ungated. Configuration uses `ALPACA_DATA_BASE_URL`, real-time `ALPACA_DATA_FEED=iex|sip`, `PAPER_TRADING_OPEN_QUOTE_MAX_AGE_SECONDS` (default 15), and `PAPER_TRADING_OPEN_ENTRY_CUTOFF_NY` (default `09:35`); `submit_orders=false` creates no actionable pending intent. A build failure marks the strategy run failed and no new paper order is submitted.

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
GET /api/backtests/{run_id}/support-resistance?symbol=AAPL&start_date=2025-01-01&end_date=2025-12-31
```

Each zone version includes query-clipped `geometry`. `regime_intervals` returns every complete original closed interval intersecting the query window, with dates, session count, both boundary keys, reason, and evidence; legacy v2 runs return an empty array. The interval calendar is rebuilt from sessions that have both EOD bars and daily features, beginning at the first persisted regime state for that materialized symbol identity. Symbol filtering is exact and a zone-key filter matches either boundary.

The lifecycle chart draws regime backgrounds first (green uptrend, red downtrend, amber range, gray transition), then support/resistance bands, the cyan valid-entry channel, candles/volume, and signal/fill markers. Legacy results without channel events display an explicit notice and are never backfilled from current rules. Each interval has a start divider; wide intervals show regime plus session count, while hover/click exposes all evidence. The client revalidates exact coverage over visible market sessions inside the materialization coverage window. On overlap, gap, or duplicate coverage it suppresses all regime backgrounds and displays an integrity error instead of silently stacking them. Post-exit candles beyond the materialization end stay visible and intentionally remain unshaded.

## Database Rollout and Recovery

This repository has no Alembic migration workflow. Do not rely on application startup to create these tables.

1. Resolve and record the exact target database and take a restorable backup.
2. Stop or drain support/resistance backtests and paper-signal runs; keep scheduler/order submission disabled.
3. For a fresh database, review and apply `backend/utils/create_zzzzzz_support_resistance.sql`. For an existing v2 database, apply the additive `backend/utils/migrate_pivot_slope_regime_v3.sql` in one transaction. Both require `ON_ERROR_STOP`.
4. Run the read-only check:

   ```bash
   .venv/bin/python backend/utils/check_support_resistance_integrity.py --json
   ```

5. Deploy backend and frontend code. Do not activate allocations as part of schema rollout.

Rollback the application first; the added regime table may remain for audit. If removal is explicitly authorized, back it up and verify that no v3 materialization depends on it before dropping it. Never drop tables merely to repair a stale cache. After an EOD, adjusted-price, or `daily_features` correction, rerun the requested backtest: the changed source fingerprint creates a new materialization while referenced old evidence remains intact.

## Validation

Run focused tests first, then the affected repository checks:

```bash
.venv/bin/python -m unittest backend.tests.test_support_resistance_strategy -v
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend && npm run lint && npm run build
```

These results are research evidence only, not profitability or live-trading safety claims.

Earlier algorithm findings do not transfer to v3. For the independent `pivot-slope-regime-v3` protocol, point-in-time liquid universe, sealed holdout, and report artifacts, see [Support/resistance effectiveness study](support-resistance-effectiveness.md).
