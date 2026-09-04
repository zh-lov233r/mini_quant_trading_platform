# Support and Resistance Strategy

[中文](support-resistance-strategy.zh-CN.md) | [Documentation index](README.md)

`support_resistance` is a long-only daily strategy implemented exclusively by the shared C++ kernel. Backtests and Paper use the same detector, signals and risk rules. Creating a strategy never enables scheduling or authorizes broker actions.

## Frozen zones and phases

- Confirm Pivots after 3 right-hand bars (3 left-hand bars by default). Extreme ties within 0.05 candidate ATR choose the earliest bar.
- Fit at least 2 confirmed Pivots over at least 10 sessions using weighted Theil–Sen. Candidate history is bounded by the 120-session detection window and the current phase's extremum dates. ATR and OHLC history are not reset.
- Rank candidates deterministically by member count, recency weight, residual, price distance and Pivot keys. Keep up to 3 zones per source kind (configurable 1–5); existing zones consume slots and cannot be replaced.
- Width is the larger of 0.5 current ATR and the largest member residual. After confirmation, membership, anchor, slope, width and role never change. Only projection along that slope continues. Rising prices do not turn resistance into support.
- All zones, including opposite roles, must remain disjoint. Reject a candidate whose band touches or crosses an existing band anywhere in their shared fitting/effective history. Do not move or narrow existing zones.
- Before each day's signal decisions, invalid geometry, projected touching/crossing, or a close strictly below support's lower edge / above resistance's upper edge ends the entire phase. Reasons are respectively `invalid_geometry`, `zone_conflict`, `close_break`.
- Equality is not a close break. A wick crossing the boundary then closing back is a touch, not a phase break. Touch episodes increment only when a bar enters an intersecting state.
- Break day T starts the new phase; every old zone ends on the preceding actual session. No old-zone BUY is allowed on T. New fits use only confirmed Pivots whose extremum dates are T or later. Insufficient evidence means waiting, not borrowing old members.

Phase identity is independent of the uptrend/downtrend/range/transition classifier. Missing structural evidence is transition. Classifier evidence uses only current-phase confirmed Pivots; frozen members remain available for audit after leaving the candidate lookback window.

## Signals, repeat trading and execution

A zone confirmed at the T close is first eligible for a signal on T+1. Signals use forward-adjusted OHLC where available, otherwise unadjusted OHLC. Fills remain at the next valid session open; SELL-first, shared cash, costs, corporate actions and deterministic sorting are unchanged.

Only `support_bounce` entries remain: previous close above a frozen support upper edge, current low touching it, and close recovering at least 0.25 ATR above it. The frozen support slope must be non-negative; a negative slope keeps the candidate in audit with `falling_support_zone` but cannot enter. Uptrend/range permit entry; downtrend/transition do not. Direct breakout and breakout-retest switches, triggers and consumers are removed; other strategies' retest rules are unchanged.

A stock may trade repeatedly in the same unchanged zone. After a prior position is closed, another qualifying bounce can enter again. There is no once-per-zone flag. Existing holdings, strength, risk, market filters and the zone-specific stop cooldown still apply.

Strength weights remain reward/risk 25%, member count 15%, observed touches 10%, fitting quality 15%, support proximity 20% and volume 15%; default minimum is 50. These are engineering defaults, not profitability evidence.

The nearest frozen support upper edge below close and resistance lower edge above close define the channel, with strictly ordered inner edges. At the next valid open, project the frozen channel and recheck slippage-adjusted entry price, costs, reward/risk, risk budget, cash and position limits. Never widen the channel. Rejected entries do not mutate cash or holdings.

Initial stop is the strictest of the frozen support lower edge, 1.5 ATR and the 8% close-stop reference. Nearest overhead resistance supplies the target (otherwise 3 ATR). Gross reward/risk defaults to 1.5 and execution repeats the gate after costs. Planned risk defaults to 0.5% of equity; nominal position cap defaults to 15%.

Phase end does not force liquidation. Existing positions retain entry-frozen stop/target and the existing close-stop, target, downtrend and maximum-holding exits. The stop never loosens; a prior close reaching 1R raises it to entry cost. A stop signal blocks re-entry to that zone for 5 subsequent sessions by default. Gap losses can exceed modeled risk.

Posterior outcomes are descriptive only, not BUY ranking or calibrated confidence. One hypothetical pending outcome per instrument/setup does not limit actual repeat trades.

## Lifecycle chart

Default SR view contains candles, volume, support/resistance bands, BUY/SELL signals and fills. No standing Pivot/touch/audit markers, cyan channels or regime backgrounds/legends.

A light dashed retrospective segment runs from the first fitting Pivot to confirmation; it was not tradable at those historical dates. The solid effective segment retains immutable geometry and stops at phase end. Both clip to the current window without changing zoom or joining phases. Invalid historical projection is unavailable, never fabricated.

Trade-linked zones are selected by persisted entry/exit evidence; “Show all zones” reveals other zones in the current window. Hover highlights one actual sloped band and its fixed members/recorded touches. Offscreen Pivot dates remain in details without invented chart coordinates. Gutter labels and leaders avoid candles and volume. Click pins details, blank click/Escape clears, and the shared workspace selector supports keyboard and touch. Nearest centerline wins edge ambiguity; the selector can switch zones. Hover does not fetch data or recreate the chart.

## Persistence and cache

Detector revision **14**, regime revision **4**, algorithm family `pivot-slope-regime-v3`. Old revisions cannot serve new calculations.

Audit schema v2 adds `support_resistance_materialization_events`. Immutable lifecycle events (`touch`, `invalidation`, `phase_ended`, `regime_transition`, `entry_channel_started`, and `entry_channel_ended`) are stored once with zone/regime rows; candidates, selections, rejections, execution decisions, and score outcomes remain run-scoped. Zone keys include phase identity. Store one active geometry and a terminal record; never rewrite fitting evidence. Old materializations remain linked to historical runs but are excluded from v2 cache lookup. Cache replay must merge shared and run events into the same stable API ordering as a cold calculation.

`GET /api/backtests/{run_id}/support-resistance` returns clipped `geometry` and nullable `formation_geometry`, immutable members/phase metadata and recorded events. Source bars are queried on actual instrument sessions.

The repository has no Alembic workflow. Apply `backend/utils/create_zzzzzz_support_resistance.sql` only after confirming the exact database, backing it up, and reviewing existing row counts; the application never runs this DDL automatically. Rebuild the native extension before restarting idle backend/workers:

```bash
.venv/bin/pip install --no-build-isolation --no-deps -e backend/native
```

Keep `PAPER_TRADING_SCHEDULER_ENABLED=false` and `PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false` during local backtest validation. Existing broker orders/positions are outside this workflow.

## Authorized old-result cleanup and recovery

The scoped cleanup target is local PostgreSQL `localhost:5432/hzy`, schema `public`. Before applying, freeze exact old run/materialization IDs and recheck statuses, references and row counts. Stop on active tasks or unexpected references; never cancel automatically or broaden the target.

Back up target rows, schema, dependency order, counts and checksums outside the repository; verify readability before deletion. In one controlled transaction, use terminal-run deletion (including explicit transactions) then remove only now-unreferenced frozen old materialization IDs and their zone/regime rows. Never truncate or reset the database.

Preserve strategies, baskets, raw data, daily features, other strategy results, Paper/live records, downloaded reports and broker state. New validation runs must not enter the frozen target list. Verify old IDs are absent/404 and retained objects unchanged.

Restore original IDs in dependency order from the retained backup. On any ID/reference conflict, stop and roll back; do not overwrite newer data. Schema definitions are recovery references, not permission to recreate/drop existing tables.

## Validation

```bash
PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_support_resistance_phases backend.tests.test_native_support_resistance backend.tests.test_support_resistance_strategy
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
.venv/bin/python backend/utils/check_support_resistance_integrity.py --json
cd frontend && npm test && npm run lint && npm run build
```

Use an isolated `NEXT_DIST_DIR` when a dev server owns the frontend build directory. Validate desktop hover, touch, keyboard, zoom and console against new-rule runs, not fabricated historical trades. See [effectiveness study](support-resistance-effectiveness.md) for research methodology; old findings do not validate these rules.
