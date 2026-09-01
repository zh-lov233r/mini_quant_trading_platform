from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.services.patterns.common import compute_recent_atr
from src.services.patterns.models import HistoryBar, PatternContext, PatternDecision
from src.services.staged_entry_service import build_pattern_setup, pattern_setup_from_metadata


PATTERN_TYPE = "double_bottom"
STOP_ATR_WINDOW = 20


@dataclass(frozen=True, slots=True)
class DoubleBottomPattern:
    left_bottom_idx: int
    neckline_idx: int
    right_bottom_idx: int
    breakout_idx: int
    left_bottom_low: float
    right_bottom_low: float
    neckline_price: float
    breakout_close: float
    breakout_volume: float
    breakout_volume_ratio: float
    bottom_distance_pct: float
    rebound_up_day_ratio: float


@dataclass(frozen=True, slots=True)
class DoubleBottomLeftCandidate:
    left_bottom_idx: int
    left_bottom_low: float


@dataclass(frozen=True, slots=True)
class DoubleBottomRightCandidate:
    left_bottom_idx: int
    neckline_idx: int
    right_bottom_idx: int
    left_bottom_low: float
    right_bottom_low: float
    neckline_price: float
    bottom_distance_pct: float
    rebound_up_day_ratio: float


@dataclass(slots=True)
class DoubleBottomSymbolState:
    history_bars: list[HistoryBar] = field(default_factory=list)
    left_candidates: list[DoubleBottomLeftCandidate] = field(default_factory=list)
    right_candidates: list[DoubleBottomRightCandidate] = field(default_factory=list)
    best_pattern: DoubleBottomPattern | None = None


@dataclass(slots=True)
class DoubleBottomState:
    symbols: dict[str, DoubleBottomSymbolState] = field(default_factory=dict)


def create_state() -> DoubleBottomState:
    return DoubleBottomState()


def build_history_bar_from_snapshot(snapshot: dict[str, Any]) -> HistoryBar:
    return {
        "dt_ny": snapshot.get("dt_ny"),
        "ts": snapshot.get("ts"),
        "open": snapshot.get("open"),
        "high": snapshot.get("high"),
        "low": snapshot.get("low"),
        "close": snapshot.get("close"),
        "volume": snapshot.get("volume"),
        "atr_14": snapshot.get("atr_14"),
        "volume_sma_20": snapshot.get("volume_sma_20"),
        "ret_20d": snapshot.get("ret_20d"),
        "ret_60d": snapshot.get("ret_60d"),
        "sma_20": snapshot.get("sma_20"),
        "sma_50": snapshot.get("sma_50"),
    }


def append_snapshot(symbol_state: DoubleBottomSymbolState, snapshot: dict[str, Any]) -> None:
    history_bar = build_history_bar_from_snapshot(snapshot)
    history_trade_date = history_bar.get("dt_ny")
    if symbol_state.history_bars:
        last_trade_date = symbol_state.history_bars[-1].get("dt_ny")
        if history_trade_date is not None and history_trade_date == last_trade_date:
            symbol_state.history_bars[-1] = history_bar
            return
    symbol_state.history_bars.append(history_bar)


def evaluate(
    context: PatternContext,
    *,
    symbol_state: DoubleBottomSymbolState | None = None,
) -> PatternDecision | None:
    if not context.bars:
        return None
    if symbol_state is None:
        candidate, pattern = replay_state(context.bars, context.signal_cfg)
    else:
        candidate = symbol_state.right_candidates[-1] if symbol_state.right_candidates else None
        pattern = symbol_state.best_pattern

    setup: dict[str, Any] | None = None
    action = reason = stage = None
    if context.position > 0:
        setup = extract_position_setup(context.entry_signal_features)
        if setup is None and pattern is not None:
            setup = build_setup_payload(context.bars, pattern)
        if setup is not None:
            action, reason, stage = resolve_exit_action(
                bars=context.bars,
                setup=setup,
                signal_cfg=context.signal_cfg,
                risk_cfg=context.risk_cfg,
                avg_entry_price=context.avg_entry_price,
            )
            if action is not None:
                setup = {**setup, "exit_stage": stage}
    if action is None:
        entry = resolve_staged_entry(
            symbol=context.symbol,
            bars=context.bars,
            candidate=candidate,
            pattern=pattern,
            signal_cfg=context.signal_cfg,
            risk_cfg=context.risk_cfg,
        )
        if entry is not None:
            action, reason, stage, setup = entry
    if action is None or reason is None or stage is None or setup is None:
        return None
    return PatternDecision(
        action=action,
        reason=reason,
        setup=setup,
        score=compute_signal_score(setup),
        strength_inputs=strength_inputs(context.bars[-1], setup),
    )


def advance_symbol(
    symbol_state: DoubleBottomSymbolState,
    signal_cfg: dict[str, Any],
) -> DoubleBottomPattern | None:
    bars = symbol_state.history_bars
    if len(bars) < 2:
        return symbol_state.best_pattern

    min_bottom_spacing = int(signal_cfg["min_bottom_spacing"])
    max_bottom_spacing = int(signal_cfg["max_bottom_spacing"])
    before = int(signal_cfg.get("left_bottom_before_bars", 1))
    after = int(signal_cfg.get("left_bottom_after_bars", 1))
    current_idx = len(bars) - 1
    left_candidate = build_left_candidate(
        bars,
        current_idx=current_idx,
        downtrend_lookback=int(signal_cfg["downtrend_lookback"]),
        downtrend_min_drop_pct=float(signal_cfg["downtrend_min_drop_pct"]),
        downtrend_max_up_day_ratio=float(signal_cfg["downtrend_max_up_day_ratio"]),
        downtrend_min_r_squared=float(signal_cfg["downtrend_min_r_squared"]),
        left_bottom_before_bars=before,
        left_bottom_after_bars=after,
        bottom_volume_ratio_max=float(signal_cfg["second_bottom_volume_ratio_max"]),
    )
    if left_candidate is not None and all(
        existing.left_bottom_idx != left_candidate.left_bottom_idx
        for existing in symbol_state.left_candidates
    ):
        symbol_state.left_candidates.append(left_candidate)
    symbol_state.left_candidates = [
        candidate
        for candidate in symbol_state.left_candidates
        if current_idx <= candidate.left_bottom_idx + max_bottom_spacing + 1
    ]

    right_bottom_idx = current_idx - after
    if right_bottom_idx >= 0:
        promoted = promote_right_candidates(
            bars,
            left_candidates=symbol_state.left_candidates,
            right_bottom_idx=right_bottom_idx,
            min_bottom_spacing=min_bottom_spacing,
            max_bottom_spacing=max_bottom_spacing,
            bottom_tolerance_pct=float(signal_cfg["bottom_tolerance_pct"]),
            neckline_min_rebound_pct=float(signal_cfg["neckline_min_rebound_pct"]),
            rebound_up_day_ratio_min=float(signal_cfg["rebound_up_day_ratio_min"]),
            bottom_volume_ratio_max=float(signal_cfg["second_bottom_volume_ratio_max"]),
            pivot_before_bars=before,
            pivot_after_bars=after,
        )
        existing_pairs = {
            (candidate.left_bottom_idx, candidate.right_bottom_idx)
            for candidate in symbol_state.right_candidates
        }
        for candidate in promoted:
            pair = (candidate.left_bottom_idx, candidate.right_bottom_idx)
            if pair not in existing_pairs:
                symbol_state.right_candidates.append(candidate)
                existing_pairs.add(pair)

    active: list[DoubleBottomRightCandidate] = []
    max_breakout = int(signal_cfg.get("max_breakout_bars_after_right_bottom", 40))
    for candidate in symbol_state.right_candidates:
        if current_idx > candidate.right_bottom_idx + max_breakout:
            continue
        pattern = build_pattern_from_right_candidate(
            bars,
            right_candidate=candidate,
            breakout_idx=current_idx,
            breakout_buffer_pct=float(signal_cfg["breakout_buffer_pct"]),
            breakout_volume_ratio_min=float(signal_cfg["breakout_volume_ratio_min"]),
        )
        if pattern is None:
            active.append(candidate)
        elif is_preferred_pattern(pattern, symbol_state.best_pattern):
            symbol_state.best_pattern = pattern
    symbol_state.right_candidates = active
    return symbol_state.best_pattern


def replay_state(
    bars: list[HistoryBar],
    signal_cfg: dict[str, Any],
) -> tuple[DoubleBottomRightCandidate | None, DoubleBottomPattern | None]:
    state = DoubleBottomSymbolState()
    pattern = None
    for bar in bars:
        state.history_bars.append(dict(bar))
        pattern = advance_symbol(state, signal_cfg)
    candidate = state.right_candidates[-1] if state.right_candidates else None
    return candidate, pattern


def resolve_staged_entry(
    *,
    symbol: str,
    bars: list[HistoryBar],
    candidate: DoubleBottomRightCandidate | None,
    pattern: DoubleBottomPattern | None,
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> tuple[Literal["BUY"], str, str, dict[str, Any]] | None:
    if not bars:
        return None
    current_idx = len(bars) - 1
    if pattern is not None and current_idx == pattern.breakout_idx:
        setup = build_staged_setup(
            symbol=symbol,
            bars=bars,
            candidate=DoubleBottomRightCandidate(
                left_bottom_idx=pattern.left_bottom_idx,
                neckline_idx=pattern.neckline_idx,
                right_bottom_idx=pattern.right_bottom_idx,
                left_bottom_low=pattern.left_bottom_low,
                right_bottom_low=pattern.right_bottom_low,
                neckline_price=pattern.neckline_price,
                bottom_distance_pct=pattern.bottom_distance_pct,
                rebound_up_day_ratio=pattern.rebound_up_day_ratio,
            ),
            risk_cfg=risk_cfg,
            signal_cfg=signal_cfg,
            stage_index=3,
            stage_key="neckline_breakout",
            pattern=pattern,
        )
        return "BUY", "broke above the double-bottom neckline on confirming volume", "neckline_breakout", setup
    if candidate is None:
        return None
    confirmation_bars = int(signal_cfg.get("left_bottom_after_bars", 1))
    if current_idx == candidate.right_bottom_idx + confirmation_bars:
        setup = build_staged_setup(
            symbol=symbol,
            bars=bars,
            candidate=candidate,
            risk_cfg=risk_cfg,
            signal_cfg=signal_cfg,
            stage_index=1,
            stage_key="second_bottom",
        )
        return "BUY", "confirmed a low-volume second bottom", "second_bottom", setup
    if not is_first_right_pullback(bars, candidate, signal_cfg):
        return None
    setup = build_staged_setup(
        symbol=symbol,
        bars=bars,
        candidate=candidate,
        risk_cfg=risk_cfg,
        signal_cfg=signal_cfg,
        stage_index=2,
        stage_key="right_side_pullback",
    )
    return "BUY", "confirmed the first low-volume right-side pullback above the second bottom", "right_side_pullback", setup


def is_first_right_pullback(
    bars: list[HistoryBar],
    candidate: DoubleBottomRightCandidate,
    signal_cfg: dict[str, Any],
) -> bool:
    current_idx = len(bars) - 1
    start = candidate.right_bottom_idx + int(signal_cfg.get("left_bottom_after_bars", 1)) + 1
    if current_idx < start + 1:
        return False
    halfway = candidate.right_bottom_low + (candidate.neckline_price - candidate.right_bottom_low) * 0.5
    prior_closes = [bar.get("close") for bar in bars[start:current_idx]]
    if not prior_closes or max((value for value in prior_closes if value is not None), default=0.0) < halfway:
        return False

    def qualifies(idx: int) -> bool:
        bar = bars[idx]
        close = bar.get("close")
        low = bar.get("low")
        previous_close = bars[idx - 1].get("close")
        volume = bar.get("volume")
        average_volume = bar.get("volume_sma_20")
        return bool(
            close is not None
            and low is not None
            and previous_close is not None
            and volume is not None
            and average_volume is not None
            and average_volume > 0
            and close < previous_close
            and low > candidate.right_bottom_low
            and close > candidate.right_bottom_low
            and volume / average_volume <= float(signal_cfg["second_bottom_volume_ratio_max"])
        )

    return qualifies(current_idx) and not any(qualifies(idx) for idx in range(start, current_idx))


def build_staged_setup(
    *,
    symbol: str,
    bars: list[HistoryBar],
    candidate: DoubleBottomRightCandidate,
    risk_cfg: dict[str, Any],
    signal_cfg: dict[str, Any],
    stage_index: int,
    stage_key: str,
    pattern: DoubleBottomPattern | None = None,
) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "left_bottom_trade_date": str(bars[candidate.left_bottom_idx]["dt_ny"]),
        "neckline_trade_date": str(bars[candidate.neckline_idx]["dt_ny"]),
        "right_bottom_trade_date": str(bars[candidate.right_bottom_idx]["dt_ny"]),
        "left_bottom_low": candidate.left_bottom_low,
        "right_bottom_low": candidate.right_bottom_low,
        "neckline_price": candidate.neckline_price,
        "bottom_distance_pct": candidate.bottom_distance_pct,
        "rebound_up_day_ratio": candidate.rebound_up_day_ratio,
    }
    if pattern is not None:
        extra.update(build_setup_payload(bars, pattern))
    return build_pattern_setup(
        pattern_type=PATTERN_TYPE,
        symbol=symbol,
        stage_index=stage_index,
        stage_key=stage_key,
        risk_cfg=risk_cfg,
        anchors={
            "left_bottom_trade_date": extra["left_bottom_trade_date"],
            "right_bottom_trade_date": extra["right_bottom_trade_date"],
            "left_bottom_price": candidate.left_bottom_low,
            "right_bottom_price": candidate.right_bottom_low,
            "neckline_price": candidate.neckline_price,
        },
        invalidation_price=min(candidate.left_bottom_low, candidate.right_bottom_low)
        * (1.0 - float(signal_cfg["support_tolerance_pct"])),
        setup_id_anchors=(extra["left_bottom_trade_date"], extra["right_bottom_trade_date"]),
        extra=extra,
    )


def build_setup_payload(
    bars: list[HistoryBar],
    pattern: DoubleBottomPattern,
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    breakout_atr = compute_recent_atr(bars[:pattern.breakout_idx + 1], STOP_ATR_WINDOW)
    payload: dict[str, Any] = {
        "left_bottom_trade_date": str(bars[pattern.left_bottom_idx]["dt_ny"]),
        "neckline_trade_date": str(bars[pattern.neckline_idx]["dt_ny"]),
        "right_bottom_trade_date": str(bars[pattern.right_bottom_idx]["dt_ny"]),
        "breakout_trade_date": str(bars[pattern.breakout_idx]["dt_ny"]),
        "left_bottom_low": pattern.left_bottom_low,
        "right_bottom_low": pattern.right_bottom_low,
        "neckline_price": pattern.neckline_price,
        "breakout_close": pattern.breakout_close,
        "breakout_volume": pattern.breakout_volume,
        "breakout_atr": breakout_atr,
        "breakout_wait_bars": pattern.breakout_idx - pattern.right_bottom_idx,
        "bottom_distance_pct": pattern.bottom_distance_pct,
        "breakout_volume_ratio": pattern.breakout_volume_ratio,
        "rebound_up_day_ratio": pattern.rebound_up_day_ratio,
    }
    if stage is not None:
        payload["stage"] = stage
    return payload


def extract_position_setup(entry_signal_features: dict[str, Any] | None) -> dict[str, Any] | None:
    parsed = pattern_setup_from_metadata(entry_signal_features)
    if parsed is None:
        return None
    left_bottom_low = parsed.get("left_bottom_low")
    right_bottom_low = parsed.get("right_bottom_low")
    neckline_price = parsed.get("neckline_price")
    if left_bottom_low is None or right_bottom_low is None or neckline_price is None:
        return None
    setup = dict(parsed)
    setup.update(
        {
            "left_bottom_low": left_bottom_low,
            "right_bottom_low": right_bottom_low,
            "neckline_price": neckline_price,
            "breakout_close": parsed.get("breakout_close"),
            "breakout_atr": parsed.get("breakout_atr"),
            "breakout_wait_bars": parsed.get("breakout_wait_bars"),
            "bottom_distance_pct": parsed.get("bottom_distance_pct"),
            "breakout_volume_ratio": parsed.get("breakout_volume_ratio"),
            "rebound_up_day_ratio": parsed.get("rebound_up_day_ratio"),
        }
    )
    return setup


def compute_signal_score(setup: dict[str, Any]) -> float:
    bottom_distance_pct = setup.get("bottom_distance_pct") or 1.0
    breakout_volume_ratio = setup.get("breakout_volume_ratio") or 0.0
    rebound_up_day_ratio = setup.get("rebound_up_day_ratio") or 0.0
    return ((1.0 - bottom_distance_pct) * 100.0) + breakout_volume_ratio + rebound_up_day_ratio * 10.0


def strength_inputs(snapshot: dict[str, Any], setup: dict[str, Any]) -> dict[str, Any]:
    neckline_price = setup.get("neckline_price")
    breakout_close = setup.get("breakout_close")
    breakout_volume = setup.get("breakout_volume")
    current_volume = snapshot.get("volume")
    close = snapshot.get("close")
    right_bottom_low = setup.get("right_bottom_low")
    average_volume = snapshot.get("volume_sma_20")
    return {
        "stage": setup.get("stage_key") or setup.get("stage"),
        "bottom_distance_pct": setup.get("bottom_distance_pct"),
        "rebound_up_day_ratio": setup.get("rebound_up_day_ratio"),
        "current_volume_ratio": (
            current_volume / average_volume
            if current_volume is not None and average_volume not in {None, 0}
            else None
        ),
        "pullback_hold_pct": (
            (close - right_bottom_low) / max(neckline_price - right_bottom_low, 1e-12)
            if close is not None and right_bottom_low is not None and neckline_price is not None
            else None
        ),
        "breakout_volume_ratio": setup.get("breakout_volume_ratio"),
        "breakout_extension_pct": (
            breakout_close / neckline_price - 1.0
            if breakout_close is not None and neckline_price is not None and neckline_price > 0
            else None
        ),
        "retest_volume_ratio": (
            current_volume / breakout_volume
            if current_volume is not None and breakout_volume is not None and breakout_volume > 0
            else None
        ),
    }


def resolve_exit_action(
    *,
    bars: list[HistoryBar],
    setup: dict[str, Any],
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    avg_entry_price: float | None = None,
) -> tuple[Literal["SELL"] | None, str | None, str | None]:
    if not bars:
        return None, None, None
    current = bars[-1]
    current_close = current.get("close")
    current_low = current.get("low")
    current_atr = compute_recent_atr(bars, STOP_ATR_WINDOW)
    breakout_close = setup.get("breakout_close")
    breakout_atr = setup.get("breakout_atr")
    left_bottom_low = setup.get("left_bottom_low")
    right_bottom_low = setup.get("right_bottom_low")
    if left_bottom_low is None or right_bottom_low is None:
        return None, None, None
    hard_stop = min(left_bottom_low, right_bottom_low) * (
        1.0 - float(signal_cfg["support_tolerance_pct"])
    )
    if current_close is not None and current_close < right_bottom_low:
        return "SELL", "price closed below the right bottom after confirmation", "right_bottom_break"
    if (
        current_close is not None
        and avg_entry_price is not None
        and avg_entry_price > 0
        and current_close <= avg_entry_price * (1.0 - float(risk_cfg["max_loss_pct"]))
    ):
        return "SELL", "price fell more than the configured max-loss threshold from entry", "max_loss_stop"
    if current_low is not None and current_low < hard_stop:
        return "SELL", "price broke below the double-bottom base", "base_break"
    if (
        current_close is not None
        and breakout_close is not None
        and breakout_atr is not None
        and current_close >= breakout_close + float(risk_cfg["take_profit_atr"]) * breakout_atr
    ):
        return "SELL", "price reached the ATR take-profit target from the breakout confirmation", "take_profit"
    stop_anchor = breakout_close if breakout_close is not None else avg_entry_price
    if (
        current_close is not None
        and current_atr is not None
        and stop_anchor is not None
        and current_close < stop_anchor - float(risk_cfg["stop_loss_atr"]) * current_atr
    ):
        return "SELL", "price hit the ATR stop from the breakout confirmation", "atr_stop"
    return None, None, None

def find_latest_pattern(
    bars: list[HistoryBar],
    signal_cfg: dict[str, Any],
) -> DoubleBottomPattern | None:
    if len(bars) < 6:
        return None
    state = DoubleBottomSymbolState()
    for bar in bars:
        state.history_bars.append(dict(bar))
        advance_symbol(state, signal_cfg)
    return state.best_pattern


def build_left_candidate(
    bars: list[HistoryBar],
    *,
    current_idx: int,
    downtrend_lookback: int,
    downtrend_min_drop_pct: float,
    downtrend_max_up_day_ratio: float,
    downtrend_min_r_squared: float,
    left_bottom_before_bars: int,
    left_bottom_after_bars: int,
    bottom_volume_ratio_max: float,
) -> DoubleBottomLeftCandidate | None:
    left_bottom_idx = current_idx - left_bottom_after_bars
    if left_bottom_idx < left_bottom_before_bars:
        return None
    if not is_local_minimum(
        bars,
        left_bottom_idx,
        before_span=left_bottom_before_bars,
        after_span=left_bottom_after_bars,
    ):
        return None
    bar = bars[left_bottom_idx]
    low = bar.get("low")
    volume = bar.get("volume")
    average = bar.get("volume_sma_20")
    if low is None or low <= 0 or volume is None or average is None or average <= 0:
        return None
    if volume / average > bottom_volume_ratio_max:
        return None
    if not has_downtrend_context(
        bars,
        left_bottom_idx=left_bottom_idx,
        downtrend_lookback=downtrend_lookback,
        min_drop_pct=downtrend_min_drop_pct,
    ):
        return None
    if not has_smooth_downtrend(
        bars,
        left_bottom_idx=left_bottom_idx,
        downtrend_lookback=downtrend_lookback,
        max_up_day_ratio=downtrend_max_up_day_ratio,
        min_r_squared=downtrend_min_r_squared,
    ):
        return None
    return DoubleBottomLeftCandidate(left_bottom_idx=left_bottom_idx, left_bottom_low=low)


def promote_right_candidates(
    bars: list[HistoryBar],
    *,
    left_candidates: list[DoubleBottomLeftCandidate],
    right_bottom_idx: int,
    min_bottom_spacing: int,
    max_bottom_spacing: int,
    bottom_tolerance_pct: float,
    neckline_min_rebound_pct: float,
    rebound_up_day_ratio_min: float,
    bottom_volume_ratio_max: float,
    pivot_before_bars: int = 1,
    pivot_after_bars: int = 1,
) -> list[DoubleBottomRightCandidate]:
    if not left_candidates or not is_local_minimum(
        bars,
        right_bottom_idx,
        before_span=pivot_before_bars,
        after_span=pivot_after_bars,
    ):
        return []
    candidates: list[DoubleBottomRightCandidate] = []
    for left_candidate in left_candidates:
        spacing = right_bottom_idx - left_candidate.left_bottom_idx
        if not min_bottom_spacing <= spacing <= max_bottom_spacing:
            continue
        candidate = build_right_candidate(
            bars,
            left_candidate=left_candidate,
            right_bottom_idx=right_bottom_idx,
            bottom_tolerance_pct=bottom_tolerance_pct,
            neckline_min_rebound_pct=neckline_min_rebound_pct,
            rebound_up_day_ratio_min=rebound_up_day_ratio_min,
            bottom_volume_ratio_max=bottom_volume_ratio_max,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def build_right_candidate(
    bars: list[HistoryBar],
    *,
    left_candidate: DoubleBottomLeftCandidate,
    right_bottom_idx: int,
    bottom_tolerance_pct: float,
    neckline_min_rebound_pct: float,
    rebound_up_day_ratio_min: float,
    bottom_volume_ratio_max: float,
) -> DoubleBottomRightCandidate | None:
    right_bar = bars[right_bottom_idx]
    right_low = right_bar.get("low")
    right_volume = right_bar.get("volume")
    right_average = right_bar.get("volume_sma_20")
    if right_low is None or right_volume is None or right_average is None or right_average <= 0:
        return None
    if right_volume / right_average > bottom_volume_ratio_max:
        return None
    left_idx = left_candidate.left_bottom_idx
    left_low = left_candidate.left_bottom_low
    distance = abs(right_low - left_low) / max(left_low, right_low)
    if distance > bottom_tolerance_pct or right_low < left_low * (1.0 - bottom_tolerance_pct):
        return None
    if not intermediate_lows_hold(
        bars,
        left_bottom_idx=left_idx,
        right_bottom_idx=right_bottom_idx,
        floor_low=min(left_low, right_low),
    ):
        return None
    neckline_idx, neckline_price = find_neckline(
        bars,
        left_bottom_idx=left_idx,
        right_bottom_idx=right_bottom_idx,
    )
    if neckline_idx is None or neckline_price is None or neckline_price <= 0:
        return None
    if neckline_price < max(left_low, right_low) * (1.0 + neckline_min_rebound_pct):
        return None
    rebound_ratio = compute_up_day_ratio(bars, start_idx=left_idx, end_idx=right_bottom_idx)
    if rebound_ratio is None or rebound_ratio < rebound_up_day_ratio_min:
        return None
    return DoubleBottomRightCandidate(
        left_bottom_idx=left_idx,
        neckline_idx=neckline_idx,
        right_bottom_idx=right_bottom_idx,
        left_bottom_low=left_low,
        right_bottom_low=right_low,
        neckline_price=neckline_price,
        bottom_distance_pct=distance,
        rebound_up_day_ratio=rebound_ratio,
    )


def build_pattern_from_right_candidate(
    bars: list[HistoryBar],
    *,
    right_candidate: DoubleBottomRightCandidate,
    breakout_idx: int,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
) -> DoubleBottomPattern | None:
    if breakout_idx <= right_candidate.right_bottom_idx:
        return None
    match = match_breakout_bar(
        bars[breakout_idx],
        neckline_price=right_candidate.neckline_price,
        breakout_buffer_pct=breakout_buffer_pct,
        breakout_volume_ratio_min=breakout_volume_ratio_min,
    )
    if match is None:
        return None
    breakout_close, breakout_volume, breakout_average = match
    return DoubleBottomPattern(
        left_bottom_idx=right_candidate.left_bottom_idx,
        neckline_idx=right_candidate.neckline_idx,
        right_bottom_idx=right_candidate.right_bottom_idx,
        breakout_idx=breakout_idx,
        left_bottom_low=right_candidate.left_bottom_low,
        right_bottom_low=right_candidate.right_bottom_low,
        neckline_price=right_candidate.neckline_price,
        breakout_close=breakout_close,
        breakout_volume=breakout_volume,
        breakout_volume_ratio=breakout_volume / breakout_average,
        bottom_distance_pct=right_candidate.bottom_distance_pct,
        rebound_up_day_ratio=right_candidate.rebound_up_day_ratio,
    )


def is_preferred_pattern(
    candidate: DoubleBottomPattern,
    incumbent: DoubleBottomPattern | None,
) -> bool:
    return bool(
        incumbent is None
        or candidate.breakout_idx > incumbent.breakout_idx
        or (
            candidate.breakout_idx == incumbent.breakout_idx
            and candidate.right_bottom_idx > incumbent.right_bottom_idx
        )
    )


def is_local_minimum(
    bars: list[HistoryBar],
    idx: int,
    *,
    before_span: int = 1,
    after_span: int | None = None,
) -> bool:
    low = bars[idx].get("low")
    if low is None:
        return False
    after_span = before_span if after_span is None else after_span
    if before_span < 0 or after_span < 0 or idx - before_span < 0 or idx + after_span >= len(bars):
        return False
    for neighbor_idx in range(idx - before_span, idx + after_span + 1):
        if neighbor_idx == idx:
            continue
        neighbor_low = bars[neighbor_idx].get("low")
        if neighbor_low is not None and neighbor_low < low:
            return False
    return True


def intermediate_lows_hold(
    bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    right_bottom_idx: int,
    floor_low: float,
) -> bool:
    if right_bottom_idx - left_bottom_idx <= 1:
        return False
    return all(
        bars[idx].get("low") is not None and float(bars[idx]["low"]) > floor_low
        for idx in range(left_bottom_idx + 1, right_bottom_idx)
    )


def compute_up_day_ratio(
    bars: list[HistoryBar],
    *,
    start_idx: int,
    end_idx: int,
) -> float | None:
    if end_idx <= start_idx:
        return None
    previous_close = bars[start_idx].get("close")
    if previous_close is None:
        return None
    up_days = 0
    directional_days = 0
    for idx in range(start_idx + 1, end_idx + 1):
        close = bars[idx].get("close")
        if close is None:
            continue
        if close > previous_close:
            up_days += 1
            directional_days += 1
        elif close < previous_close:
            directional_days += 1
        previous_close = close
    return up_days / float(directional_days) if directional_days else None


def compute_linear_trend_fit(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    count = len(values)
    x_mean = (count - 1) / 2.0
    y_mean = sum(values) / float(count)
    sum_squared_x = 0.0
    sum_xy = 0.0
    total_variance = 0.0
    for idx, value in enumerate(values):
        x_delta = idx - x_mean
        y_delta = value - y_mean
        sum_squared_x += x_delta * x_delta
        sum_xy += x_delta * y_delta
        total_variance += y_delta * y_delta
    if sum_squared_x <= 0:
        return None
    slope = sum_xy / sum_squared_x
    intercept = y_mean - slope * x_mean
    if total_variance <= 0:
        return slope, 1.0
    residual_variance = sum(
        (value - (intercept + slope * idx)) ** 2
        for idx, value in enumerate(values)
    )
    return slope, max(0.0, min(1.0, 1.0 - residual_variance / total_variance))


def has_smooth_downtrend(
    bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    downtrend_lookback: int,
    max_up_day_ratio: float,
    min_r_squared: float,
) -> bool:
    anchor_idx = left_bottom_idx - downtrend_lookback
    if anchor_idx < 0:
        return False
    ratio = compute_up_day_ratio(bars, start_idx=anchor_idx, end_idx=left_bottom_idx)
    if ratio is None or ratio > max_up_day_ratio:
        return False
    closes: list[float] = []
    for idx in range(anchor_idx, left_bottom_idx + 1):
        close = bars[idx].get("close")
        if close is None or close <= 0:
            return False
        closes.append(close)
    fit = compute_linear_trend_fit(closes)
    return bool(fit is not None and fit[0] < 0 and fit[1] >= min_r_squared)


def has_downtrend_context(
    bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    downtrend_lookback: int,
    min_drop_pct: float,
) -> bool:
    close = bars[left_bottom_idx].get("close")
    anchor_idx = left_bottom_idx - downtrend_lookback
    if close is None or anchor_idx < 0:
        return False
    anchor_close = bars[anchor_idx].get("close")
    return bool(anchor_close is not None and anchor_close > 0 and close / anchor_close - 1.0 <= -min_drop_pct)


def find_neckline(
    bars: list[HistoryBar],
    *,
    left_bottom_idx: int,
    right_bottom_idx: int,
) -> tuple[int | None, float | None]:
    if right_bottom_idx - left_bottom_idx <= 1:
        return None, None
    candidates = [
        (idx, bars[idx].get("high"))
        for idx in range(left_bottom_idx + 1, right_bottom_idx)
    ]
    valid = [(idx, value) for idx, value in candidates if value is not None]
    return max(valid, key=lambda item: item[1]) if valid else (None, None)


def match_breakout_bar(
    bar: HistoryBar,
    *,
    neckline_price: float,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
) -> tuple[float, float, float] | None:
    threshold = neckline_price * (1.0 + breakout_buffer_pct)
    high = bar.get("high")
    close = bar.get("close")
    volume = bar.get("volume")
    average = bar.get("volume_sma_20")
    if high is None or close is None or volume is None or average is None or average <= 0:
        return None
    if high <= threshold or volume / average < breakout_volume_ratio_min:
        return None
    return close, volume, average


def find_first_breakout_idx(
    bars: list[HistoryBar],
    *,
    right_bottom_idx: int,
    neckline_price: float,
    breakout_buffer_pct: float,
    breakout_volume_ratio_min: float,
    max_breakout_bars_after_right_bottom: int,
) -> int | None:
    search_end = min(len(bars), right_bottom_idx + max_breakout_bars_after_right_bottom + 1)
    for idx in range(right_bottom_idx + 1, search_end):
        if match_breakout_bar(
            bars[idx],
            neckline_price=neckline_price,
            breakout_buffer_pct=breakout_buffer_pct,
            breakout_volume_ratio_min=breakout_volume_ratio_min,
        ) is not None:
            return idx
    return None


def resolve_action(
    *,
    bars: list[HistoryBar],
    pattern: DoubleBottomPattern,
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    position: float,
    avg_entry_price: float | None = None,
) -> tuple[Literal["BUY", "SELL", "HOLD"] | None, str | None, str | None]:
    current_idx = len(bars) - 1
    current = bars[current_idx]
    current_close = current.get("close")
    current_low = current.get("low")
    current_volume = current.get("volume")
    neckline_support = pattern.neckline_price * (1.0 - float(signal_cfg["support_tolerance_pct"]))
    if position > 0:
        return resolve_exit_action(
            bars=bars,
            setup=build_setup_payload(bars, pattern),
            signal_cfg=signal_cfg,
            risk_cfg=risk_cfg,
            avg_entry_price=avg_entry_price,
        )
    if current_idx <= pattern.breakout_idx:
        return None, None, None
    if current_idx > pattern.breakout_idx + int(signal_cfg["retest_window"]):
        return None, None, None
    if current_low is None or current_close is None or current_volume is None:
        return None, None, None
    if any(
        (bar.get("close") or float("inf")) < neckline_support
        for bar in bars[pattern.breakout_idx + 1:current_idx]
    ):
        return None, None, None
    touched = current_low <= pattern.neckline_price * (1.0 + float(signal_cfg["support_tolerance_pct"]))
    held = current_low >= neckline_support and current_close >= pattern.neckline_price
    low_volume = current_volume <= pattern.breakout_volume * float(signal_cfg["retest_volume_ratio_max"])
    if touched and held and low_volume:
        return "BUY", "low-volume retest held the neckline after the double-bottom breakout", "retest"
    return None, None, None
