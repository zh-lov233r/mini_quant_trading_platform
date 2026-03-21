from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable


ENGINE_SUPPORTED_TYPES = {"trend", "mean_reversion"}
_INDICATOR_PATTERN = re.compile(r"^(EMA|SMA)(\d+)$", re.IGNORECASE)

TREND_DEFAULTS: Dict[str, Any] = {
    "signal": {
        "fast_indicator": {"kind": "ema", "window": 15},
        "slow_indicator": {"kind": "sma", "window": 200},
        "volume_multiplier": 1.5,
        "atr_multiplier": 2.0,
        "price_field": "close",
        "trigger": "cross_over",
    },
    "universe": {
        "symbols": [],
        "selection_mode": "manual",
    },
    "risk": {
        "max_positions": 10,
        "position_size_pct": 0.1,
        "stop_loss_atr": 2.0,
    },
    "execution": {
        "timeframe": "1d",
        "rebalance": "daily",
        "run_at": "close",
    },
    "metadata": {
        "description": "",
        "schema_version": 1,
    },
}

MEAN_REVERSION_DEFAULTS: Dict[str, Any] = {
    "signal": {
        "lookback_window": 20,
        "zscore_entry": 2.0,
        "zscore_exit": 0.5,
        "price_field": "close",
    },
    "universe": {
        "symbols": [],
        "selection_mode": "manual",
    },
    "risk": {
        "max_positions": 10,
        "position_size_pct": 0.1,
    },
    "execution": {
        "timeframe": "1d",
        "rebalance": "daily",
        "run_at": "close",
    },
    "metadata": {
        "description": "",
        "schema_version": 1,
    },
}

CUSTOM_DEFAULTS: Dict[str, Any] = {
    "rules": [],
    "universe": {
        "symbols": [],
        "selection_mode": "manual",
    },
    "risk": {
        "max_positions": 10,
        "position_size_pct": 0.1,
    },
    "execution": {
        "timeframe": "1d",
        "rebalance": "daily",
        "run_at": "close",
    },
    "metadata": {
        "description": "",
        "schema_version": 1,
    },
}


def build_strategy_catalog() -> list[Dict[str, Any]]:
    return [
        {
            "strategy_type": "trend",
            "label": "Trend Following",
            "description": "双均线趋势策略，带成交量过滤、ATR 风控和调仓配置。",
            "engine_ready": True,
            "defaults": copy.deepcopy(TREND_DEFAULTS),
        },
        {
            "strategy_type": "mean_reversion",
            "label": "Mean Reversion",
            "description": "均值回归配置模板，基于 z-score / ATR / 流动性特征做日线信号。",
            "engine_ready": True,
            "defaults": copy.deepcopy(MEAN_REVERSION_DEFAULTS),
        },
        {
            "strategy_type": "custom",
            "label": "Custom Config",
            "description": "自定义 JSON/DSL 策略定义。建议存储规则，不要直接存储可执行代码。",
            "engine_ready": False,
            "defaults": copy.deepcopy(CUSTOM_DEFAULTS),
        },
    ]


def normalize_strategy_params(
    strategy_type: str,
    params: Dict[str, Any],
    description: str | None = None,
) -> Dict[str, Any]:
    raw = copy.deepcopy(params or {})

    if strategy_type == "trend":
        normalized = _normalize_trend_params(raw)
    elif strategy_type == "mean_reversion":
        normalized = _normalize_mean_reversion_params(raw)
    elif strategy_type == "custom":
        normalized = _normalize_custom_params(raw)
    else:
        raise ValueError(f"unsupported strategy_type: {strategy_type}")

    metadata = normalized.setdefault("metadata", {})
    metadata["description"] = (description or metadata.get("description") or "").strip()
    metadata.setdefault("schema_version", 1)
    return normalized


def extract_description(params: Dict[str, Any] | None) -> str | None:
    if not isinstance(params, dict):
        return None
    metadata = params.get("metadata")
    if not isinstance(metadata, dict):
        return None
    description = metadata.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return None


def is_engine_ready(strategy_type: str, params: Dict[str, Any]) -> bool:
    if strategy_type not in ENGINE_SUPPORTED_TYPES:
        return False
    execution = params.get("execution") or {}
    if execution.get("timeframe") != "1d":
        return False
    signal = params.get("signal") or {}
    if strategy_type == "trend":
        return bool(signal.get("fast_indicator") and signal.get("slow_indicator"))
    if strategy_type == "mean_reversion":
        return bool(
            signal.get("lookback_window")
            and signal.get("zscore_entry")
            and signal.get("zscore_exit")
        )
    return False


def json_signature(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_runtime_payload(strategy: Any) -> Dict[str, Any]:
    normalized_params = normalize_strategy_params(
        strategy.strategy_type,
        strategy.params,
        extract_description(strategy.params),
    )
    return {
        "strategy_id": str(strategy.id),
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
        return [
            "close",
            f"zscore_{lookback}",
            "rsi_14",
            "atr_14",
            "volume_sma_20",
        ]
    return []


def _normalize_trend_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(TREND_DEFAULTS)

    if "signal" in raw:
        signal = raw.get("signal") or {}
        normalized["signal"]["fast_indicator"] = _normalize_indicator(
            signal.get("fast_indicator"),
            fallback=normalized["signal"]["fast_indicator"],
            label="signal.fast_indicator",
        )
        normalized["signal"]["slow_indicator"] = _normalize_indicator(
            signal.get("slow_indicator"),
            fallback=normalized["signal"]["slow_indicator"],
            label="signal.slow_indicator",
        )
        normalized["signal"]["volume_multiplier"] = _positive_float(
            signal.get("volume_multiplier", normalized["signal"]["volume_multiplier"]),
            "signal.volume_multiplier",
        )
        normalized["signal"]["atr_multiplier"] = _positive_float(
            signal.get("atr_multiplier", normalized["signal"]["atr_multiplier"]),
            "signal.atr_multiplier",
        )
        normalized["signal"]["price_field"] = str(
            signal.get("price_field", normalized["signal"]["price_field"])
        )
        normalized["signal"]["trigger"] = str(
            signal.get("trigger", normalized["signal"]["trigger"])
        )
    else:
        normalized["signal"]["fast_indicator"] = _normalize_indicator(
            raw.get("ema_short") or raw.get("fast_indicator"),
            fallback=normalized["signal"]["fast_indicator"],
            label="ema_short",
        )
        normalized["signal"]["slow_indicator"] = _normalize_indicator(
            raw.get("sma_long") or raw.get("slow_indicator"),
            fallback=normalized["signal"]["slow_indicator"],
            label="sma_long",
        )
        normalized["signal"]["volume_multiplier"] = _positive_float(
            raw.get("volume_multiplier", normalized["signal"]["volume_multiplier"]),
            "volume_multiplier",
        )
        normalized["signal"]["atr_multiplier"] = _positive_float(
            raw.get("atr_multiplier", normalized["signal"]["atr_multiplier"]),
            "atr_multiplier",
        )

    normalized["universe"] = _merge_nested_section(
        normalized["universe"],
        raw.get("universe"),
        symbols=raw.get("symbols") or raw.get("universe_symbols"),
    )
    normalized["risk"] = _merge_nested_section(normalized["risk"], raw.get("risk"))
    normalized["execution"] = _merge_nested_section(normalized["execution"], raw.get("execution"))
    normalized["metadata"] = _merge_nested_section(normalized["metadata"], raw.get("metadata"))

    if "max_positions" in raw:
        normalized["risk"]["max_positions"] = raw["max_positions"]
    if "position_size_pct" in raw:
        normalized["risk"]["position_size_pct"] = raw["position_size_pct"]
    if "stop_loss_atr" in raw:
        normalized["risk"]["stop_loss_atr"] = raw["stop_loss_atr"]
    if "rebalance" in raw:
        normalized["execution"]["rebalance"] = raw["rebalance"]
    if "timeframe" in raw:
        normalized["execution"]["timeframe"] = raw["timeframe"]
    if "run_at" in raw:
        normalized["execution"]["run_at"] = raw["run_at"]
    if "description" in raw:
        normalized["metadata"]["description"] = raw["description"]

    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", TREND_DEFAULTS["risk"]["max_positions"]),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", TREND_DEFAULTS["risk"]["position_size_pct"]),
        "risk.position_size_pct",
    )
    normalized["risk"]["stop_loss_atr"] = _positive_float(
        normalized["risk"].get("stop_loss_atr", normalized["signal"]["atr_multiplier"]),
        "risk.stop_loss_atr",
    )
    normalized["execution"]["timeframe"] = str(
        normalized["execution"].get("timeframe", TREND_DEFAULTS["execution"]["timeframe"])
    )
    normalized["execution"]["rebalance"] = str(
        normalized["execution"].get("rebalance", TREND_DEFAULTS["execution"]["rebalance"])
    )
    normalized["execution"]["run_at"] = str(
        normalized["execution"].get("run_at", TREND_DEFAULTS["execution"]["run_at"])
    )
    normalized["metadata"]["description"] = str(normalized["metadata"].get("description", "")).strip()
    normalized["metadata"]["schema_version"] = _positive_int(
        normalized["metadata"].get("schema_version", 1),
        "metadata.schema_version",
    )
    return normalized


def _normalize_mean_reversion_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(MEAN_REVERSION_DEFAULTS)
    normalized["signal"] = _merge_nested_section(normalized["signal"], raw.get("signal"))
    normalized["universe"] = _merge_nested_section(
        normalized["universe"],
        raw.get("universe"),
        symbols=raw.get("symbols") or raw.get("universe_symbols"),
    )
    normalized["risk"] = _merge_nested_section(normalized["risk"], raw.get("risk"))
    normalized["execution"] = _merge_nested_section(normalized["execution"], raw.get("execution"))
    normalized["metadata"] = _merge_nested_section(normalized["metadata"], raw.get("metadata"))

    if "lookback_window" in raw:
        normalized["signal"]["lookback_window"] = raw["lookback_window"]
    if "zscore_entry" in raw:
        normalized["signal"]["zscore_entry"] = raw["zscore_entry"]
    if "zscore_exit" in raw:
        normalized["signal"]["zscore_exit"] = raw["zscore_exit"]
    if "description" in raw:
        normalized["metadata"]["description"] = raw["description"]

    normalized["signal"]["lookback_window"] = _positive_int(
        normalized["signal"].get("lookback_window"),
        "signal.lookback_window",
    )
    normalized["signal"]["zscore_entry"] = _positive_float(
        normalized["signal"].get("zscore_entry"),
        "signal.zscore_entry",
    )
    normalized["signal"]["zscore_exit"] = _positive_float(
        normalized["signal"].get("zscore_exit"),
        "signal.zscore_exit",
    )
    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", 10),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", 0.1),
        "risk.position_size_pct",
    )
    normalized["metadata"]["description"] = str(normalized["metadata"].get("description", "")).strip()
    normalized["metadata"]["schema_version"] = _positive_int(
        normalized["metadata"].get("schema_version", 1),
        "metadata.schema_version",
    )
    return normalized


def _normalize_custom_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(CUSTOM_DEFAULTS)
    if not isinstance(raw, dict):
        raise ValueError("custom params must be a JSON object")

    normalized.update({k: copy.deepcopy(v) for k, v in raw.items() if k not in {"universe", "risk", "execution", "metadata"}})
    normalized["universe"] = _merge_nested_section(
        normalized["universe"],
        raw.get("universe"),
        symbols=raw.get("symbols") or raw.get("universe_symbols"),
    )
    normalized["risk"] = _merge_nested_section(normalized["risk"], raw.get("risk"))
    normalized["execution"] = _merge_nested_section(normalized["execution"], raw.get("execution"))
    normalized["metadata"] = _merge_nested_section(normalized["metadata"], raw.get("metadata"))

    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", 10),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", 0.1),
        "risk.position_size_pct",
    )
    normalized["metadata"]["description"] = str(normalized["metadata"].get("description", "")).strip()
    normalized["metadata"]["schema_version"] = _positive_int(
        normalized["metadata"].get("schema_version", 1),
        "metadata.schema_version",
    )
    return normalized


def _merge_nested_section(
    defaults: Dict[str, Any],
    incoming: Any,
    *,
    symbols: Any = None,
) -> Dict[str, Any]:
    merged = copy.deepcopy(defaults)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            merged[key] = copy.deepcopy(value)
    if symbols is not None:
        merged["symbols"] = symbols
    return merged


def _normalize_indicator(value: Any, *, fallback: Dict[str, Any], label: str) -> Dict[str, Any]:
    if value in (None, ""):
        return copy.deepcopy(fallback)

    if isinstance(value, str):
        matched = _INDICATOR_PATTERN.match(value.strip())
        if not matched:
            raise ValueError(f"{label} must look like EMA15 or SMA200")
        return {"kind": matched.group(1).lower(), "window": int(matched.group(2))}

    if isinstance(value, dict):
        kind = str(value.get("kind", fallback.get("kind", "ema"))).lower()
        if kind not in {"ema", "sma"}:
            raise ValueError(f"{label}.kind must be ema or sma")
        window = _positive_int(value.get("window", fallback.get("window")), f"{label}.window")
        return {"kind": kind, "window": window}

    raise ValueError(f"{label} must be a string or object")


def _normalize_symbols(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        raw_items = list(value)
    else:
        raise ValueError("symbols must be a string or array")

    symbols: list[str] = []
    seen = set()
    for item in raw_items:
        symbol = str(item).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _positive_int(value: Any, label: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if ivalue <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return ivalue


def _positive_float(value: Any, label: str) -> float:
    try:
        fvalue = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive number") from exc
    if fvalue <= 0:
        raise ValueError(f"{label} must be a positive number")
    return fvalue


def _fraction(value: Any, label: str) -> float:
    fvalue = _positive_float(value, label)
    if fvalue > 1:
        raise ValueError(f"{label} must be within (0, 1]")
    return fvalue
