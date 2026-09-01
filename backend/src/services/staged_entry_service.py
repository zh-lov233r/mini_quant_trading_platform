from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, cast

from src.services.patterns.models import PatternSetup
from src.services.strategy_types import StageIndex, StagedPatternType


def build_setup_id(pattern_type: str, symbol: str, *anchors: Any) -> str:
    payload = json.dumps(
        [str(pattern_type), str(symbol).upper(), *[str(anchor) for anchor in anchors]],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{pattern_type}:{str(symbol).upper()}:{digest}"


def stage_targets(risk_cfg: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(risk_cfg["stage_1_target_pct"]),
        float(risk_cfg["stage_2_target_pct"]),
        float(risk_cfg["stage_3_target_pct"]),
    )


def build_pattern_setup(
    *,
    pattern_type: StagedPatternType,
    symbol: str,
    stage_index: StageIndex,
    stage_key: str,
    risk_cfg: dict[str, Any],
    anchors: dict[str, Any],
    invalidation_price: float | None,
    setup_id_anchors: Iterable[Any],
    extra: dict[str, Any] | None = None,
) -> PatternSetup:
    targets = stage_targets(risk_cfg)
    setup: PatternSetup = {
        "pattern_type": pattern_type,
        "setup_id": build_setup_id(pattern_type, symbol, *setup_id_anchors),
        "stage_index": stage_index,
        "stage_key": str(stage_key),
        "stage_target_pct": targets[stage_index - 1],
        "anchors": dict(anchors),
        "invalidation_price": invalidation_price,
    }
    if extra:
        setup.update(extra)
    # Keep the historical field used by existing chart and strength consumers.
    setup["stage"] = str(stage_key)
    return setup


def pattern_setup_from_metadata(metadata: dict[str, Any] | None) -> PatternSetup | None:
    if metadata is None:
        return None
    setup = metadata.get("setup")
    if setup is None:
        return None
    return cast(PatternSetup, setup)


def staged_entry_progress(entry_features: dict[str, Any] | None) -> tuple[str | None, int]:
    if entry_features is None:
        return None, 0
    setup = pattern_setup_from_metadata(entry_features)
    if setup is None:
        return None, 0
    return str(setup["setup_id"]), int(setup["stage_index"])


def can_apply_staged_entry(
    event_metadata: dict[str, Any],
    entry_features: dict[str, Any] | None,
) -> bool:
    incoming = pattern_setup_from_metadata(event_metadata)
    if incoming is None:
        return False
    current_setup_id, current_stage = staged_entry_progress(entry_features)
    if current_setup_id is None:
        return True
    # Equal-stage retries are permitted so a cash-limited or partially filled
    # order can finish its cumulative target. The executor still computes only
    # the missing notional, so a fully reached target becomes a no-op.
    return current_setup_id == str(incoming["setup_id"]) and int(incoming["stage_index"]) >= current_stage


def merge_entry_features(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    incoming_payload = dict(incoming)
    current_history = list(current.get("entry_history", [])) if current is not None else []
    history_item = {
        "setup": dict(incoming_payload.get("setup") or {}),
        "strength": dict(incoming_payload.get("strength") or {}),
    }
    current_history.append(history_item)
    incoming_payload["entry_history"] = current_history
    return incoming_payload


def select_highest_stage_signals(signals: Iterable[Any]) -> list[Any]:
    selected: dict[tuple[Any, str], Any] = {}
    passthrough: list[Any] = []
    for event in signals:
        metadata = getattr(event, "metadata", None)
        setup = pattern_setup_from_metadata(metadata)
        if setup is None:
            passthrough.append(event)
            continue
        identity = getattr(event, "instrument_id", None)
        symbol = str(getattr(event, "symbol", "")).upper()
        key = (identity if identity is not None else symbol, str(setup["setup_id"]))
        current = selected.get(key)
        current_setup = pattern_setup_from_metadata(getattr(current, "metadata", None)) if current else None
        if current_setup is None or int(setup["stage_index"]) > int(current_setup["stage_index"]):
            selected[key] = event
    return [*passthrough, *selected.values()]
