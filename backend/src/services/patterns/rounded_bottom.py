from __future__ import annotations

import math

from src.services.patterns.common import (
    build_setup,
    buy_decision,
    confirmed_pivot_lows,
    number,
    position_exit,
    quadratic_fit,
    volume_ratio,
)
from src.services.patterns.models import PatternContext, PatternDecision


PATTERN_TYPE = "rounded_bottom"


def evaluate(context: PatternContext) -> PatternDecision | None:
    if not context.bars:
        return None
    exit_decision = position_exit(context, PATTERN_TYPE)
    if exit_decision is not None:
        return exit_decision

    bars = context.bars
    cfg = context.signal_cfg
    if len(bars) < int(cfg["min_lookback"]):
        return None
    window = bars[-int(cfg["max_lookback"]):]
    closes = [number(bar.get("close")) for bar in window]
    if any(value is None or value <= 0 for value in closes):
        return None
    values = [math.log(float(value)) for value in closes if value is not None]
    fit = quadratic_fit(values)
    if fit is None:
        return None
    curvature, vertex_position, r_squared = fit
    if curvature <= 0 or r_squared < float(cfg["min_r_squared"]):
        return None
    if not float(cfg["vertex_position_min"]) <= vertex_position <= float(cfg["vertex_position_max"]):
        return None
    bottom_local = min(range(len(window)), key=lambda idx: float(closes[idx]))
    bottom_idx = len(bars) - len(window) + bottom_local
    bottom_close = float(closes[bottom_local])
    left_rim = max(float(bar["high"]) for bar in window[: max(3, len(window) // 10)])
    depth = (left_rim - bottom_close) / left_rim if left_rim > 0 else 0.0
    if depth < float(cfg["min_depth_pct"]):
        return None
    right = int(cfg["pivot_right_bars"])
    pivots = [
        idx
        for idx in confirmed_pivot_lows(bars, int(cfg["pivot_left_bars"]), right)
        if idx > bottom_idx
    ]
    qualified: list[int] = []
    for pivot_idx in pivots:
        pivot_volume = volume_ratio(bars[pivot_idx])
        surge = max(
            (volume_ratio(bar) or 0.0)
            for bar in bars[max(bottom_idx + 1, pivot_idx - 5):pivot_idx] or [{}]
        )
        if pivot_volume is None or pivot_volume > float(cfg["pullback_volume_ratio_max"]):
            continue
        if surge < float(cfg["right_volume_ratio_min"]):
            continue
        if qualified:
            if pivot_idx - qualified[-1] < int(cfg["min_pullback_spacing"]):
                continue
            if float(bars[pivot_idx]["low"]) <= float(bars[qualified[-1]]["low"]):
                continue
        qualified.append(pivot_idx)
    current_idx = len(bars) - 1
    anchors = {"bottom": bottom_idx, "pullbacks": qualified[:2]}
    setup_id_anchors = (bars[bottom_idx].get("dt_ny"),)
    structure = min(
        max(
            (r_squared - float(cfg["min_r_squared"]))
            / max(1.0 - float(cfg["min_r_squared"]), 1e-12),
            0.0,
        ),
        1.0,
    )
    for stage_index, pivot_idx in enumerate(qualified[:2], start=1):
        if current_idx != pivot_idx + right:
            continue
        stage_key = "first_right_pullback" if stage_index == 1 else "second_right_pullback"
        setup = build_setup(
            pattern_type=PATTERN_TYPE,
            context=context,
            stage_index=stage_index,
            stage_key=stage_key,
            anchors=anchors,
            invalidation_price=bottom_close,
            setup_id_anchors=setup_id_anchors,
            extra={
                "r_squared": r_squared,
                "depth_pct": depth,
                "rim_price": left_rim,
                "vertex_position": vertex_position,
            },
        )
        ratio = volume_ratio(bars[pivot_idx]) or float(cfg["pullback_volume_ratio_max"])
        volume_quality = max(0.0, 1.0 - ratio / float(cfg["pullback_volume_ratio_max"]))
        price_quality = min(depth / (float(cfg["min_depth_pct"]) * 2.0), 1.0)
        return buy_decision(
            "confirmed a higher low-volume pullback on the bowl's right side",
            setup,
            structure,
            price_quality,
            volume_quality,
            stage_index / 3,
        )
    current = bars[-1]
    close = number(current.get("close"))
    current_volume_ratio = volume_ratio(current)
    buffer = float(cfg["breakout_buffer_pct"])
    if (
        len(qualified) >= 2
        and close is not None
        and current_volume_ratio is not None
        and close >= left_rim * (1.0 + buffer)
        and current_volume_ratio >= float(cfg["breakout_volume_ratio_min"])
    ):
        setup = build_setup(
            pattern_type=PATTERN_TYPE,
            context=context,
            stage_index=3,
            stage_key="rim_breakout",
            anchors=anchors,
            invalidation_price=bottom_close,
            setup_id_anchors=setup_id_anchors,
            extra={
                "r_squared": r_squared,
                "depth_pct": depth,
                "rim_price": left_rim,
                "vertex_position": vertex_position,
            },
        )
        price_quality = min(
            max((close / left_rim - 1.0) / max(buffer * 2.0, 1e-12), 0.0),
            1.0,
        )
        volume_quality = min(
            current_volume_ratio / (float(cfg["breakout_volume_ratio_min"]) * 2.0),
            1.0,
        )
        return buy_decision(
            "broke above the rounded-bottom rim on confirming volume",
            setup,
            structure,
            price_quality,
            volume_quality,
            1.0,
        )
    return None
