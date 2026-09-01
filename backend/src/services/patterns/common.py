from __future__ import annotations

import math
from typing import Any

from src.services.patterns.models import HistoryBar, PatternContext, PatternDecision
from src.services.staged_entry_service import build_pattern_setup, pattern_setup_from_metadata
from src.services.strategy_types import StageIndex, StagedPatternType


def date_text(value: Any) -> str:
    return str(value)


def unit(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 6)


def volume_ratio(bar: HistoryBar) -> float | None:
    volume = bar.get("volume")
    average = bar.get("volume_sma_20")
    if volume is None or average is None or average <= 0:
        return None
    return volume / average


def true_range(current: HistoryBar, previous_close: float | None) -> float | None:
    high = current.get("high")
    low = current.get("low")
    if high is None or low is None:
        return None
    candidates = [high - low]
    if previous_close is not None:
        candidates.extend([abs(high - previous_close), abs(low - previous_close)])
    return max(candidates)


def compute_recent_atr(bars: list[HistoryBar], window: int) -> float | None:
    if window <= 0 or len(bars) < window:
        return None
    ranges: list[float] = []
    for idx in range(len(bars)):
        previous_close = bars[idx - 1].get("close") if idx > 0 else None
        value = true_range(bars[idx], previous_close)
        if value is not None:
            ranges.append(value)
    if len(ranges) < window:
        return None
    return sum(ranges[-window:]) / float(window)


def confirmed_pivot_lows(bars: list[HistoryBar], left: int, right: int) -> list[int]:
    """Return lows whose complete right-confirmation window is already present."""
    pivots: list[int] = []
    for idx in range(left, len(bars) - right):
        low = bars[idx].get("low")
        if low is None:
            continue
        neighbors = [
            bars[pos].get("low")
            for pos in range(idx - left, idx + right + 1)
            if pos != idx
        ]
        if all(value is not None and low <= value for value in neighbors) and any(
            low < value for value in neighbors if value is not None
        ):
            pivots.append(idx)
    return pivots


def downtrend_context(bars: list[HistoryBar], idx: int, lookback: int, minimum_drop: float) -> bool:
    start = max(0, idx - lookback)
    prior = [bar.get("close") for bar in bars[start:idx]]
    current = bars[idx].get("close")
    values = [value for value in prior if value is not None]
    return bool(current is not None and values and (max(values) - current) / max(values) >= minimum_drop)


def highest_index(bars: list[HistoryBar], start: int, end: int) -> int | None:
    if end <= start:
        return None
    candidates = [(idx, bars[idx].get("high")) for idx in range(start, end + 1)]
    valid = [(idx, value) for idx, value in candidates if value is not None]
    return max(valid, key=lambda item: item[1])[0] if valid else None


def project_line(x1: int, y1: float, x2: int, y2: float, x: int) -> float:
    return y1 + ((y2 - y1) / (x2 - x1)) * (x - x1)


def quadratic_fit(values: list[float]) -> tuple[float, float, float] | None:
    n = len(values)
    if n < 3:
        return None
    xs = [idx / (n - 1) for idx in range(n)]
    sums = [sum(x**power for x in xs) for power in range(5)]
    rhs = [sum((x**power) * y for x, y in zip(xs, values)) for power in range(3)]
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
            matrix[row] = [
                matrix[row][col] - factor * matrix[column][col]
                for col in range(4)
            ]
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


def build_setup(
    *,
    pattern_type: StagedPatternType,
    context: PatternContext,
    stage_index: StageIndex,
    stage_key: str,
    anchors: dict[str, Any],
    invalidation_price: float,
    setup_id_anchors: tuple[Any, ...],
    extra: dict[str, Any],
) -> dict[str, Any]:
    dated_anchors = {
        key: date_text(context.bars[value].get("dt_ny")) if isinstance(value, int) else value
        for key, value in anchors.items()
        if key != "pullbacks"
    }
    if anchors.get("pullbacks") is not None:
        dated_anchors["pullbacks"] = [
            date_text(context.bars[idx].get("dt_ny")) for idx in anchors["pullbacks"]
        ]
    return build_pattern_setup(
        pattern_type=pattern_type,
        symbol=context.symbol,
        stage_index=stage_index,
        stage_key=stage_key,
        risk_cfg=context.risk_cfg,
        anchors=dated_anchors,
        invalidation_price=invalidation_price,
        setup_id_anchors=setup_id_anchors,
        extra=extra,
    )


def buy_decision(
    reason: str,
    setup: dict[str, Any],
    structure: float,
    price: float,
    volume: float,
    stage: float,
    *,
    score: float | None = None,
) -> PatternDecision:
    return PatternDecision(
        action="BUY",
        reason=reason,
        setup=setup,
        score=score,
        strength_inputs={
            "structure_quality": unit(structure),
            "price_confirmation": unit(price),
            "volume_quality": unit(volume),
            "stage_confirmation": unit(stage),
        },
    )


def position_exit(
    context: PatternContext,
    pattern_type: StagedPatternType,
) -> PatternDecision | None:
    if context.position <= 0 or not context.bars:
        return None
    current = context.bars[-1]
    close = current.get("close")
    low = current.get("low")
    atr = current.get("atr_14")
    setup = pattern_setup_from_metadata(context.entry_signal_features) or {
        "pattern_type": pattern_type,
        "setup_id": f"{pattern_type}:{context.symbol}:position",
        "stage_index": 3,
        "stage_key": "position",
        "stage_target_pct": 1.0,
        "stage": "position",
        "anchors": {},
        "invalidation_price": None,
    }
    invalidation = setup.get("invalidation_price")

    reason = None
    stage_key = None
    if invalidation is not None and low is not None and low < invalidation:
        reason, stage_key = "pattern invalidation price was breached", "pattern_invalidation"
    elif (
        close is not None
        and context.avg_entry_price is not None
        and context.avg_entry_price > 0
        and close <= context.avg_entry_price * (1.0 - float(context.risk_cfg["max_loss_pct"]))
    ):
        reason, stage_key = "price fell through the configured maximum loss", "max_loss_stop"
    elif (
        close is not None
        and atr is not None
        and context.avg_entry_price is not None
        and close <= context.avg_entry_price - float(context.risk_cfg["stop_loss_atr"]) * atr
    ):
        reason, stage_key = "price hit the ATR stop", "atr_stop"
    elif (
        close is not None
        and atr is not None
        and context.avg_entry_price is not None
        and close >= context.avg_entry_price + float(context.risk_cfg["take_profit_atr"]) * atr
    ):
        reason, stage_key = "price reached the ATR take-profit target", "take_profit"
    elif pattern_type == "v_reversal":
        open_price = current.get("open")
        ratio = volume_ratio(current)
        current_stage = int(setup.get("stage_index") or 0)
        if (
            current_stage < 3
            and close is not None
            and open_price is not None
            and close < open_price
            and ratio is not None
            and ratio >= float(context.signal_cfg["bearish_reversal_volume_ratio_min"])
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
        strength_inputs={
            "structure_quality": 1.0,
            "price_confirmation": 1.0,
            "volume_quality": 1.0,
            "stage_confirmation": 1.0,
        },
    )
