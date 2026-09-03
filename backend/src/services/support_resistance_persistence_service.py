from __future__ import annotations

"""Persistence and cache identity helpers for support/resistance materializations."""

import json
from datetime import date, datetime, timezone
from hashlib import sha256
from math import isfinite
from time import perf_counter
from typing import Any, Callable, Iterable
from uuid import uuid4

from sqlalchemy import bindparam, delete, insert, select, text
from sqlalchemy.orm import Session

from src.models.tables import (
    Instrument,
    StrategyRun,
    SupportResistanceMaterialization,
    SupportResistanceRegimeVersion,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    SupportResistanceZoneVersion,
)
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
    normalized_detector_params,
)


BATCH_INSERT_SIZE = 5_000
NUMERIC_24_10_ABS_LIMIT = 100_000_000_000_000.0
NUMERIC_20_10_ABS_LIMIT = 10_000_000_000.0
PersistenceProgressCallback = Callable[[str, int, int], None]
CancellationCheck = Callable[[], bool]

_COPY_JSON_COLUMNS = {
    SupportResistanceZoneVersion: {"source_metadata"},
    SupportResistanceRegimeVersion: {"evidence"},
    SupportResistanceRunEvent: {"payload"},
}


class SupportResistanceMaterializationBuildError(RuntimeError):
    """Carries an immutable cache identity across the caller's transaction rollback."""

    def __init__(
        self,
        *,
        cache_key: str,
        algorithm_version: str,
        detector_params: dict[str, Any],
        symbols_hash: str,
        symbols: list[str],
        coverage_start: date,
        coverage_end: date,
        price_semantics: str,
        detail: str,
    ) -> None:
        super().__init__(f"support/resistance materialization failed: {detail}")
        self.cache_key = cache_key
        self.algorithm_version = algorithm_version
        self.detector_params = detector_params
        self.symbols_hash = symbols_hash
        self.symbols = symbols
        self.coverage_start = coverage_start
        self.coverage_end = coverage_end
        self.price_semantics = price_semantics
        self.detail = detail


class SupportResistancePersistenceCancelledError(RuntimeError):
    pass


def universe_hash(symbols: list[str]) -> str:
    return _hash_json(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))


def materialization_cache_key(
    *,
    algorithm_version: str,
    detector_params: dict[str, Any],
    price_semantics: str,
    symbols_hash: str,
    coverage_start: date,
    coverage_end: date,
) -> str:
    return _hash_json(
        {
            "algorithm_version": algorithm_version,
            "detector_params": detector_params,
            "price_semantics": price_semantics,
            "universe_hash": symbols_hash,
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(),
        }
    )


def find_reusable_materialization(
    db: Session,
    *,
    runtime: dict[str, Any],
    symbols: list[str],
    coverage_start: date,
    coverage_end: date,
) -> SupportResistanceMaterialization | None:
    metadata = runtime["params"].get("metadata", {}) or {}
    algorithm_version = str(metadata.get("algorithm_version") or "pivot-slope-regime-v3")
    price_semantics = str(
        metadata.get("price_semantics")
        or "forward_adjusted_preferred_unadjusted_fallback"
    )
    detector = normalized_detector_params(runtime["params"])
    symbols_hash = universe_hash(symbols)
    candidates = db.execute(
        select(SupportResistanceMaterialization)
        .where(SupportResistanceMaterialization.algorithm_version == algorithm_version)
        .where(SupportResistanceMaterialization.universe_hash == symbols_hash)
        .where(SupportResistanceMaterialization.price_semantics == price_semantics)
        .where(SupportResistanceMaterialization.coverage_start == coverage_start)
        .where(SupportResistanceMaterialization.coverage_end == coverage_end)
        .where(SupportResistanceMaterialization.status == "completed")
        .where(SupportResistanceMaterialization.invalidated_at.is_(None))
        .order_by(SupportResistanceMaterialization.coverage_start.desc())
    ).scalars().all()
    return next((item for item in candidates if item.detector_params == detector), None)


def hydrate_state_from_materialization(
    db: Session,
    materialization: SupportResistanceMaterialization,
) -> SupportResistanceState:
    """Hydrate sparse detector timelines so callers skip Pivot recomputation."""
    state = SupportResistanceState()
    versions = db.execute(
        select(SupportResistanceZoneVersion)
        .where(SupportResistanceZoneVersion.materialization_id == materialization.id)
        .order_by(
            SupportResistanceZoneVersion.symbol,
            SupportResistanceZoneVersion.zone_key,
            SupportResistanceZoneVersion.effective_from,
            SupportResistanceZoneVersion.version,
        )
    ).scalars().all()
    for version in versions:
        metadata = version.source_metadata or {}
        state_key = (
            str(version.instrument_id)
            if version.instrument_id is not None
            else version.symbol
        )
        symbol_state = state.symbols.setdefault(
            state_key,
            SupportResistanceSymbolState(
                instrument_id=version.instrument_id,
                symbol=version.symbol,
            ),
        )
        symbol_state.cached_zone_timeline.append(
            {
                "zone_key": version.zone_key,
                "effective_from": version.effective_from,
                "effective_to": version.effective_to,
                "source_kind": metadata.get("source_kind") or ("low" if version.role == "support" else "high"),
                "role": version.role,
                "status": version.status,
                "center": float(version.center_price),
                "lower": float(version.lower_price),
                "upper": float(version.upper_price),
                "atr": float(version.atr_width),
                "anchor_session_index": version.anchor_session_index,
                "anchor_center": float(metadata.get("anchor_center", version.center_price)),
                "anchor_lower": float(metadata.get("anchor_lower", version.lower_price)),
                "anchor_upper": float(metadata.get("anchor_upper", version.upper_price)),
                "slope_per_session": float(version.slope_per_session),
                "fit_residual_atr": float(version.fit_residual_atr),
                "recency_weight": float(metadata.get("recency_weight") or 0.0),
                "last_inside": bool(metadata.get("last_inside", False)),
                "pivot_keys": list(metadata.get("pivot_keys") or []),
                "pivot_count": version.pivot_count,
                "touch_count": version.touch_count,
                "first_pivot_date": _as_date(metadata.get("first_pivot_date"), version.effective_from),
                "last_pivot_date": _as_date(metadata.get("last_pivot_date"), version.effective_from),
                "valid_from": _as_date(metadata.get("valid_from"), version.effective_from),
            }
        )
    regime_versions = db.execute(
        select(SupportResistanceRegimeVersion)
        .where(SupportResistanceRegimeVersion.materialization_id == materialization.id)
        .order_by(
            SupportResistanceRegimeVersion.symbol,
            SupportResistanceRegimeVersion.effective_from,
            SupportResistanceRegimeVersion.version,
        )
    ).scalars().all()
    for version in regime_versions:
        state_key = (
            str(version.instrument_id)
            if version.instrument_id is not None
            else version.symbol
        )
        symbol_state = state.symbols.setdefault(
            state_key,
            SupportResistanceSymbolState(
                instrument_id=version.instrument_id,
                symbol=version.symbol,
            ),
        )
        symbol_state.cached_regime_timeline.append(
            {
                "id": str(version.id),
                "version": version.version,
                "effective_from": version.effective_from,
                "regime": version.regime,
                "lower_zone_key": version.lower_zone_key,
                "upper_zone_key": version.upper_zone_key,
                "reason_code": version.reason_code,
                "evidence": dict(version.evidence or {}),
            }
        )
    return state


def persist_support_resistance_run(
    db: Session,
    *,
    run: StrategyRun,
    runtime: dict[str, Any],
    state: SupportResistanceState,
    symbols: list[str],
    coverage_start: date,
    coverage_end: date,
    persist_run_events: bool = True,
    performance: dict[str, Any] | None = None,
    progress_callback: PersistenceProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
    batch_size: int = BATCH_INSERT_SIZE,
) -> SupportResistanceMaterialization:
    """Reuse or build one immutable sparse cache, then attach run-scoped events."""
    persist_started = perf_counter()
    metadata = runtime["params"].get("metadata", {}) or {}
    algorithm_version = str(metadata.get("algorithm_version") or "pivot-slope-regime-v3")
    price_semantics = str(
        metadata.get("price_semantics")
        or "forward_adjusted_preferred_unadjusted_fallback"
    )
    detector = normalized_detector_params(runtime["params"])
    normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    symbols_hash = universe_hash(normalized_symbols)
    candidates = db.execute(
        select(SupportResistanceMaterialization)
        .where(SupportResistanceMaterialization.algorithm_version == algorithm_version)
        .where(SupportResistanceMaterialization.universe_hash == symbols_hash)
        .where(SupportResistanceMaterialization.price_semantics == price_semantics)
        .where(SupportResistanceMaterialization.coverage_start == coverage_start)
        .where(SupportResistanceMaterialization.coverage_end == coverage_end)
        .where(SupportResistanceMaterialization.invalidated_at.is_(None))
        .order_by(SupportResistanceMaterialization.coverage_start.desc())
    ).scalars().all()
    materialization = next(
        (
            candidate
            for candidate in candidates
            if candidate.status == "completed" and candidate.detector_params == detector
        ),
        None,
    )
    should_write_zones = materialization is None
    if materialization is None:
        cache_key = materialization_cache_key(
            algorithm_version=algorithm_version,
            detector_params=detector,
            price_semantics=price_semantics,
            symbols_hash=symbols_hash,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:cache_key, 0))"),
                {"cache_key": cache_key},
            )
        materialization = db.execute(
            select(SupportResistanceMaterialization)
            .where(SupportResistanceMaterialization.cache_key == cache_key)
            .where(SupportResistanceMaterialization.invalidated_at.is_(None))
        ).scalar_one_or_none()
        if materialization is not None and materialization.status == "completed":
            should_write_zones = False
        elif materialization is not None and materialization.status == "building":
            raise RuntimeError("support/resistance materialization is already being built")
        elif materialization is None:
            materialization = SupportResistanceMaterialization(
                cache_key=cache_key,
                algorithm_version=algorithm_version,
                detector_params=detector,
                universe_hash=symbols_hash,
                symbols=normalized_symbols,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                price_semantics=price_semantics,
                status="building",
                statistics={},
            )
            db.add(materialization)
            db.flush()
        elif materialization.status == "failed":
            materialization.status = "building"
            materialization.error_message = None
            db.execute(
                delete(SupportResistanceZoneVersion).where(
                    SupportResistanceZoneVersion.materialization_id == materialization.id
                )
            )
            db.execute(
                delete(SupportResistanceRegimeVersion).where(
                    SupportResistanceRegimeVersion.materialization_id == materialization.id
                )
            )

    try:
        instrument_ids = _instrument_ids(
            db,
            normalized_symbols,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        state_instrument_ids = {
            symbol_state.instrument_id
            for symbol_state in state.symbols.values()
            if symbol_state.instrument_id is not None
        }
        existing_state_instrument_ids = set(
            db.scalars(select(Instrument.id).where(Instrument.id.in_(state_instrument_ids)))
            if state_instrument_ids
            else []
        )
        missing_state_instrument_ids = sorted(
            state_instrument_ids - existing_state_instrument_ids
        )
        if missing_state_instrument_ids:
            raise ValueError(
                "support/resistance persistence received unknown instrument identities: "
                + ", ".join(str(value) for value in missing_state_instrument_ids[:10])
            )
        missing_instruments = sorted(
            {
                symbol
                for state_key, symbol_state in state.symbols.items()
                for symbol, instrument_id in [
                    _state_identity(state_key, symbol_state, instrument_ids)
                ]
                if instrument_id is None
            }
        )
        if missing_instruments:
            raise ValueError(
                "support/resistance persistence could not resolve instrument identity for: "
                + ", ".join(missing_instruments[:10])
            )
        _validate_support_resistance_state_for_persistence(
            materialization,
            state,
            instrument_ids,
            persist_run_events=persist_run_events,
            run=run,
        )
        zone_version_total = (
            sum(len(symbol_state.zone_versions) for symbol_state in state.symbols.values())
            if should_write_zones
            else 0
        )
        regime_version_total = (
            sum(len(symbol_state.regime_versions) for symbol_state in state.symbols.values())
            if should_write_zones
            else 0
        )
        event_count_at_build = sum(len(symbol_state.events) for symbol_state in state.symbols.values())
        run_event_total = event_count_at_build if persist_run_events else 0
        total_items = zone_version_total + regime_version_total + run_event_total
        completed_items = 0

        if progress_callback is not None and zone_version_total:
            progress_callback("zone_versions", completed_items, total_items)
        if should_write_zones:
            zone_started = perf_counter()

            def report_zone_batch(written: int) -> None:
                if progress_callback is not None:
                    progress_callback("zone_versions", written, total_items)

            version_count = _write_zone_versions(
                db,
                materialization,
                state,
                instrument_ids,
                batch_size=batch_size,
                batch_callback=report_zone_batch,
                cancel_check=cancel_check,
            )
            completed_items = version_count
            if performance is not None:
                performance["support_resistance_zone_versions_ms"] = _elapsed_ms(zone_started)
            if progress_callback is not None and regime_version_total:
                progress_callback("regime_versions", completed_items, total_items)
            regime_started = perf_counter()

            def report_regime_batch(written: int) -> None:
                if progress_callback is not None:
                    progress_callback("regime_versions", completed_items + written, total_items)

            regime_count = _write_regime_versions(
                db,
                materialization,
                state,
                instrument_ids,
                batch_size=batch_size,
                batch_callback=report_regime_batch,
                cancel_check=cancel_check,
            )
            completed_items += regime_count
            if performance is not None:
                performance["support_resistance_regime_versions_ms"] = _elapsed_ms(regime_started)
            materialization.statistics = {
                "symbol_count": len(normalized_symbols),
                "zone_version_count": version_count,
                "regime_version_count": regime_count,
                "regime_timeline_count": sum(
                    1 for symbol_state in state.symbols.values() if symbol_state.history
                ),
                "event_count_at_build": event_count_at_build,
            }
            materialization.status = "completed"
            materialization.completed_at = datetime.now(timezone.utc)
        elif performance is not None:
            performance["support_resistance_zone_versions_ms"] = 0.0
            performance["support_resistance_regime_versions_ms"] = 0.0

        if progress_callback is not None and run_event_total:
            progress_callback("run_events", completed_items, total_items)
        events_started = perf_counter()

        def report_event_batch(written: int) -> None:
            if progress_callback is not None:
                progress_callback("run_events", completed_items + written, total_items)

        event_count = _replace_run_audit_rows(
            db,
            run=run,
            materialization=materialization,
            state=state,
            instrument_ids=instrument_ids,
            persist_run_events=persist_run_events,
            batch_size=batch_size,
            batch_callback=report_event_batch,
            cancel_check=cancel_check,
        )
        db.flush()
        if performance is not None:
            performance.update(
                {
                    "support_resistance_cache_reused": not should_write_zones,
                    "support_resistance_zone_versions": zone_version_total,
                    "support_resistance_regime_versions": regime_version_total,
                    "support_resistance_run_events": event_count,
                    "support_resistance_run_events_ms": _elapsed_ms(events_started),
                    "support_resistance_persist_total_ms": _elapsed_ms(persist_started),
                }
            )
        return materialization
    except SupportResistancePersistenceCancelledError:
        raise
    except Exception as exc:
        materialization.status = "failed"
        materialization.error_message = str(exc)
        if should_write_zones:
            raise SupportResistanceMaterializationBuildError(
                cache_key=materialization.cache_key,
                algorithm_version=algorithm_version,
                detector_params=detector,
                symbols_hash=symbols_hash,
                symbols=normalized_symbols,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                price_semantics=price_semantics,
                detail=str(exc),
            ) from exc
        raise


def _validate_support_resistance_state_for_persistence(
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
    *,
    persist_run_events: bool,
    run: StrategyRun,
) -> None:
    for row in _zone_version_rows(materialization, state, instrument_ids):
        _validate_strict_json(row["source_metadata"], "zone source_metadata")
    for row in _regime_version_rows(materialization, state, instrument_ids):
        _validate_strict_json(row["evidence"], "regime evidence")
    if persist_run_events:
        for row in _run_event_rows(run, materialization, state, instrument_ids):
            _validate_run_event_row(row)


def record_failed_materialization_after_rollback(
    db: Session,
    error: SupportResistanceMaterializationBuildError,
) -> SupportResistanceMaterialization:
    """Persist failed build evidence after the strategy transaction was rolled back."""
    materialization = db.execute(
        select(SupportResistanceMaterialization)
        .where(SupportResistanceMaterialization.cache_key == error.cache_key)
        .where(SupportResistanceMaterialization.invalidated_at.is_(None))
    ).scalar_one_or_none()
    if materialization is not None and materialization.status == "completed":
        return materialization
    if materialization is None:
        materialization = SupportResistanceMaterialization(
            cache_key=error.cache_key,
            algorithm_version=error.algorithm_version,
            detector_params=error.detector_params,
            universe_hash=error.symbols_hash,
            symbols=error.symbols,
            coverage_start=error.coverage_start,
            coverage_end=error.coverage_end,
            price_semantics=error.price_semantics,
            status="failed",
            statistics={},
            error_message=error.detail,
        )
        db.add(materialization)
    else:
        materialization.status = "failed"
        materialization.error_message = error.detail
        materialization.completed_at = None
    db.flush()
    return materialization


def _write_zone_versions(
    db: Session,
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
    *,
    batch_size: int = BATCH_INSERT_SIZE,
    batch_callback: Callable[[int], None] | None = None,
    cancel_check: CancellationCheck | None = None,
) -> int:
    # Validate the complete materialization before the first INSERT. This keeps
    # a late-sorting symbol from wasting a large transactional bulk write before
    # a duplicate date or invalid geometry is discovered.
    for _ in _zone_version_rows(materialization, state, instrument_ids):
        pass
    return _insert_in_batches(
        db,
        SupportResistanceZoneVersion,
        _zone_version_rows(materialization, state, instrument_ids),
        batch_size=batch_size,
        batch_callback=batch_callback,
        cancel_check=cancel_check,
    )


def _write_regime_versions(
    db: Session,
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
    *,
    batch_size: int = BATCH_INSERT_SIZE,
    batch_callback: Callable[[int], None] | None = None,
    cancel_check: CancellationCheck | None = None,
) -> int:
    rows = list(_regime_version_rows(materialization, state, instrument_ids))
    return _insert_in_batches(
        db,
        SupportResistanceRegimeVersion,
        rows,
        batch_size=batch_size,
        batch_callback=batch_callback,
        cancel_check=cancel_check,
    )


def _regime_version_rows(
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
) -> Iterable[dict[str, Any]]:
    for state_key, symbol_state in sorted(state.symbols.items()):
        symbol, instrument_id = _state_identity(state_key, symbol_state, instrument_ids)
        session_dates = sorted(
            {
                item["dt_ny"]
                for item in symbol_state.history
                if isinstance(item.get("dt_ny"), date)
                and materialization.coverage_start <= item["dt_ny"] <= materialization.coverage_end
            }
        )
        versions = sorted(
            symbol_state.regime_versions,
            key=lambda item: (str(item["effective_from"]), int(item["version"])),
        )
        _validate_regime_versions(symbol, session_dates, versions)
        for payload in versions:
            yield {
                "materialization_id": materialization.id,
                "instrument_id": instrument_id,
                "symbol": symbol,
                "version": int(payload["version"]),
                "effective_from": date.fromisoformat(str(payload["effective_from"])),
                "regime": payload["regime"],
                "lower_zone_key": payload.get("lower_zone_key"),
                "upper_zone_key": payload.get("upper_zone_key"),
                "reason_code": payload.get("reason_code") or "unknown",
                "evidence": payload.get("evidence") or {},
            }


def _validate_regime_versions(
    symbol: str,
    session_dates: list[date],
    versions: list[dict[str, Any]],
) -> None:
    if not session_dates:
        if versions:
            raise ValueError(f"{symbol}: regime versions exist without market sessions")
        return
    if not versions:
        raise ValueError(f"{symbol}: missing regime timeline")
    starts = [date.fromisoformat(str(item["effective_from"])) for item in versions]
    if starts[0] != session_dates[0]:
        raise ValueError(f"{symbol}: regime timeline does not start on the first market session")
    if any(left >= right for left, right in zip(starts, starts[1:])):
        raise ValueError(f"{symbol}: regime effective dates must be strictly increasing")
    states = [str(item["regime"]) for item in versions]
    allowed_states = {"uptrend", "downtrend", "range", "transition"}
    if any(state not in allowed_states for state in states):
        raise ValueError(f"{symbol}: regime timeline contains an invalid state")
    if any(left == right for left, right in zip(states, states[1:])):
        raise ValueError(f"{symbol}: adjacent regime versions must differ")
    session_set = set(session_dates)
    if any(start not in session_set for start in starts):
        raise ValueError(f"{symbol}: regime transition is not aligned to a market session")
    expected_versions = list(range(1, len(versions) + 1))
    actual_versions = [int(item["version"]) for item in versions]
    if actual_versions != expected_versions:
        raise ValueError(f"{symbol}: regime versions must be contiguous and one-based")


def _zone_version_rows(
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
) -> Iterable[dict[str, Any]]:
    for state_key, symbol_state in sorted(state.symbols.items()):
        symbol, instrument_id = _state_identity(state_key, symbol_state, instrument_ids)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for payload in symbol_state.zone_versions:
            grouped.setdefault(str(payload["zone_key"]), []).append(payload)
        for zone_key, versions in sorted(grouped.items()):
            ordered = sorted(versions, key=lambda item: item["effective_from"])
            session_index_by_date = {
                item["dt_ny"]: index for index, item in enumerate(symbol_state.history)
            }
            session_dates = sorted(session_index_by_date)
            effective_dates = [date.fromisoformat(str(item["effective_from"])) for item in ordered]
            if any(left >= right for left, right in zip(effective_dates, effective_dates[1:])):
                raise ValueError(
                    f"{symbol}:{zone_key}: zone effective dates must be strictly increasing"
                )
            if any(item not in session_index_by_date for item in effective_dates):
                raise ValueError(
                    f"{symbol}:{zone_key}: zone transition is not aligned to a market session"
                )
            for index, payload in enumerate(ordered):
                effective_from = effective_dates[index]
                effective_to = (
                    session_dates[session_index_by_date[effective_dates[index + 1]] - 1]
                    if index + 1 < len(ordered)
                    else None
                )
                projection_limit = (
                    effective_from
                    if payload["status"] != "active"
                    else min(
                        effective_to or materialization.coverage_end,
                        materialization.coverage_end,
                    )
                )
                projection_dates = [item for item in session_dates if effective_from <= item <= projection_limit]
                projection_end = projection_dates[-1] if projection_dates else effective_from
                start_index = session_index_by_date.get(effective_from, payload["anchor_session_index"])
                end_index = session_index_by_date.get(projection_end, start_index)
                start_delta = payload["slope_per_session"] * (
                    start_index - payload["anchor_session_index"]
                )
                end_delta = payload["slope_per_session"] * (
                    end_index - payload["anchor_session_index"]
                )
                row = {
                    "materialization_id": materialization.id,
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "zone_key": zone_key,
                    "version": index + 1,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "role": payload["role"],
                    "status": payload["status"],
                    "center_price": payload["anchor_center"] + start_delta,
                    "lower_price": payload["anchor_lower"] + start_delta,
                    "upper_price": payload["anchor_upper"] + start_delta,
                    "atr_width": payload["atr"],
                    "anchor_session_index": payload["anchor_session_index"],
                    "slope_per_session": payload["slope_per_session"],
                    "fit_residual_atr": payload["fit_residual_atr"],
                    "projection_end": projection_end,
                    "end_center_price": payload["anchor_center"] + end_delta,
                    "end_lower_price": payload["anchor_lower"] + end_delta,
                    "end_upper_price": payload["anchor_upper"] + end_delta,
                    "pivot_count": payload["pivot_count"],
                    "touch_count": payload["touch_count"],
                    "source_metadata": {
                        "source_kind": payload["source_kind"],
                        "pivot_keys": payload["pivot_keys"],
                        "first_pivot_date": payload["first_pivot_date"],
                        "last_pivot_date": payload["last_pivot_date"],
                        "valid_from": payload["valid_from"],
                        "anchor_center": payload["anchor_center"],
                        "anchor_lower": payload["anchor_lower"],
                        "anchor_upper": payload["anchor_upper"],
                        "recency_weight": payload["recency_weight"],
                        "last_inside": payload["last_inside"],
                    },
                }
                _validate_persisted_zone_row(row)
                yield row


def _validate_persisted_zone_row(row: dict[str, Any]) -> None:
    price_fields = (
        "center_price",
        "lower_price",
        "upper_price",
        "atr_width",
        "slope_per_session",
        "end_center_price",
        "end_lower_price",
        "end_upper_price",
    )
    values = {field: float(row[field]) for field in price_fields}
    identity = f"{row['symbol']}:{row['zone_key']}@{row['effective_from']}"
    if row["role"] not in {"support", "resistance"}:
        raise ValueError(f"{identity}: invalid zone role")
    if row["status"] not in {"active", "expired", "broken", "transformed"}:
        raise ValueError(f"{identity}: invalid zone status")
    if any(
        not isfinite(value) or abs(value) >= NUMERIC_24_10_ABS_LIMIT
        for value in values.values()
    ):
        raise ValueError(f"{identity}: zone geometry exceeds the NUMERIC(24,10) domain")
    if (
        values["atr_width"] <= 0
        or values["lower_price"] <= 0
        or values["end_lower_price"] <= 0
        or not values["lower_price"] <= values["center_price"] <= values["upper_price"]
        or not values["end_lower_price"] <= values["end_center_price"] <= values["end_upper_price"]
    ):
        raise ValueError(f"{identity}: zone geometry is non-positive or unordered")
    residual = float(row["fit_residual_atr"])
    if not isfinite(residual) or residual < 0 or residual >= NUMERIC_20_10_ABS_LIMIT:
        raise ValueError(f"{identity}: fit residual exceeds the NUMERIC(20,10) domain")


def _replace_run_audit_rows(
    db: Session,
    *,
    run: StrategyRun,
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
    persist_run_events: bool = True,
    batch_size: int = BATCH_INSERT_SIZE,
    batch_callback: Callable[[int], None] | None = None,
    cancel_check: CancellationCheck | None = None,
) -> int:
    rows = (
        list(_run_event_rows(run, materialization, state, instrument_ids))
        if persist_run_events
        else []
    )
    for row in rows:
        _validate_run_event_row(row)
    if cancel_check is not None and cancel_check():
        raise SupportResistancePersistenceCancelledError(
            "backtest cancellation requested before support/resistance persistence"
        )
    db.execute(delete(SupportResistanceRunEvent).where(SupportResistanceRunEvent.run_id == run.id))
    db.execute(
        delete(SupportResistanceRunMaterialization).where(
            SupportResistanceRunMaterialization.run_id == run.id
        )
    )
    db.add(
        SupportResistanceRunMaterialization(
            run_id=run.id,
            materialization_id=materialization.id,
        )
    )
    if not persist_run_events:
        return 0
    return _insert_in_batches(
        db,
        SupportResistanceRunEvent,
        rows,
        batch_size=batch_size,
        batch_callback=batch_callback,
        cancel_check=cancel_check,
    )


def _run_event_rows(
    run: StrategyRun,
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
) -> Iterable[dict[str, Any]]:
    for state_key, symbol_state in sorted(state.symbols.items()):
        symbol, instrument_id = _state_identity(state_key, symbol_state, instrument_ids)
        for payload in symbol_state.events:
            zone = payload.get("zone") or {}
            score_evidence = payload.get("score_evidence") or {}
            posterior_sample_count = score_evidence.get("resolved_samples")
            if posterior_sample_count is None:
                posterior_sample_count = payload.get("resolved_samples")
            yield {
                "run_id": run.id,
                "materialization_id": materialization.id,
                "instrument_id": instrument_id,
                "symbol": symbol,
                "event_date": date.fromisoformat(str(payload["event_date"])),
                "event_type": str(payload["event_type"]),
                "zone_key": payload.get("zone_key"),
                "setup": payload.get("setup"),
                "selected": payload.get("event_type") == "selection",
                "score": payload.get("score"),
                "posterior_sample_count": posterior_sample_count,
                "lower_price": payload.get("lower") or zone.get("lower"),
                "upper_price": payload.get("upper") or zone.get("upper"),
                "payload": payload,
            }


def _validate_run_event_row(row: dict[str, Any]) -> None:
    identity = f"{row['symbol']}:{row['event_type']}@{row['event_date']}"
    score = row.get("score")
    if score is not None and (
        not isfinite(float(score)) or abs(float(score)) >= NUMERIC_20_10_ABS_LIMIT
    ):
        raise ValueError(f"{identity}: event score exceeds the NUMERIC(20,10) domain")
    sample_count = row.get("posterior_sample_count")
    if sample_count is not None and int(sample_count) < 0:
        raise ValueError(f"{identity}: posterior sample count is negative")
    lower = row.get("lower_price")
    upper = row.get("upper_price")
    if lower is not None and upper is not None:
        lower_value = float(lower)
        upper_value = float(upper)
        if (
            not isfinite(lower_value)
            or not isfinite(upper_value)
            or lower_value <= 0
            or lower_value > upper_value
            or abs(lower_value) >= NUMERIC_24_10_ABS_LIMIT
            or abs(upper_value) >= NUMERIC_24_10_ABS_LIMIT
        ):
            raise ValueError(f"{identity}: event geometry exceeds the NUMERIC(24,10) domain")
    _validate_strict_json(row["payload"], f"{identity}: event payload")


def _validate_strict_json(value: Any, label: str) -> None:
    try:
        json.dumps(value, allow_nan=False, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict JSON") from exc


def _insert_in_batches(
    db: Session,
    model: type[Any],
    rows: Iterable[dict[str, Any]],
    *,
    batch_size: int,
    batch_callback: Callable[[int], None] | None = None,
    cancel_check: CancellationCheck | None = None,
) -> int:
    resolved_batch_size = max(1, int(batch_size))
    batch: list[dict[str, Any]] = []
    written = 0
    for row in rows:
        batch.append(row)
        if len(batch) < resolved_batch_size:
            continue
        _insert_batch(db, model, batch, cancel_check=cancel_check)
        written += len(batch)
        batch.clear()
        if batch_callback is not None:
            batch_callback(written)
    if batch:
        _insert_batch(db, model, batch, cancel_check=cancel_check)
        written += len(batch)
        batch.clear()
        if batch_callback is not None:
            batch_callback(written)
    return written


def _insert_batch(
    db: Session,
    model: type[Any],
    rows: list[dict[str, Any]],
    *,
    cancel_check: CancellationCheck | None,
) -> None:
    if cancel_check is not None and cancel_check():
        raise SupportResistancePersistenceCancelledError(
            "backtest cancellation requested before support/resistance COPY batch"
        )
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        db.execute(insert(model), rows)
        return
    if bind.dialect.driver != "psycopg":
        raise RuntimeError("support/resistance COPY persistence requires postgresql+psycopg")
    row_columns = tuple(rows[0])
    if any(tuple(row) != row_columns for row in rows[1:]):
        raise ValueError("support/resistance COPY batch column order differs")
    columns = ("id", *row_columns)
    json_columns = _COPY_JSON_COLUMNS.get(model, set())
    statement = f"COPY {model.__table__.name} ({','.join(columns)}) FROM STDIN"
    raw = db.connection().connection.driver_connection
    with raw.cursor() as cursor:
        with cursor.copy(statement) as copy:
            for row in rows:
                copy.write_row(
                    (
                        uuid4(),
                        *(
                            json.dumps(
                                row[column],
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                                allow_nan=False,
                            )
                            if column in json_columns
                            else row[column]
                            for column in row_columns
                        ),
                    )
                )


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000.0, 3)


def _state_identity(
    state_key: str,
    symbol_state: SupportResistanceSymbolState,
    instrument_ids: dict[str, int],
) -> tuple[str, int | None]:
    symbol = str(symbol_state.symbol or state_key).upper()
    instrument_id = symbol_state.instrument_id
    return (
        symbol,
        instrument_id if instrument_id is not None else instrument_ids.get(symbol),
    )


def _instrument_ids(
    db: Session,
    symbols: list[str],
    *,
    coverage_start: date,
    coverage_end: date,
) -> dict[str, int]:
    if not symbols:
        return {}
    canonical_query = text(
        "SELECT id, ticker_canonical FROM instruments WHERE ticker_canonical IN :symbols"
    ).bindparams(bindparam("symbols", expanding=True))
    canonical_rows = db.execute(
        canonical_query,
        {"symbols": symbols},
    ).mappings().all()
    resolved = {
        str(row["ticker_canonical"]).upper(): int(row["id"])
        for row in canonical_rows
    }
    history_rows = db.execute(
        text(
            "SELECT symbol, instrument_id FROM symbol_history "
            "WHERE symbol IN :symbols AND is_primary = TRUE "
            "AND valid_from <= :coverage_end "
            "AND (valid_to IS NULL OR valid_to >= :coverage_start)"
        ).bindparams(bindparam("symbols", expanding=True)),
        {
            "symbols": symbols,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
        },
    ).mappings().all()
    history_ids: dict[str, set[int]] = {}
    for row in history_rows:
        history_ids.setdefault(str(row["symbol"]).upper(), set()).add(
            int(row["instrument_id"])
        )
    for symbol, instrument_ids in history_ids.items():
        if len(instrument_ids) == 1:
            resolved[symbol] = next(iter(instrument_ids))
        else:
            resolved.pop(symbol, None)
    return resolved


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _as_date(value: Any, fallback: date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return fallback
    return fallback
