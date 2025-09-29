# core/db.py
from __future__ import annotations
import os
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session


# === 1) 数据库 URL ===
# 优先用环境变量 DATABASE_URL；示例：
# postgresql+psycopg2://user:password@localhost:5432/hzy
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://hzy:5041899@localhost:5432/hzy",
)

# === 2) 引擎与连接池 ===
# - pool_pre_ping=True：取连接前做 ping，避免“死连接”
# - future=True：启用 2.0 风格
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    future=True,
)

# === 3) 会话工厂 ===
# - autocommit=False：显式提交
# - autoflush=False：手动 flush/commit 时才把改动发给数据库
# - expire_on_commit=False：commit 后对象不立刻过期，便于直接返回
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,  # 明确类型提示
)


# === 4) FastAPI 依赖：按请求创建/清理 Session ===
def get_db() -> Generator[Session, None, None]:
    """
    用法：
        @app.get("/...")
        def handler(db: Session = Depends(get_db)):
            ...
    说明：
        - 由业务代码显式 db.commit()/db.rollback()
        - 这里不做自动提交；无论成功/失败最终都会 close()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# === 可选：在应用启动时调用，确保扩展存在（Postgres） ===
def ensure_extensions() -> None:
    """
    在 Postgres 上启用常用扩展（幂等）：
      - pgcrypto: 支持 gen_random_uuid() (用于 UUID 默认值)
    典型调用位置：应用启动时 main.py -> on_startup 事件。
    """
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
    except Exception:
        # 如果非 Postgres 或无权限，静默跳过；也可改为记录日志
        pass


# === 可选：开发期一键建表（生产用 Alembic 迁移） ===
def create_all_tables(Base) -> None:
    """
    仅开发/本地环境使用：
        from models.tables import Base
        create_all_tables(Base)
    生产环境请使用 Alembic 管理迁移，不要在运行时自动建表。
    """
    Base.metadata.create_all(bind=engine)
