# src/api/strategies.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.models.tables import Strategy
from src.services.strategy_registry import (
    build_runtime_payload,
    build_strategy_catalog,
    extract_description,
    is_engine_ready,
    json_signature,
    normalize_strategy_params,
)


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="策略名称")
    description: Optional[str] = Field(default=None, max_length=500, description="策略说明")
    strategy_type: Literal["trend", "mean_reversion", "custom"] = Field(..., description="策略类型")
    params: Dict[str, Any] = Field(..., description="策略参数 (JSON 对象)")
    status: Literal["draft", "active", "archived"] = "draft"


class StrategyCatalogItem(BaseModel):
    strategy_type: str
    label: str
    description: str
    engine_ready: bool
    defaults: Dict[str, Any]


class StrategyOut(BaseModel):
    id: UUID
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
    name: str
    version: int
    status: str
    strategy_type: str
    engine_ready: bool
    params: Dict[str, Any]


router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _to_strategy_out(obj: Strategy) -> StrategyOut:
    normalized_params = normalize_strategy_params(
        obj.strategy_type,
        obj.params,
        extract_description(obj.params),
    )
    return StrategyOut(
        id=obj.id,
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


@router.get("/catalog", response_model=list[StrategyCatalogItem])
def get_strategy_catalog():
    return [StrategyCatalogItem(**item) for item in build_strategy_catalog()]


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
        next_version = latest_same_name.version + 1
    else:
        next_version = 1

    obj = Strategy(
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
