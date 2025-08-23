# scripts/smoke_db.py
from sqlalchemy import text
from backends.src.core.db import engine, get_db, ensure_extensions

def main():
    # 1) 扩展（幂等）
    ensure_extensions()

    # 2) 基础连接
    with engine.begin() as conn:
        ver = conn.execute(text("select version();")).scalar()
        print("db version:", ver)

    # 3) 会话生命周期 + 事务
    gen = get_db()
    db = next(gen)
    try:
        # 不改动业务表：做一次只读查询
        r = db.execute(text("select 1")).scalar()
        assert r == 1
        print("session ok")
    finally:
        try:
            next(gen)  # 触发 finally: close()
        except StopIteration:
            pass

if __name__ == "__main__":
    main()
