# Support and Resistance Strategy

[中文](support-resistance-strategy.zh-CN.md) | [Documentation index](README.md)

`support_resistance` is an engine-ready, long-only daily strategy. New execution uses the causal confirmed-Pivot, sloped-ATR-zone, four-regime detector `pivot-slope-regime-v3`. Legacy `pivot-slope-atr-v2` definitions, backtests, and materializations remain readable for audit but cannot start new execution. Registering or creating the strategy does not create an allocation, activate a portfolio, enable the scheduler, or submit an order.

The shared C++ kernel is the only executable implementation. Its descriptor owns defaults, validation, feature/history declarations, and the algorithm revision; native state objects own Pivot membership, zones, regimes, pending outcomes, posterior evidence, entry channels, signals, exits, and audit events. Python only loads and fingerprints data, locks/queries cache tables, adapts typed results for persistence, and performs separately authorized Paper broker operations. Backtests call native `run_backtest`; Paper converts bounded history to an in-memory PreparedDataset v4 and calls `evaluate_day(dataset_day, strategy, portfolio_state)`.

## Timing and Price Semantics

Session T evaluates only zones frozen after session T-1. The T close is then appended, a Pivot becomes eligible only after its configured right-side bars are complete, and the resulting zone state first becomes visible on T+1. This prevents the current or a future candle from confirming a zone used earlier.

Signals use forward-adjusted OHLC when available and fall back to unadjusted OHLC. Volume, ATR14, and ADV20 come from the matching daily snapshot. A signal generated at the T close fills at the next valid session open. Existing split handling, deterministic symbol/date ordering, and long-only behavior remain unchanged. Lifecycle charts request the same adjusted price semantics.

## Default Detector

- Confirm Pivots with 3 bars on either side in a 120-session window. Extremes within `pivot_tolerance_atr=0.05` of the candidate's ATR are ties; select the earliest bar in the confirmation window. No future bar beyond the configured right window is read.
- Seed lines from eligible high/high or low/low Pivot pairs, then reuse the deterministic two-stage weighted Theil-Sen fit. Each line needs 3 inliers and a 10-session pair span. Deduplicate membership and overlapping fits of the same kind, then retain up to `max_zones_per_kind=3` (configurable 1–5).
- Order by inlier count, mean recency weight, ATR-normalized residual, current-price distance, and stable Pivot keys. The decay half-life is 60 sessions. There is no horizontal fallback.
- Half-width is the maximum of `zone_half_width_atr × current ATR` (default 0.5) and the largest absolute residual of the fitted members. This contains every defining Pivot at its own session. Geometry stays anchored while membership is unchanged; rebase when current ATR leaves `[0.5, 2] × anchor ATR`. Rebasing appends a sparse version instead of rewriting history.
- ATR denominators have distinct purposes: each Pivot's ATR normalizes inlier tolerance (default 0.75); weighted-median member ATR limits slope (default 0.25 ATR/session); current ATR sets the minimum width. Residual width is in price units. These are intentional, explicit definitions.
- `pivot_count` counts fitted members. `touch_count` starts at zero and increments only on an observed transition from outside to intersecting the frozen band; continuous residence counts once. A touch is an intersection, not proof that support held.
- Recompute display role from the close relative to the projected center after each decision. Setup detection and channel/target geometry do not depend on this label. Volume-confirmed crossings still create breakout records when the breakout audit switch is off, so retests remain detectable.
- Invalid, non-positive, unordered, or non-finite projections expire before classification. Preserve the last valid tombstone geometry and database numeric validation.

## Four mutually exclusive regimes

Every visible session belongs to exactly one of `uptrend`, `downtrend`, `range`, or `transition`. T uses T-1 frozen boundaries and already confirmed Pivots. Select the best high/low boundary by quality. Structure direction uses the median pairwise ATR-normalized price change among the latest four confirmed swings of each kind, independently of line membership; record all contributing keys.

Uptrend requires rising boundaries and higher high/low structures with an intact lower boundary. Downtrend requires all four directions to fall with an intact upper boundary. Ordered flat, contracting, or expanding structures containing price are range; missing, broken, or conflicting evidence is transition. The four-direction agreement requirement remains unchanged; a high transition share alone does not justify relaxing a trading rule. Regime intervals remain append-only, gap-free, and non-overlapping. Classification runs daily, but signal `regime_evidence` cites the interval-start evidence dated by `evidence_trade_date`, keeping fresh and cached signals identical. The entry channel separately records current-day geometry.

## Entry, Scoring, and Exit

The three detection switches default on, but at least **bounce or retest** must be enabled:

- `support_bounce`: previous close above the band, a new intersection, and recovery at least 0.25 ATR above its upper edge.
- `resistance_breakout`: crossing above the frozen upper edge plus 0.5 ATR with at least 1.5 ADV20 volume. This switch enables audit candidates only; no direct breakout BUY.
- `breakout_retest`: within 10 sessions of a confirmed crossing, retest the projected band, hold its upper edge, and use at most 0.8 of breakout volume.

Uptrend permits bounce and retest; range permits bounce; downtrend/transition prohibit entries. Store all candidate reasons. Choose the strongest eligible candidate deterministically. Strength model v2 weights reward/risk 25%, Pivot count 15%, observed touches 10%, fitting quality 15%, proximity to support 20%, and volume 15%. After confirmation, closer entries score higher. Breakouts use confirmation instead of proximity; retests reward volume contraction. The minimum strength remains 50. These fixed weights are engineering defaults, not fitted evidence of profitability.

Channel selection is geometric: the nearest active upper edge below the close and lower edge above it define the inclusive interval, regardless of role or Pivot kind. Require `support.upper < resistance.lower`. Freeze both bands and project one session for the slippage-adjusted next-valid-session open check. Never widen the upper edge automatically. Rejected opens create `execution_rejection`; cash and positions stay unchanged, and SELL is ungated.

The initial stop is the strictest of zone lower edge, 1.5 ATR, and the 8% close-stop reference (`max_loss_pct` remains the configuration name). The nearest overhead band supplies the target regardless of role; no overhead uses 3 ATR. Candidate events include `overhead_count` and `target_source`. Initial gross reward/risk must be at least 1.5; actual entry repeats the gate **after entry/exit commissions and exit slippage** at the next open. Signal strength is a gross setup measure; the execution gate uses actual modeled costs.

`risk_per_trade_pct=0.005` budgets planned stop loss at 0.5% of equity, including both modeled commissions. Quantity is bounded by this budget, available cash, and `position_size_pct` as the maximum nominal holding. SELL-first/shared-cash execution and deterministic ranking remain serial. Gap losses can exceed this planned budget; neither the 8% stop reference nor risk sizing is a loss guarantee.

Project the frozen support lower edge through subsequent observed sessions; the stop never loosens below its initial reference. After a **prior close** reaches `break_even_at_r=1` times initial risk, lift the stop to entry cost. A stop signal blocks that zone for `stop_cooldown_sessions=5` following sessions (0 disables the extra cooldown). Paper reloads prior stop signals only from the same strategy/portfolio and only up to the requested date. Exit priority remains close-stop, target, confirmed downtrend, then 40-session maximum holding; exits fill at the next valid session open.

The Beta evidence remains descriptive and never ranks/orders trades. Track at most one active eligible episode per instrument/setup; use the next open and channel/RR check, the same close-based stop/target/downtrend/holding rules, and the following open for exit. Timeouts count as non-success: `(wins + 1) / (wins + losses + censored + 2)`. Current-day outcomes become visible only on the next day. Unfilled entries do not count. This gross hypothetical setup statistic excludes portfolio cash and position-slot competition and actual broker fees, and is **not** realized portfolio performance or a calibrated confidence interval. The independent `score_outcome_window`, `score_target_atr`, and `score_stop_atr` settings were removed.

## Optional Market Filter

Disabled by default. `market_filter_enabled=true` requires `market_filter_symbol` (default `SPY`, configurable to a stored A-share index). New entries require that signal-day adjusted close be at or above the mean of its latest 200 observed closes. Missing same-day data or insufficient history blocks BUY; no future data or forward-filled benchmark is used. Existing positions retain their exit rules.

The strategy does not load industry classifications or cap holdings by industry; missing SIC never blocks entries. Security-master SIC data and stock-basket industry screening remain independent. Maximum positions, per-symbol notional caps, and per-trade risk budgets still apply, but do not control industry concentration.

Backtest and Paper signal runs freeze market context in `config_snapshot.support_risk_context`; the native kernel owns the gates and sizing. Paper uses zero modeled commission and sizes at a limit capped by both the channel and minimum reward/risk, so a better quote cannot authorize a larger worst-case loss. It does not predict broker fees or guarantee gap protection.

## Read-only Review and Revision Rollout

Detector revision **12**, regime revision **3**, and strength v2 invalidate old cache identities without changing database tables. No migration or reset is required for this revision. Recreate a strategy from the current catalog, or remove retired scoring fields and `risk.max_industry_positions` from its parameters; start a fresh effectiveness study under the two tradable modes. Old results remain audit artifacts and are not evidence for the new rules. Do not resume an old three-mode study under the revised protocol.

Revision 12 validates the final `NUMERIC(24,10)`-rounded zone geometry before selecting or recording a new/refitted zone. A positive raw lower bound or ATR that rounds to zero is rejected; projection and persistence validation remain in force, without clamping prices or widening database precision. This prevents late materialization failures such as `zone geometry is non-positive or unordered`. Rebuild the native extension with `.venv/bin/pip install --no-build-isolation --no-deps -e backend/native`, then restart the backend and worker processes once active tasks have finished or been explicitly cancelled; loaded native modules do not hot-reload. Keep `PAPER_TRADING_SCHEDULER_ENABLED=false` and `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false` during backtest-only verification. Retry only the identified failed backtest after these checks, retaining its failure record; no database repair or market-data change is needed.

```bash
.venv/bin/python backend/utils/audit_support_resistance_run.py --run-id <run-uuid>
```

This opens a repeatable-read, read-only transaction and reports candidate/regime/channel/strength funnels, R/R and strength dispersion, regime-session coverage, rejection reasons and 1/5/10/20-session forward returns, and next-open stop gaps. Forward returns and session coverage use currently stored market data and may reflect later corrections; old events without `overhead_count` cannot establish the fallback-target rate. Small or overlapping rejection samples do not justify loosening the open channel. The materialization planner reports measured zone/regime/event counts and estimated 5,000-row COPY batches for the configured line count.

## Sparse Persistence and Cache Invalidation

The system does not store a full daily market-wide snapshot. It stores a new zone version only when Pivot membership, role, status, or volatility rebasing changes, plus run-specific events actually observed by a backtest or paper-signal run:

- `support_resistance_materializations`: immutable cache identity and build status.
- `support_resistance_zone_versions`: sparse zone timelines.
- `support_resistance_regime_versions`: append-only regime starts with boundary keys, directions, reason code, and full evidence.
- `support_resistance_run_materializations`: run-to-cache audit link.
- `support_resistance_run_events`: touches, breakouts, retests, candidates, selections, channel starts/ends, signal/fill rejections, score outcomes, transitions, and invalidations.

The structural identity includes the v3 algorithm, detector revision, regime-logic revision, normalized detector parameters, price semantics, universe hash, and coverage range, so it cannot reuse a v2 materialization. Coverage must match exactly to avoid changing projected prices through shifted session ordinals. Only a row with `invalidated_at IS NULL` is reusable. Before any supported market-data write, the exclusive maintenance window invalidates the current row; the next run may create a new current materialization with the same structural key while historical run links remain intact. A regime-integrity failure still fails the entire build. Zone and regime versions are immutable.

Before the first detail write, persistence validates all typed column lengths/order, enum and JSON values, stable instrument references, finite numeric/database bounds, exact regime timelines, and projected zone/event geometry. PostgreSQL writes zone versions, regime versions, and run events with the current transaction's psycopg3 `COPY FROM STDIN` connection in 5,000-row batches, checking cancellation before every batch. No batch commits independently; any validation, COPY, cancellation, or materialization failure rolls back the entire run result.

Detector state and sparse timelines are partitioned by stable `instrument_id`; `symbol` is display metadata only. The native backtest result carries that identity into persistence, while non-native callers may resolve a unique primary symbol-history interval for the requested coverage. If one ticker belongs to multiple instruments inside the same range, their histories remain independent instead of being merged or guessed from the current canonical ticker. Existing databases must rerun `backend/utils/migrate_pivot_slope_regime_v3.sql` in a separately authorized schema rollout so regime uniqueness is enforced by `instrument_id`; perform the documented read-only preflight and backup first.

For paper trading, cache materialization and run-event persistence complete first. A support/resistance BUY is stored as `paper_execution=pending`; at the next broker session open, a current-session ask must be inside the projected channel before a regular-hours day limit order is submitted with the lesser of the resistance inner edge and the reward/risk price cap. The remainder is cancelled when the quote leaves the channel or at 09:35 New York time. A fill below the support inner edge records `channel_fill_violation`, cancels the remainder, and blocks adds until the position is flat, without automatic liquidation. SELL remains first and ungated. Configuration uses `ALPACA_DATA_BASE_URL`, real-time `ALPACA_DATA_FEED=iex|sip`, `PAPER_TRADING_OPEN_QUOTE_MAX_AGE_SECONDS` (default 15), and `PAPER_TRADING_OPEN_ENTRY_CUTOFF_NY` (default `09:35`); `submit_orders=false` creates no actionable pending intent. A build failure marks the strategy run failed and no new paper order is submitted.

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

Rollback the application first; the added regime table may remain for audit. If removal is explicitly authorized, back it up and verify that no v3 materialization depends on it before dropping it. Never drop tables merely to repair a stale cache. After an EOD, adjusted-price, or `daily_features` correction through the maintenance pipeline, rerun the requested backtest: the invalidated historical materialization remains linked as evidence and a new current materialization is built.

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
