from __future__ import annotations

from src.services.patterns.common import (
    build_setup,
    buy_decision,
    confirmed_pivot_lows,
    downtrend_context,
    highest_index,
    number,
    position_exit,
    project_line,
    volume_ratio,
)
from src.services.patterns.models import PatternContext, PatternDecision


PATTERN_TYPE = "head_shoulders_bottom"


def evaluate(context: PatternContext) -> PatternDecision | None:
    if not context.bars:
        return None
    exit_decision = position_exit(context, PATTERN_TYPE)
    if exit_decision is not None:
        return exit_decision

    bars = context.bars
    cfg = context.signal_cfg
    left = int(cfg["pivot_left_bars"])
    right = int(cfg["pivot_right_bars"])
    pivots = confirmed_pivot_lows(bars, left, right)
    if len(pivots) < 2:
        return None
    current_idx = len(bars) - 1
    min_gap = int(cfg["min_segment_bars"])
    max_gap = int(cfg["max_segment_bars"])
    shoulder_tol = float(cfg["shoulder_tolerance_pct"])
    head_depth = float(cfg["head_depth_min_pct"])

    # A head becomes actionable only after its full right-side confirmation exists.
    for left_idx, head_idx in reversed(list(zip(pivots, pivots[1:]))):
        gap = head_idx - left_idx
        if not min_gap <= gap <= max_gap:
            continue
        left_low = number(bars[left_idx].get("low"))
        head_low = number(bars[head_idx].get("low"))
        if left_low is None or head_low is None or head_low > left_low * (1.0 - head_depth):
            continue
        if not downtrend_context(
            bars,
            left_idx,
            int(cfg["downtrend_lookback"]),
            float(cfg["downtrend_min_drop_pct"]),
        ):
            continue
        head_volume = volume_ratio(bars[head_idx])
        if head_volume is None or head_volume > float(cfg["head_volume_ratio_max"]):
            continue
        if current_idx == head_idx + right:
            quality = min((left_low - head_low) / max(left_low * head_depth, 1e-12), 1.0)
            setup = build_setup(
                pattern_type=PATTERN_TYPE,
                context=context,
                stage_index=1,
                stage_key="head_candidate",
                anchors={"left_shoulder": left_idx, "head": head_idx},
                invalidation_price=head_low,
                setup_id_anchors=(bars[left_idx].get("dt_ny"), bars[head_idx].get("dt_ny")),
                extra={"left_shoulder_low": left_low, "head_low": head_low},
            )
            return buy_decision(
                "confirmed a low-volume head candidate",
                setup,
                quality,
                quality,
                1.0 - head_volume / float(cfg["head_volume_ratio_max"]),
                1 / 3,
            )

    if len(pivots) < 3:
        return None
    for left_idx, head_idx, shoulder_idx in reversed(list(zip(pivots, pivots[1:], pivots[2:]))):
        if not (
            min_gap <= head_idx - left_idx <= max_gap
            and min_gap <= shoulder_idx - head_idx <= max_gap
        ):
            continue
        left_low = number(bars[left_idx].get("low"))
        head_low = number(bars[head_idx].get("low"))
        shoulder_low = number(bars[shoulder_idx].get("low"))
        if left_low is None or head_low is None or shoulder_low is None:
            continue
        shoulder_distance = abs(shoulder_low - left_low) / left_low
        if shoulder_distance > shoulder_tol or head_low > min(left_low, shoulder_low) * (1.0 - head_depth):
            continue
        shoulder_volume = volume_ratio(bars[shoulder_idx])
        if shoulder_volume is None or shoulder_volume > float(cfg["right_shoulder_volume_ratio_max"]):
            continue
        first_high_idx = highest_index(bars, left_idx, head_idx)
        second_high_idx = highest_index(bars, head_idx, shoulder_idx)
        if first_high_idx is None or second_high_idx is None or first_high_idx == second_high_idx:
            continue
        first_high = number(bars[first_high_idx].get("high"))
        second_high = number(bars[second_high_idx].get("high"))
        if first_high is None or second_high is None:
            continue
        neckline = project_line(first_high_idx, first_high, second_high_idx, second_high, current_idx)
        anchors = {
            "left_shoulder": left_idx,
            "head": head_idx,
            "right_shoulder": shoulder_idx,
            "neckline_1": first_high_idx,
            "neckline_2": second_high_idx,
        }
        setup_id_anchors = (bars[left_idx].get("dt_ny"), bars[head_idx].get("dt_ny"))
        structure = max(0.0, 1.0 - shoulder_distance / shoulder_tol)
        if current_idx == shoulder_idx + right:
            setup = build_setup(
                pattern_type=PATTERN_TYPE,
                context=context,
                stage_index=2,
                stage_key="right_shoulder",
                anchors=anchors,
                invalidation_price=head_low,
                setup_id_anchors=setup_id_anchors,
                extra={"neckline_price": neckline, "head_low": head_low},
            )
            volume_quality = max(
                0.0,
                1.0 - shoulder_volume / float(cfg["right_shoulder_volume_ratio_max"]),
            )
            return buy_decision(
                "confirmed a low-volume right shoulder",
                setup,
                structure,
                structure,
                volume_quality,
                2 / 3,
            )
        close = number(bars[-1].get("close"))
        current_volume_ratio = volume_ratio(bars[-1])
        buffer = float(cfg["breakout_buffer_pct"])
        if (
            close is not None
            and current_volume_ratio is not None
            and close >= neckline * (1.0 + buffer)
            and current_volume_ratio >= float(cfg["breakout_volume_ratio_min"])
        ):
            setup = build_setup(
                pattern_type=PATTERN_TYPE,
                context=context,
                stage_index=3,
                stage_key="neckline_breakout",
                anchors=anchors,
                invalidation_price=head_low,
                setup_id_anchors=setup_id_anchors,
                extra={"neckline_price": neckline, "head_low": head_low},
            )
            price_quality = min(
                max((close / neckline - 1.0) / max(buffer * 2.0, 1e-12), 0.0),
                1.0,
            )
            volume_quality = min(
                current_volume_ratio / (float(cfg["breakout_volume_ratio_min"]) * 2.0),
                1.0,
            )
            return buy_decision(
                "broke above the projected neckline on confirming volume",
                setup,
                structure,
                price_quality,
                volume_quality,
                1.0,
            )
    return None
