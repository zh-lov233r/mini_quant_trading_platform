# src/api/strategies.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.agent_auth import require_agent_service
from src.models.tables import (
    PortfolioSnapshot,
    Signal,
    Strategy,
    StrategyAllocation,
    StrategyRun,
    SupportResistanceMaterializationEvent,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    Transaction,
)
from src.services.alpaca_services import AlpacaClientError
from src.services.strategy_delete_service import (
    StrategyDeleteCloseError,
    build_strategy_delete_manual_reconcile_message,
    build_strategy_delete_position_close_message,
    inspect_strategy_delete_broker_positions,
    close_strategy_delete_broker_positions,
)
from src.services.strategy_registry import (
    build_runtime_payload,
    build_strategy_catalog,
    extract_description,
    get_trend_engine_supported_windows,
    is_engine_ready,
    json_signature,
    normalize_strategy_params,
)
from src.services.strategy_service import (
    StrategyCreateConflictError,
    StrategyNameConflictError,
    create_independent_strategy,
    create_strategy_version,
    load_feature_support,
    validate_strategy_params,
)
from src.services.adaptive_research_service import (
    archive_unused_research_draft,
)
from src.services.research_experiment_service import ExperimentConflictError, ExperimentNotFoundError
from src.services.strategy_types import EngineReadyStrategyType, StrategyType


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="策略名称")
    description: Optional[str] = Field(default=None, max_length=500, description="策略说明")
    strategy_type: StrategyType = Field(..., description="策略类型")
    params: Dict[str, Any] = Field(..., description="策略参数 (JSON 对象)")
    status: Literal["draft", "active", "archived"] = "draft"


class StrategyRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="新的策略名称")


class StrategyCloneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128, description="独立副本名称")
    description: Optional[str] = Field(default=None, max_length=500, description="策略说明")
    params: Dict[str, Any] = Field(..., description="基于来源策略修改后的参数")


class StrategyConfigUpdate(BaseModel):
    description: Optional[str] = Field(default=None, max_length=500, description="策略说明")
    params: Dict[str, Any] = Field(..., description="策略参数 (JSON 对象)")
    status: Optional[Literal["draft", "active", "archived"]] = Field(
        default=None,
        description="策略状态",
    )


class StrategyCatalogItem(BaseModel):
    strategy_type: StrategyType
    label: str
    description: str
    engine_ready: bool
    defaults: Dict[str, Any]
    parameter_schema: Dict[str, Any]
    required_features: list[str]
    algorithm_revision: int | None
    history_length: int = 0


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
    strategy_type: StrategyType
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
    strategy_type: StrategyType
    engine_ready: bool
    params: Dict[str, Any]


class StrategyValidationOut(BaseModel):
    valid: bool = True
    strategy_type: StrategyType
    normalized_params: Dict[str, Any]
    engine_ready: bool


class StrategyParameterOverride(BaseModel):
    path: str = Field(min_length=1, max_length=120)
    value: Any


class StrategyProposal(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    strategy_type: EngineReadyStrategyType
    overrides: list[StrategyParameterOverride] = Field(default_factory=list, max_length=30)
    symbols: list[str] = Field(min_length=1, max_length=500)


class StrategyProposalValidationOut(BaseModel):
    valid: bool = True
    strategy: StrategyCreate


class ResearchDraftArchiveRequest(BaseModel):
    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")


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
    deleted_support_resistance_run_events: int
    deleted_support_resistance_run_links: int
    retained_support_resistance_materializations: int
    retained_support_resistance_materialization_events: int


router = APIRouter(prefix="/api/strategies", tags=["strategies"])
agent_router = APIRouter(prefix="/api/agent", tags=["agent-integration"])


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


def _build_feature_support_payload(db: Session) -> StrategyFeatureSupportOut:
    support = load_feature_support(db)["trend"]
    return StrategyFeatureSupportOut(
        trend=TrendIndicatorSupportOut(
            ema_windows=support["ema_windows"],
            sma_windows=support["sma_windows"],
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
    deleted_support_resistance_run_events = int(
        db.execute(
            select(func.count())
            .select_from(SupportResistanceRunEvent)
            .join(StrategyRun, StrategyRun.id == SupportResistanceRunEvent.run_id)
            .where(StrategyRun.strategy_id == strategy_id)
        ).scalar_one()
    )
    deleted_support_resistance_run_links = int(
        db.execute(
            select(func.count())
            .select_from(SupportResistanceRunMaterialization)
            .join(StrategyRun, StrategyRun.id == SupportResistanceRunMaterialization.run_id)
            .where(StrategyRun.strategy_id == strategy_id)
        ).scalar_one()
    )
    retained_support_resistance_materializations = int(
        db.execute(
            select(func.count(func.distinct(SupportResistanceRunMaterialization.materialization_id)))
            .select_from(SupportResistanceRunMaterialization)
            .join(StrategyRun, StrategyRun.id == SupportResistanceRunMaterialization.run_id)
            .where(StrategyRun.strategy_id == strategy_id)
        ).scalar_one()
    )
    retained_support_resistance_materialization_events = int(
        db.execute(
            select(func.count(func.distinct(SupportResistanceMaterializationEvent.id)))
            .select_from(SupportResistanceMaterializationEvent)
            .join(
                SupportResistanceRunMaterialization,
                SupportResistanceRunMaterialization.materialization_id
                == SupportResistanceMaterializationEvent.materialization_id,
            )
            .join(StrategyRun, StrategyRun.id == SupportResistanceRunMaterialization.run_id)
            .where(StrategyRun.strategy_id == strategy_id)
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
        "deleted_support_resistance_run_events": deleted_support_resistance_run_events,
        "deleted_support_resistance_run_links": deleted_support_resistance_run_links,
        "retained_support_resistance_materializations": retained_support_resistance_materializations,
        "retained_support_resistance_materialization_events": (
            retained_support_resistance_materialization_events
        ),
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


@router.post("/validate", response_model=StrategyValidationOut)
def validate_strategy(payload: StrategyCreate, db: Session = Depends(get_db)):
    try:
        normalized = validate_strategy_params(
            db,
            strategy_type=payload.strategy_type,
            params=payload.params,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": str(exc)}) from exc
    return StrategyValidationOut(
        strategy_type=payload.strategy_type,
        normalized_params=normalized,
        engine_ready=True,
    )


@router.post("/proposals/validate", response_model=StrategyProposalValidationOut)
def validate_strategy_proposal(payload: StrategyProposal, db: Session = Depends(get_db)):
    catalog_item = next(
        (item for item in build_strategy_catalog() if item["strategy_type"] == payload.strategy_type),
        None,
    )
    if catalog_item is None or not catalog_item["engine_ready"]:
        raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": "strategy is not engine-ready"})
    params = catalog_item["defaults"]
    override_paths = [item.path for item in payload.overrides]
    if len(override_paths) != len(set(override_paths)):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_strategy", "message": "override paths must be unique"},
        )
    for override in sorted(payload.overrides, key=lambda item: item.path):
        if not override.path.startswith(("signal.", "risk.")):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_strategy", "message": f"override path is not allowed: {override.path}"},
            )
        current: Any = params
        parts = override.path.split(".")
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": f"override path does not exist: {override.path}"})
            current = current[part]
        if not isinstance(current, dict) or parts[-1] not in current:
            raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": f"override path does not exist: {override.path}"})
        current[parts[-1]] = override.value
    params["universe"]["symbols"] = sorted({symbol.strip().upper() for symbol in payload.symbols if symbol.strip()})
    params["universe"]["selection_mode"] = "manual"
    try:
        normalized = validate_strategy_params(
            db,
            strategy_type=payload.strategy_type,
            params=params,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": str(exc)}) from exc
    return StrategyProposalValidationOut(
        strategy=StrategyCreate(
            name=payload.name,
            description=payload.description,
            strategy_type=payload.strategy_type,
            params=normalized,
            status="draft",
        )
    )


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
    try:
        obj = create_strategy_version(
            db,
            name=payload.name,
            strategy_type=payload.strategy_type,
            params=payload.params,
            description=payload.description,
            status=payload.status,
            idempotency_key=idem_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except StrategyCreateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="create strategy failed",
        ) from exc
    return _to_strategy_out(obj)


@router.post("/{strategy_id}/clone", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
def clone_strategy(
    strategy_id: UUID,
    payload: StrategyCloneCreate,
    db: Session = Depends(get_db),
    idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    source = db.get(Strategy, strategy_id)
    if source is None:
        raise HTTPException(status_code=404, detail="strategy not found")

    try:
        clone = create_independent_strategy(
            db,
            name=payload.name,
            strategy_type=source.strategy_type,
            params=payload.params,
            description=payload.description,
            idempotency_key=idem_key,
        )
    except StrategyNameConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "strategy_name_conflict",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except StrategyCreateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "strategy_clone_conflict",
                "message": str(exc),
            },
        ) from exc
    return _to_strategy_out(clone)


@agent_router.post(
    "/strategies",
    response_model=StrategyOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_agent_service)],
)
def create_agent_strategy(
    payload: StrategyCreate,
    db: Session = Depends(get_db),
    idem_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    try:
        strategy = create_strategy_version(
            db,
            name=payload.name,
            strategy_type=payload.strategy_type,
            params=payload.params,
            description=payload.description,
            status="draft",
            idempotency_key=idem_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": str(exc)}) from exc
    except StrategyCreateConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "strategy_conflict", "message": str(exc)}) from exc
    return _to_strategy_out(strategy)


@agent_router.post(
    "/strategies/{strategy_id}/archive-unused-research-draft",
    response_model=StrategyOut,
    dependencies=[Depends(require_agent_service)],
)
def archive_agent_research_draft(
    strategy_id: UUID,
    payload: ResearchDraftArchiveRequest,
    db: Session = Depends(get_db),
    _idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    try:
        strategy = archive_unused_research_draft(
            db,
            strategy_id,
            workflow_run_id=payload.workflow_run_id,
        )
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except ExperimentConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "archive_conflict", "message": str(exc)},
        ) from exc
    return _to_strategy_out(strategy)


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
    close_positions: bool = Query(
        default=False,
        description="If true, first flatten any matching Alpaca positions before deleting the strategy",
    ),
    db: Session = Depends(get_db),
):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")

    try:
        broker_positions = inspect_strategy_delete_broker_positions(db, strategy_id)
    except AlpacaClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"could not verify Alpaca positions before deleting strategy: {exc}",
        ) from exc

    unsafe_positions = [item for item in broker_positions if not item.can_close]
    if unsafe_positions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=build_strategy_delete_manual_reconcile_message(
                strategy_name=obj.name,
                broker_positions=unsafe_positions,
            ),
        )

    if broker_positions and not close_positions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=build_strategy_delete_position_close_message(
                strategy_name=obj.name,
                broker_positions=broker_positions,
            ),
        )

    if broker_positions:
        try:
            close_strategy_delete_broker_positions(
                db,
                strategy_id,
                broker_positions=broker_positions,
            )
        except AlpacaClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"failed to close Alpaca positions before deleting strategy: {exc}",
            ) from exc
        except StrategyDeleteCloseError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

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
