from __future__ import annotations

"""Persistence DTOs and thin bindings for the native support/resistance kernel.

Signal generation, Pivot/zone/regime evolution, posterior tracking, and entry
channel rules are implemented by ``quant_kernel``.  The Python dataclasses in
this module only adapt typed native audit output to database persistence and
test fixtures; they are not a separately routable strategy engine.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any, Literal

from quant_kernel import support_resistance as native_support_resistance

ZoneRole = Literal["support", "resistance"]
MarketRegime = Literal["uptrend", "downtrend", "range", "transition"]
SetupMode = Literal["support_bounce", "resistance_breakout", "breakout_retest"]
DETECTOR_IMPLEMENTATION_REVISION = native_support_resistance.DETECTOR_IMPLEMENTATION_REVISION
REGIME_LOGIC_REVISION = native_support_resistance.REGIME_LOGIC_REVISION
ENTRY_CHANNEL_SEMANTICS = native_support_resistance.ENTRY_CHANNEL_SEMANTICS


@dataclass(slots=True)
class Pivot:
    pivot_key: str
    kind: Literal["low", "high"]
    session_index: int
    trade_date: date
    confirmed_on: date
    price: float
    atr: float


@dataclass(slots=True)
class Zone:
    zone_key: str
    source_kind: Literal["low", "high"]
    role: ZoneRole
    status: str
    center: float
    lower: float
    upper: float
    atr: float
    pivot_keys: tuple[str, ...]
    pivot_count: int
    touch_count: int
    first_pivot_date: date
    last_pivot_date: date
    valid_from: date
    anchor_session_index: int = 0
    anchor_center: float | None = None
    anchor_lower: float | None = None
    anchor_upper: float | None = None
    slope_per_session: float = 0.0
    fit_residual_atr: float = 0.0
    recency_weight: float = 0.0
    last_inside: bool = False
    timeline_effective_from: date | None = None

    def __post_init__(self) -> None:
        if self.anchor_center is None:
            self.anchor_center = self.center
        if self.anchor_lower is None:
            self.anchor_lower = self.lower
        if self.anchor_upper is None:
            self.anchor_upper = self.upper

    def projected(self, session_index: int) -> "Zone":
        offset = session_index - self.anchor_session_index
        delta = self.slope_per_session * offset
        assert self.anchor_center is not None
        assert self.anchor_lower is not None
        assert self.anchor_upper is not None
        return replace(
            self,
            center=_stored_zone_price(self.anchor_center + delta),
            lower=_stored_zone_price(self.anchor_lower + delta),
            upper=_stored_zone_price(self.anchor_upper + delta),
        )

    def snapshot(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("first_pivot_date", "last_pivot_date", "valid_from", "timeline_effective_from"):
            if payload[key] is not None:
                payload[key] = payload[key].isoformat()
        payload.pop("timeline_effective_from", None)
        payload["pivot_keys"] = list(self.pivot_keys)
        return payload


@dataclass(slots=True)
class BreakoutRecord:
    zone_key: str
    breakout_date: date
    breakout_session_index: int
    breakout_volume: float
    # Kept for fixture and persistence hydration; the native kernel projects
    # the live zone and does not use these horizontal bounds.
    original_lower: float | None = None
    original_upper: float | None = None


@dataclass(slots=True)
class PendingOutcome:
    setup: SetupMode
    zone_key: str
    origin_date: date
    origin_session_index: int
    target: float
    stop: float


@dataclass(slots=True)
class SetupStats:
    wins: int = 0
    losses: int = 0
    censored: int = 0

    @property
    def resolved(self) -> int:
        return self.wins + self.losses

    @property
    def posterior(self) -> float:
        return (self.wins + 1.0) / (self.resolved + 2.0)


@dataclass(slots=True)
class SupportResistanceSymbolState:
    instrument_id: int | None = None
    symbol: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)
    zones: dict[str, Zone] = field(default_factory=dict)
    breakouts: dict[str, BreakoutRecord] = field(default_factory=dict)
    pending_outcomes: list[PendingOutcome] = field(default_factory=list)
    stats: dict[SetupMode, SetupStats] = field(
        default_factory=lambda: {
            "support_bounce": SetupStats(),
            "resistance_breakout": SetupStats(),
            "breakout_retest": SetupStats(),
        }
    )
    events: list[dict[str, Any]] = field(default_factory=list)
    zone_versions: list[dict[str, Any]] = field(default_factory=list)
    version_signatures: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    cached_zone_timeline: list[dict[str, Any]] = field(default_factory=list)
    regime_versions: list[dict[str, Any]] = field(default_factory=list)
    current_regime: MarketRegime = "transition"
    current_regime_evidence: dict[str, Any] = field(default_factory=dict)
    cached_regime_timeline: list[dict[str, Any]] = field(default_factory=list)
    cached_lifecycle_events: set[tuple[date, str, str]] = field(default_factory=set)
    current_entry_channel: dict[str, Any] | None = None


@dataclass(slots=True)
class SupportResistanceState:
    symbols: dict[str, SupportResistanceSymbolState] = field(default_factory=dict)


def normalized_detector_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return the cache-key subset in deterministic key order."""
    return dict(native_support_resistance.normalized_detector_params(params))


def advance_symbol(
    state: SupportResistanceSymbolState,
    snapshot: dict[str, Any],
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    *,
    emit_signals: bool = True,
) -> dict[str, Any] | None:
    """Advance one symbol by one session and optionally return one BUY/SELL decision."""
    decision = native_support_resistance.advance_symbol(
        state,
        snapshot,
        signal_cfg,
        risk_cfg,
        emit_signals,
    )
    return dict(decision) if decision is not None else None


def build_entry_channel(
    zones: list[Zone],
    close: float,
    trade_date: date,
) -> dict[str, Any]:
    """Freeze the nearest role-based inner-edge channel around one close."""
    return dict(
        native_support_resistance.build_entry_channel(
            [zone.snapshot() for zone in zones],
            close,
            trade_date,
        )
    )


def project_entry_channel(
    channel: dict[str, Any] | None,
    sessions: int = 1,
) -> dict[str, Any]:
    """Project a frozen channel without consulting future market data."""
    return dict(native_support_resistance.project_entry_channel(channel, sessions))


def entry_price_is_inside_channel(
    channel: dict[str, Any] | None,
    price: float,
) -> tuple[bool, str]:
    inside, reason = native_support_resistance.entry_price_is_inside_channel(channel, price)
    return bool(inside), str(reason)


def classify_market_regime(
    state: SupportResistanceSymbolState,
    zones: list[Zone],
    bar: dict[str, Any],
    signal_cfg: dict[str, Any],
) -> tuple[MarketRegime, dict[str, Any]]:
    """Classify one close from frozen T-1 boundaries and confirmed pivots."""
    regime, evidence = native_support_resistance.classify_market_regime(
        state,
        zones,
        bar,
        signal_cfg,
    )
    return regime, dict(evidence)


def _record_regime_version(
    state: SupportResistanceSymbolState,
    effective_from: date,
    regime: MarketRegime,
    evidence: dict[str, Any],
) -> None:
    native_support_resistance.record_regime_version(
        state,
        effective_from,
        regime,
        evidence,
    )


def _rebuild_zones(
    state: SupportResistanceSymbolState,
    bar: dict[str, Any],
    signal_cfg: dict[str, Any],
) -> None:
    native_support_resistance.rebuild_zones(state, bar, signal_cfg)


def _fit_pivot_line(
    pivots: list[Pivot],
    current_index: int,
    signal_cfg: dict[str, Any],
) -> tuple[list[Pivot], float, float, float, float] | None:
    """Fit a deterministic two-stage recency-weighted Theil-Sen line."""
    result = native_support_resistance.fit_pivot_line(
        [asdict(pivot) for pivot in pivots],
        current_index,
        signal_cfg,
    )
    if result is None:
        return None
    by_key = {pivot.pivot_key: pivot for pivot in pivots}
    return (
        [by_key[key] for key in result["inlier_pivot_keys"]],
        float(result["center"]),
        float(result["slope"]),
        float(result["residual_atr"]),
        float(result["total_weight"]),
    )


def _match_zone(
    old_zones: Any,
    source_kind: str,
    center: float,
    half_width: float,
    pivot_keys: tuple[str, ...],
) -> Zone | None:
    return native_support_resistance.match_zone(
        old_zones,
        source_kind,
        center,
        half_width,
        pivot_keys,
    )


def _new_zone_key(source_kind: str, pivots: list[Pivot]) -> str:
    return str(
        native_support_resistance.new_zone_key(
            source_kind,
            [{"pivot_key": pivot.pivot_key} for pivot in pivots],
        )
    )


def _record_zone_version(
    state: SupportResistanceSymbolState,
    zone: Zone,
    effective_date: date,
    *,
    status: str,
) -> None:
    native_support_resistance.record_zone_version(
        state,
        zone,
        effective_date,
        status,
    )


def _stored_zone_price(value: float) -> float:
    """Match the NUMERIC(24, 10) representation used by persisted zone rows."""
    return float(native_support_resistance.stored_zone_price(value))
