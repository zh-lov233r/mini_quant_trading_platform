from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, NotRequired, TypedDict


StrategyType = Literal[
    "trend",
    "mean_reversion",
    "momentum_breakout",
    "island_reversal",
    "double_bottom",
    "head_shoulders_bottom",
    "rounded_bottom",
    "v_reversal",
    "support_resistance",
    "custom",
]

EngineReadyStrategyType = Literal[
    "trend",
    "mean_reversion",
    "momentum_breakout",
    "island_reversal",
    "double_bottom",
    "head_shoulders_bottom",
    "rounded_bottom",
    "v_reversal",
    "support_resistance",
]

StagedPatternType = Literal[
    "island_reversal",
    "double_bottom",
    "head_shoulders_bottom",
    "rounded_bottom",
    "v_reversal",
]

StageIndex = Literal[1, 2, 3]


class StrategyUniverse(TypedDict):
    symbols: list[str]
    selection_mode: str


class RuntimeStrategyParams(TypedDict):
    signal: dict[str, Any]
    universe: StrategyUniverse
    risk: dict[str, Any]
    execution: dict[str, Any]
    metadata: dict[str, Any]


class RuntimeStrategy(TypedDict):
    strategy_id: str
    strategy_key: str
    display_name: str
    name: str
    version: int
    status: str
    strategy_type: StrategyType
    engine_ready: bool
    params: RuntimeStrategyParams


class HistoryBar(TypedDict):
    dt_ny: date | None
    ts: datetime | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    atr_14: float | None
    volume_sma_20: float | None
    ret_20d: float | None
    ret_60d: float | None
    sma_20: float | None
    sma_50: float | None


class MarketSnapshot(HistoryBar, total=False):
    symbol: str
    asset_type: str
    position: float
    avg_entry_price: float | None
    entry_trade_date: date | None
    position_holding_days: int | None
    entry_signal_features: dict[str, Any] | None
    recent_bars: list[HistoryBar]
    instrument_id: int
    sma_10: float | None
    sma_100: float | None
    sma_200: float | None
    ema_12: float | None
    ema_15: float | None
    ema_20: float | None
    ema_50: float | None
    rsi_2: float | None
    rsi_5: float | None
    rsi_14: float | None
    zscore_5: float | None
    zscore_10: float | None
    zscore_20: float | None
    prev_fast: NotRequired[float | None]
    prev_slow: NotRequired[float | None]


MarketDataBySymbol = dict[str, MarketSnapshot]
