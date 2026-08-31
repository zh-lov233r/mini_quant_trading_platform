# BUY Signal Strength

[中文](signal-strength.zh-CN.md)

The nine engine-ready strategy categories calculate a deterministic, strategy-local BUY strength from 0 to 100. Strength compares entry candidates produced by the same strategy on the same signal date. It is not a historical win probability and must not be compared across strategy categories.

## Execution semantics

- `signal.min_strength_score` is validated within `[0, 100]` and defaults to `50`.
- Day T data produces the score, level, threshold result, and deterministic rank. These values are frozen before the next session.
- Exit signals always run first. Passing BUY entries then run by `strength DESC, instrument_id ASC, symbol ASC` at the next valid session open until cash or `risk.max_positions` is exhausted.
- A missing open price skips that candidate and allows the next ranked candidate to proceed. T+1 prices never change the frozen rank.
- Signals below the threshold remain in full-persistence backtests and paper-run audit data but cannot open a position.

Levels are `weak` below 50, `medium` from 50, `strong` from 70, and `very_strong` from 85. The API exposes the score, level, threshold, pass flag, rank, model version, and weighted components as `SignalRecord.strength`; legacy runs return `null`.

## Category formulas

Each component uses one of these clamped normalizers, and the weighted result is rounded to two decimals:

```text
rise(value, gate, cap) = 100 × clamp((value - gate) / (cap - gate), 0, 1)
fall(value, gate, ideal) = 100 × clamp((gate - value) / (gate - ideal), 0, 1)
```

| Category/setup | v1 components and normalization |
|---|---|
| Trend | Moving-average separation/ATR 60% and crossover impulse/ATR 20%, both `rise(0, 0.5)`; volume ratio 20%, `rise(volume_multiplier, 2 × volume_multiplier)`. |
| Mean reversion | `abs(zscore)` 100%, `rise(zscore_entry, 2 × zscore_entry)`. |
| Momentum breakout | 20-day return 40%, SMA20 extension 35%, and volume ratio 25%; each uses `rise(configured gate, 2 × configured gate)`. |
| Island exhaustion | Downside gap 60% and declining volume 40%; the configured gap and volume limits define the gates. |
| Island breakout | Left gap 30%, right gap 40%, and breakout volume ratio 30%; each uses its configured minimum as gate and twice that minimum as cap. |
| Island retest | Left gap 15%, right gap 20%, breakout volume 20%, retest volume contraction 25% with `fall(retest_volume_ratio_max, 0)`, and ATR-normalized hold margin 20% with `rise(0, 1)`. |
| Double bottom | Stage-specific combinations of bottom symmetry, rebound quality, volume contraction, right-side hold, breakout volume, and neckline extension. The confirmed breakout uses four equal 25% components. |
| Head-and-shoulders / rounded / V reversal | Structure quality, price confirmation, volume quality, and stage confirmation each contribute 25%. |
| Support bounce | ATR confirmation 70% with gate-to-double-gate normalization and reward/risk 30% from `min_reward_risk` to twice that value. |
| Resistance breakout | ATR confirmation 45%, volume ratio 35%, and reward/risk 20%; each rises from its configured minimum to twice that minimum. |
| Breakout retest | ATR hold margin 35% from 0 to `bounce_confirmation_atr`, retest volume contraction 35% from `retest_volume_ratio_max` to 0, and reward/risk 30% from its configured minimum to twice that value. |

For support/resistance, the existing Beta posterior remains in raw `score` and `score_evidence` as audit evidence; it does not determine v1 candidate selection or strength rank.

All formula inputs are available by the T-day close. Missing or non-finite required inputs fail the run instead of silently falling back to symbol order.

## Persistence and compatibility

The raw strategy `signals.score` is retained. The normalized record is stored under `signals.features.strength`, so no database schema migration is required. Existing strategy JSON without the threshold normalizes to `50`; historical signals are not backfilled.
