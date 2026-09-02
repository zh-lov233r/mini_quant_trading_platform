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

CUSTOM_DEFAULTS: Dict[str, Any] = {
    "rules": [],
    "universe": {"symbols": [], "selection_mode": "all_common_stock"},
    "risk": {"max_positions": 10, "position_size_pct": 0.1},
    "execution": {"timeframe": "1d", "rebalance": "daily", "run_at": "close"},
    "metadata": {"description": "", "schema_version": 1},
}


def build_strategy_catalog() -> list[Dict[str, Any]]:
    return [
        *[copy.deepcopy(item) for item in _NATIVE_CATALOG],
        {
            "strategy_type": "custom",
            "label": "Custom Config",
            "description": "自定义 JSON/DSL 策略定义。建议存储规则，不要直接存储可执行代码。",
            "engine_ready": False,
            "defaults": copy.deepcopy(CUSTOM_DEFAULTS),
            "parameter_schema": {"type": "object"},
            "required_features": [],
            "algorithm_revision": None,
            "history_length": 0,
        },
    ]


def get_trend_engine_supported_windows() -> Dict[str, list[int]]:
    return {"ema": [12, 15, 20, 50], "sma": [10, 20, 50, 100, 200]}


def normalize_strategy_params(
    strategy_type: str,
    params: Dict[str, Any],
    description: str | None = None,
) -> Dict[str, Any]:
    raw = copy.deepcopy(params or {})
    if strategy_type in ENGINE_SUPPORTED_TYPES:
        if description is not None:
            metadata = raw.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                raw["metadata"] = metadata
            metadata["description"] = description.strip()
        return dict(quant_kernel.normalize_strategy(strategy_type, raw))
    if strategy_type != "custom":
        raise ValueError(f"unsupported strategy_type: {strategy_type}")
    normalized = _normalize_custom_params(raw)
    metadata = normalized.setdefault("metadata", {})
    metadata["description"] = (description or metadata.get("description") or "").strip()
    metadata.setdefault("schema_version", 1)
    return normalized


def extract_description(params: Dict[str, Any] | None) -> str | None:
    if not isinstance(params, dict) or not isinstance(params.get("metadata"), dict):
        return None
    description = params["metadata"].get("description")
    return description.strip() if isinstance(description, str) and description.strip() else None


def is_engine_ready(strategy_type: str, params: Dict[str, Any]) -> bool:
    if strategy_type not in ENGINE_SUPPORTED_TYPES:
        return False
    try:
        quant_kernel.normalize_strategy(strategy_type, params)
    except (TypeError, ValueError):
        return False
    return True


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
        "engine_ready": is_engine_ready(strategy.strategy_type, normalized_params),
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


def _normalize_custom_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("custom params must be a JSON object")
    normalized = copy.deepcopy(CUSTOM_DEFAULTS)
    for key, value in raw.items():
        if key not in {"universe", "risk", "execution", "metadata"}:
            normalized[key] = copy.deepcopy(value)
    for section in ("universe", "risk", "execution", "metadata"):
        incoming = raw.get(section)
        if incoming is not None and not isinstance(incoming, dict):
            raise ValueError(f"{section} must be a JSON object")
        if incoming:
            normalized[section].update(copy.deepcopy(incoming))
    legacy_symbols = raw.get("symbols") or raw.get("universe_symbols")
    if legacy_symbols is not None:
        normalized["universe"]["symbols"] = legacy_symbols
    normalized["universe"]["symbols"] = _normalize_symbols(
        normalized["universe"].get("symbols")
    )
    normalized["universe"]["selection_mode"] = _normalize_selection_mode(
        normalized["universe"].get("selection_mode"),
        normalized["universe"]["symbols"],
    )
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions"), "risk.max_positions"
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct"), "risk.position_size_pct"
    )
    normalized["metadata"]["description"] = str(
        normalized["metadata"].get("description") or ""
    ).strip()
    normalized["metadata"]["schema_version"] = _positive_int(
        normalized["metadata"].get("schema_version"), "metadata.schema_version"
    )
    return normalized


def _normalize_symbols(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("universe.symbols must be an array")
    result: list[str] = []
    for raw in value:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _normalize_selection_mode(value: Any, symbols: list[str]) -> str:
    mode = str(value or ("explicit" if symbols else "all_common_stock")).strip()
    if mode not in {"explicit", "manual", "all_common_stock", "point_in_time_liquid"}:
        raise ValueError("universe.selection_mode is invalid")
    return mode


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if result <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return result


def _fraction(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be within (0, 1]") from exc
    if not 0 < result <= 1:
        raise ValueError(f"{label} must be within (0, 1]")
    return result
