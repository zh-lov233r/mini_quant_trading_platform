from __future__ import annotations

"""Causal Pivot + ATR support/resistance detector shared by signal runtimes.

The important timing rule is implemented in :func:`advance_symbol`: the current
bar is evaluated against a frozen copy of zones produced after the previous
bar.  Only after decisions and outcome resolution are complete is the current
bar appended and a newly-confirmed pivot made available to the next session.
"""

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from math import isfinite
from typing import Any, Literal


ZoneRole = Literal["support", "resistance"]
SetupMode = Literal["support_bounce", "resistance_breakout", "breakout_retest"]
SETUP_TIE_PRIORITY: dict[SetupMode, int] = {
    "breakout_retest": 0,
    "support_bounce": 1,
    "resistance_breakout": 2,
}
DETECTOR_IMPLEMENTATION_REVISION = 6
ZONE_PRICE_QUANTUM = Decimal("0.0000000001")


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
    # Kept only for deserializing older in-memory fixtures; v2 always projects
    # the live zone and never uses these horizontal bounds.
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
    cached_lifecycle_events: set[tuple[date, str, str]] = field(default_factory=set)


@dataclass(slots=True)
class SupportResistanceState:
    symbols: dict[str, SupportResistanceSymbolState] = field(default_factory=dict)


def normalized_detector_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return the cache-key subset in deterministic key order."""
    signal = params.get("signal", {}) or {}
    keys = (
        "pivot_left_bars",
        "pivot_right_bars",
        "detection_window",
        "min_line_pivots",
        "min_line_span_sessions",
        "line_inlier_tolerance_atr",
        "max_abs_slope_atr_per_session",
        "zone_half_width_atr",
        "decay_half_life",
        "breakout_confirmation_atr",
        "breakout_volume_ratio_min",
        "retest_window",
        "retest_volume_ratio_max",
    )
    return {
        "implementation_revision": DETECTOR_IMPLEMENTATION_REVISION,
        **{key: signal[key] for key in keys},
    }


def advance_symbol(
    state: SupportResistanceSymbolState,
    snapshot: dict[str, Any],
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
    *,
    emit_signals: bool = True,
) -> dict[str, Any] | None:
    """Advance one symbol by one session and optionally return one BUY/SELL decision."""
    bar = _normalize_bar(snapshot)
    if bar is None:
        return None

    session_index = len(state.history)
    if state.cached_zone_timeline:
        _activate_cached_zones(state, bar["dt_ny"])
    frozen_zones = [
        zone.projected(session_index)
        for zone in state.zones.values()
        if zone.status == "active"
    ]
    frozen_zones.sort(key=lambda zone: (zone.role, zone.center, zone.zone_key))

    position = _number(snapshot.get("position")) or 0.0
    exit_decision = _resolve_exit(snapshot, bar, risk_cfg) if position > 0 else None

    candidates = _detect_candidates(
        state,
        bar,
        frozen_zones,
        session_index,
        signal_cfg,
        risk_cfg,
    )
    selected = _select_candidate(candidates)

    # Scores for day T are frozen before consuming any outcome that resolves on
    # day T.  This keeps the expanding posterior strictly prior to the signal
    # date, even though the current high/low are available by the close.
    _resolve_prior_outcomes(state, bar, session_index, signal_cfg)
    for candidate in candidates:
        state.events.append(
            {
                "event_date": bar["dt_ny"].isoformat(),
                "event_type": "candidate",
                **candidate,
            }
        )
        atr = bar["atr_14"]
        state.pending_outcomes.append(
            PendingOutcome(
                setup=candidate["setup"],
                zone_key=candidate["zone_key"],
                origin_date=bar["dt_ny"],
                origin_session_index=session_index,
                target=bar["close"] + float(signal_cfg["score_target_atr"]) * atr,
                stop=bar["close"] - float(signal_cfg["score_stop_atr"]) * atr,
            )
        )

    if selected is not None:
        state.events.append(
            {
                "event_date": bar["dt_ny"].isoformat(),
                "event_type": "selection",
                "zone_key": selected["zone_key"],
                "setup": selected["setup"],
                "score": selected["score"],
                "score_evidence": selected["score_evidence"],
                "zone": selected["zone"],
                "candidate_setups": [candidate["setup"] for candidate in candidates],
            }
        )

    _apply_current_bar_zone_state(state, bar, session_index, signal_cfg)
    if state.cached_zone_timeline:
        _record_cached_lifecycle_events(state, bar["dt_ny"])
    state.history.append(bar)
    if not state.cached_zone_timeline:
        _confirm_pivots(state, signal_cfg)
        _rebuild_zones(state, bar, signal_cfg)

    if not emit_signals:
        return None
    if exit_decision is not None:
        return exit_decision
    if position > 0 or selected is None or not selected["entry_eligible"]:
        return None

    return {
        "action": "BUY",
        "reason": selected["reason"],
        "score": selected["score"],
        "support_resistance": {
            "zone_key": selected["zone_key"],
            "selected_setup": selected["setup"],
            "candidate_setups": [candidate["setup"] for candidate in candidates],
            "zone": selected["zone"],
            "entry_atr": bar["atr_14"],
            "entry_close": bar["close"],
            "stop_price": selected["stop_price"],
            "target_price": selected["target_price"],
            "reward_risk": selected["reward_risk"],
            "score_evidence": selected["score_evidence"],
            "candidates": candidates,
            "price_semantics": "forward_adjusted_preferred_unadjusted_fallback",
        },
    }


def replay_latest(
    snapshot: dict[str, Any],
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> tuple[dict[str, Any] | None, SupportResistanceSymbolState]:
    """Replay available history for paper signal generation using the same state machine."""
    state = SupportResistanceSymbolState()
    history = list(snapshot.get("recent_bars") or [])
    if not history or _bar_date(history[-1]) != _bar_date(snapshot):
        history.append(snapshot)
    for index, raw_bar in enumerate(history):
        replay_snapshot = dict(raw_bar)
        is_last = index == len(history) - 1
        if is_last:
            replay_snapshot.update(
                {
                    "position": snapshot.get("position"),
                    "avg_entry_price": snapshot.get("avg_entry_price"),
                    "position_holding_days": snapshot.get("position_holding_days"),
                    "entry_signal_features": snapshot.get("entry_signal_features"),
                }
            )
        decision = advance_symbol(
            state,
            replay_snapshot,
            signal_cfg,
            risk_cfg,
            emit_signals=is_last,
        )
    return decision, state


def _normalize_bar(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    trade_date = _bar_date(snapshot)
    values = {name: _number(snapshot.get(name)) for name in ("open", "high", "low", "close")}
    if trade_date is None or any(value is None for value in values.values()):
        return None
    atr = _number(snapshot.get("atr_14"))
    if atr is None or atr <= 0:
        atr = max(float(values["high"]) - float(values["low"]), float(values["close"]) * 0.005)
    return {
        "dt_ny": trade_date,
        "ts": snapshot.get("ts"),
        **{key: float(value) for key, value in values.items()},
        "volume": _number(snapshot.get("volume")) or 0.0,
        "volume_sma_20": _number(snapshot.get("volume_sma_20")) or 0.0,
        "atr_14": float(atr),
    }


def _activate_cached_zones(state: SupportResistanceSymbolState, trade_date: date) -> None:
    """Load the latest sparse versions known strictly before the current session."""
    latest: dict[str, dict[str, Any]] = {}
    for payload in state.cached_zone_timeline:
        effective_from = payload["effective_from"]
        if effective_from >= trade_date:
            continue
        previous = latest.get(payload["zone_key"])
        if previous is None or previous["effective_from"] < effective_from:
            latest[payload["zone_key"]] = payload
    activated: dict[str, Zone] = {}
    for zone_key, payload in latest.items():
        if payload["status"] != "active":
            continue
        old = state.zones.get(zone_key)
        center = float(payload["center"])
        lower = float(payload["lower"])
        upper = float(payload["upper"])
        same_cached_version = (
            old is not None and old.timeline_effective_from == payload["effective_from"]
        )
        activated[zone_key] = Zone(
            zone_key=zone_key,
            source_kind=payload["source_kind"],
            role=payload["role"],
            status="active",
            center=center,
            lower=lower,
            upper=upper,
            atr=payload["atr"],
            anchor_session_index=int(payload.get("anchor_session_index") or 0),
            anchor_center=float(payload.get("anchor_center", center)),
            anchor_lower=float(payload.get("anchor_lower", lower)),
            anchor_upper=float(payload.get("anchor_upper", upper)),
            slope_per_session=float(payload.get("slope_per_session") or 0.0),
            fit_residual_atr=float(payload.get("fit_residual_atr") or 0.0),
            recency_weight=float(payload.get("recency_weight") or 0.0),
            pivot_keys=tuple(payload["pivot_keys"]),
            pivot_count=payload["pivot_count"],
            touch_count=(
                old.touch_count
                if same_cached_version and old is not None
                else int(payload["touch_count"])
            ),
            first_pivot_date=payload["first_pivot_date"],
            last_pivot_date=payload["last_pivot_date"],
            valid_from=payload["valid_from"],
            last_inside=(
                old.last_inside
                if same_cached_version
                else bool(payload.get("last_inside", False))
            ),
            timeline_effective_from=payload["effective_from"],
        )
    state.zones = activated


def _record_cached_lifecycle_events(
    state: SupportResistanceSymbolState,
    trade_date: date,
) -> None:
    """Replay sparse end-of-session invalidations from the shared timeline."""
    for payload in state.cached_zone_timeline:
        if payload["effective_from"] != trade_date or payload["status"] != "expired":
            continue
        signature = (trade_date, str(payload["zone_key"]), "invalidation")
        if signature in state.cached_lifecycle_events:
            continue
        state.cached_lifecycle_events.add(signature)
        state.events.append(
            {
                "event_date": trade_date.isoformat(),
                "event_type": "invalidation",
                "zone_key": payload["zone_key"],
                "role": payload["role"],
            }
        )


def _resolve_prior_outcomes(
    state: SupportResistanceSymbolState,
    bar: dict[str, Any],
    session_index: int,
    signal_cfg: dict[str, Any],
) -> None:
    remaining: list[PendingOutcome] = []
    horizon = int(signal_cfg["score_outcome_window"])
    for outcome in state.pending_outcomes:
        elapsed = session_index - outcome.origin_session_index
        hit_target = bar["high"] >= outcome.target
        hit_stop = bar["low"] <= outcome.stop
        stats = state.stats[outcome.setup]
        result: str | None = None
        if hit_target and hit_stop:
            stats.losses += 1
            result = "loss_same_day_both"
        elif hit_stop:
            stats.losses += 1
            result = "loss"
        elif hit_target:
            stats.wins += 1
            result = "win"
        elif elapsed >= horizon:
            stats.censored += 1
            result = "censored"
        else:
            remaining.append(outcome)
        if result is not None:
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "score_outcome",
                    "zone_key": outcome.zone_key,
                    "setup": outcome.setup,
                    "origin_date": outcome.origin_date.isoformat(),
                    "result": result,
                    "posterior": stats.posterior,
                    "resolved_samples": stats.resolved,
                }
            )
    state.pending_outcomes = remaining


def _detect_candidates(
    state: SupportResistanceSymbolState,
    bar: dict[str, Any],
    zones: list[Zone],
    session_index: int,
    signal_cfg: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    previous_close = state.history[-1]["close"] if state.history else None
    candidates: list[dict[str, Any]] = []
    for zone in zones:
        setup: SetupMode | None = None
        is_breakout = (
            zone.role == "resistance"
            and previous_close is not None
            and previous_close <= zone.upper + float(signal_cfg["breakout_confirmation_atr"]) * bar["atr_14"]
            and bar["close"] > zone.upper + float(signal_cfg["breakout_confirmation_atr"]) * bar["atr_14"]
            and bar["volume_sma_20"] > 0
            and bar["volume"] >= float(signal_cfg["breakout_volume_ratio_min"]) * bar["volume_sma_20"]
        )
        if (
            zone.role == "support"
            and signal_cfg["support_bounce_enabled"]
            and previous_close is not None
            and previous_close > zone.upper
            and bar["low"] <= zone.upper
            and bar["close"] >= zone.upper + float(signal_cfg["bounce_confirmation_atr"]) * bar["atr_14"]
        ):
            setup = "support_bounce"
        if is_breakout:
            state.breakouts[zone.zone_key] = BreakoutRecord(
                zone_key=zone.zone_key,
                breakout_date=bar["dt_ny"],
                breakout_session_index=session_index,
                breakout_volume=bar["volume"],
            )
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "breakout",
                    "zone_key": zone.zone_key,
                    "setup": "resistance_breakout",
                    "role": zone.role,
                    "lower": zone.lower,
                    "upper": zone.upper,
                    "breakout_volume": bar["volume"],
                }
            )
            if signal_cfg["resistance_breakout_enabled"]:
                setup = "resistance_breakout"

        if setup is not None:
            candidates.append(_candidate_payload(state, setup, zone, zones, bar, risk_cfg))

    for zone_key, breakout in sorted(state.breakouts.items()):
        elapsed = session_index - breakout.breakout_session_index
        if elapsed <= 0 or elapsed > int(signal_cfg["retest_window"]):
            continue
        zone = next((item for item in zones if item.zone_key == zone_key), None)
        if zone is None:
            continue
        if (
            bar["low"] <= zone.upper
            and bar["close"] >= zone.upper
            and bar["volume"] <= breakout.breakout_volume * float(signal_cfg["retest_volume_ratio_max"])
        ):
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "retest",
                    "zone_key": zone.zone_key,
                    "setup": "breakout_retest",
                    "role": zone.role,
                    "lower": zone.lower,
                    "upper": zone.upper,
                    "breakout_date": breakout.breakout_date.isoformat(),
                    "breakout_volume": breakout.breakout_volume,
                    "retest_volume": bar["volume"],
                }
            )
            if signal_cfg["breakout_retest_enabled"]:
                candidates.append(
                    _candidate_payload(state, "breakout_retest", zone, zones, bar, risk_cfg)
                )
    candidates.sort(key=lambda item: (SETUP_TIE_PRIORITY[item["setup"]], item["zone_key"]))
    return candidates


def _candidate_payload(
    state: SupportResistanceSymbolState,
    setup: SetupMode,
    zone: Zone,
    zones: list[Zone],
    bar: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> dict[str, Any]:
    entry = bar["close"]
    stop = max(
        zone.lower,
        entry - float(risk_cfg["stop_loss_atr"]) * bar["atr_14"],
        entry * (1.0 - float(risk_cfg["max_loss_pct"])),
    )
    overhead = sorted(
        candidate.lower
        for candidate in zones
        if candidate.role == "resistance"
        and candidate.zone_key != zone.zone_key
        and candidate.lower > entry
    )
    target = overhead[0] if overhead else entry + float(risk_cfg["take_profit_atr"]) * bar["atr_14"]
    risk = entry - stop
    reward_risk = (target - entry) / risk if risk > 0 else 0.0
    eligible = reward_risk >= float(risk_cfg["min_reward_risk"])
    stats = state.stats[setup]
    return {
        "setup": setup,
        "zone_key": zone.zone_key,
        "zone": zone.snapshot(),
        "score": stats.posterior,
        "score_evidence": {
            "wins": stats.wins,
            "losses": stats.losses,
            "censored": stats.censored,
            "resolved_samples": stats.resolved,
            "alpha": stats.wins + 1,
            "beta": stats.losses + 1,
        },
        "entry_eligible": eligible,
        "rejection_reason": None if eligible else "nearest resistance yields reward/risk below minimum",
        "stop_price": stop,
        "target_price": target,
        "reward_risk": reward_risk,
        "reason": {
            "support_bounce": "confirmed bounce above a frozen support zone",
            "resistance_breakout": "volume-confirmed close above a frozen resistance zone",
            "breakout_retest": "low-volume retest held the former resistance zone",
        }[setup],
    }


def _select_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [candidate for candidate in candidates if candidate["entry_eligible"]]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda item: (-float(item["score"]), SETUP_TIE_PRIORITY[item["setup"]], item["zone_key"]),
    )


def _resolve_exit(
    snapshot: dict[str, Any],
    bar: dict[str, Any],
    risk_cfg: dict[str, Any],
) -> dict[str, Any] | None:
    features = snapshot.get("entry_signal_features") or {}
    frozen = features.get("support_resistance") if isinstance(features, dict) else None
    if not isinstance(frozen, dict):
        return None
    entry = _number(snapshot.get("avg_entry_price")) or _number(frozen.get("entry_close"))
    atr = _number(frozen.get("entry_atr")) or bar["atr_14"]
    zone = frozen.get("zone") or {}
    zone_line = _number(zone.get("lower"))
    if entry is None:
        return None
    stop = max(
        value
        for value in (
            zone_line,
            entry - float(risk_cfg["stop_loss_atr"]) * atr,
            entry * (1.0 - float(risk_cfg["max_loss_pct"])),
        )
        if value is not None
    )
    target = _number(frozen.get("target_price")) or entry + float(risk_cfg["take_profit_atr"]) * atr
    holding_days = int(snapshot.get("position_holding_days") or 0)
    if bar["close"] < stop:
        reason = "closed below the frozen zone-aware invalidation line"
    elif bar["close"] >= target:
        reason = "reached the frozen support/resistance target"
    elif holding_days >= int(risk_cfg["max_holding_days"]):
        reason = "reached the maximum support/resistance holding period"
    else:
        return None
    return {
        "action": "SELL",
        "reason": reason,
        "score": None,
        "support_resistance": {**frozen, "exit_stop_price": stop, "exit_target_price": target},
    }


def _apply_current_bar_zone_state(
    state: SupportResistanceSymbolState,
    bar: dict[str, Any],
    session_index: int,
    signal_cfg: dict[str, Any],
) -> None:
    projected_zones = {
        zone_key: zone.projected(session_index)
        for zone_key, zone in state.zones.items()
    }
    state.zones = projected_zones
    for zone in sorted(projected_zones.values(), key=lambda item: item.zone_key):
        if zone.status != "active":
            continue
        inside = bar["high"] >= zone.lower and bar["low"] <= zone.upper
        if inside and not zone.last_inside:
            zone.touch_count += 1
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "touch",
                    "zone_key": zone.zone_key,
                    "role": zone.role,
                    "lower": zone.lower,
                    "upper": zone.upper,
                }
            )
        zone.last_inside = inside

        breakout = state.breakouts.get(zone.zone_key)
        if zone.role == "resistance" and bar["close"] > zone.upper:
            old_role = zone.role
            zone.role = "support"
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "role_transition",
                    "zone_key": zone.zone_key,
                    "from_role": old_role,
                    "to_role": zone.role,
                    "lower": zone.lower,
                    "upper": zone.upper,
                    "reason": (
                        "confirmed_breakout"
                        if breakout is not None
                        and session_index == breakout.breakout_session_index
                        else "close_above_resistance"
                    ),
                }
            )
        elif zone.role == "support" and bar["close"] < zone.lower:
            old_role = zone.role
            zone.role = "resistance"
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "role_transition",
                    "zone_key": zone.zone_key,
                    "from_role": old_role,
                    "to_role": zone.role,
                    "lower": zone.lower,
                    "upper": zone.upper,
                    "reason": "support_breakdown",
                }
            )
            state.breakouts.pop(zone.zone_key, None)
        elif (
            zone.role == "support"
            and breakout is not None
            and session_index > breakout.breakout_session_index
            and session_index <= breakout.breakout_session_index + int(signal_cfg["retest_window"])
            and bar["low"] <= zone.upper
            and bar["close"] >= zone.upper
            and bar["volume"] <= breakout.breakout_volume * float(signal_cfg["retest_volume_ratio_max"])
        ):
            # Candidate detection already recorded the successful retest using
            # this session's projected bounds. The breakout-day role change is
            # retained while this record is consumed to prevent repeat setups.
            state.breakouts.pop(zone.zone_key, None)

    expired_breakouts = [
        key
        for key, breakout in state.breakouts.items()
        if session_index - breakout.breakout_session_index > int(signal_cfg["retest_window"])
    ]
    for key in expired_breakouts:
        state.breakouts.pop(key, None)


def _confirm_pivots(state: SupportResistanceSymbolState, signal_cfg: dict[str, Any]) -> None:
    left = int(signal_cfg["pivot_left_bars"])
    right = int(signal_cfg["pivot_right_bars"])
    pivot_index = len(state.history) - 1 - right
    if pivot_index < left:
        return
    window = state.history[pivot_index - left : pivot_index + right + 1]
    candidate = state.history[pivot_index]
    high_values = [bar["high"] for bar in window]
    low_values = [bar["low"] for bar in window]
    confirmed_on = state.history[-1]["dt_ny"]
    for kind, price, values in (
        ("high", candidate["high"], high_values),
        ("low", candidate["low"], low_values),
    ):
        extreme = max(values) if kind == "high" else min(values)
        if price != extreme or values.count(extreme) != 1:
            continue
        pivot_key = f"{kind}:{candidate['dt_ny'].isoformat()}"
        if any(existing.pivot_key == pivot_key for existing in state.pivots):
            continue
        state.pivots.append(
            Pivot(
                pivot_key=pivot_key,
                kind=kind,
                session_index=pivot_index,
                trade_date=candidate["dt_ny"],
                confirmed_on=confirmed_on,
                price=price,
                atr=candidate["atr_14"],
            )
        )


def _rebuild_zones(
    state: SupportResistanceSymbolState,
    bar: dict[str, Any],
    signal_cfg: dict[str, Any],
) -> None:
    current_index = len(state.history) - 1
    lookback = int(signal_cfg["detection_window"])
    state.pivots = [pivot for pivot in state.pivots if current_index - pivot.session_index < lookback]
    half_width = float(signal_cfg["zone_half_width_atr"]) * bar["atr_14"]
    old_zones = {
        key: zone.projected(current_index)
        for key, zone in state.zones.items()
    }
    rebuilt: dict[str, Zone] = {}
    for source_kind, default_role in (("low", "support"), ("high", "resistance")):
        pivots = sorted(
            (pivot for pivot in state.pivots if pivot.kind == source_kind),
            key=lambda pivot: (pivot.session_index, pivot.trade_date, pivot.pivot_key),
        )
        fit = _fit_pivot_line(pivots, current_index, signal_cfg)
        if fit is None:
            continue
        cluster, center, slope, residual_atr, recency_weight = fit
        pivot_keys = tuple(sorted(pivot.pivot_key for pivot in cluster))
        matched = _match_zone(old_zones.values(), source_kind, center, half_width, pivot_keys)
        zone_key = matched.zone_key if matched else _new_zone_key(source_kind, cluster)
        if matched is not None:
            role: ZoneRole = matched.role
        elif bar["close"] > center + half_width:
            role = "support"
        elif bar["close"] < center - half_width:
            role = "resistance"
        else:
            role = default_role  # type: ignore[assignment]
        unchanged_membership = matched is not None and matched.pivot_keys == pivot_keys
        if unchanged_membership:
            zone = replace(
                matched,
                role=role,
                status="active",
                touch_count=max(len(cluster), matched.touch_count),
            )
        else:
            stored_center = _stored_zone_price(center)
            stored_half_width = _stored_zone_price(half_width)
            zone = Zone(
                zone_key=zone_key,
                source_kind=source_kind,  # type: ignore[arg-type]
                role=role,
                status="active",
                center=stored_center,
                lower=_stored_zone_price(stored_center - stored_half_width),
                upper=_stored_zone_price(stored_center + stored_half_width),
                atr=_stored_zone_price(bar["atr_14"]),
                anchor_session_index=current_index,
                anchor_center=stored_center,
                anchor_lower=_stored_zone_price(stored_center - stored_half_width),
                anchor_upper=_stored_zone_price(stored_center + stored_half_width),
                slope_per_session=_stored_zone_price(slope),
                fit_residual_atr=_stored_zone_price(residual_atr),
                recency_weight=recency_weight,
                pivot_keys=pivot_keys,
                pivot_count=len(cluster),
                touch_count=max(len(cluster), matched.touch_count if matched else 0),
                first_pivot_date=min(pivot.trade_date for pivot in cluster),
                last_pivot_date=max(pivot.trade_date for pivot in cluster),
                valid_from=matched.valid_from if matched else bar["dt_ny"],
                last_inside=matched.last_inside if matched else False,
            )
        rebuilt[zone_key] = zone

    selected: dict[str, Zone] = {}
    for role in ("support", "resistance"):
        role_zones = [zone for zone in rebuilt.values() if zone.role == role]
        role_zones.sort(
            key=lambda zone: (
                -zone.pivot_count,
                -zone.recency_weight,
                zone.fit_residual_atr,
                abs(zone.center - bar["close"]),
                zone.zone_key,
            )
        )
        for zone in role_zones[:1]:
            selected[zone.zone_key] = zone

    for zone_key, old in old_zones.items():
        if zone_key not in selected:
            _record_zone_version(state, old, bar["dt_ny"], status="expired")
            state.events.append(
                {
                    "event_date": bar["dt_ny"].isoformat(),
                    "event_type": "invalidation",
                    "zone_key": zone_key,
                    "role": old.role,
                }
            )
    state.zones = selected
    for zone in sorted(selected.values(), key=lambda item: item.zone_key):
        _record_zone_version(state, zone, bar["dt_ny"], status="active")


def _weighted_median(values: list[tuple[float, float]]) -> float:
    weighted = sorted(values, key=lambda item: item[0])
    threshold = sum(weight for _, weight in weighted) / 2.0
    cumulative = 0.0
    for value, weight in weighted:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return weighted[-1][0]


def _fit_pivot_line(
    pivots: list[Pivot],
    current_index: int,
    signal_cfg: dict[str, Any],
) -> tuple[list[Pivot], float, float, float, float] | None:
    """Fit a deterministic two-stage recency-weighted Theil-Sen line."""
    minimum = int(signal_cfg["min_line_pivots"])
    minimum_span = int(signal_cfg["min_line_span_sessions"])
    if len(pivots) < minimum or pivots[-1].session_index - pivots[0].session_index < minimum_span:
        return None
    half_life = int(signal_cfg["decay_half_life"])
    weights = {
        pivot.pivot_key: 0.5 ** (max(current_index - pivot.session_index, 0) / half_life)
        for pivot in pivots
    }

    def fit(items: list[Pivot]) -> tuple[float, float] | None:
        slopes = [
            (
                (right.price - left.price) / (right.session_index - left.session_index),
                (weights[left.pivot_key] * weights[right.pivot_key]) ** 0.5,
            )
            for left_index, left in enumerate(items)
            for right in items[left_index + 1 :]
            if right.session_index - left.session_index >= minimum_span
        ]
        if not slopes:
            return None
        slope = _weighted_median(slopes)
        intercept = _weighted_median(
            [
                (pivot.price - slope * pivot.session_index, weights[pivot.pivot_key])
                for pivot in items
            ]
        )
        return slope, intercept

    initial = fit(pivots)
    if initial is None:
        return None
    initial_slope, initial_intercept = initial
    tolerance = float(signal_cfg["line_inlier_tolerance_atr"])
    inliers = [
        pivot
        for pivot in pivots
        if abs(pivot.price - (initial_intercept + initial_slope * pivot.session_index))
        <= tolerance * pivot.atr
    ]
    if len(inliers) < minimum or inliers[-1].session_index - inliers[0].session_index < minimum_span:
        return None
    refined = fit(inliers)
    if refined is None:
        return None
    slope, intercept = refined
    representative_atr = _weighted_median(
        [(pivot.atr, weights[pivot.pivot_key]) for pivot in inliers]
    )
    if representative_atr <= 0:
        return None
    if abs(slope) / representative_atr > float(signal_cfg["max_abs_slope_atr_per_session"]):
        return None
    total_weight = sum(weights[pivot.pivot_key] for pivot in inliers)
    residual_atr = sum(
        weights[pivot.pivot_key]
        * abs(pivot.price - (intercept + slope * pivot.session_index))
        / max(pivot.atr, 1e-12)
        for pivot in inliers
    ) / total_weight
    return (
        inliers,
        intercept + slope * current_index,
        slope,
        residual_atr,
        total_weight,
    )


def _match_zone(
    old_zones: Any,
    source_kind: str,
    center: float,
    half_width: float,
    pivot_keys: tuple[str, ...],
) -> Zone | None:
    candidates = [
        zone
        for zone in old_zones
        if zone.source_kind == source_kind
        and zone.lower <= center + half_width
        and zone.upper >= center - half_width
    ]
    if not candidates:
        return None
    exact = [zone for zone in candidates if zone.pivot_keys == pivot_keys]
    if exact:
        return min(exact, key=lambda zone: (abs(zone.center - center), zone.zone_key))
    return min(candidates, key=lambda zone: (abs(zone.center - center), zone.zone_key))


def _new_zone_key(source_kind: str, pivots: list[Pivot]) -> str:
    seed = f"{source_kind}|{pivots[0].trade_date.isoformat()}|{pivots[0].price:.8f}"
    return f"srz_{sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _record_zone_version(
    state: SupportResistanceSymbolState,
    zone: Zone,
    effective_date: date,
    *,
    status: str,
) -> None:
    signature = (
        zone.role,
        status,
        zone.pivot_keys,
        zone.anchor_session_index,
        zone.anchor_center,
        zone.slope_per_session,
    )
    if state.version_signatures.get(zone.zone_key) == signature:
        return
    state.version_signatures[zone.zone_key] = signature
    state.zone_versions.append(
        {
            **zone.snapshot(),
            "status": status,
            "effective_from": effective_date.isoformat(),
        }
    )


def _bar_date(bar: dict[str, Any]) -> date | None:
    value = bar.get("dt_ny")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    ts = bar.get("ts")
    return ts.date() if isinstance(ts, datetime) else None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _stored_zone_price(value: float) -> float:
    """Match the NUMERIC(24, 10) representation used by persisted zone rows."""
    return float(Decimal(str(value)).quantize(ZONE_PRICE_QUANTUM, rounding=ROUND_HALF_UP))
