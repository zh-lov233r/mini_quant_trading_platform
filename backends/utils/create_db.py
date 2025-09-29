# 此脚本用于创建一个新的数据库，包括插入表中的数据

# pip install sqlalchemy psycopg2-binary
from sqlalchemy import create_engine, text
from pathlib import Path
from dotenv import load_dotenv
import os
import sys
import psycopg

load_dotenv()
sqlalchemy_url = os.getenv("SQLALCHEMY_DATABASE_URL")
psycopg_url = os.getenv("DATABASE_URL")
engine = create_engine(sqlalchemy_url)
SQL_DIR = Path("/Users/hzy/PycharmProjects/quant/quant-trading-system/backends/utils")

def run_all():
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        raise SystemExit(f"No .sql in {SQL_DIR.resolve()}")
    eng = create_engine(sqlalchemy_url, pool_pre_ping=True)
    with eng.begin() as conn:  # 一个事务内
        for f in files:
            sql = f.read_text(encoding="utf-8")
            print(f"-> Running {f.name} ({len(sql)} bytes)")
            conn.exec_driver_sql(sql)  # 多语句可一次执行（但不能含 psql 元命令）
    print("All done")

# sql_script = Path("./create_stock_symbol_table.sql").read_text(encoding="utf-8")
# # 一个事务里跑完整个脚本；失败会整体回滚
# with engine.begin() as conn:
#     conn.exec_driver_sql(sql_script)   # 脚本里是标准 SQL（不能含 \copy 这类 psql 元命令）

try:
    run_all()
except Exception as e:
    print("FAILED:", e, file=sys.stderr)
    sys.exit(1)
print('Table Creation Sucessful')


# Data Insertion into min_stock table
# pip install psycopg
schema_stage = """
CREATE TEMP TABLE IF NOT EXISTS stocks_stage (
  symbol     text,
  name       text,
  last_sale  text,
  net_change text,
  pct_change text,
  country    text,
  ipo_year   text,
  sector     text,
  industry   text
);
"""
upsert_sql = """
INSERT INTO public.stocks_min(symbol, name, ipo_year, sector, industry)
SELECT
  UPPER(TRIM(symbol)),
  TRIM(name),
  NULLIF(regexp_replace(ipo_year, '\\D', '', 'g'), '')::int,
  NULLIF(TRIM(sector),   ''),
  NULLIF(TRIM(industry), '')
FROM stocks_stage
WHERE NULLIF(TRIM(symbol),'') IS NOT NULL
ON CONFLICT (symbol) DO UPDATE
SET name=EXCLUDED.name,
    ipo_year=EXCLUDED.ipo_year,
    sector=EXCLUDED.sector,
    industry=EXCLUDED.industry;
"""
csv_path = "/Users/hzy/PycharmProjects/quant/quant-trading-system/data/stock_symbols.csv"
with psycopg.connect(psycopg_url) as conn:
    print('Trying to insert data into table min_stock')
    with conn.cursor() as cur:
        cur.execute(schema_stage)

        # COPY FROM STDIN（psycopg3 写法）
        with cur.copy(
            "COPY stocks_stage FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')"
        ) as copy:
            with open(csv_path, "rb") as f:            # 用二进制更稳
                while chunk := f.read(1024 * 1024):
                    copy.write(chunk)

        cur.execute(upsert_sql)
    conn.commit()
print('Insertion Completed')


