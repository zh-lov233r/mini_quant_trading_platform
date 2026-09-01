from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from src.services.strategy_types import HistoryBar, StageIndex, StagedPatternType


class PatternSetup(TypedDict):
    pattern_type: StagedPatternType
    setup_id: str
    stage_index: StageIndex
    stage_key: str
    stage_target_pct: float
    stage: str
    anchors: dict[str, Any]
    invalidation_price: float | None


@dataclass(frozen=True, slots=True)
class PatternContext:
    """All causally available inputs required to evaluate one symbol."""

    symbol: str
    bars: list[HistoryBar]
    signal_cfg: dict[str, Any]
    risk_cfg: dict[str, Any]
    position: float = 0.0
    avg_entry_price: float | None = None
    entry_signal_features: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PatternDecision:
    """Pattern-domain decision before it is adapted into a SignalEvent."""

    action: Literal["BUY", "SELL"]
    reason: str
    setup: PatternSetup
    strength_inputs: dict[str, float | str | None]
    score: float | None = None


class PatternEvaluator(Protocol):
    def __call__(self, context: PatternContext) -> PatternDecision | None: ...
