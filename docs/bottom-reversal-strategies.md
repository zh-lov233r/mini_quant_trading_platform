# Bottom reversal strategies

[简体中文](bottom-reversal-strategies.zh-CN.md)

Five daily long-only strategies share the current C++ pattern kernel, all at algorithm revision 2: `island_reversal`, `double_bottom`, `head_shoulders_bottom`, `rounded_bottom`, and `v_reversal`. Backtests and Paper daily evaluation share detection; new instances default to `draft`. New thresholds are testable initial quantifications, not research-validated optima. Rule correctness does not establish profitability.

## Data, timing, and cumulative positions

- Volume ratio is `volume / volume_sma_20`; rebound volume is the arithmetic mean of daily ratios. Candle body is `abs(close-open) / ATR14`. Platform/range width and first-to-last close drift use ATR14 at the window end. Missing required fields make the corresponding condition false.
- Preserve prepared-data price semantics (forward-adjusted preferred, existing unadjusted fallback), fill prices, costs, slippage, and split handling. Pivots become available only after all right-side confirmation bars; charts place extrema on their pivot dates and signals on confirmation dates.
- Breakouts require a **close strictly above key price × `(1 + breakout_buffer_pct)` with qualifying volume**. Islands use the real upward-gap threshold; low-volume retests do not themselves require high volume. Signals use day-T close; fills use the next valid session open. Missing next-open candidates follow the existing skip behavior.
- Cumulative defaults are `risk.stage_1_target_pct=0.20`, `stage_2_target_pct=0.50`, and `stage_3_target_pct=1.00`, with `0 < stage 1 < stage 2 < stage 3 = 1`. Target notional is `current portfolio equity × position_size_pct × current stage target`.
- Later stages can enter directly if all foundational conditions hold; highest stage wins for the same setup/day. Third and later rounded pullbacks and further V continuation signals only complete stage 2. No buy occurs at target. Later equal-stage signals may complete a cash-limited or partially filled target; stages never regress.
- SELL precedes BUY; additions to a setup do not consume new position slots. Preserve idempotent orders, individual fill lots, weighted costs, and generic stops/profit targets. Exits liquidate the entire position.

## Source requirements and quantitative definitions

| Strategy / source requirement | Executable definition and stages | Parameters and regressions |
|---|---|---|
| Island: late decline, long bearish predecessor, small-body island, high-volume bullish gap out | Stage 1 is a low-volume downward gap: preceding bearish body ≥0.5 ATR, gap-down bearish body ≤1 ATR. Other island bodies ≤0.5 ATR; alternating colors are optional. Stage 2 requires a real upward gap and bullish body ≥0.5 ATR with volume. Stage 3 is a low-volume gap-support retest. Required lookback decline cannot be replaced by being below SMA50. | `previous_body_atr_min`, `breakout_body_atr_min`, `exhaustion_body_atr_max`, `island_body_atr_max`; `test_island_*` covers colors, bodies, volume, real gaps, island gap fill, and failed support. |
| Double bottom: no lower second low, moderate rebound volume, right-side confirmation | Right low lies in `[left low, left low×1.03]`. Up-day share is measured after the left low through the neckline peak inclusive, default ≥60%; mean volume ratio 1.0–1.5. Stage 1 confirms the right low. Stage 2 must occur within `retest_window` after it, following a moderate-volume rebound; retest volume ≤maximum rebound daily volume×`retest_volume_ratio_max`. Stage 3 is a volume-confirmed close above the neckline and can skip the retest. | `bottom_tolerance_pct` is upward only; `rebound_up_day_ratio_min`, `rebound_volume_ratio_min/max`, `breakout_buffer_pct`; `test_double_bottom_*` covers rejecting 98/96.5, equal/higher lows, intraday-only breaks, volume, and timing. |
| Head and shoulders: left platform, low-volume head, platform recovery, low-volume right shoulder | Every stage rechecks decline, head depth/volume, and left platform. Platform: 5 consecutive pre-head bars containing the left shoulder, width ≤3 ATR and close drift ≤1 ATR; choose the latest ending match. Post-head rebound closes return inside the platform high/low interval and mean volume through its high is 1.0–1.5, then check the low-volume right shoulder and dynamic neckline. Stages: head candidate, right shoulder, neckline break. | `platform_bars`, `platform_range_atr_max`, `platform_drift_atr_max`, `rebound_volume_ratio_min/max`; `test_head_*` prevents later-stage prerequisite bypass and checks same-day highest stage. |
| Rounded bottom: volume-backed advances, higher low-volume pullbacks, exit on weakness | Retain the 80–240-session log-close quadratic fit. Each pullback needs an earlier rising close with qualifying volume in its rebound leg; successive qualified lows and corresponding rebound highs must rise. First pullback is stage 1, all later ones stage 2; at least two precede a volume-confirmed rim close at stage 3. Before stage 3, a confirmed high ≥0.5% below the previous peak followed by a close at least 0.5% below the intervening confirmed pullback low exits fully, unless volume backed a renewed break above the former peak. | `right_volume_ratio_min`, `pullback_volume_ratio_max`, `weakening_buffer_pct`; `test_rounded_*` checks third/fourth pullbacks, weakness before generic stops, and valid-structure non-exits. |
| V reversal: volume turn after a sharp drop, continued rise, consolidation breakout/retest, bearish-volume failure | Reversal may form on the low day or either of the next two bars, preserving return and ATR thresholds; later bars cannot undercut the low. Compare rising closes starting with the reversal close. Consolidation: longest qualifying 3–10-bar window, width ≤3 ATR and drift ≤1 ATR. Breakout close exceeds the top by 0.5% with volume. Retest low must touch top ±2%, close ≥top with shrinking volume; intermediate closes cannot break the tolerance floor. Before a valid top breakout, a high-volume bearish body ≥0.5 ATR exits only after two consecutive volume-backed rising days. | `pivot_max_bars`, `consolidation_range_atr_max`, `consolidation_drift_atr_max`, `breakout_buffer_pct`, `bearish_body_atr_min`; `test_v_*` rejects top 98.5 / low 105, covers real touches, support failure, delayed turns, and bearish exits. |

## New advanced parameter contract

The creation wizard and edit form expose these fields; head-and-shoulders, rounded, and V forms also expose their full signal configuration. The five detailed API schemas track the native descriptor. Minimum/maximum pairs must be ordered; integer windows must be positive. JSON percentages are fractions; forms show percentages.

| Strategy | `signal` parameter | Default | Range |
|---|---|---:|---|
| `island_reversal` | `previous_body_atr_min` | 0.5 | >0 |
| `island_reversal` | `breakout_body_atr_min` | 0.5 | >0 |
| `island_reversal` | `exhaustion_body_atr_max` | 1.0 | >0 |
| `island_reversal` | `island_body_atr_max` | 0.5 | >0 |
| `double_bottom` | `rebound_volume_ratio_min` | 1.0 | >0 |
| `double_bottom` | `rebound_volume_ratio_max` | 1.5 | >0 |
| `head_shoulders_bottom` | `platform_bars` | 5 | integer ≥3 |
| `head_shoulders_bottom` | `platform_range_atr_max` | 3.0 | >0 |
| `head_shoulders_bottom` | `platform_drift_atr_max` | 1.0 | >0 |
| `head_shoulders_bottom` | `rebound_volume_ratio_min` | 1.0 | >0 |
| `head_shoulders_bottom` | `rebound_volume_ratio_max` | 1.5 | >0 |
| `rounded_bottom` | `weakening_buffer_pct` | 0.005 | (0, 1) |
| `v_reversal` | `consolidation_range_atr_max` | 3.0 | >0 |
| `v_reversal` | `consolidation_drift_atr_max` | 1.0 | >0 |
| `v_reversal` | `breakout_buffer_pct` | 0.005 | [0, 1) |
| `v_reversal` | `bearish_body_atr_min` | 0.5 | >0 |

## Audit and lifecycle charts

Reuse signal audit JSON with no new table or endpoint. `setup` carries pattern type, stable setup ID, stage, cumulative target, invalidation price, and anchors. New observations include candle body ratios, rebound mean volume, left-platform dates/bounds, rounded rebound peaks, and V consolidation dates/bounds. Weakness SELLs record the former peak, lower high, broken pullback support, and exit date; charts expose these points and bilingual exit reasons. Signals retain observations; charts show only structures confirmed at the time. Crowded right-edge labels move left or to another lane so exit reasons remain visible.

## Validation and operational boundaries

`backend/tests/test_bottom_reversal_rules.py` contains positive/negative examples with explicit action, stage, date, and key-price assertions. Daily prefix backtests ensure future suffixes never rewrite past signals. `test_native_backtest_kernel.py` covers next-valid-session open, missing opens, costs, cash limits, cumulative targets, and split-stage continuity. `test_paper_trading_service.py` uses an in-memory database and broker stubs for partial fills. Frontend `patternLifecycle.test.ts` and `BottomReversalFields.test.ts` cover markers, parameters, and bilingual display.

The five golden audit changes reflect new observations and revisions. The short double-bottom fixture now uses 1.0–1.5 rebound volume and a 5-session retest window; the short head fixture explicitly uses a 3-bar platform, while the default remains 5. All five retain financial summaries and fill stages in these fixtures; added audit observations and confirmation scores change signal and nested position metadata hashes. The rounded fixture hits a generic profit target on its second pullback date: that SELL still carries stage 1 and must not be mistaken for a second entry. Separate positive examples verify stage 2 and repeated additions. The other four strategies retain their complete ledger goldens.

Generic exits remain invalidation, maximum loss, ATR stop, or take-profit; new special exits apply before final confirmation. Source-document fundamentals, news explanations, and return claims remain explanatory, with no new automated condition or data feed. This change needs no database migration, reset, or historical rewrite and does not start scheduling, activate portfolios, or submit broker orders. Return research remains separate, with conclusions labeled `validated`, `not_validated`, or `inconclusive`.
