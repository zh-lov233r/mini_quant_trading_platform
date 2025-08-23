# 目前只需要实现趋势跟踪
# 
# 本文件实现的功能：
#   从前端接收用户自定义的策略，并存储在本地 (创建策略)
#   一个策略下可以有多个个股的持仓，需要用数据库存储一个持仓列表
#   


# api/v1/strategies.py
from typing import Any, Dict, Optional, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import get_db
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, Integer, DateTime, func, JSON
try:
    # Postgres 下优先用 JSONB（若切到 MySQL 依然能工作，只是走 JSON）
    from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
    USE_JSONB = True
except Exception:
    PGUUID = None
    JSONB = None
    USE_JSONB = False

Base = declarative_base()


# ==== ORM mapping to current strategies table ====
class Strategy(Base):
    __tablename__ = "strategies"

    # 注意：你的表里 id 是 UUID 主键；用 PGUUID 更精准（MySQL 下可退化）
    id = Column(PGUUID(as_uuid=True) if PGUUID else String, primary_key=True)

    name = Column(String(128), nullable=False)
    strategy_type = Column(String(32), nullable=False)
    params = Column(JSONB if USE_JSONB else JSON, nullable=False)

    # 这些字段在你的建表 SQL 中均存在
    status = Column(String(16), nullable=False, default="draft")
    version = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String(64), unique=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True),
                        server_default=func.now(), onupdate=func.now())

    # 注意：如果你后来为表新增了 cur_position/jsonb 列，
    # 这份 ORM 没有声明也没关系——插入时不触碰该列，数据库会用默认值。


# ==== Pydantic 入参/出参模型 ====
class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="策略名称")
    strategy_type: Literal["trend", "mean_reversion", "custom"] = Field(..., description="策略类型")
    params: Dict[str, Any] = Field(..., description="策略参数 (自由结构 JSON)")
    status: Literal["draft", "active"] = "draft"


class StrategyOut(BaseModel):
    id: UUID
    name: str
    strategy_type: str
    params: Dict[str, Any]
    status: str
    version: int


router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


# ==== 创建策略 ====
@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreate,
    db: Session = Depends(get_db),
    idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """
    接收前端 JSON 并持久化到 strategies 表。
    - 幂等：前端可在 Header 里传 Idempotency-Key, 防止重复提交
    - 返回：创建好的策略对象 (含数据库生成的 id)
    """

    # 1) 幂等性：如果带了 Idempotency-Key，查重
    if idem_key:
        existed = db.execute(
            select(Strategy).where(Strategy.idempotency_key == idem_key)
        ).scalars().first()
        if existed:
            return StrategyOut(
                id=existed.id,
                name=existed.name,
                strategy_type=existed.strategy_type,
                params=existed.params,
                status=existed.status,
                version=existed.version,
            )

    # 2) 组装 ORM 对象并插入
    obj = Strategy(
        name=payload.name,
        strategy_type=payload.strategy_type,
        params=payload.params,
        status=payload.status,
        idempotency_key=idem_key,
        # version 由默认值 1 开始；若你有版本逻辑可在这里覆盖
    )
    db.add(obj)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        # 典型冲突：name+version 唯一约束、idempotency_key 唯一约束
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"create strategy failed: {str(e)}"
        )

    db.refresh(obj)  # 获取数据库生成的 id / 时间戳等

    return StrategyOut(
        id=obj.id,
        name=obj.name,
        strategy_type=obj.strategy_type,
        params=obj.params,
        status=obj.status,
        version=obj.version,
    )


# ====（可选）按 ID 查询，便于你本地自测 ====
@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: UUID, db: Session = Depends(get_db)):
    obj = db.get(Strategy, strategy_id)
    if not obj:
        raise HTTPException(status_code=404, detail="strategy not found")
    return StrategyOut(
        id=obj.id,
        name=obj.name,
        strategy_type=obj.strategy_type,
        params=obj.params,
        status=obj.status,
        version=obj.version,
    )














