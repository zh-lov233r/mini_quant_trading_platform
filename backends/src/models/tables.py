# 数据模型; Data Model
# Pydantic 模型; 数据类型验证
from sqlalchemy.orm import declarative_base

# Base 就是所有 ORM 模型的基类
Base = declarative_base()

# 示例模型：strategies 表
class Strategy(Base):
    __tablename__ = "strategies"

    # 列定义（你的建表 SQL 要对应）
    from sqlalchemy import Column, String, Integer, DateTime, func
    from sqlalchemy.dialects.postgresql import UUID, JSONB
    import uuid

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False)
    strategy_type = Column(String(32), nullable=False)
    params = Column(JSONB, nullable=False)
    status = Column(String(16), nullable=False, default="draft")
    cur_position = Column(JSONB, default="{}")
    version = Column(Integer, nullable=False, default=1)
    idempotency_key = Column(String(64), unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


