from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Any, Literal

from src.services.staged_entry_service import build_pattern_setup, pattern_setup_from_metadata


HistoryBar = dict[str, Any]


@dataclass(frozen=True, slots=True)
class PatternDecision:
    action: Literal["BUY", "SELL"]
    reason: str
    setup: dict[str, Any]
    strength_inputs: dict[str, float | str]


def evaluate_bottom_reversal(
    *,
    pattern_type: str,
    symbol: str,
    recent_bars: list[HistoryBar],
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    position: float,
    avg_entry_price: float | None,
    entry_signal_features: dict[str, Any] | None,
) -> PatternDecision | None:
    if not recent_bars:
        return None
    exit_decision = _position_exit(
        pattern_type=pattern_type,
        symbol=symbol,
        bars=recent_bars,
        signal_cfg=signal_cfg,
        risk_cfg=risk_cfg,
        position=position,
        avg_entry_price=avg_entry_price,
        entry_signal_features=entry_signal_features,
    )
    if exit_decision is not None:
        return exit_decision
    if pattern_type == "head_shoulders_bottom":
        return _head_shoulders_bottom(symbol, recent_bars, signal_cfg, risk_cfg)
    if pattern_type == "rounded_bottom":
        return _rounded_bottom(symbol, recent_bars, signal_cfg, risk_cfg)
    if pattern_type == "v_reversal":
        return _v_reversal(symbol, recent_bars, signal_cfg, risk_cfg)
    raise ValueError(f"unsupported bottom reversal type: {pattern_type}")


def _position_exit(
    *,
    pattern_type: str,
    symbol: str,
    bars: list[HistoryBar],
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    position: float,
    avg_entry_price: float | None,
    entry_signal_features: dict[str, Any] | None,
) -> PatternDecision | None:
    if position <= 0:
        return None
    current = bars[-1]
    close = _number(current.get("close"))
    low = _number(current.get("low"))
    atr = _number(current.get("atr_14"))
    setup = pattern_setup_from_metadata(entry_signal_features) or {
        "pattern_type": pattern_type,
        "setup_id": f"{pattern_type}:{symbol}:position",
        "stage_index": 3,
        "stage_key": "position",
        "stage_target_pct": 1.0,
        "stage": "position",
        "anchors": {},
        "invalidation_price": None,
    }
    invalidation = _number(setup.get("invalidation_price"))

    reason = None
    stage_key = None
    if invalidation is not None and low is not None and low < invalidation:
        reason, stage_key = "pattern invalidation price was breached", "pattern_invalidation"
    elif (
        close is not None
        and avg_entry_price is not None
        and avg_entry_price > 0
        and close <= avg_entry_price * (1.0 - float(risk_cfg["max_loss_pct"]))
    ):
        reason, stage_key = "price fell through the configured maximum loss", "max_loss_stop"
    elif close is not None and atr is not None and avg_entry_price is not None and close <= avg_entry_price - float(risk_cfg["stop_loss_atr"]) * atr:
        reason, stage_key = "price hit the ATR stop", "atr_stop"
    elif close is not None and atr is not None and avg_entry_price is not None and close >= avg_entry_price + float(risk_cfg["take_profit_atr"]) * atr:
        reason, stage_key = "price reached the ATR take-profit target", "take_profit"
    elif pattern_type == "v_reversal":
        open_price = _number(current.get("open"))
        volume_ratio = _volume_ratio(current)
        current_stage = int(setup.get("stage_index") or 0)
        if (
            current_stage < 3
            and close is not None
            and open_price is not None
            and close < open_price
            and volume_ratio is not None
            and volume_ratio >= float(signal_cfg["bearish_reversal_volume_ratio_min"])
        ):
            reason, stage_key = "high-volume bearish reversal invalidated the V setup", "bearish_volume_failure"
    if reason is None or stage_key is None:
        return None
    exit_setup = dict(setup)
    exit_setup["exit_stage"] = stage_key
    return PatternDecision(
        action="SELL",
        reason=reason,
        setup=exit_setup,
        strength_inputs={"structure_quality": 1.0, "price_confirmation": 1.0, "volume_quality": 1.0, "stage_confirmation": 1.0},
    )


def _head_shoulders_bottom(
    symbol: str,
    bars: list[HistoryBar],
    cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> PatternDecision | None:
    left = int(cfg["pivot_left_bars"])
    right = int(cfg["pivot_right_bars"])
    pivots = _confirmed_pivot_lows(bars, left, right)
    if len(pivots) < 2:
        return None
    current_idx = len(bars) - 1
    min_gap = int(cfg["min_segment_bars"])
    max_gap = int(cfg["max_segment_bars"])
    shoulder_tol = float(cfg["shoulder_tolerance_pct"])
    head_depth = float(cfg["head_depth_min_pct"])

    # A head becomes actionable only after its right-side confirmation bars exist.
    for left_idx, head_idx in reversed(list(zip(pivots, pivots[1:]))):
        gap = head_idx - left_idx
        if not min_gap <= gap <= max_gap:
            continue
        left_low = _number(bars[left_idx].get("low"))
        head_low = _number(bars[head_idx].get("low"))
        if left_low is None or head_low is None or head_low > left_low * (1.0 - head_depth):
            continue
        if not _downtrend_context(bars, left_idx, int(cfg["downtrend_lookback"]), float(cfg["downtrend_min_drop_pct"])):
            continue
        head_volume = _volume_ratio(bars[head_idx])
        if head_volume is None or head_volume > float(cfg["head_volume_ratio_max"]):
            continue
        if current_idx == head_idx + right:
            quality = min((left_low - head_low) / max(left_low * head_depth, 1e-12), 1.0)
            setup = _pattern_setup(
                "head_shoulders_bottom", symbol, 1, "head_candidate", risk_cfg, bars,
                {"left_shoulder": left_idx, "head": head_idx},
                head_low,
                (bars[left_idx].get("dt_ny"), bars[head_idx].get("dt_ny")),
                {"left_shoulder_low": left_low, "head_low": head_low},
            )
            return _buy("confirmed a low-volume head candidate", setup, quality, quality, 1.0 - head_volume / float(cfg["head_volume_ratio_max"]), 1 / 3)

    if len(pivots) < 3:
        return None
    for left_idx, head_idx, shoulder_idx in reversed(list(zip(pivots, pivots[1:], pivots[2:]))):
        if not (min_gap <= head_idx - left_idx <= max_gap and min_gap <= shoulder_idx - head_idx <= max_gap):
            continue
        left_low = _number(bars[left_idx].get("low"))
        head_low = _number(bars[head_idx].get("low"))
        shoulder_low = _number(bars[shoulder_idx].get("low"))
        if left_low is None or head_low is None or shoulder_low is None:
            continue
        shoulder_distance = abs(shoulder_low - left_low) / left_low
        if shoulder_distance > shoulder_tol or head_low > min(left_low, shoulder_low) * (1.0 - head_depth):
            continue
        shoulder_volume = _volume_ratio(bars[shoulder_idx])
        if shoulder_volume is None or shoulder_volume > float(cfg["right_shoulder_volume_ratio_max"]):
            continue
        first_high_idx = _highest_index(bars, left_idx, head_idx)
        second_high_idx = _highest_index(bars, head_idx, shoulder_idx)
        if first_high_idx is None or second_high_idx is None or first_high_idx == second_high_idx:
            continue
        neckline = _project_line(
            first_high_idx,
            float(bars[first_high_idx]["high"]),
            second_high_idx,
            float(bars[second_high_idx]["high"]),
            current_idx,
        )
        anchors = {"left_shoulder": left_idx, "head": head_idx, "right_shoulder": shoulder_idx, "neckline_1": first_high_idx, "neckline_2": second_high_idx}
        setup_id_anchors = (bars[left_idx].get("dt_ny"), bars[head_idx].get("dt_ny"))
        structure = max(0.0, 1.0 - shoulder_distance / shoulder_tol)
        if current_idx == shoulder_idx + right:
            setup = _pattern_setup("head_shoulders_bottom", symbol, 2, "right_shoulder", risk_cfg, bars, anchors, head_low, setup_id_anchors, {"neckline_price": neckline, "head_low": head_low})
            volume_quality = max(0.0, 1.0 - shoulder_volume / float(cfg["right_shoulder_volume_ratio_max"]))
            return _buy("confirmed a low-volume right shoulder", setup, structure, structure, volume_quality, 2 / 3)
        close = _number(bars[-1].get("close"))
        volume_ratio = _volume_ratio(bars[-1])
        buffer = float(cfg["breakout_buffer_pct"])
        if close is not None and volume_ratio is not None and close >= neckline * (1.0 + buffer) and volume_ratio >= float(cfg["breakout_volume_ratio_min"]):
            setup = _pattern_setup("head_shoulders_bottom", symbol, 3, "neckline_breakout", risk_cfg, bars, anchors, head_low, setup_id_anchors, {"neckline_price": neckline, "head_low": head_low})
            price_quality = min(max((close / neckline - 1.0) / max(buffer * 2.0, 1e-12), 0.0), 1.0)
            volume_quality = min(volume_ratio / (float(cfg["breakout_volume_ratio_min"]) * 2.0), 1.0)
            return _buy("broke above the projected neckline on confirming volume", setup, structure, price_quality, volume_quality, 1.0)
    return None


def _rounded_bottom(
    symbol: str,
    bars: list[HistoryBar],
    cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> PatternDecision | None:
    if len(bars) < int(cfg["min_lookback"]):
        return None
    window = bars[-int(cfg["max_lookback"]):]
    closes = [_number(bar.get("close")) for bar in window]
    if any(value is None or value <= 0 for value in closes):
        return None
    values = [math.log(float(value)) for value in closes if value is not None]
    fit = _quadratic_fit(values)
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
    pivots = [idx for idx in _confirmed_pivot_lows(bars, int(cfg["pivot_left_bars"]), right) if idx > bottom_idx]
    qualified: list[int] = []
    for pivot_idx in pivots:
        pivot_volume = _volume_ratio(bars[pivot_idx])
        surge = max((_volume_ratio(bar) or 0.0) for bar in bars[max(bottom_idx + 1, pivot_idx - 5):pivot_idx] or [{}])
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
    structure = min(max((r_squared - float(cfg["min_r_squared"])) / max(1.0 - float(cfg["min_r_squared"]), 1e-12), 0.0), 1.0)
    for stage_index, pivot_idx in enumerate(qualified[:2], start=1):
        if current_idx != pivot_idx + right:
            continue
        stage_key = "first_right_pullback" if stage_index == 1 else "second_right_pullback"
        setup = _pattern_setup("rounded_bottom", symbol, stage_index, stage_key, risk_cfg, bars, anchors, bottom_close, setup_id_anchors, {"r_squared": r_squared, "depth_pct": depth, "rim_price": left_rim, "vertex_position": vertex_position})
        volume_ratio = _volume_ratio(bars[pivot_idx]) or float(cfg["pullback_volume_ratio_max"])
        volume_quality = max(0.0, 1.0 - volume_ratio / float(cfg["pullback_volume_ratio_max"]))
        price_quality = min(depth / (float(cfg["min_depth_pct"]) * 2.0), 1.0)
        return _buy("confirmed a higher low-volume pullback on the bowl's right side", setup, structure, price_quality, volume_quality, stage_index / 3)
    current = bars[-1]
    close = _number(current.get("close"))
    volume_ratio = _volume_ratio(current)
    buffer = float(cfg["breakout_buffer_pct"])
    if len(qualified) >= 2 and close is not None and volume_ratio is not None and close >= left_rim * (1.0 + buffer) and volume_ratio >= float(cfg["breakout_volume_ratio_min"]):
        setup = _pattern_setup("rounded_bottom", symbol, 3, "rim_breakout", risk_cfg, bars, anchors, bottom_close, setup_id_anchors, {"r_squared": r_squared, "depth_pct": depth, "rim_price": left_rim, "vertex_position": vertex_position})
        price_quality = min(max((close / left_rim - 1.0) / max(buffer * 2.0, 1e-12), 0.0), 1.0)
        volume_quality = min(volume_ratio / (float(cfg["breakout_volume_ratio_min"]) * 2.0), 1.0)
        return _buy("broke above the rounded-bottom rim on confirming volume", setup, structure, price_quality, volume_quality, 1.0)
    return None


def _v_reversal(
    symbol: str,
    bars: list[HistoryBar],
    cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> PatternDecision | None:
    anchor_idx = _find_latest_v_anchor(bars, cfg)
    if anchor_idx is None:
        return None
    current_idx = len(bars) - 1
    anchor = bars[anchor_idx]
    anchor_low = float(anchor["low"])
    setup_id_anchors = (anchor.get("dt_ny"),)
    anchors = {"pivot": anchor_idx}
    reversal_return = (float(anchor["close"]) - anchor_low) / anchor_low
    anchor_volume = _volume_ratio(anchor) or float(cfg["pivot_volume_ratio_min"])
    if current_idx == anchor_idx:
        setup = _pattern_setup("v_reversal", symbol, 1, "volume_pivot", risk_cfg, bars, anchors, anchor_low, setup_id_anchors, {"pivot_low": anchor_low})
        price_quality = min(reversal_return / (float(cfg["reversal_min_return_pct"]) * 2.0), 1.0)
        volume_quality = min(anchor_volume / (float(cfg["pivot_volume_ratio_min"]) * 2.0), 1.0)
        return _buy("confirmed a high-volume V reversal pivot", setup, price_quality, price_quality, volume_quality, 1 / 3)
    distance = current_idx - anchor_idx
    current = bars[-1]
    close = _number(current.get("close"))
    open_price = _number(current.get("open"))
    volume_ratio = _volume_ratio(current)
    continuation = bars[anchor_idx + 1:current_idx + 1]
    continuous_advance = (
        len(continuation) >= 2
        and all(
            _number(bar.get("close")) is not None
            and _number(bar.get("open")) is not None
            and float(bar["close"]) > float(bar["open"])
            and (_volume_ratio(bar) or 0.0) >= float(cfg["continuation_volume_ratio_min"])
            for bar in continuation
        )
        and all(float(continuation[idx]["close"]) > float(continuation[idx - 1]["close"]) for idx in range(1, len(continuation)))
    )
    if (
        2 <= distance <= int(cfg["continuation_window"])
        and continuous_advance
        and close is not None
        and open_price is not None
        and close > open_price
        and close > float(anchor["close"])
        and volume_ratio is not None
        and volume_ratio >= float(cfg["continuation_volume_ratio_min"])
    ):
        setup = _pattern_setup("v_reversal", symbol, 2, "continuation", risk_cfg, bars, anchors, anchor_low, setup_id_anchors, {"pivot_low": anchor_low})
        price_quality = min((close / float(anchor["close"]) - 1.0) / max(float(cfg["reversal_min_return_pct"]), 1e-12), 1.0)
        volume_quality = min(volume_ratio / (float(cfg["continuation_volume_ratio_min"]) * 2.0), 1.0)
        return _buy("continued higher with confirming volume after the V pivot", setup, price_quality, price_quality, volume_quality, 2 / 3)

    retest = _find_v_breakout_retest(bars, anchor_idx, cfg)
    if retest is None:
        return None
    breakout_idx, top = retest
    anchors["breakout"] = breakout_idx
    setup = _pattern_setup("v_reversal", symbol, 3, "top_breakout_retest", risk_cfg, bars, anchors, anchor_low, setup_id_anchors, {"pivot_low": anchor_low, "consolidation_top": top})
    breakout_volume = _volume_ratio(bars[breakout_idx]) or float(cfg["breakout_volume_ratio_min"])
    current_volume = _number(current.get("volume")) or 0.0
    breakout_raw_volume = _number(bars[breakout_idx].get("volume")) or 1.0
    volume_quality = max(0.0, 1.0 - current_volume / max(breakout_raw_volume * float(cfg["retest_volume_ratio_max"]), 1e-12))
    price_quality = min(max((float(current["close"]) - top) / max(top * float(cfg["support_tolerance_pct"]), 1e-12), 0.0), 1.0)
    structure = min(breakout_volume / (float(cfg["breakout_volume_ratio_min"]) * 2.0), 1.0)
    return _buy("low-volume retest held the V consolidation top", setup, structure, price_quality, volume_quality, 1.0)


def _find_latest_v_anchor(bars: list[HistoryBar], cfg: dict[str, Any]) -> int | None:
    lookback = int(cfg["downtrend_lookback"])
    pivot_bars = int(cfg["pivot_max_bars"])
    for idx in range(len(bars) - 1, max(lookback - 1, 0), -1):
        bar = bars[idx]
        open_price = _number(bar.get("open"))
        close = _number(bar.get("close"))
        low = _number(bar.get("low"))
        atr = _number(bar.get("atr_14"))
        volume_ratio = _volume_ratio(bar)
        if None in {open_price, close, low, atr, volume_ratio} or atr == 0 or low == 0:
            continue
        if close <= open_price or (close - low) / low < float(cfg["reversal_min_return_pct"]):
            continue
        if (close - low) / atr < float(cfg["reversal_min_atr"]):
            continue
        if volume_ratio < float(cfg["pivot_volume_ratio_min"]):
            continue
        if low > min(float(item["low"]) for item in bars[max(0, idx - pivot_bars + 1):idx + 1]):
            continue
        prior = [float(item["close"]) for item in bars[max(0, idx - lookback):idx] if _number(item.get("close")) is not None]
        if not prior or (max(prior) - low) / max(prior) < float(cfg["downtrend_min_drop_pct"]):
            continue
        return idx
    return None


def _find_v_breakout_retest(bars: list[HistoryBar], anchor_idx: int, cfg: dict[str, Any]) -> tuple[int, float] | None:
    current_idx = len(bars) - 1
    min_bars = int(cfg["consolidation_min_bars"])
    max_bars = int(cfg["consolidation_max_bars"])
    retest_window = int(cfg["retest_window"])
    tolerance = float(cfg["support_tolerance_pct"])
    current_low = _number(bars[-1].get("low"))
    current_close = _number(bars[-1].get("close"))
    current_volume = _number(bars[-1].get("volume"))
    if current_low is None or current_close is None or current_volume is None:
        return None
    for breakout_idx in range(max(anchor_idx + min_bars + 1, current_idx - retest_window), current_idx):
        consolidation = bars[max(anchor_idx + 1, breakout_idx - max_bars):breakout_idx]
        if not min_bars <= len(consolidation) <= max_bars:
            continue
        top = max(float(item["high"]) for item in consolidation)
        breakout = bars[breakout_idx]
        breakout_close = _number(breakout.get("close"))
        breakout_ratio = _volume_ratio(breakout)
        breakout_volume = _number(breakout.get("volume"))
        if breakout_close is None or breakout_ratio is None or breakout_volume is None:
            continue
        if breakout_close <= top or breakout_ratio < float(cfg["breakout_volume_ratio_min"]):
            continue
        if current_low <= top * (1.0 + tolerance) and current_low >= top * (1.0 - tolerance) and current_close >= top and current_volume <= breakout_volume * float(cfg["retest_volume_ratio_max"]):
            return breakout_idx, top
    return None


def _pattern_setup(
    pattern_type: str,
    symbol: str,
    stage_index: int,
    stage_key: str,
    risk_cfg: dict[str, Any],
    bars: list[HistoryBar],
    anchors: dict[str, Any],
    invalidation_price: float,
    setup_id_anchors: tuple[Any, ...],
    extra: dict[str, Any],
) -> dict[str, Any]:
    dated_anchors = {
        key: _date_text(bars[value].get("dt_ny")) if isinstance(value, int) else value
        for key, value in anchors.items()
        if key != "pullbacks"
    }
    if isinstance(anchors.get("pullbacks"), list):
        dated_anchors["pullbacks"] = [_date_text(bars[idx].get("dt_ny")) for idx in anchors["pullbacks"]]
    return build_pattern_setup(
        pattern_type=pattern_type,
        symbol=symbol,
        stage_index=stage_index,
        stage_key=stage_key,
        risk_cfg=risk_cfg,
        anchors=dated_anchors,
        invalidation_price=invalidation_price,
        setup_id_anchors=setup_id_anchors,
        extra=extra,
    )


def _buy(reason: str, setup: dict[str, Any], structure: float, price: float, volume: float, stage: float) -> PatternDecision:
    return PatternDecision(
        action="BUY",
        reason=reason,
        setup=setup,
        strength_inputs={
            "structure_quality": _unit(structure),
            "price_confirmation": _unit(price),
            "volume_quality": _unit(volume),
            "stage_confirmation": _unit(stage),
        },
    )


def _confirmed_pivot_lows(bars: list[HistoryBar], left: int, right: int) -> list[int]:
    pivots: list[int] = []
    for idx in range(left, len(bars) - right):
        low = _number(bars[idx].get("low"))
        if low is None:
            continue
        neighbors = [_number(bars[pos].get("low")) for pos in range(idx - left, idx + right + 1) if pos != idx]
        if all(value is not None and low <= value for value in neighbors) and any(low < value for value in neighbors if value is not None):
            pivots.append(idx)
    return pivots


def _downtrend_context(bars: list[HistoryBar], idx: int, lookback: int, minimum_drop: float) -> bool:
    start = max(0, idx - lookback)
    prior = [_number(bar.get("close")) for bar in bars[start:idx]]
    current = _number(bars[idx].get("close"))
    values = [value for value in prior if value is not None]
    return bool(current is not None and values and (max(values) - current) / max(values) >= minimum_drop)


def _highest_index(bars: list[HistoryBar], start: int, end: int) -> int | None:
    if end <= start:
        return None
    return max(range(start, end + 1), key=lambda idx: float(bars[idx]["high"]))


def _project_line(x1: int, y1: float, x2: int, y2: float, x: int) -> float:
    return y1 + ((y2 - y1) / (x2 - x1)) * (x - x1)


def _quadratic_fit(values: list[float]) -> tuple[float, float, float] | None:
    n = len(values)
    if n < 3:
        return None
    xs = [idx / (n - 1) for idx in range(n)]
    sums = [sum(x ** power for x in xs) for power in range(5)]
    rhs = [sum((x ** power) * y for x, y in zip(xs, values)) for power in range(3)]
    matrix = [
        [sums[0], sums[1], sums[2], rhs[0]],
        [sums[1], sums[2], sums[3], rhs[1]],
        [sums[2], sums[3], sums[4], rhs[2]],
    ]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        matrix[column] = [value / divisor for value in matrix[column]]
        for row in range(3):
            if row == column:
                continue
            factor = matrix[row][column]
            matrix[row] = [matrix[row][col] - factor * matrix[column][col] for col in range(4)]
    intercept, linear, quadratic = (matrix[row][3] for row in range(3))
    if quadratic == 0:
        return None
    vertex = -linear / (2.0 * quadratic)
    predicted = [intercept + linear * x + quadratic * x * x for x in xs]
    mean = sum(values) / n
    total = sum((value - mean) ** 2 for value in values)
    residual = sum((value - fitted) ** 2 for value, fitted in zip(values, predicted))
    r_squared = 1.0 - residual / total if total > 0 else 0.0
    return quadratic, vertex, r_squared


def _volume_ratio(bar: HistoryBar) -> float | None:
    volume = _number(bar.get("volume"))
    average = _number(bar.get("volume_sma_20"))
    if volume is None or average is None or average <= 0:
        return None
    return volume / average


def _number(value: Any) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _unit(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 6)
