from __future__ import annotations

"""Persistence and cache identity helpers for support/resistance materializations."""

import json
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import delete, select, text
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
) -> SupportResistanceMaterialization:
    """Reuse or build one immutable sparse cache, then attach run-scoped events."""
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
        if should_write_zones:
            version_count = _write_zone_versions(
                db,
                materialization,
                state,
                instrument_ids,
            )
            materialization.statistics = {
                "symbol_count": len(normalized_symbols),
                "zone_version_count": version_count,
                "event_count_at_build": sum(len(item.events) for item in state.symbols.values()),
            }
            materialization.status = "completed"
            materialization.completed_at = datetime.now(timezone.utc)

        _replace_run_audit_rows(
            db,
            run=run,
            materialization=materialization,
            state=state,
            instrument_ids=instrument_ids,
            persist_run_events=persist_run_events,
        )
        db.flush()
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
) -> int:
    written = 0
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
                db.add(
                    SupportResistanceZoneVersion(
                        materialization_id=materialization.id,
                        instrument_id=instrument_ids.get(symbol),
                        symbol=symbol,
                        zone_key=zone_key,
                        version=index + 1,
                        effective_from=effective_from,
                        effective_to=effective_to,
                        role=payload["role"],
                        status=payload["status"],
                        center_price=payload["anchor_center"] + start_delta,
                        lower_price=payload["anchor_lower"] + start_delta,
                        upper_price=payload["anchor_upper"] + start_delta,
                        atr_width=payload["atr"],
                        anchor_session_index=payload["anchor_session_index"],
                        slope_per_session=payload["slope_per_session"],
                        fit_residual_atr=payload["fit_residual_atr"],
                        projection_end=projection_end,
                        end_center_price=payload["anchor_center"] + end_delta,
                        end_lower_price=payload["anchor_lower"] + end_delta,
                        end_upper_price=payload["anchor_upper"] + end_delta,
                        pivot_count=payload["pivot_count"],
                        touch_count=payload["touch_count"],
                        source_metadata={
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
                    )
                )
                written += 1
    return written


def _replace_run_audit_rows(
    db: Session,
    *,
    run: StrategyRun,
    materialization: SupportResistanceMaterialization,
    state: SupportResistanceState,
    instrument_ids: dict[str, int],
    persist_run_events: bool = True,
) -> None:
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
        return
    for symbol, symbol_state in sorted(state.symbols.items()):
        for payload in symbol_state.events:
            zone = payload.get("zone") or {}
            score_evidence = payload.get("score_evidence") or {}
            posterior_sample_count = score_evidence.get("resolved_samples")
            if posterior_sample_count is None:
                posterior_sample_count = payload.get("resolved_samples")
            db.add(
                SupportResistanceRunEvent(
                    run_id=run.id,
                    materialization_id=materialization.id,
                    instrument_id=instrument_ids.get(symbol),
                    symbol=symbol,
                    event_date=date.fromisoformat(str(payload["event_date"])),
                    event_type=str(payload["event_type"]),
                    zone_key=payload.get("zone_key"),
                    setup=payload.get("setup"),
                    selected=payload.get("event_type") == "selection",
                    score=payload.get("score"),
                    posterior_sample_count=posterior_sample_count,
                    lower_price=payload.get("lower") or zone.get("lower"),
                    upper_price=payload.get("upper") or zone.get("upper"),
                    payload=payload,
                )
            )


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
