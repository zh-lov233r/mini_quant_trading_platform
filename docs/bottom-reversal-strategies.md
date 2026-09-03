# Bottom-Reversal Strategies

[中文](bottom-reversal-strategies.zh-CN.md)

The platform includes five engine-ready daily US-long bottom-reversal categories: `island_reversal`, `double_bottom`, `head_shoulders_bottom`, `rounded_bottom`, and `v_reversal`. New instances still default to `draft`. Implementation does not establish effectiveness or profitability; research outcomes must remain `validated`, `not_validated`, or `inconclusive`.

## Cumulative three-stage targets

All five categories share `risk.stage_1_target_pct=0.20`, `stage_2_target_pct=0.50`, and `stage_3_target_pct=1.00`. Values must increase strictly and stage 3 must equal 1. Target market value is:

```text
current portfolio equity × risk.position_size_pct × current cumulative stage target
```

If stage 1 was missed, stage 2 catches up directly to 50%; stage 3 catches up to the full target. Adding to an existing position for the same `setup_id` does not consume another `max_positions` slot. If multiple stages for one setup occur on the same date, only the highest stage executes. Other strategies retain single-entry behavior.

When cash is insufficient or a Paper order fills partially, only the available quantity is added. A later signal at the same stage may finish the missing cumulative target, but execution cannot regress to an earlier stage.

Each stage records `pattern_type`, `setup_id`, `stage_index`, `stage_key`, `stage_target_pct`, anchors, and an invalidation price. Backtests and Paper Trading fill at the next valid session open and preserve per-batch fills, actual incremental notional, and weighted average cost. SELL remains ordered before BUY. Order idempotency includes the setup and stage.

## Default pattern stages

| Strategy | Stage 1 | Stage 2 | Stage 3 |
|---|---|---|---|
| Island reversal | Low-volume downside exhaustion gap | Volume-backed upside gap | Low-volume gap retest |
| Double bottom | Low-volume second bottom | Low-volume right-side pullback | Volume-backed neckline breakout |
| Head-and-shoulders bottom | Low-volume head candidate | Low-volume right shoulder | Volume-backed dynamic-neckline breakout |
| Rounded bottom | First right-side pullback | Second higher pullback | Volume-backed rim breakout |
| V reversal | High-volume bottom pivot | Volume-backed continuation | Range breakout and low-volume retest |

A Pivot is usable only after its right-side confirmation bars exist. Rounded-bottom detection fits a quadratic curve to log closes over an 80–240 session window by default. Every stage uses only data observable at its signal timestamp: signals form after the day-T close and fill at the next valid session (T+1) open.

## Lifecycle pattern annotations

The position-lifecycle candlestick chart in backtest details merges stages already observed for the same `setup_id` during that holding cycle. It marks the known structural points: the island bottom and reversal confirmation, Double Bottom lows and neckline, Head-and-Shoulders shoulders and head low, Rounded Bottom low and right-side pullbacks, and the V pivot. When later stages exist, it also marks the corresponding neckline, rim, or consolidation breakout/retest confirmation. The chart automatically extends its lookback to the earliest pattern anchor. These annotations come from signal audit data and describe what the detector recognized at that time; they are not hindsight confirmation or a profitability claim.

## Exits and safety boundary

The first version does not scale out. Pattern invalidation, maximum loss, ATR stop, or take profit closes the full position. A V setup also exits before stage 3 when a bearish bar trades at twice average volume.

Strategy creation, tests, and research do not create allocations, activate a Portfolio, start the scheduler, or submit Alpaca orders. A real database backtest writes run and audit records, so its target database and write scope still require separate confirmation.
