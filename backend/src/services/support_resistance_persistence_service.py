from __future__ import annotations

"""Persistence and cache identity helpers for support/resistance materializations."""

import json
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from time import perf_counter
from typing import Any, Callable, Iterable

from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from src.models.tables import (
    StrategyRun,
    SupportResistanceMaterialization,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    SupportResistanceZoneVersion,
)
from src.services.support_resistance_service import (
    SupportResistanceState,
    SupportResistanceSymbolState,
    normalized_detector_params,
)


SOURCE_REVISION_SQL = """
SELECT
  (SELECT count(*) FROM instruments) AS instrument_count,
  (SELECT max(updated_at) FROM instruments) AS instrument_max_updated_at,
  (SELECT count(*) FROM symbol_history) AS symbol_history_count,
  (SELECT max(updated_at) FROM symbol_history) AS symbol_history_max_updated_at,
  (SELECT count(*) FROM eod_bars) AS eod_count,
  (SELECT max(asof) FROM eod_bars) AS eod_max_asof,
  (SELECT count(*) FROM daily_features) AS feature_count,
  (SELECT max(asof) FROM daily_features) AS feature_max_asof
"""

BATCH_INSERT_SIZE = 5_000
PersistenceProgressCallback = Callable[[str, int, int], None]


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
        data_fingerprint: str,
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
        self.data_fingerprint = data_fingerprint
        self.price_semantics = price_semantics
        self.detail = detail


def source_data_fingerprint(db: Session) -> str:
    """Fingerprint the adjusted-price/features revision without mutating source tables.

    The revision is deliberately global: a correction invalidates more caches than
    strictly necessary, but can never cause an older cache to be silently reused.
    """
    row = db.execute(text(SOURCE_REVISION_SQL)).mappings().one()
    payload = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in sorted(row.items())
    }
    return _hash_json(payload)


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
    data_fingerprint: str,
) -> str:
    return _hash_json(
        {
            "algorithm_version": algorithm_version,
            "detector_params": detector_params,
            "price_semantics": price_semantics,
            "universe_hash": symbols_hash,
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(),
            "source_data_fingerprint": data_fingerprint,
        }
    )


def find_reusable_materialization(
    db: Session,
    *,
    runtime: dict[str, Any],
    symbols: list[str],
    coverage_start: date,
    coverage_end: date,
    expected_data_fingerprint: str | None = None,
) -> SupportResistanceMaterialization | None:
    metadata = runtime["params"].get("metadata", {}) or {}
    algorithm_version = str(metadata.get("algorithm_version") or "pivot-slope-atr-v2")
    price_semantics = str(
        metadata.get("price_semantics")
        or "forward_adjusted_preferred_unadjusted_fallback"
    )
    detector = normalized_detector_params(runtime["params"])
    symbols_hash = universe_hash(symbols)
    fingerprint = expected_data_fingerprint or source_data_fingerprint(db)
    candidates = db.execute(
        select(SupportResistanceMaterialization)
        .where(SupportResistanceMaterialization.algorithm_version == algorithm_version)
        .where(SupportResistanceMaterialization.universe_hash == symbols_hash)
        .where(SupportResistanceMaterialization.source_data_fingerprint == fingerprint)
        .where(SupportResistanceMaterialization.price_semantics == price_semantics)
        .where(SupportResistanceMaterialization.coverage_start == coverage_start)
        .where(SupportResistanceMaterialization.coverage_end == coverage_end)
        .where(SupportResistanceMaterialization.status == "completed")
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
        symbol_state = state.symbols.setdefault(version.symbol, SupportResistanceSymbolState())
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
    expected_data_fingerprint: str | None = None,
    persist_run_events: bool = True,
    performance: dict[str, Any] | None = None,
    progress_callback: PersistenceProgressCallback | None = None,
    batch_size: int = BATCH_INSERT_SIZE,
) -> SupportResistanceMaterialization:
    """Reuse or build one immutable sparse cache, then attach run-scoped events."""
    persist_started = perf_counter()
    metadata = runtime["params"].get("metadata", {}) or {}
    algorithm_version = str(metadata.get("algorithm_version") or "pivot-slope-atr-v2")
    price_semantics = str(
        metadata.get("price_semantics")
        or "forward_adjusted_preferred_unadjusted_fallback"
    )
    detector = normalized_detector_params(runtime["params"])
    normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    symbols_hash = universe_hash(normalized_symbols)
    observed_fingerprint = source_data_fingerprint(db)
    data_fingerprint = expected_data_fingerprint or observed_fingerprint
    if expected_data_fingerprint is not None and observed_fingerprint != expected_data_fingerprint:
        cache_key = materialization_cache_key(
            algorithm_version=algorithm_version,
            detector_params=detector,
            price_semantics=price_semantics,
            symbols_hash=symbols_hash,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            data_fingerprint=data_fingerprint,
        )
        raise SupportResistanceMaterializationBuildError(
            cache_key=cache_key,
            algorithm_version=algorithm_version,
            detector_params=detector,
            symbols_hash=symbols_hash,
            symbols=normalized_symbols,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            data_fingerprint=data_fingerprint,
            price_semantics=price_semantics,
            detail="source data fingerprint changed while the strategy run was executing",
        )

    candidates = db.execute(
        select(SupportResistanceMaterialization)
        .where(SupportResistanceMaterialization.algorithm_version == algorithm_version)
        .where(SupportResistanceMaterialization.universe_hash == symbols_hash)
        .where(SupportResistanceMaterialization.source_data_fingerprint == data_fingerprint)
        .where(SupportResistanceMaterialization.price_semantics == price_semantics)
        .where(SupportResistanceMaterialization.coverage_start == coverage_start)
        .where(SupportResistanceMaterialization.coverage_end == coverage_end)
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
            data_fingerprint=data_fingerprint,
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:cache_key, 0))"),
                {"cache_key": cache_key},
            )
        materialization = db.execute(
            select(SupportResistanceMaterialization).where(
                SupportResistanceMaterialization.cache_key == cache_key
            )
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
                source_data_fingerprint=data_fingerprint,
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

    try:
        instrument_ids = _instrument_ids(db, normalized_symbols)
        zone_version_total = (
            sum(len(symbol_state.zone_versions) for symbol_state in state.symbols.values())
            if should_write_zones
            else 0
        )
        event_count_at_build = sum(len(symbol_state.events) for symbol_state in state.symbols.values())
        run_event_total = event_count_at_build if persist_run_events else 0
        total_items = zone_version_total + run_event_total
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
            )
            completed_items = version_count
            if performance is not None:
                performance["support_resistance_zone_versions_ms"] = _elapsed_ms(zone_started)
            materialization.statistics = {
                "symbol_count": len(normalized_symbols),
                "zone_version_count": version_count,
                "event_count_at_build": event_count_at_build,
            }
            materialization.status = "completed"
            materialization.completed_at = datetime.now(timezone.utc)
        elif performance is not None:
            performance["support_resistance_zone_versions_ms"] = 0.0

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
        )
        db.flush()
        if performance is not None:
            performance.update(
                {
                    "support_resistance_cache_reused": not should_write_zones,
                    "support_resistance_zone_versions": zone_version_total,
                    "support_resistance_run_events": event_count,
                    "support_resistance_run_events_ms": _elapsed_ms(events_started),
                    "support_resistance_persist_total_ms": _elapsed_ms(persist_started),
                }
            )
        return materialization
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
                data_fingerprint=data_fingerprint,
                price_semantics=price_semantics,
                detail=str(exc),
            ) from exc
        raise


def record_failed_materialization_after_rollback(
    db: Session,
    error: SupportResistanceMaterializationBuildError,
) -> SupportResistanceMaterialization:
    """Persist failed build evidence after the strategy transaction was rolled back."""
    materialization = db.execute(
        select(SupportResistanceMaterialization).where(
            SupportResistanceMaterialization.cache_key == error.cache_key
        )
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
            source_data_fingerprint=error.data_fingerprint,
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
) -> int:
    return _insert_in_batches(
        db,
        SupportResistanceZoneVersion,
        _zone_version_rows(materialization, state, instrument_ids),
        batch_size=batch_size,
        batch_callback=batch_callback,
    )


def _zone_version_rows(
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
) -> Iterable[dict[str, Any]]:
    for symbol, symbol_state in sorted(state.symbols.items()):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for payload in symbol_state.zone_versions:
            grouped.setdefault(str(payload["zone_key"]), []).append(payload)
        for zone_key, versions in sorted(grouped.items()):
            ordered = sorted(versions, key=lambda item: item["effective_from"])
            session_index_by_date = {
                item["dt_ny"]: index for index, item in enumerate(symbol_state.history)
            }
            session_dates = sorted(session_index_by_date)
            for index, payload in enumerate(ordered):
                effective_from = date.fromisoformat(str(payload["effective_from"]))
                effective_to = (
                    date.fromisoformat(str(ordered[index + 1]["effective_from"])) - timedelta(days=1)
                    if index + 1 < len(ordered)
                    else None
                )
                projection_limit = min(
                    effective_to or materialization.coverage_end,
                    materialization.coverage_end,
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
                yield {
                    "materialization_id": materialization.id,
                    "instrument_id": instrument_ids.get(symbol),
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
) -> int:
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
        _run_event_rows(run, materialization, state, instrument_ids),
        batch_size=batch_size,
        batch_callback=batch_callback,
    )


def _run_event_rows(
    run: StrategyRun,
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
) -> Iterable[dict[str, Any]]:
    for symbol, symbol_state in sorted(state.symbols.items()):
        for payload in symbol_state.events:
            zone = payload.get("zone") or {}
            score_evidence = payload.get("score_evidence") or {}
            posterior_sample_count = score_evidence.get("resolved_samples")
            if posterior_sample_count is None:
                posterior_sample_count = payload.get("resolved_samples")
            yield {
                "run_id": run.id,
                "materialization_id": materialization.id,
                "instrument_id": instrument_ids.get(symbol),
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


def _insert_in_batches(
    db: Session,
    model: type[Any],
    rows: Iterable[dict[str, Any]],
    *,
    batch_size: int,
    batch_callback: Callable[[int], None] | None = None,
) -> int:
    resolved_batch_size = max(1, int(batch_size))
    batch: list[dict[str, Any]] = []
    written = 0
    for row in rows:
        batch.append(row)
        if len(batch) < resolved_batch_size:
            continue
        db.execute(insert(model), batch)
        written += len(batch)
        batch.clear()
        if batch_callback is not None:
            batch_callback(written)
    if batch:
        db.execute(insert(model), batch)
        written += len(batch)
        batch.clear()
        if batch_callback is not None:
            batch_callback(written)
    return written


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000.0, 3)


def _instrument_ids(db: Session, symbols: list[str]) -> dict[str, int]:
    if not symbols:
        return {}
    rows = db.execute(
        text(
            "SELECT id, ticker_canonical FROM instruments "
            "WHERE ticker_canonical = ANY(:symbols)"
        ),
        {"symbols": symbols},
    ).mappings().all()
    return {str(row["ticker_canonical"]).upper(): int(row["id"]) for row in rows}


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
