from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


HistoryBar = dict[str, Any]


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
    setup: dict[str, Any]
    strength_inputs: dict[str, float | str | None]
    score: float | None = None


class PatternEvaluator(Protocol):
    def __call__(self, context: PatternContext) -> PatternDecision | None: ...
