from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo


StrengthDirection = Literal["rise", "fall"]
NEW_YORK = ZoneInfo("America/New_York")


class SignalStrengthError(ValueError):
    """Raised when an engine-ready BUY entry cannot produce a valid strength score."""


def _finite(value: Any, label: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise SignalStrengthError(f"{label} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise SignalStrengthError(f"{label} must be a finite number")
    return normalized


def rise_score(value: Any, gate: Any, cap: Any) -> float:
    raw = _finite(value, "strength value")
    minimum = _finite(gate, "strength gate")
    maximum = _finite(cap, "strength cap")
    if maximum <= minimum:
        raise SignalStrengthError("strength cap must be greater than gate")
    return round(100.0 * min(max((raw - minimum) / (maximum - minimum), 0.0), 1.0), 2)


def fall_score(value: Any, gate: Any, ideal: Any) -> float:
    raw = _finite(value, "strength value")
    maximum = _finite(gate, "strength gate")
    minimum = _finite(ideal, "strength ideal")
    if maximum <= minimum:
        raise SignalStrengthError("strength gate must be greater than ideal")
    return round(100.0 * min(max((maximum - raw) / (maximum - minimum), 0.0), 1.0), 2)


def strength_level(score: Any) -> str:
    normalized = _finite(score, "strength score")
    if normalized < 0 or normalized > 100:
        raise SignalStrengthError("strength score must be within [0, 100]")
    if normalized < 50:
        return "weak"
    if normalized < 70:
        return "medium"
    if normalized < 85:
        return "strong"
    return "very_strong"


def strength_component(
    key: str,
    *,
    raw_value: Any,
    weight: Any,
    direction: StrengthDirection,
    gate: Any,
    cap_or_ideal: Any,
) -> dict[str, Any]:
    normalized_weight = _finite(weight, f"{key}.weight")
    if normalized_weight <= 0:
        raise SignalStrengthError(f"{key}.weight must be positive")
    raw = _finite(raw_value, f"{key}.raw_value")
    normalized_score = (
        rise_score(raw, gate, cap_or_ideal)
        if direction == "rise"
        else fall_score(raw, gate, cap_or_ideal)
    )
    return {
        "key": key,
        "raw_value": raw,
        "normalized_score": normalized_score,
        "weight": normalized_weight,
    }


def build_strength_record(
    *,
    model_version: str,
    threshold: Any,
    components: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    normalized_threshold = _finite(threshold, "signal.min_strength_score")
    if normalized_threshold < 0 or normalized_threshold > 100:
        raise SignalStrengthError("signal.min_strength_score must be within [0, 100]")
    normalized_components = [dict(component) for component in components]
    if not normalized_components:
        raise SignalStrengthError("at least one strength component is required")
    total_weight = sum(_finite(item.get("weight"), f"{item.get('key')}.weight") for item in normalized_components)
    if total_weight <= 0:
        raise SignalStrengthError("strength component weights must sum to a positive number")
    score = round(
        sum(
            _finite(item.get("normalized_score"), f"{item.get('key')}.normalized_score")
            * _finite(item.get("weight"), f"{item.get('key')}.weight")
            for item in normalized_components
        )
        / total_weight,
        2,
    )
    return {
        "score": score,
        "level": strength_level(score),
        "threshold": normalized_threshold,
        "passes_threshold": score >= normalized_threshold,
        "rank": None,
        "model_version": str(model_version),
        "components": normalized_components,
    }


def _component(
    key: str,
    raw_value: Any,
    weight: float,
    gate: float,
    cap_or_ideal: float,
    *,
    direction: StrengthDirection = "rise",
) -> dict[str, Any]:
    return strength_component(
        key,
        raw_value=raw_value,
        weight=weight,
        direction=direction,
        gate=gate,
        cap_or_ideal=cap_or_ideal,
    )


def evaluate_support_resistance_strength(
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    setup = str(candidate.get("setup") or "")
    measurements = candidate.get("strength_inputs") or {}
    threshold = signal_cfg.get("min_strength_score", 50.0)
    reward_risk = measurements.get("reward_risk", candidate.get("reward_risk"))
    min_reward_risk = float(risk_cfg["min_reward_risk"])
    reward_component = _component(
        "reward_risk",
        reward_risk,
        0.30 if setup != "resistance_breakout" else 0.20,
        min_reward_risk,
        min_reward_risk * 2.0,
    )
    if setup == "support_bounce":
        components = [
            _component(
                "confirmation_atr",
                measurements.get("confirmation_atr"),
                0.70,
                float(signal_cfg["bounce_confirmation_atr"]),
                float(signal_cfg["bounce_confirmation_atr"]) * 2.0,
            ),
            reward_component,
        ]
    elif setup == "resistance_breakout":
        components = [
            _component(
                "confirmation_atr",
                measurements.get("confirmation_atr"),
                0.45,
                float(signal_cfg["breakout_confirmation_atr"]),
                float(signal_cfg["breakout_confirmation_atr"]) * 2.0,
            ),
            _component(
                "volume_ratio",
                measurements.get("volume_ratio"),
                0.35,
                float(signal_cfg["breakout_volume_ratio_min"]),
                float(signal_cfg["breakout_volume_ratio_min"]) * 2.0,
            ),
            reward_component,
        ]
    elif setup == "breakout_retest":
        components = [
            _component(
                "hold_margin_atr",
                measurements.get("hold_margin_atr"),
                0.35,
                0.0,
                float(signal_cfg["bounce_confirmation_atr"]),
            ),
            _component(
                "retest_volume_ratio",
                measurements.get("retest_volume_ratio"),
                0.35,
                float(signal_cfg["retest_volume_ratio_max"]),
                0.0,
                direction="fall",
            ),
            reward_component,
        ]
    else:
        raise SignalStrengthError(f"unsupported support/resistance setup: {setup}")
    return build_strength_record(
        model_version=f"support_resistance:{setup}:v1",
        threshold=threshold,
        components=components,
    )


def evaluate_signal_strength(
    strategy_type: str,
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    threshold = signal_cfg.get("min_strength_score", 50.0)
    inputs = metadata.get("strength_inputs") or {}
    if strategy_type == "trend":
        components = [
            _component("separation_atr", inputs.get("separation_atr"), 0.60, 0.0, 0.5),
            _component("crossover_impulse_atr", inputs.get("crossover_impulse_atr"), 0.20, 0.0, 0.5),
            _component(
                "volume_ratio",
                inputs.get("volume_ratio"),
                0.20,
                float(signal_cfg["volume_multiplier"]),
                float(signal_cfg["volume_multiplier"]) * 2.0,
            ),
        ]
        return build_strength_record(model_version="trend:v1", threshold=threshold, components=components)
    if strategy_type == "mean_reversion":
        entry = float(signal_cfg["zscore_entry"])
        return build_strength_record(
            model_version="mean_reversion:v1",
            threshold=threshold,
            components=[_component("absolute_zscore", inputs.get("absolute_zscore"), 1.0, entry, entry * 2.0)],
        )
    if strategy_type == "momentum_breakout":
        minimum_return = float(signal_cfg["minimum_return_20d"])
        breakout_buffer = float(signal_cfg["breakout_buffer_pct"])
        volume_minimum = float(signal_cfg["volume_multiplier"])
        return build_strength_record(
            model_version="momentum_breakout:v1",
            threshold=threshold,
            components=[
                _component("return_20d", inputs.get("return_20d"), 0.40, minimum_return, minimum_return * 2.0),
                _component("price_extension", inputs.get("price_extension"), 0.35, breakout_buffer, breakout_buffer * 2.0),
                _component("volume_ratio", inputs.get("volume_ratio"), 0.25, volume_minimum, volume_minimum * 2.0),
            ],
        )
    if strategy_type == "island_reversal":
        stage = str(inputs.get("stage") or "")
        if stage == "exhaustion_gap":
            return build_strength_record(
                model_version="island_reversal:exhaustion_gap:v1",
                threshold=threshold,
                components=[
                    _component("left_gap_pct", inputs.get("left_gap_pct"), 0.60, float(signal_cfg["left_gap_min_pct"]), float(signal_cfg["left_gap_min_pct"]) * 2.0),
                    _component("left_volume_ratio", inputs.get("left_volume_ratio"), 0.40, float(signal_cfg["left_volume_ratio_max"]), 0.0, direction="fall"),
                ],
            )
        normalized_stage = {"upside_gap": "breakout", "gap_retest": "retest"}.get(stage, stage)
        common = [
            _component("left_gap_pct", inputs.get("left_gap_pct"), 0.30 if normalized_stage == "breakout" else 0.15, float(signal_cfg["left_gap_min_pct"]), float(signal_cfg["left_gap_min_pct"]) * 2.0),
            _component("right_gap_pct", inputs.get("right_gap_pct"), 0.40 if normalized_stage == "breakout" else 0.20, float(signal_cfg["right_gap_min_pct"]), float(signal_cfg["right_gap_min_pct"]) * 2.0),
            _component("breakout_volume_ratio", inputs.get("breakout_volume_ratio"), 0.30 if normalized_stage == "breakout" else 0.20, float(signal_cfg["right_volume_ratio_min"]), float(signal_cfg["right_volume_ratio_min"]) * 2.0),
        ]
        if normalized_stage == "retest":
            common.extend(
                [
                    _component("retest_volume_ratio", inputs.get("retest_volume_ratio"), 0.25, float(signal_cfg["retest_volume_ratio_max"]), 0.0, direction="fall"),
                    _component("hold_margin_atr", inputs.get("hold_margin_atr"), 0.20, 0.0, 1.0),
                ]
            )
        elif normalized_stage != "breakout":
            raise SignalStrengthError(f"unsupported island reversal stage: {stage}")
        return build_strength_record(model_version=f"island_reversal:{stage}:v1", threshold=threshold, components=common)
    if strategy_type == "double_bottom":
        stage = str(inputs.get("stage") or "retest")
        tolerance = float(signal_cfg["bottom_tolerance_pct"])
        rebound_minimum = float(signal_cfg["rebound_up_day_ratio_min"])
        volume_minimum = float(signal_cfg["breakout_volume_ratio_min"])
        breakout_buffer = float(signal_cfg["breakout_buffer_pct"])
        if stage == "second_bottom":
            return build_strength_record(
                model_version="double_bottom:second_bottom:v1",
                threshold=threshold,
                components=[
                    _component("bottom_distance_pct", inputs.get("bottom_distance_pct"), 0.40, tolerance, 0.0, direction="fall"),
                    _component("rebound_up_day_ratio", inputs.get("rebound_up_day_ratio"), 0.30, rebound_minimum, 1.0),
                    _component("current_volume_ratio", inputs.get("current_volume_ratio"), 0.30, float(signal_cfg["second_bottom_volume_ratio_max"]), 0.0, direction="fall"),
                ],
            )
        if stage == "right_side_pullback":
            return build_strength_record(
                model_version="double_bottom:right_side_pullback:v1",
                threshold=threshold,
                components=[
                    _component("bottom_distance_pct", inputs.get("bottom_distance_pct"), 0.25, tolerance, 0.0, direction="fall"),
                    _component("rebound_up_day_ratio", inputs.get("rebound_up_day_ratio"), 0.25, rebound_minimum, 1.0),
                    _component("current_volume_ratio", inputs.get("current_volume_ratio"), 0.25, float(signal_cfg["second_bottom_volume_ratio_max"]), 0.0, direction="fall"),
                    _component("pullback_hold_pct", inputs.get("pullback_hold_pct"), 0.25, 0.0, 1.0),
                ],
            )
        if stage == "neckline_breakout":
            return build_strength_record(
                model_version="double_bottom:neckline_breakout:v1",
                threshold=threshold,
                components=[
                    _component("bottom_distance_pct", inputs.get("bottom_distance_pct"), 0.25, tolerance, 0.0, direction="fall"),
                    _component("rebound_up_day_ratio", inputs.get("rebound_up_day_ratio"), 0.25, rebound_minimum, 1.0),
                    _component("breakout_volume_ratio", inputs.get("breakout_volume_ratio"), 0.25, volume_minimum, volume_minimum * 2.0),
                    _component("breakout_extension_pct", inputs.get("breakout_extension_pct"), 0.25, breakout_buffer, breakout_buffer * 2.0),
                ],
            )
        return build_strength_record(
            model_version="double_bottom:retest:v1",
            threshold=threshold,
            components=[
                _component("bottom_distance_pct", inputs.get("bottom_distance_pct"), 0.25, tolerance, 0.0, direction="fall"),
                _component("rebound_up_day_ratio", inputs.get("rebound_up_day_ratio"), 0.20, rebound_minimum, 1.0),
                _component("breakout_volume_ratio", inputs.get("breakout_volume_ratio"), 0.20, volume_minimum, volume_minimum * 2.0),
                _component("breakout_extension_pct", inputs.get("breakout_extension_pct"), 0.15, breakout_buffer, breakout_buffer * 2.0),
                _component("retest_volume_ratio", inputs.get("retest_volume_ratio"), 0.20, float(signal_cfg["retest_volume_ratio_max"]), 0.0, direction="fall"),
            ],
        )
    if strategy_type in {"head_shoulders_bottom", "rounded_bottom", "v_reversal"}:
        return build_strength_record(
            model_version=f"{strategy_type}:{(metadata.get('setup') or {}).get('stage_key', 'unknown')}:v1",
            threshold=threshold,
            components=[
                _component("structure_quality", inputs.get("structure_quality"), 0.25, 0.0, 1.0),
                _component("price_confirmation", inputs.get("price_confirmation"), 0.25, 0.0, 1.0),
                _component("volume_quality", inputs.get("volume_quality"), 0.25, 0.0, 1.0),
                _component("stage_confirmation", inputs.get("stage_confirmation"), 0.25, 0.0, 1.0),
            ],
        )
    if strategy_type == "support_resistance":
        support_resistance = metadata.get("support_resistance") or {}
        strength = support_resistance.get("strength")
        if not isinstance(strength, dict):
            raise SignalStrengthError("support/resistance BUY signal is missing strength")
        return dict(strength)
    raise SignalStrengthError(f"unsupported engine-ready strategy type: {strategy_type}")


def _is_entry_buy(event: Any) -> bool:
    if getattr(event, "action", None) != "BUY":
        return False
    metadata = getattr(event, "metadata", None)
    position = (metadata or {}).get("position", 0) if isinstance(metadata, dict) else 0
    return _finite(position or 0, "signal position") >= 0


def _event_strength(event: Any) -> dict[str, Any]:
    metadata = getattr(event, "metadata", None)
    strength = metadata.get("strength") if isinstance(metadata, dict) else None
    if not isinstance(strength, dict):
        raise SignalStrengthError(f"BUY signal {getattr(event, 'symbol', '?')} is missing strength")
    _finite(strength.get("score"), "strength.score")
    return strength


def _signal_trade_date(event: Any) -> Any:
    timestamp = getattr(event, "ts", None)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise SignalStrengthError(
            f"BUY signal {getattr(event, 'symbol', '?')} must have a timezone-aware timestamp"
        )
    return timestamp.astimezone(NEW_YORK).date()


def annotate_and_rank_signals(runtime: dict[str, Any], signals: list[Any]) -> list[Any]:
    signal_cfg = runtime["params"]["signal"]
    risk_cfg = runtime["params"]["risk"]
    strategy_type = str(runtime["strategy_type"])
    entries: list[Any] = []
    for event in signals:
        if not _is_entry_buy(event):
            continue
        metadata = getattr(event, "metadata", None)
        if not isinstance(metadata, dict):
            raise SignalStrengthError(f"BUY signal {getattr(event, 'symbol', '?')} metadata must be an object")
        metadata["strength"] = evaluate_signal_strength(strategy_type, signal_cfg, risk_cfg, metadata)
        entries.append(event)

    def rank_key(event: Any) -> tuple[float, int, str]:
        strength = _event_strength(event)
        instrument_id = getattr(event, "instrument_id", None)
        stable_id = int(instrument_id) if instrument_id is not None else 2**63 - 1
        return (-float(strength["score"]), stable_id, str(getattr(event, "symbol", "")).upper())

    entries_by_trade_date: dict[Any, list[Any]] = {}
    for event in entries:
        entries_by_trade_date.setdefault(_signal_trade_date(event), []).append(event)
    for trade_date in sorted(entries_by_trade_date):
        for rank, event in enumerate(sorted(entries_by_trade_date[trade_date], key=rank_key), start=1):
            _event_strength(event)["rank"] = rank
    return signals


def ordered_entry_buy_signals(signals: Iterable[Any]) -> list[Any]:
    entries = [event for event in signals if _is_entry_buy(event)]
    for event in entries:
        _event_strength(event)
    return sorted(
        entries,
        key=lambda event: (
            int(_event_strength(event).get("rank") or 2**31 - 1),
            -float(_event_strength(event)["score"]),
            int(getattr(event, "instrument_id", None) or 2**63 - 1),
            str(getattr(event, "symbol", "")).upper(),
        ),
    )


def passes_strength_threshold(event: Any) -> bool:
    if not _is_entry_buy(event):
        return True
    return bool(_event_strength(event).get("passes_threshold"))


def get_signal_strength(event: Any) -> dict[str, Any] | None:
    metadata = getattr(event, "metadata", None)
    strength = metadata.get("strength") if isinstance(metadata, dict) else None
    return dict(strength) if isinstance(strength, dict) else None
