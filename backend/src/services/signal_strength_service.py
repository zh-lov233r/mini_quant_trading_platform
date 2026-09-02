from __future__ import annotations

import math
from typing import Any, Iterable


class SignalStrengthError(ValueError):
    pass


def _finite(value: Any, label: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise SignalStrengthError(f"{label} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise SignalStrengthError(f"{label} must be a finite number")
    return normalized


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
        raise SignalStrengthError(
            f"BUY signal {getattr(event, 'symbol', '?')} is missing native strength"
        )
    _finite(strength.get("score"), "strength.score")
    return strength


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
