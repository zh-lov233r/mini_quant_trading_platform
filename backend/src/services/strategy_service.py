from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from src.models.tables import Strategy
from src.services.strategy_registry import (
    extract_description,
    get_trend_engine_supported_windows,
    is_engine_ready,
    json_signature,
    normalize_strategy_params,
)


class StrategyCreateConflictError(RuntimeError):
    pass


class StrategyNameConflictError(StrategyCreateConflictError):
    pass


def load_feature_support(db: Session) -> dict[str, dict[str, list[int]]]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'daily_features'
            """
        )
    ).all()
    available = {str(row[0]).strip().lower() for row in rows}
    supported = get_trend_engine_supported_windows()
    return {
        "trend": {
            "ema_windows": [window for window in supported["ema"] if f"ema_{window}" in available],
            "sma_windows": [window for window in supported["sma"] if f"sma_{window}" in available],
        }
    }


def validate_strategy_params(
    db: Session,
    *,
    strategy_type: str,
    params: dict[str, Any],
    description: str | None,
) -> dict[str, Any]:
    normalized = normalize_strategy_params(strategy_type, params, description)
    if not is_engine_ready(strategy_type, normalized):
        raise ValueError(f"strategy type is not engine-ready: {strategy_type}")
    if strategy_type != "trend":
        return normalized

    support = load_feature_support(db)["trend"]
    signal = normalized.get("signal") or {}
    for label, indicator in (
        ("fast indicator", signal.get("fast_indicator") or {}),
        ("slow indicator", signal.get("slow_indicator") or {}),
    ):
        kind = str(indicator.get("kind") or "").strip().lower()
        window = indicator.get("window")
        if kind not in {"ema", "sma"}:
            raise ValueError(f"unsupported {label} kind: {kind or 'empty'}")
        if not isinstance(window, int):
            raise ValueError(f"invalid {label} window")
        windows = support["ema_windows"] if kind == "ema" else support["sma_windows"]
        if window not in windows:
            available = ", ".join(str(item) for item in windows) or "none"
            raise ValueError(f"unsupported {label} {kind.upper()}{window}; available windows: {available}")
    return normalized


def normalize_creatable_strategy_params(
    db: Session,
    *,
    strategy_type: str,
    params: dict[str, Any],
    description: str | None,
) -> dict[str, Any]:
    if strategy_type == "custom":
        return normalize_strategy_params(strategy_type, params, description)
    return validate_strategy_params(
        db,
        strategy_type=strategy_type,
        params=params,
        description=description,
    )


def _matches_create_request(
    strategy: Strategy,
    *,
    name: str,
    strategy_type: str,
    status: str,
    normalized_params: dict[str, Any],
) -> bool:
    existing_normalized = normalize_strategy_params(
        strategy.strategy_type,
        strategy.params,
        extract_description(strategy.params),
    )
    return (
        strategy.name == name
        and strategy.strategy_type == strategy_type
        and strategy.status == status
        and json_signature(existing_normalized) == json_signature(normalized_params)
    )


def create_strategy_version(
    db: Session,
    *,
    name: str,
    strategy_type: str,
    params: dict[str, Any],
    description: str | None,
    status: str,
    idempotency_key: str | None,
) -> Strategy:
    normalized = normalize_creatable_strategy_params(
        db,
        strategy_type=strategy_type,
        params=params,
        description=description,
    )
    clean_name = name.strip()
    if idempotency_key:
        existing = db.execute(
            select(Strategy).where(Strategy.idempotency_key == idempotency_key)
        ).scalars().first()
        if existing is not None:
            if _matches_create_request(
                existing,
                name=clean_name,
                strategy_type=strategy_type,
                status=status,
                normalized_params=normalized,
            ):
                return existing
            raise StrategyCreateConflictError(
                "idempotency key was already used with a different strategy request"
            )
    latest_same_name = db.execute(
        select(Strategy)
        .where(Strategy.name == clean_name)
        .order_by(Strategy.version.desc())
    ).scalars().first()

    if latest_same_name:
        existing_normalized = normalize_strategy_params(
            latest_same_name.strategy_type,
            latest_same_name.params,
            extract_description(latest_same_name.params),
        )
        if (
            latest_same_name.strategy_type == strategy_type
            and latest_same_name.status == status
            and json_signature(existing_normalized) == json_signature(normalized)
        ):
            return latest_same_name
        strategy_key = latest_same_name.strategy_key
        latest_family = db.execute(
            select(Strategy)
            .where(Strategy.strategy_key == strategy_key)
            .order_by(Strategy.version.desc())
        ).scalars().first()
        next_version = (latest_family.version if latest_family else latest_same_name.version) + 1
    else:
        strategy_key = clean_name
        next_version = 1

    strategy = Strategy(
        strategy_key=strategy_key,
        name=clean_name,
        strategy_type=strategy_type,
        params=normalized,
        status=status,
        version=next_version,
        idempotency_key=idempotency_key,
    )
    db.add(strategy)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        if idempotency_key:
            concurrent = db.execute(
                select(Strategy).where(Strategy.idempotency_key == idempotency_key)
            ).scalars().first()
            if concurrent is not None:
                if _matches_create_request(
                    concurrent,
                    name=clean_name,
                    strategy_type=strategy_type,
                    status=status,
                    normalized_params=normalized,
                ):
                    return concurrent
                raise StrategyCreateConflictError(
                    "idempotency key was concurrently used with a different strategy request"
                ) from exc
        raise StrategyCreateConflictError("create strategy failed") from exc
    db.refresh(strategy)
    return strategy


def create_independent_strategy(
    db: Session,
    *,
    name: str,
    strategy_type: str,
    params: dict[str, Any],
    description: str | None,
    idempotency_key: str | None,
) -> Strategy:
    normalized = normalize_creatable_strategy_params(
        db,
        strategy_type=strategy_type,
        params=params,
        description=description,
    )
    clean_name = name.strip()

    if idempotency_key:
        existing = db.execute(
            select(Strategy).where(Strategy.idempotency_key == idempotency_key)
        ).scalars().first()
        if existing is not None:
            if (
                existing.strategy_key == clean_name
                and existing.version == 1
                and _matches_create_request(
                    existing,
                    name=clean_name,
                    strategy_type=strategy_type,
                    status="draft",
                    normalized_params=normalized,
                )
            ):
                return existing
            raise StrategyCreateConflictError(
                "idempotency key was already used with a different strategy request"
            )

    conflicting = db.execute(
        select(Strategy)
        .where(or_(Strategy.name == clean_name, Strategy.strategy_key == clean_name))
        .limit(1)
    ).scalars().first()
    if conflicting is not None:
        raise StrategyNameConflictError(
            "strategy name must be unique when creating an independent strategy"
        )

    strategy = Strategy(
        strategy_key=clean_name,
        name=clean_name,
        strategy_type=strategy_type,
        params=normalized,
        status="draft",
        version=1,
        idempotency_key=idempotency_key,
    )
    db.add(strategy)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        if idempotency_key:
            concurrent = db.execute(
                select(Strategy).where(Strategy.idempotency_key == idempotency_key)
            ).scalars().first()
            if concurrent is not None and (
                concurrent.strategy_key == clean_name
                and concurrent.version == 1
                and _matches_create_request(
                    concurrent,
                    name=clean_name,
                    strategy_type=strategy_type,
                    status="draft",
                    normalized_params=normalized,
                )
            ):
                return concurrent
        conflicting = db.execute(
            select(Strategy)
            .where(or_(Strategy.name == clean_name, Strategy.strategy_key == clean_name))
            .limit(1)
        ).scalars().first()
        if conflicting is not None:
            raise StrategyNameConflictError(
                "strategy name must be unique when creating an independent strategy"
            ) from exc
        raise StrategyCreateConflictError("create independent strategy failed") from exc
    db.refresh(strategy)
    return strategy
