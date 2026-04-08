# src/api/strategies.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.models.tables import PortfolioSnapshot, Signal, Strategy, StrategyAllocation, StrategyRun, Transaction
from src.services.strategy_registry import (
    build_runtime_payload,
    build_strategy_catalog,
    extract_description,
    get_trend_engine_supported_windows,
    is_engine_ready,
    json_signature,
    normalize_strategy_params,
)


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="策略名称")
    description: Optional[str] = Field(default=None, max_length=500, description="策略说明")
    strategy_type: Literal["trend", "mean_reversion", "island_reversal", "double_bottom", "custom"] = Field(..., description="策略类型")
    params: Dict[str, Any] = Field(..., description="策略参数 (JSON 对象)")
    status: Literal["draft", "active", "archived"] = "draft"


class StrategyRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="新的策略名称")


class StrategyConfigUpdate(BaseModel):
    description: Optional[str] = Field(default=None, max_length=500, description="策略说明")
    params: Dict[str, Any] = Field(..., description="策略参数 (JSON 对象)")
    status: Optional[Literal["draft", "active", "archived"]] = Field(
        default=None,
        description="策略状态",
    )


class StrategyCatalogItem(BaseModel):
    strategy_type: str
    label: str
    description: str
    engine_ready: bool
    defaults: Dict[str, Any]


class TrendIndicatorSupportOut(BaseModel):
    ema_windows: list[int]
    sma_windows: list[int]


class StrategyFeatureSupportOut(BaseModel):
    trend: TrendIndicatorSupportOut


class StrategyOut(BaseModel):
    id: UUID
    strategy_key: str
    display_name: str
    name: str
    description: Optional[str] = None
    strategy_type: str
    params: Dict[str, Any]
    status: str
    version: int
    engine_ready: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StrategyRuntimeOut(BaseModel):
    strategy_id: str
    strategy_key: str
    display_name: str
    name: str
    version: int
    status: str
    strategy_type: str
    engine_ready: bool
    params: Dict[str, Any]


class StrategyDeleteOut(BaseModel):
    strategy_id: UUID
    strategy_name: str
    deleted_backtest_runs: int
    deleted_paper_runs: int
    deleted_live_runs: int
    deleted_backtest_snapshots: int
    deleted_signals: int
    deleted_transactions: int
    deleted_allocations: int


router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _to_strategy_out(obj: Strategy) -> StrategyOut:
    normalized_params = normalize_strategy_params(
        obj.strategy_type,
        obj.params,
        extract_description(obj.params),
    )
    return StrategyOut(
        id=obj.id,
        strategy_key=obj.strategy_key,
        display_name=obj.name,
        name=obj.name,
        description=extract_description(normalized_params),
        strategy_type=obj.strategy_type,
        params=normalized_params,
        status=obj.status,
        version=obj.version,
        engine_ready=is_engine_ready(obj.strategy_type, normalized_params),
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _load_supported_daily_feature_columns(db: Session) -> set[str]:
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
    return {str(row[0]).strip().lower() for row in rows}


def _build_feature_support_payload(db: Session) -> StrategyFeatureSupportOut:
    available_columns = _load_supported_daily_feature_columns(db)
    engine_supported = get_trend_engine_supported_windows()

    ema_windows = [
        window
        for window in engine_supported["ema"]
        if f"ema_{window}" in available_columns
    ]
    sma_windows = [
        window
        for window in engine_supported["sma"]
        if f"sma_{window}" in available_columns
    ]

    return StrategyFeatureSupportOut(
        trend=TrendIndicatorSupportOut(
            ema_windows=ema_windows,
            sma_windows=sma_windows,
        )
    )


def _build_delete_summary(db: Session, strategy_id: UUID) -> dict[str, int]:
    run_counts = {
        str(mode): int(count)
        for mode, count in db.execute(
            select(StrategyRun.mode, func.count())
            .where(StrategyRun.strategy_id == strategy_id)
            .group_by(StrategyRun.mode)
        ).all()
    }

    deleted_backtest_snapshots = int(
        db.execute(
            select(func.count())
            .select_from(PortfolioSnapshot)
            .join(StrategyRun, StrategyRun.id == PortfolioSnapshot.run_id)
            .where(StrategyRun.strategy_id == strategy_id)
            .where(StrategyRun.mode == "backtest")
        ).scalar_one()
    )

    deleted_signals = int(
        db.execute(
            select(func.count())
            .select_from(Signal)
            .where(Signal.strategy_id == strategy_id)
        ).scalar_one()
    )
    deleted_transactions = int(
        db.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.strategy_id == strategy_id)
        ).scalar_one()
    )
    deleted_allocations = int(
        db.execute(
            select(func.count())
            .select_from(StrategyAllocation)
            .where(StrategyAllocation.strategy_id == strategy_id)
        ).scalar_one()
    )

    return {
        "deleted_backtest_runs": int(run_counts.get("backtest", 0)),
        "deleted_paper_runs": int(run_counts.get("paper", 0)),
        "deleted_live_runs": int(run_counts.get("live", 0)),
        "deleted_backtest_snapshots": deleted_backtest_snapshots,
        "deleted_signals": deleted_signals,
        "deleted_transactions": deleted_transactions,
        "deleted_allocations": deleted_allocations,
    }


def _validate_feature_support(
    db: Session,
    *,
    strategy_type: str,
    params: Dict[str, Any],
) -> None:
    if strategy_type != "trend":
        return

    support = _build_feature_support_payload(db)
    signal = params.get("signal") or {}
    fast = signal.get("fast_indicator") or {}
    slow = signal.get("slow_indicator") or {}

    for label, indicator in (("快线", fast), ("慢线", slow)):
        kind = str(indicator.get("kind") or "").strip().lower()
        window = indicator.get("window")
        if kind not in {"ema", "sma"}:
            raise ValueError(f"{label}类型不受支持: {kind or '空'}")
        if not isinstance(window, int):
            raise ValueError(f"{label}周期格式不正确")

        supported_windows = (
            support.trend.ema_windows if kind == "ema" else support.trend.sma_windows
        )
        if window not in supported_windows:
            supported_text = ", ".join(str(item) for item in supported_windows) or "无"
            raise ValueError(
                f"当前数据库不支持 {label}{kind.upper()}{window}。"
                f"可用 {kind.upper()} 周期: {supported_text}"
            )


@router.get("/catalog", response_model=list[StrategyCatalogItem])
def get_strategy_catalog():
    return [StrategyCatalogItem(**item) for item in build_strategy_catalog()]


@router.get("/feature-support", response_model=StrategyFeatureSupportOut)
def get_strategy_feature_support(db: Session = Depends(get_db)):
    return _build_feature_support_payload(db)


@router.get("", response_model=list[StrategyOut])
def list_strategies(
    db: Session = Depends(get_db),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    strategy_type: Optional[str] = Query(default=None),
    name: Optional[str] = Query(default=None, description="按策略名模糊搜索"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Strategy).order_by(Strategy.created_at.desc(), Strategy.version.desc())

    if status_filter:
        stmt = stmt.where(Strategy.status == status_filter)
    if strategy_type:
        stmt = stmt.where(Strategy.strategy_type == strategy_type)
    if name:
        stmt = stmt.where(Strategy.name.ilike(f"%{name.strip()}%"))

    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return [_to_strategy_out(row) for row in rows]


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreate,
    db: Session = Depends(get_db),
    idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if idem_key:
        existed = db.execute(
            select(Strategy).where(Strategy.idempotency_key == idem_key)
        ).scalars().first()
        if existed:
            return _to_strategy_out(existed)

    try:
        normalized_params = normalize_strategy_params(
            payload.strategy_type,
            payload.params,
            payload.description,
        )
        _validate_feature_support(
            db,
            strategy_type=payload.strategy_type,
            params=normalized_params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    latest_same_name = db.execute(
        select(Strategy)
        .where(Strategy.name == payload.name.strip())
        .order_by(Strategy.version.desc())
    ).scalars().first()

    if latest_same_name:
        existing_normalized = normalize_strategy_params(
            latest_same_name.strategy_type,
            latest_same_name.params,
            extract_description(latest_same_name.params),
        )
        if (
            latest_same_name.strategy_type == payload.strategy_type
            and latest_same_name.status == payload.status
            and json_signature(existing_normalized) == json_signature(normalized_params)
        ):
            return _to_strategy_out(latest_same_name)
        strategy_key = latest_same_name.strategy_key
        latest_same_family = db.execute(
            select(Strategy)
            .where(Strategy.strategy_key == strategy_key)
            .order_by(Strategy.version.desc())
        ).scalars().first()
        next_version = (latest_same_family.version if latest_same_family else latest_same_name.version) + 1
    else:
        strategy_key = payload.name.strip()
        next_version = 1

    obj = Strategy(
        strategy_key=strategy_key,
        name=payload.name.strip(),
        strategy_type=payload.strategy_type,
        params=normalized_params,
        status=payload.status,
        version=next_version,
        idempotency_key=idem_key,
    )
    db.add(obj)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"create strategy failed: {str(exc)}",
        ) from exc

    db.refresh(obj)
    return _to_strategy_out(obj)


@router.get("/{strategy_id}/runtime", response_model=StrategyRuntimeOut)
def get_strategy_runtime(strategy_id: UUID, db: Session = Depends(get_db)):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")
    return StrategyRuntimeOut(**build_runtime_payload(obj))


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: UUID, db: Session = Depends(get_db)):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")
    return _to_strategy_out(obj)


@router.patch("/{strategy_id}", response_model=StrategyOut)
def rename_strategy(
    strategy_id: UUID,
    payload: StrategyRename,
    db: Session = Depends(get_db),
):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")

    next_name = payload.name.strip()
    if not next_name:
        raise HTTPException(status_code=422, detail="strategy name cannot be empty")

    if next_name == obj.name:
        return _to_strategy_out(obj)

    conflicting = db.execute(
        select(Strategy)
        .where(Strategy.id != strategy_id)
        .where(Strategy.name == next_name)
        .limit(1)
    ).scalars().first()
    if conflicting is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "target strategy name already exists; first rename version only allows "
                "renaming to a brand-new name to avoid merging strategy version families"
            ),
        )

    obj.name = next_name

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"rename strategy failed: {str(exc)}",
        ) from exc

    db.refresh(obj)
    return _to_strategy_out(obj)


@router.patch("/{strategy_id}/config", response_model=StrategyOut)
def update_strategy_config(
    strategy_id: UUID,
    payload: StrategyConfigUpdate,
    db: Session = Depends(get_db),
):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")

    next_description = (
        payload.description.strip()
        if isinstance(payload.description, str)
        else extract_description(obj.params)
    )
    next_status = payload.status or obj.status

    try:
        normalized_params = normalize_strategy_params(
            obj.strategy_type,
            payload.params,
            next_description,
        )
        _validate_feature_support(
            db,
            strategy_type=obj.strategy_type,
            params=normalized_params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    obj.params = normalized_params
    obj.status = next_status

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"update strategy config failed: {str(exc)}",
        ) from exc

    db.refresh(obj)
    return _to_strategy_out(obj)


@router.delete("/{strategy_id}", response_model=StrategyDeleteOut)
def delete_strategy(
    strategy_id: UUID,
    db: Session = Depends(get_db),
):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")

    delete_summary = _build_delete_summary(db, strategy_id)
    strategy_name = obj.name

    try:
        db.delete(obj)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"delete strategy failed: {str(exc)}",
        ) from exc

    return StrategyDeleteOut(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        **delete_summary,
    )
