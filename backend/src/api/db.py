# app/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base


DATABASE_URL = "postgresql+psycopg2://quant_user:quant_password@localhost:5432/quant_db"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# FastAPI 依赖注入用：每请求一个数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
