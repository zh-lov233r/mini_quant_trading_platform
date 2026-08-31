from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.services.patterns.common import compute_recent_atr, number
from src.services.patterns.models import HistoryBar, PatternContext, PatternDecision
from src.services.staged_entry_service import build_pattern_setup, pattern_setup_from_metadata


PATTERN_TYPE = "island_reversal"
STOP_ATR_WINDOW = 20


@dataclass(frozen=True, slots=True)
class IslandReversalPattern:
    left_gap_idx: int
    breakout_idx: int
    island_low: float
    island_high: float
    breakout_gap_low: float
    breakout_close: float
    breakout_volume: float
    breakout_volume_ratio: float
    left_gap_pct: float
    breakout_gap_pct: float


def evaluate(context: PatternContext) -> PatternDecision | None:
    if not context.bars:
        return None
    bars = context.bars
    cfg = context.signal_cfg
    stored_setup = pattern_setup_from_metadata(context.entry_signal_features)
    exit_decision = resolve_staged_exit(context, stored_setup)
    pattern = find_latest_pattern(bars, cfg)
    exhaustion = None if pattern is not None else find_current_exhaustion_gap(bars, cfg)

    if exit_decision is not None:
        action, reason, stage = exit_decision
    elif pattern is not None:
        action, reason, stage = resolve_action(
            bars=bars,
            pattern=pattern,
            signal_cfg=cfg,
            risk_cfg=context.risk_cfg,
            position=0.0,
            avg_entry_price=context.avg_entry_price,
        )
    elif exhaustion is not None:
        action, reason, stage = "BUY", "confirmed a low-volume downside exhaustion gap", "exhaustion_gap"
    else:
        return None
    if action is None or reason is None or stage is None:
        return None

    if pattern is not None:
        score = pattern.left_gap_pct * 100.0 + pattern.breakout_gap_pct * 100.0 + pattern.breakout_volume_ratio
    else:
        score = float(exhaustion["left_gap_pct"] * 100.0) if exhaustion is not None else None

    if action == "SELL" and stored_setup is not None:
        setup = {**stored_setup, "exit_stage": stage}
    elif pattern is not None:
        stage_index = 2 if stage == "breakout" else 3
        setup = build_pattern_setup(
            pattern_type=PATTERN_TYPE,
            symbol=context.symbol,
            stage_index=stage_index,
            stage_key="upside_gap" if stage_index == 2 else "gap_retest",
            risk_cfg=context.risk_cfg,
            anchors={
                "left_gap_trade_date": str(bars[pattern.left_gap_idx]["dt_ny"]),
                "breakout_trade_date": str(bars[pattern.breakout_idx]["dt_ny"]),
                "left_gap_price": pattern.island_high,
                "breakout_price": pattern.breakout_close,
            },
            invalidation_price=pattern.island_low * (1.0 - float(cfg["support_tolerance_pct"])),
            setup_id_anchors=(bars[pattern.left_gap_idx]["dt_ny"],),
            extra={
                "island_low": pattern.island_low,
                "island_high": pattern.island_high,
                "breakout_gap_low": pattern.breakout_gap_low,
                "left_gap_pct": pattern.left_gap_pct,
                "breakout_gap_pct": pattern.breakout_gap_pct,
                "breakout_volume": pattern.breakout_volume,
                "breakout_volume_ratio": pattern.breakout_volume_ratio,
            },
        )
    else:
        assert exhaustion is not None
        setup = build_pattern_setup(
            pattern_type=PATTERN_TYPE,
            symbol=context.symbol,
            stage_index=1,
            stage_key="exhaustion_gap",
            risk_cfg=context.risk_cfg,
            anchors={
                "left_gap_trade_date": str(exhaustion["trade_date"]),
                "left_gap_price": exhaustion["high"],
            },
            invalidation_price=float(exhaustion["low"]) * (1.0 - float(cfg["support_tolerance_pct"])),
            setup_id_anchors=(exhaustion["trade_date"],),
            extra={
                "island_low": exhaustion["low"],
                "island_high": exhaustion["high"],
                "left_gap_pct": exhaustion["left_gap_pct"],
                "left_volume_ratio": exhaustion["volume_ratio"],
            },
        )

    current = bars[-1]
    current_close = number(current.get("close"))
    current_volume = number(current.get("volume"))
    current_atr = number(current.get("atr_14"))
    return PatternDecision(
        action=action,
        reason=reason,
        setup=setup,
        score=score,
        strength_inputs={
            "stage": stage,
            "left_gap_pct": setup.get("left_gap_pct"),
            "right_gap_pct": setup.get("breakout_gap_pct"),
            "breakout_volume_ratio": setup.get("breakout_volume_ratio"),
            "left_volume_ratio": setup.get("left_volume_ratio"),
            "retest_volume_ratio": (
                current_volume / float(setup["breakout_volume"])
                if current_volume is not None and float(setup.get("breakout_volume") or 0) > 0
                else None
            ),
            "hold_margin_atr": (
                (current_close - float(setup["island_high"])) / current_atr
                if current_close is not None
                and current_atr is not None
                and current_atr > 0
                and setup.get("island_high") is not None
                else None
            ),
        },
    )


def find_current_exhaustion_gap(
    bars: list[HistoryBar],
    signal_cfg: dict[str, Any],
) -> dict[str, Any] | None:
    if len(bars) < 2:
        return None
    idx = len(bars) - 1
    bar = bars[idx]
    previous = bars[idx - 1]
    high = number(bar.get("high"))
    low = number(bar.get("low"))
    open_price = number(bar.get("open"))
    close = number(bar.get("close"))
    volume = number(bar.get("volume"))
    average_volume = number(bar.get("volume_sma_20"))
    previous_low = number(previous.get("low"))
    if any(value is None for value in (high, low, open_price, close, volume, average_volume, previous_low)):
        return None
    assert high is not None and low is not None and open_price is not None and close is not None
    assert volume is not None and average_volume is not None and previous_low is not None
    if average_volume <= 0 or previous_low <= 0 or close >= open_price:
        return None
    gap_pct = (previous_low - high) / previous_low
    ratio = volume / average_volume
    if gap_pct < float(signal_cfg["left_gap_min_pct"]) or ratio > float(signal_cfg["left_volume_ratio_max"]):
        return None
    if not has_downtrend_context(
        bars,
        left_gap_idx=idx,
        downtrend_lookback=int(signal_cfg["downtrend_lookback"]),
        min_drop_pct=float(signal_cfg["downtrend_min_drop_pct"]),
    ):
        return None
    return {
        "trade_date": bar.get("dt_ny"),
        "high": high,
        "low": low,
        "left_gap_pct": gap_pct,
        "volume_ratio": ratio,
    }


def resolve_staged_exit(
    context: PatternContext,
    setup: dict[str, Any] | None,
) -> tuple[Literal["SELL"], str, str] | None:
    if context.position <= 0 or setup is None or not context.bars:
        return None
    current = context.bars[-1]
    close = number(current.get("close"))
    low = number(current.get("low"))
    atr = number(current.get("atr_14")) or compute_recent_atr(context.bars, STOP_ATR_WINDOW)
    invalidation = number(setup.get("invalidation_price"))
    if invalidation is not None and low is not None and low < invalidation:
        return "SELL", "price broke the staged pattern invalidation level", "pattern_invalidation"
    if (
        close is not None
        and context.avg_entry_price is not None
        and close <= context.avg_entry_price * (1.0 - float(context.risk_cfg["max_loss_pct"]))
    ):
        return "SELL", "price fell more than the configured max-loss threshold from entry", "max_loss_stop"
    if (
        close is not None
        and atr is not None
        and context.avg_entry_price is not None
        and close <= context.avg_entry_price - float(context.risk_cfg["stop_loss_atr"]) * atr
    ):
        return "SELL", "price hit the ATR stop", "atr_stop"
    if (
        close is not None
        and atr is not None
        and context.avg_entry_price is not None
        and close >= context.avg_entry_price + float(context.risk_cfg["take_profit_atr"]) * atr
    ):
        return "SELL", "price reached the ATR take-profit target", "take_profit"
    return None


def find_latest_pattern(
    bars: list[HistoryBar],
    signal_cfg: dict[str, Any],
) -> IslandReversalPattern | None:
    if len(bars) < 4:
        return None
    min_island_bars = int(signal_cfg["min_island_bars"])
    max_island_bars = int(signal_cfg["max_island_bars"])
    downtrend_lookback = int(signal_cfg["downtrend_lookback"])
    downtrend_min_drop_pct = float(signal_cfg["downtrend_min_drop_pct"])
    left_gap_min_pct = float(signal_cfg["left_gap_min_pct"])
    right_gap_min_pct = float(signal_cfg["right_gap_min_pct"])
    left_volume_ratio_max = float(signal_cfg["left_volume_ratio_max"])
    right_volume_ratio_min = float(signal_cfg["right_volume_ratio_min"])

    earliest_breakout_idx = min_island_bars + 1
    for breakout_idx in range(len(bars) - 1, earliest_breakout_idx - 1, -1):
        breakout_bar = bars[breakout_idx]
        breakout_open = number(breakout_bar.get("open"))
        breakout_close = number(breakout_bar.get("close"))
        breakout_low = number(breakout_bar.get("low"))
        breakout_volume = number(breakout_bar.get("volume"))
        breakout_avg_volume = number(breakout_bar.get("volume_sma_20"))
        if (
            breakout_open is None
            or breakout_close is None
            or breakout_low is None
            or breakout_volume is None
            or breakout_avg_volume is None
            or breakout_avg_volume <= 0
            or breakout_close <= breakout_open
        ):
            continue
        breakout_volume_ratio = breakout_volume / breakout_avg_volume
        if breakout_volume_ratio < right_volume_ratio_min:
            continue
        latest_left_gap_idx = breakout_idx - min_island_bars
        earliest_left_gap_idx = max(1, breakout_idx - max_island_bars)
        if latest_left_gap_idx < earliest_left_gap_idx:
            continue

        for left_gap_idx in range(latest_left_gap_idx, earliest_left_gap_idx - 1, -1):
            left_gap_bar = bars[left_gap_idx]
            pre_gap_bar = bars[left_gap_idx - 1]
            left_gap_high = number(left_gap_bar.get("high"))
            left_gap_open = number(left_gap_bar.get("open"))
            left_gap_close = number(left_gap_bar.get("close"))
            left_gap_volume = number(left_gap_bar.get("volume"))
            left_gap_avg_volume = number(left_gap_bar.get("volume_sma_20"))
            prev_low = number(pre_gap_bar.get("low"))
            if (
                left_gap_high is None
                or left_gap_open is None
                or left_gap_close is None
                or left_gap_volume is None
                or left_gap_avg_volume is None
                or left_gap_avg_volume <= 0
                or prev_low is None
                or left_gap_close >= left_gap_open
            ):
                continue
            left_gap_pct = (prev_low - left_gap_high) / prev_low if prev_low > 0 else 0.0
            if left_gap_pct < left_gap_min_pct or left_gap_volume / left_gap_avg_volume > left_volume_ratio_max:
                continue
            if not has_downtrend_context(
                bars,
                left_gap_idx=left_gap_idx,
                downtrend_lookback=downtrend_lookback,
                min_drop_pct=downtrend_min_drop_pct,
            ):
                continue
            island_bars = bars[left_gap_idx:breakout_idx]
            if len(island_bars) < min_island_bars:
                continue
            island_high = max(number(bar.get("high")) or float("-inf") for bar in island_bars)
            island_low = min(number(bar.get("low")) or float("inf") for bar in island_bars)
            if island_high == float("-inf") or island_low == float("inf"):
                continue
            if any((number(bar.get("high")) or float("inf")) >= prev_low for bar in island_bars):
                continue
            breakout_gap_pct = (breakout_low - island_high) / island_high if island_high > 0 else 0.0
            if breakout_gap_pct < right_gap_min_pct:
                continue
            return IslandReversalPattern(
                left_gap_idx=left_gap_idx,
                breakout_idx=breakout_idx,
                island_low=island_low,
                island_high=island_high,
                breakout_gap_low=breakout_low,
                breakout_close=breakout_close,
                breakout_volume=breakout_volume,
                breakout_volume_ratio=breakout_volume_ratio,
                left_gap_pct=left_gap_pct,
                breakout_gap_pct=breakout_gap_pct,
            )
    return None


def has_downtrend_context(
    bars: list[HistoryBar],
    *,
    left_gap_idx: int,
    downtrend_lookback: int,
    min_drop_pct: float,
) -> bool:
    left_gap_bar = bars[left_gap_idx]
    close = number(left_gap_bar.get("close"))
    lookback_return = None
    anchor_index = left_gap_idx - downtrend_lookback
    if close is not None and anchor_index >= 0:
        anchor_close = number(bars[anchor_index].get("close"))
        if anchor_close is not None and anchor_close > 0:
            lookback_return = (close / anchor_close) - 1.0
    sma_50 = number(left_gap_bar.get("sma_50"))
    return bool(
        (lookback_return is not None and lookback_return <= -min_drop_pct)
        or (close is not None and sma_50 is not None and close < sma_50)
    )


def resolve_action(
    *,
    bars: list[HistoryBar],
    pattern: IslandReversalPattern,
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    position: float,
    avg_entry_price: float | None = None,
) -> tuple[Literal["BUY", "SELL", "HOLD"] | None, str | None, str | None]:
    current_idx = len(bars) - 1
    current_bar = bars[current_idx]
    current_close = number(current_bar.get("close"))
    current_low = number(current_bar.get("low"))
    current_volume = number(current_bar.get("volume"))
    current_atr = compute_recent_atr(bars, STOP_ATR_WINDOW)
    breakout_atr = compute_recent_atr(bars[:pattern.breakout_idx + 1], STOP_ATR_WINDOW)
    support_tolerance_pct = float(signal_cfg["support_tolerance_pct"])
    support_floor = pattern.island_high * (1.0 - support_tolerance_pct)
    hard_stop = pattern.island_low * (1.0 - support_tolerance_pct)

    if position > 0:
        if (
            current_close is not None
            and avg_entry_price is not None
            and avg_entry_price > 0
            and current_close <= avg_entry_price * (1.0 - float(risk_cfg["max_loss_pct"]))
        ):
            return "SELL", "price fell more than the configured max-loss threshold from entry", "max_loss_stop"
        if current_low is not None and current_low < hard_stop:
            return "SELL", "price broke below the island base low", "base_break"
        if (
            current_close is not None
            and breakout_atr is not None
            and current_close >= pattern.breakout_close + float(risk_cfg["take_profit_atr"]) * breakout_atr
        ):
            return "SELL", "price reached the ATR take-profit target from the breakout confirmation", "take_profit"
        if (
            current_close is not None
            and current_atr is not None
            and current_close < pattern.breakout_close - float(risk_cfg["stop_loss_atr"]) * current_atr
        ):
            return "SELL", "price hit the ATR stop from the breakout confirmation", "atr_stop"
        return None, None, None
    if current_idx == pattern.breakout_idx:
        return "BUY", "confirmed the island reversal with a volume-backed upside gap", "breakout"
    if current_idx <= pattern.breakout_idx:
        return None, None, None
    if current_idx > pattern.breakout_idx + int(signal_cfg["retest_window"]):
        return None, None, None
    if current_low is None or current_close is None or current_volume is None:
        return None, None, None
    if any(
        (number(bar.get("close")) or float("inf")) < support_floor
        for bar in bars[pattern.breakout_idx + 1:current_idx]
    ):
        return None, None, None
    touched_gap = current_low <= pattern.breakout_gap_low * (1.0 + support_tolerance_pct)
    held_support = current_low >= support_floor and current_close >= pattern.island_high
    low_volume_retest = current_volume <= pattern.breakout_volume * float(signal_cfg["retest_volume_ratio_max"])
    if touched_gap and held_support and low_volume_retest:
        return "BUY", "low-volume retest held the upside gap after the island reversal", "retest"
    return None, None, None
