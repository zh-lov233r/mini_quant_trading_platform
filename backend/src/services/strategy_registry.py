from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from typing import Any, Dict

import quant_kernel

from src.services.strategy_types import RuntimeStrategy


_NATIVE_CATALOG = tuple(dict(item) for item in quant_kernel.catalog())
_NATIVE_DESCRIPTOR_BY_TYPE = {
    str(item["strategy_type"]): item for item in _NATIVE_CATALOG
}
ENGINE_SUPPORTED_TYPES = set(_NATIVE_DESCRIPTOR_BY_TYPE)
MEAN_REVERSION_SUPPORTED_LOOKBACK_WINDOWS = (5, 10, 20)


@dataclass(frozen=True, slots=True)
class StrategyDataRequirements:
    current_fields: tuple[str, ...]
    previous_fields: tuple[str, ...] = ()
    history_fields: tuple[str, ...] = ()
    history_length: int = 0
    signal_metadata_fields: tuple[str, ...] = ()


def strategy_data_requirements(strategy_type: str) -> StrategyDataRequirements:
    try:
        descriptor = _NATIVE_DESCRIPTOR_BY_TYPE[str(strategy_type).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported strategy type: {strategy_type}") from exc
    features = tuple(str(value) for value in descriptor["required_features"])
    history_length = int(descriptor["history_length"])
    return StrategyDataRequirements(
        current_fields=features,
        history_fields=features if history_length else (),
        history_length=history_length,
        signal_metadata_fields=features,
    )


def _native_defaults(strategy_type: str) -> Dict[str, Any]:
    return copy.deepcopy(_NATIVE_DESCRIPTOR_BY_TYPE[strategy_type]["defaults"])


# Public aliases remain for fixture construction; their values are sourced from C++.
TREND_DEFAULTS = _native_defaults("trend")
MEAN_REVERSION_DEFAULTS = _native_defaults("mean_reversion")
MOMENTUM_BREAKOUT_DEFAULTS = _native_defaults("momentum_breakout")
ISLAND_REVERSAL_DEFAULTS = _native_defaults("island_reversal")
DOUBLE_BOTTOM_DEFAULTS = _native_defaults("double_bottom")
HEAD_SHOULDERS_BOTTOM_DEFAULTS = _native_defaults("head_shoulders_bottom")
ROUNDED_BOTTOM_DEFAULTS = _native_defaults("rounded_bottom")
V_REVERSAL_DEFAULTS = _native_defaults("v_reversal")
SUPPORT_RESISTANCE_DEFAULTS = _native_defaults("support_resistance")

def build_strategy_catalog() -> list[Dict[str, Any]]:
    return [copy.deepcopy(item) for item in _NATIVE_CATALOG]


def get_trend_engine_supported_windows() -> Dict[str, list[int]]:
    return {"ema": [12, 15, 20, 50], "sma": [10, 20, 50, 100, 200]}


def normalize_strategy_params(
    strategy_type: str,
    params: Dict[str, Any],
    description: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ValueError("strategy params must be a JSON object")
    raw = copy.deepcopy(params)
    if strategy_type in ENGINE_SUPPORTED_TYPES:
        if description is not None:
            metadata = raw.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                raw["metadata"] = metadata
            metadata["description"] = description.strip()
        return dict(quant_kernel.normalize_strategy(strategy_type, raw))
    raise ValueError(f"unsupported strategy_type: {strategy_type}")

def extract_description(params: Dict[str, Any] | None) -> str | None:
    if not isinstance(params, dict) or not isinstance(params.get("metadata"), dict):
        return None
    description = params["metadata"].get("description")
    return description.strip() if isinstance(description, str) and description.strip() else None


def json_signature(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_runtime_payload(strategy: Any) -> RuntimeStrategy:
    normalized_params = normalize_strategy_params(
        strategy.strategy_type,
        strategy.params,
        extract_description(strategy.params),
    )
    return {
        "strategy_id": str(strategy.id),
        "strategy_key": str(strategy.strategy_key),
        "display_name": strategy.name,
        "name": strategy.name,
        "version": strategy.version,
        "status": strategy.status,
        "strategy_type": strategy.strategy_type,
        "params": normalized_params,
    }


def required_feature_keys(strategy_type: str, params: Dict[str, Any]) -> list[str]:
    normalized = normalize_strategy_params(strategy_type, params, extract_description(params))
    if strategy_type == "trend":
        signal = normalized["signal"]
        fast = signal["fast_indicator"]
        slow = signal["slow_indicator"]
        return [
            "close",
            f"{fast['kind']}_{fast['window']}",
            f"{slow['kind']}_{slow['window']}",
            f"prev_{fast['kind']}_{fast['window']}",
            f"prev_{slow['kind']}_{slow['window']}",
            "volume",
            "volume_sma_20",
            "atr_14",
        ]
    if strategy_type == "mean_reversion":
        lookback = normalized["signal"]["lookback_window"]
        return ["close", f"zscore_{lookback}", "rsi_14", "atr_14", "volume_sma_20"]
    return list(_NATIVE_DESCRIPTOR_BY_TYPE.get(strategy_type, {}).get("required_features") or [])
