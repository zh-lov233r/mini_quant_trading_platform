from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable


ENGINE_SUPPORTED_TYPES = {"trend", "mean_reversion", "island_reversal", "double_bottom"}
_INDICATOR_PATTERN = re.compile(r"^(EMA|SMA)(\d+)$", re.IGNORECASE)
MEAN_REVERSION_SUPPORTED_LOOKBACK_WINDOWS = (5, 10, 20)

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
        "selection_mode": "all_common_stock",
    },
    "risk": {
        "max_positions": 10,
        "position_size_pct": 0.1,
        "stop_loss_pct": 0.10,
        "stop_loss_atr": 2.0,
        "take_profit_atr": 4.0,
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
        "selection_mode": "all_common_stock",
    },
    "risk": {
        "max_positions": 10,
        "position_size_pct": 0.1,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.10,
        "max_holding_days": 0,
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

ISLAND_REVERSAL_DEFAULTS: Dict[str, Any] = {
    "signal": {
        "downtrend_lookback": 60,
        "downtrend_min_drop_pct": 0.15,
        "left_gap_min_pct": 0.02,
        "right_gap_min_pct": 0.02,
        "min_island_bars": 1,
        "max_island_bars": 8,
        "left_volume_ratio_max": 0.8,
        "right_volume_ratio_min": 1.5,
        "retest_window": 10,
        "retest_volume_ratio_max": 0.7,
        "support_tolerance_pct": 0.01,
    },
    "universe": {
        "symbols": [],
        "selection_mode": "all_common_stock",
    },
    "risk": {
        "max_positions": 6,
        "position_size_pct": 0.15,
        "stop_loss_atr": 1.5,
        "max_loss_pct": 0.10,
        "take_profit_atr": 3.0,
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

DOUBLE_BOTTOM_DEFAULTS: Dict[str, Any] = {
    "signal": {
        "downtrend_lookback": 60,
        "downtrend_min_drop_pct": 0.20,
        "downtrend_max_up_day_ratio": 0.35,
        "downtrend_min_r_squared": 0.65,
        "min_bottom_spacing": 5,
        "max_bottom_spacing": 30,
        "left_bottom_before_bars": 1,
        "left_bottom_after_bars": 1,
        "bottom_tolerance_pct": 0.03,
        "neckline_min_rebound_pct": 0.06,
        "rebound_up_day_ratio_min": 0.60,
        "second_bottom_volume_ratio_max": 0.90,
        "breakout_volume_ratio_min": 1.50,
        "max_breakout_bars_after_right_bottom": 40,
        "breakout_buffer_pct": 0.005,
        "retest_window": 10,
        "retest_volume_ratio_max": 0.80,
        "support_tolerance_pct": 0.02,
    },
    "universe": {
        "symbols": [],
        "selection_mode": "all_common_stock",
    },
    "risk": {
        "max_positions": 6,
        "position_size_pct": 0.15,
        "stop_loss_atr": 1.5,
        "max_loss_pct": 0.08,
        "take_profit_atr": 3.0,
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
        "selection_mode": "all_common_stock",
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

TREND_ENGINE_SUPPORTED_WINDOWS: Dict[str, list[int]] = {
    "ema": [12, 15, 20, 50],
    "sma": [10, 20, 50, 100, 200],
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
            "strategy_type": "island_reversal",
            "label": "Island Reversal Bottom",
            "description": "底部岛形反转策略，识别缩量向下衰竭缺口、放量向上突破缺口和缩量回踩缺口。",
            "engine_ready": True,
            "defaults": copy.deepcopy(ISLAND_REVERSAL_DEFAULTS),
        },
        {
            "strategy_type": "double_bottom",
            "label": "Double Bottom",
            "description": "保守版双底形态策略，确认长期下跌后的双底、放量突破颈线与缩量回踩。",
            "engine_ready": True,
            "defaults": copy.deepcopy(DOUBLE_BOTTOM_DEFAULTS),
        },
        {
            "strategy_type": "custom",
            "label": "Custom Config",
            "description": "自定义 JSON/DSL 策略定义。建议存储规则，不要直接存储可执行代码。",
            "engine_ready": False,
            "defaults": copy.deepcopy(CUSTOM_DEFAULTS),
        },
    ]


def get_trend_engine_supported_windows() -> Dict[str, list[int]]:
    return {
        kind: list(windows)
        for kind, windows in TREND_ENGINE_SUPPORTED_WINDOWS.items()
    }


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
    elif strategy_type == "island_reversal":
        normalized = _normalize_island_reversal_params(raw)
    elif strategy_type == "double_bottom":
        normalized = _normalize_double_bottom_params(raw)
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
    if strategy_type == "island_reversal":
        return bool(
            signal.get("downtrend_lookback")
            and signal.get("left_gap_min_pct")
            and signal.get("right_gap_min_pct")
            and signal.get("max_island_bars")
            and signal.get("retest_window")
        )
    if strategy_type == "double_bottom":
        return bool(
            signal.get("downtrend_lookback")
            and signal.get("min_bottom_spacing")
            and signal.get("max_bottom_spacing")
            and signal.get("bottom_tolerance_pct")
            and signal.get("breakout_volume_ratio_min")
            and signal.get("retest_window")
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
        "strategy_key": str(getattr(strategy, "strategy_key", strategy.name)),
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
        return [
            "close",
            f"zscore_{lookback}",
            "rsi_14",
            "atr_14",
            "volume_sma_20",
        ]
    if strategy_type == "island_reversal":
        return [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volume_sma_20",
            "atr_14",
            "ret_20d",
            "ret_60d",
            "sma_50",
        ]
    if strategy_type == "double_bottom":
        return [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volume_sma_20",
            "atr_14",
            "sma_20",
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
    if "stop_loss_pct" in raw:
        normalized["risk"]["stop_loss_pct"] = raw["stop_loss_pct"]
    if "stop_loss_atr" in raw:
        normalized["risk"]["stop_loss_atr"] = raw["stop_loss_atr"]
    if "take_profit_atr" in raw:
        normalized["risk"]["take_profit_atr"] = raw["take_profit_atr"]
    if "rebalance" in raw:
        normalized["execution"]["rebalance"] = raw["rebalance"]
    if "timeframe" in raw:
        normalized["execution"]["timeframe"] = raw["timeframe"]
    if "run_at" in raw:
        normalized["execution"]["run_at"] = raw["run_at"]
    if "description" in raw:
        normalized["metadata"]["description"] = raw["description"]

    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["universe"]["selection_mode"] = _normalize_selection_mode(
        normalized["universe"].get("selection_mode"),
        normalized["universe"]["symbols"],
    )
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", TREND_DEFAULTS["risk"]["max_positions"]),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", TREND_DEFAULTS["risk"]["position_size_pct"]),
        "risk.position_size_pct",
    )
    normalized["risk"]["stop_loss_pct"] = _fraction(
        normalized["risk"].get("stop_loss_pct", TREND_DEFAULTS["risk"]["stop_loss_pct"]),
        "risk.stop_loss_pct",
    )
    normalized["risk"]["stop_loss_atr"] = _positive_float(
        normalized["risk"].get("stop_loss_atr", normalized["signal"]["atr_multiplier"]),
        "risk.stop_loss_atr",
    )
    normalized["risk"]["take_profit_atr"] = _positive_float(
        normalized["risk"].get("take_profit_atr", TREND_DEFAULTS["risk"]["take_profit_atr"]),
        "risk.take_profit_atr",
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
    if "max_positions" in raw:
        normalized["risk"]["max_positions"] = raw["max_positions"]
    if "position_size_pct" in raw:
        normalized["risk"]["position_size_pct"] = raw["position_size_pct"]
    if "stop_loss_pct" in raw:
        normalized["risk"]["stop_loss_pct"] = raw["stop_loss_pct"]
    if "take_profit_pct" in raw:
        normalized["risk"]["take_profit_pct"] = raw["take_profit_pct"]
    if "max_holding_days" in raw:
        normalized["risk"]["max_holding_days"] = raw["max_holding_days"]
    if "rebalance" in raw:
        normalized["execution"]["rebalance"] = raw["rebalance"]
    if "timeframe" in raw:
        normalized["execution"]["timeframe"] = raw["timeframe"]
    if "run_at" in raw:
        normalized["execution"]["run_at"] = raw["run_at"]
    if "description" in raw:
        normalized["metadata"]["description"] = raw["description"]

    normalized["signal"]["lookback_window"] = _positive_int(
        normalized["signal"].get("lookback_window"),
        "signal.lookback_window",
    )
    if normalized["signal"]["lookback_window"] not in MEAN_REVERSION_SUPPORTED_LOOKBACK_WINDOWS:
        allowed = ", ".join(str(window) for window in MEAN_REVERSION_SUPPORTED_LOOKBACK_WINDOWS)
        raise ValueError(f"signal.lookback_window must be one of: {allowed}")
    normalized["signal"]["zscore_entry"] = _positive_float(
        normalized["signal"].get("zscore_entry"),
        "signal.zscore_entry",
    )
    normalized["signal"]["zscore_exit"] = _positive_float(
        normalized["signal"].get("zscore_exit"),
        "signal.zscore_exit",
    )
    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["universe"]["selection_mode"] = _normalize_selection_mode(
        normalized["universe"].get("selection_mode"),
        normalized["universe"]["symbols"],
    )
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", 10),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", MEAN_REVERSION_DEFAULTS["risk"]["position_size_pct"]),
        "risk.position_size_pct",
    )
    normalized["risk"]["stop_loss_pct"] = _fraction(
        normalized["risk"].get("stop_loss_pct", MEAN_REVERSION_DEFAULTS["risk"]["stop_loss_pct"]),
        "risk.stop_loss_pct",
    )
    normalized["risk"]["take_profit_pct"] = _fraction(
        normalized["risk"].get("take_profit_pct", MEAN_REVERSION_DEFAULTS["risk"]["take_profit_pct"]),
        "risk.take_profit_pct",
    )
    normalized["risk"]["max_holding_days"] = _non_negative_int(
        normalized["risk"].get("max_holding_days", MEAN_REVERSION_DEFAULTS["risk"]["max_holding_days"]),
        "risk.max_holding_days",
    )
    normalized["execution"]["timeframe"] = str(
        normalized["execution"].get("timeframe", MEAN_REVERSION_DEFAULTS["execution"]["timeframe"])
    )
    normalized["execution"]["rebalance"] = str(
        normalized["execution"].get("rebalance", MEAN_REVERSION_DEFAULTS["execution"]["rebalance"])
    )
    normalized["execution"]["run_at"] = str(
        normalized["execution"].get("run_at", MEAN_REVERSION_DEFAULTS["execution"]["run_at"])
    )
    normalized["metadata"]["description"] = str(normalized["metadata"].get("description", "")).strip()
    normalized["metadata"]["schema_version"] = _positive_int(
        normalized["metadata"].get("schema_version", 1),
        "metadata.schema_version",
    )
    return normalized


def _normalize_island_reversal_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(ISLAND_REVERSAL_DEFAULTS)
    normalized["signal"] = _merge_nested_section(normalized["signal"], raw.get("signal"))
    normalized["universe"] = _merge_nested_section(
        normalized["universe"],
        raw.get("universe"),
        symbols=raw.get("symbols") or raw.get("universe_symbols"),
    )
    normalized["risk"] = _merge_nested_section(normalized["risk"], raw.get("risk"))
    normalized["execution"] = _merge_nested_section(normalized["execution"], raw.get("execution"))
    normalized["metadata"] = _merge_nested_section(normalized["metadata"], raw.get("metadata"))

    for field in (
        "downtrend_lookback",
        "downtrend_min_drop_pct",
        "left_gap_min_pct",
        "right_gap_min_pct",
        "min_island_bars",
        "max_island_bars",
        "left_volume_ratio_max",
        "right_volume_ratio_min",
        "retest_window",
        "retest_volume_ratio_max",
        "support_tolerance_pct",
    ):
        if field in raw:
            normalized["signal"][field] = raw[field]

    if "max_positions" in raw:
        normalized["risk"]["max_positions"] = raw["max_positions"]
    if "position_size_pct" in raw:
        normalized["risk"]["position_size_pct"] = raw["position_size_pct"]
    if "stop_loss_atr" in raw:
        normalized["risk"]["stop_loss_atr"] = raw["stop_loss_atr"]
    if "take_profit_atr" in raw:
        normalized["risk"]["take_profit_atr"] = raw["take_profit_atr"]
    if "rebalance" in raw:
        normalized["execution"]["rebalance"] = raw["rebalance"]
    if "timeframe" in raw:
        normalized["execution"]["timeframe"] = raw["timeframe"]
    if "run_at" in raw:
        normalized["execution"]["run_at"] = raw["run_at"]
    if "description" in raw:
        normalized["metadata"]["description"] = raw["description"]

    normalized["signal"]["downtrend_lookback"] = _positive_int(
        normalized["signal"].get("downtrend_lookback"),
        "signal.downtrend_lookback",
    )
    normalized["signal"]["downtrend_min_drop_pct"] = _fraction(
        normalized["signal"].get("downtrend_min_drop_pct"),
        "signal.downtrend_min_drop_pct",
    )
    normalized["signal"]["left_gap_min_pct"] = _fraction(
        normalized["signal"].get("left_gap_min_pct"),
        "signal.left_gap_min_pct",
    )
    normalized["signal"]["right_gap_min_pct"] = _fraction(
        normalized["signal"].get("right_gap_min_pct"),
        "signal.right_gap_min_pct",
    )
    normalized["signal"]["min_island_bars"] = _positive_int(
        normalized["signal"].get("min_island_bars"),
        "signal.min_island_bars",
    )
    normalized["signal"]["max_island_bars"] = _positive_int(
        normalized["signal"].get("max_island_bars"),
        "signal.max_island_bars",
    )
    if normalized["signal"]["min_island_bars"] > normalized["signal"]["max_island_bars"]:
        raise ValueError("signal.min_island_bars cannot exceed signal.max_island_bars")
    normalized["signal"]["left_volume_ratio_max"] = _positive_float(
        normalized["signal"].get("left_volume_ratio_max"),
        "signal.left_volume_ratio_max",
    )
    normalized["signal"]["right_volume_ratio_min"] = _positive_float(
        normalized["signal"].get("right_volume_ratio_min"),
        "signal.right_volume_ratio_min",
    )
    normalized["signal"]["retest_window"] = _positive_int(
        normalized["signal"].get("retest_window"),
        "signal.retest_window",
    )
    normalized["signal"]["retest_volume_ratio_max"] = _positive_float(
        normalized["signal"].get("retest_volume_ratio_max"),
        "signal.retest_volume_ratio_max",
    )
    normalized["signal"]["support_tolerance_pct"] = _fraction(
        normalized["signal"].get("support_tolerance_pct"),
        "signal.support_tolerance_pct",
    )

    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["universe"]["selection_mode"] = _normalize_selection_mode(
        normalized["universe"].get("selection_mode"),
        normalized["universe"]["symbols"],
    )
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", ISLAND_REVERSAL_DEFAULTS["risk"]["max_positions"]),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", ISLAND_REVERSAL_DEFAULTS["risk"]["position_size_pct"]),
        "risk.position_size_pct",
    )
    normalized["risk"]["stop_loss_atr"] = _positive_float(
        normalized["risk"].get("stop_loss_atr", ISLAND_REVERSAL_DEFAULTS["risk"]["stop_loss_atr"]),
        "risk.stop_loss_atr",
    )
    normalized["risk"]["max_loss_pct"] = _fraction(
        normalized["risk"].get("max_loss_pct", ISLAND_REVERSAL_DEFAULTS["risk"]["max_loss_pct"]),
        "risk.max_loss_pct",
    )
    normalized["risk"]["take_profit_atr"] = _positive_float(
        normalized["risk"].get("take_profit_atr", ISLAND_REVERSAL_DEFAULTS["risk"]["take_profit_atr"]),
        "risk.take_profit_atr",
    )
    normalized["execution"]["timeframe"] = str(
        normalized["execution"].get("timeframe", ISLAND_REVERSAL_DEFAULTS["execution"]["timeframe"])
    )
    normalized["execution"]["rebalance"] = str(
        normalized["execution"].get("rebalance", ISLAND_REVERSAL_DEFAULTS["execution"]["rebalance"])
    )
    normalized["execution"]["run_at"] = str(
        normalized["execution"].get("run_at", ISLAND_REVERSAL_DEFAULTS["execution"]["run_at"])
    )
    normalized["metadata"]["description"] = str(normalized["metadata"].get("description", "")).strip()
    normalized["metadata"]["schema_version"] = _positive_int(
        normalized["metadata"].get("schema_version", 1),
        "metadata.schema_version",
    )
    return normalized


def _normalize_double_bottom_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(DOUBLE_BOTTOM_DEFAULTS)
    normalized["signal"] = _merge_nested_section(normalized["signal"], raw.get("signal"))
    normalized["universe"] = _merge_nested_section(
        normalized["universe"],
        raw.get("universe"),
        symbols=raw.get("symbols") or raw.get("universe_symbols"),
    )
    normalized["risk"] = _merge_nested_section(normalized["risk"], raw.get("risk"))
    normalized["execution"] = _merge_nested_section(normalized["execution"], raw.get("execution"))
    normalized["metadata"] = _merge_nested_section(normalized["metadata"], raw.get("metadata"))

    for field in (
        "downtrend_lookback",
        "downtrend_min_drop_pct",
        "downtrend_max_up_day_ratio",
        "downtrend_min_r_squared",
        "min_bottom_spacing",
        "max_bottom_spacing",
        "left_bottom_before_bars",
        "left_bottom_after_bars",
        "bottom_tolerance_pct",
        "neckline_min_rebound_pct",
        "rebound_up_day_ratio_min",
        "second_bottom_volume_ratio_max",
        "breakout_volume_ratio_min",
        "max_breakout_bars_after_right_bottom",
        "breakout_buffer_pct",
        "retest_window",
        "retest_volume_ratio_max",
        "support_tolerance_pct",
    ):
        if field in raw:
            normalized["signal"][field] = raw[field]

    if "max_positions" in raw:
        normalized["risk"]["max_positions"] = raw["max_positions"]
    if "position_size_pct" in raw:
        normalized["risk"]["position_size_pct"] = raw["position_size_pct"]
    if "stop_loss_atr" in raw:
        normalized["risk"]["stop_loss_atr"] = raw["stop_loss_atr"]
    if "max_loss_pct" in raw:
        normalized["risk"]["max_loss_pct"] = raw["max_loss_pct"]
    if "take_profit_atr" in raw:
        normalized["risk"]["take_profit_atr"] = raw["take_profit_atr"]
    if "rebalance" in raw:
        normalized["execution"]["rebalance"] = raw["rebalance"]
    if "timeframe" in raw:
        normalized["execution"]["timeframe"] = raw["timeframe"]
    if "run_at" in raw:
        normalized["execution"]["run_at"] = raw["run_at"]
    if "description" in raw:
        normalized["metadata"]["description"] = raw["description"]

    normalized["signal"]["downtrend_lookback"] = _positive_int(
        normalized["signal"].get("downtrend_lookback"),
        "signal.downtrend_lookback",
    )
    normalized["signal"]["downtrend_min_drop_pct"] = _fraction(
        normalized["signal"].get("downtrend_min_drop_pct"),
        "signal.downtrend_min_drop_pct",
    )
    normalized["signal"]["downtrend_max_up_day_ratio"] = _fraction(
        normalized["signal"].get("downtrend_max_up_day_ratio"),
        "signal.downtrend_max_up_day_ratio",
    )
    normalized["signal"]["downtrend_min_r_squared"] = _fraction(
        normalized["signal"].get("downtrend_min_r_squared"),
        "signal.downtrend_min_r_squared",
    )
    normalized["signal"]["min_bottom_spacing"] = _positive_int(
        normalized["signal"].get("min_bottom_spacing"),
        "signal.min_bottom_spacing",
    )
    normalized["signal"]["max_bottom_spacing"] = _positive_int(
        normalized["signal"].get("max_bottom_spacing"),
        "signal.max_bottom_spacing",
    )
    if normalized["signal"]["min_bottom_spacing"] > normalized["signal"]["max_bottom_spacing"]:
        raise ValueError("signal.min_bottom_spacing cannot exceed signal.max_bottom_spacing")
    normalized["signal"]["left_bottom_before_bars"] = _positive_int(
        normalized["signal"].get("left_bottom_before_bars"),
        "signal.left_bottom_before_bars",
    )
    normalized["signal"]["left_bottom_after_bars"] = _positive_int(
        normalized["signal"].get("left_bottom_after_bars"),
        "signal.left_bottom_after_bars",
    )
    normalized["signal"]["bottom_tolerance_pct"] = _fraction(
        normalized["signal"].get("bottom_tolerance_pct"),
        "signal.bottom_tolerance_pct",
    )
    normalized["signal"]["neckline_min_rebound_pct"] = _fraction(
        normalized["signal"].get("neckline_min_rebound_pct"),
        "signal.neckline_min_rebound_pct",
    )
    normalized["signal"]["rebound_up_day_ratio_min"] = _fraction(
        normalized["signal"].get("rebound_up_day_ratio_min"),
        "signal.rebound_up_day_ratio_min",
    )
    normalized["signal"]["second_bottom_volume_ratio_max"] = _positive_float(
        normalized["signal"].get("second_bottom_volume_ratio_max"),
        "signal.second_bottom_volume_ratio_max",
    )
    normalized["signal"]["breakout_volume_ratio_min"] = _positive_float(
        normalized["signal"].get("breakout_volume_ratio_min"),
        "signal.breakout_volume_ratio_min",
    )
    normalized["signal"]["max_breakout_bars_after_right_bottom"] = _positive_int(
        normalized["signal"].get("max_breakout_bars_after_right_bottom"),
        "signal.max_breakout_bars_after_right_bottom",
    )
    normalized["signal"]["breakout_buffer_pct"] = _fraction(
        normalized["signal"].get("breakout_buffer_pct"),
        "signal.breakout_buffer_pct",
    )
    normalized["signal"]["retest_window"] = _positive_int(
        normalized["signal"].get("retest_window"),
        "signal.retest_window",
    )
    normalized["signal"]["retest_volume_ratio_max"] = _positive_float(
        normalized["signal"].get("retest_volume_ratio_max"),
        "signal.retest_volume_ratio_max",
    )
    normalized["signal"]["support_tolerance_pct"] = _fraction(
        normalized["signal"].get("support_tolerance_pct"),
        "signal.support_tolerance_pct",
    )

    normalized["universe"]["symbols"] = _normalize_symbols(normalized["universe"].get("symbols", []))
    normalized["universe"]["selection_mode"] = _normalize_selection_mode(
        normalized["universe"].get("selection_mode"),
        normalized["universe"]["symbols"],
    )
    normalized["risk"]["max_positions"] = _positive_int(
        normalized["risk"].get("max_positions", DOUBLE_BOTTOM_DEFAULTS["risk"]["max_positions"]),
        "risk.max_positions",
    )
    normalized["risk"]["position_size_pct"] = _fraction(
        normalized["risk"].get("position_size_pct", DOUBLE_BOTTOM_DEFAULTS["risk"]["position_size_pct"]),
        "risk.position_size_pct",
    )
    normalized["risk"]["stop_loss_atr"] = _positive_float(
        normalized["risk"].get("stop_loss_atr", DOUBLE_BOTTOM_DEFAULTS["risk"]["stop_loss_atr"]),
        "risk.stop_loss_atr",
    )
    normalized["risk"]["max_loss_pct"] = _fraction(
        normalized["risk"].get("max_loss_pct", DOUBLE_BOTTOM_DEFAULTS["risk"]["max_loss_pct"]),
        "risk.max_loss_pct",
    )
    normalized["risk"]["take_profit_atr"] = _positive_float(
        normalized["risk"].get("take_profit_atr", DOUBLE_BOTTOM_DEFAULTS["risk"]["take_profit_atr"]),
        "risk.take_profit_atr",
    )
    normalized["execution"]["timeframe"] = str(
        normalized["execution"].get("timeframe", DOUBLE_BOTTOM_DEFAULTS["execution"]["timeframe"])
    )
    normalized["execution"]["rebalance"] = str(
        normalized["execution"].get("rebalance", DOUBLE_BOTTOM_DEFAULTS["execution"]["rebalance"])
    )
    normalized["execution"]["run_at"] = str(
        normalized["execution"].get("run_at", DOUBLE_BOTTOM_DEFAULTS["execution"]["run_at"])
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
    normalized["universe"]["selection_mode"] = _normalize_selection_mode(
        normalized["universe"].get("selection_mode"),
        normalized["universe"]["symbols"],
    )
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


def _normalize_selection_mode(value: Any, symbols: list[str]) -> str:
    raw = str(value).strip().lower() if value is not None else ""
    if raw not in {"manual", "all_common_stock", "stock_basket"}:
        return "manual" if symbols else "all_common_stock"
    if raw == "manual" and not symbols:
        return "all_common_stock"
    return raw


def _positive_int(value: Any, label: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if ivalue <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return ivalue


def _non_negative_int(value: Any, label: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if ivalue < 0:
        raise ValueError(f"{label} must be a non-negative integer")
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
