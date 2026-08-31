from __future__ import annotations

from typing import Any

from src.services.patterns.common import (
    build_setup,
    buy_decision,
    number,
    position_exit,
    volume_ratio,
)
from src.services.patterns.models import HistoryBar, PatternContext, PatternDecision


PATTERN_TYPE = "v_reversal"


def evaluate(context: PatternContext) -> PatternDecision | None:
    if not context.bars:
        return None
    exit_decision = position_exit(context, PATTERN_TYPE)
    if exit_decision is not None:
        return exit_decision

    bars = context.bars
    cfg = context.signal_cfg
    anchor_idx = find_latest_anchor(bars, cfg)
    if anchor_idx is None:
        return None
    current_idx = len(bars) - 1
    anchor = bars[anchor_idx]
    anchor_low = float(anchor["low"])
    setup_id_anchors = (anchor.get("dt_ny"),)
    anchors = {"pivot": anchor_idx}
    reversal_return = (float(anchor["close"]) - anchor_low) / anchor_low
    anchor_volume = volume_ratio(anchor) or float(cfg["pivot_volume_ratio_min"])
    if current_idx == anchor_idx:
        setup = build_setup(
            pattern_type=PATTERN_TYPE,
            context=context,
            stage_index=1,
            stage_key="volume_pivot",
            anchors=anchors,
            invalidation_price=anchor_low,
            setup_id_anchors=setup_id_anchors,
            extra={"pivot_low": anchor_low},
        )
        price_quality = min(
            reversal_return / (float(cfg["reversal_min_return_pct"]) * 2.0),
            1.0,
        )
        volume_quality = min(
            anchor_volume / (float(cfg["pivot_volume_ratio_min"]) * 2.0),
            1.0,
        )
        return buy_decision(
            "confirmed a high-volume V reversal pivot",
            setup,
            price_quality,
            price_quality,
            volume_quality,
            1 / 3,
        )

    distance = current_idx - anchor_idx
    current = bars[-1]
    close = number(current.get("close"))
    open_price = number(current.get("open"))
    current_volume_ratio = volume_ratio(current)
    continuation = bars[anchor_idx + 1:current_idx + 1]
    continuous_advance = (
        len(continuation) >= 2
        and all(
            number(bar.get("close")) is not None
            and number(bar.get("open")) is not None
            and float(bar["close"]) > float(bar["open"])
            and (volume_ratio(bar) or 0.0) >= float(cfg["continuation_volume_ratio_min"])
            for bar in continuation
        )
        and all(
            float(continuation[idx]["close"]) > float(continuation[idx - 1]["close"])
            for idx in range(1, len(continuation))
        )
    )
    if (
        2 <= distance <= int(cfg["continuation_window"])
        and continuous_advance
        and close is not None
        and open_price is not None
        and close > open_price
        and close > float(anchor["close"])
        and current_volume_ratio is not None
        and current_volume_ratio >= float(cfg["continuation_volume_ratio_min"])
    ):
        setup = build_setup(
            pattern_type=PATTERN_TYPE,
            context=context,
            stage_index=2,
            stage_key="continuation",
            anchors=anchors,
            invalidation_price=anchor_low,
            setup_id_anchors=setup_id_anchors,
            extra={"pivot_low": anchor_low},
        )
        price_quality = min(
            (close / float(anchor["close"]) - 1.0)
            / max(float(cfg["reversal_min_return_pct"]), 1e-12),
            1.0,
        )
        volume_quality = min(
            current_volume_ratio / (float(cfg["continuation_volume_ratio_min"]) * 2.0),
            1.0,
        )
        return buy_decision(
            "continued higher with confirming volume after the V pivot",
            setup,
            price_quality,
            price_quality,
            volume_quality,
            2 / 3,
        )

    retest = find_breakout_retest(bars, anchor_idx, cfg)
    if retest is None:
        return None
    breakout_idx, top = retest
    anchors["breakout"] = breakout_idx
    setup = build_setup(
        pattern_type=PATTERN_TYPE,
        context=context,
        stage_index=3,
        stage_key="top_breakout_retest",
        anchors=anchors,
        invalidation_price=anchor_low,
        setup_id_anchors=setup_id_anchors,
        extra={"pivot_low": anchor_low, "consolidation_top": top},
    )
    breakout_volume = volume_ratio(bars[breakout_idx]) or float(cfg["breakout_volume_ratio_min"])
    current_volume = number(current.get("volume")) or 0.0
    breakout_raw_volume = number(bars[breakout_idx].get("volume")) or 1.0
    volume_quality = max(
        0.0,
        1.0
        - current_volume
        / max(breakout_raw_volume * float(cfg["retest_volume_ratio_max"]), 1e-12),
    )
    price_quality = min(
        max(
            (float(current["close"]) - top)
            / max(top * float(cfg["support_tolerance_pct"]), 1e-12),
            0.0,
        ),
        1.0,
    )
    structure = min(
        breakout_volume / (float(cfg["breakout_volume_ratio_min"]) * 2.0),
        1.0,
    )
    return buy_decision(
        "low-volume retest held the V consolidation top",
        setup,
        structure,
        price_quality,
        volume_quality,
        1.0,
    )


def find_latest_anchor(bars: list[HistoryBar], cfg: dict[str, Any]) -> int | None:
    lookback = int(cfg["downtrend_lookback"])
    pivot_bars = int(cfg["pivot_max_bars"])
    for idx in range(len(bars) - 1, max(lookback - 1, 0), -1):
        bar = bars[idx]
        open_price = number(bar.get("open"))
        close = number(bar.get("close"))
        low = number(bar.get("low"))
        atr = number(bar.get("atr_14"))
        ratio = volume_ratio(bar)
        if None in {open_price, close, low, atr, ratio} or atr == 0 or low == 0:
            continue
        assert open_price is not None and close is not None and low is not None
        assert atr is not None and ratio is not None
        if close <= open_price or (close - low) / low < float(cfg["reversal_min_return_pct"]):
            continue
        if (close - low) / atr < float(cfg["reversal_min_atr"]):
            continue
        if ratio < float(cfg["pivot_volume_ratio_min"]):
            continue
        lows = [number(item.get("low")) for item in bars[max(0, idx - pivot_bars + 1):idx + 1]]
        valid_lows = [value for value in lows if value is not None]
        if not valid_lows or low > min(valid_lows):
            continue
        prior = [
            float(item["close"])
            for item in bars[max(0, idx - lookback):idx]
            if number(item.get("close")) is not None
        ]
        if not prior or (max(prior) - low) / max(prior) < float(cfg["downtrend_min_drop_pct"]):
            continue
        return idx
    return None


def find_breakout_retest(
    bars: list[HistoryBar],
    anchor_idx: int,
    cfg: dict[str, Any],
) -> tuple[int, float] | None:
    current_idx = len(bars) - 1
    min_bars = int(cfg["consolidation_min_bars"])
    max_bars = int(cfg["consolidation_max_bars"])
    retest_window = int(cfg["retest_window"])
    tolerance = float(cfg["support_tolerance_pct"])
    current_low = number(bars[-1].get("low"))
    current_close = number(bars[-1].get("close"))
    current_volume = number(bars[-1].get("volume"))
    if current_low is None or current_close is None or current_volume is None:
        return None
    for breakout_idx in range(
        max(anchor_idx + min_bars + 1, current_idx - retest_window),
        current_idx,
    ):
        consolidation = bars[max(anchor_idx + 1, breakout_idx - max_bars):breakout_idx]
        if not min_bars <= len(consolidation) <= max_bars:
            continue
        top = max(float(item["high"]) for item in consolidation)
        breakout = bars[breakout_idx]
        breakout_close = number(breakout.get("close"))
        breakout_ratio = volume_ratio(breakout)
        breakout_volume = number(breakout.get("volume"))
        if breakout_close is None or breakout_ratio is None or breakout_volume is None:
            continue
        if breakout_close <= top or breakout_ratio < float(cfg["breakout_volume_ratio_min"]):
            continue
        if (
            current_low >= top * (1.0 - tolerance)
            and current_close >= top
            and current_volume <= breakout_volume * float(cfg["retest_volume_ratio_max"])
        ):
            return breakout_idx, top
    return None
