# 此脚本用于清空数据库的所有schemas并创建一个空的public schema

import os
import sys
import psycopg
from psycopg import sql
from dotenv import load_dotenv

# 1) 读取连接串：优先 .env 的 DATABASE_URL，否则用默认值
load_dotenv()
DSN = os.getenv("DATABASE_URL", "postgresql://hzy:5041899@localhost:5432/hzy")

SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
TEMP_PATTERNS = ("pg_temp_%", "pg_toast_temp_%")

def main():
    print(f"Connecting: {DSN}")
    dropped = []
    owner = None

    # 用事务包起来；出错会整体回滚
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            # 2) 找出所有可删除的 schema（排除系统与临时）
            cur.execute("""
                SELECT nspname
                FROM pg_namespace
                WHERE nspname NOT IN ('pg_catalog','information_schema','pg_toast')
                  AND nspname NOT LIKE %s
                  AND nspname NOT LIKE %s
                ORDER BY nspname;
            """, TEMP_PATTERNS)
            schemas = [r[0] for r in cur.fetchall()]

            # 3) 逐个 DROP SCHEMA ... CASCADE
            for s in schemas:
                print(f"Dropping schema: {s}")
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE")
                            .format(sql.Identifier(s)))
                dropped.append(s)

            # 4) 重建 public，并把所有权与权限授给当前会话用户
            cur.execute("SELECT current_user")
            owner = cur.fetchone()[0]
            print(f"Recreating schema: public (owner: {owner})")
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS public AUTHORIZATION {}")
                        .format(sql.Identifier(owner)))
            cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}")
                        .format(sql.Identifier(owner)))
            cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")

        conn.commit()

    print(f"\nDone. Dropped {len(dropped)} schema(s): {dropped}")
    print(f"Schema `public` recreated and owned by `{owner}`.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
