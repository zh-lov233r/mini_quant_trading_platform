# 此脚本用于创建一个新的数据库，包括插入表中的数据

from pathlib import Path
import os
import sys

from dotenv import load_dotenv
import psycopg

load_dotenv()

SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL") or os.getenv("DATABASE_URL")
SQL_DIR = Path(__file__).resolve().parent


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def run_all() -> None:
    if not SQLALCHEMY_DATABASE_URL:
        raise SystemExit("Missing SQLALCHEMY_DATABASE_URL or DATABASE_URL")

    files = sorted(
        path for path in SQL_DIR.glob("*.sql")
        if path.name.startswith("create_")
    )
    if not files:
        raise SystemExit(f"No create_*.sql files found in {SQL_DIR.resolve()}")

    with psycopg.connect(_psycopg_dsn(SQLALCHEMY_DATABASE_URL)) as conn:
        with conn.cursor() as cur:
            for file_path in files:
                sql = file_path.read_text(encoding="utf-8")
                print(f"-> Running {file_path.name} ({len(sql)} bytes)")
                cur.execute(sql)
        conn.commit()
    print("All done")


if __name__ == "__main__":
    try:
        run_all()
    except Exception as exc:
        print("FAILED:", exc, file=sys.stderr)
        sys.exit(1)



# Back fill instrument table




# # Data Insertion into min_stock table
# # pip install psycopg
# schema_stage = """
# CREATE TEMP TABLE IF NOT EXISTS stocks_stage (
#   symbol     text,
#   name       text,
#   last_sale  text,
#   net_change text,
#   pct_change text,
#   country    text,
#   ipo_year   text,
#   sector     text,
#   industry   text
# );
# """
# upsert_sql = """
# INSERT INTO public.stocks_min(symbol, name, ipo_year, sector, industry)
# SELECT
#   UPPER(TRIM(symbol)),
#   TRIM(name),
#   NULLIF(regexp_replace(ipo_year, '\\D', '', 'g'), '')::int,
#   NULLIF(TRIM(sector),   ''),
#   NULLIF(TRIM(industry), '')
# FROM stocks_stage
# WHERE NULLIF(TRIM(symbol),'') IS NOT NULL
# ON CONFLICT (symbol) DO UPDATE
# SET name=EXCLUDED.name,
#     ipo_year=EXCLUDED.ipo_year,
#     sector=EXCLUDED.sector,
#     industry=EXCLUDED.industry;
# """
# csv_path = "/Users/hzy/PycharmProjects/quant/quant-trading-system/data/stock_symbols.csv"
# with psycopg.connect(psycopg_url) as conn:
#     print('Trying to insert data into table min_stock')
#     with conn.cursor() as cur:
#         cur.execute(schema_stage)

#         # COPY FROM STDIN（psycopg3 写法）
#         with cur.copy(
#             "COPY stocks_stage FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')"
#         ) as copy:
#             with open(csv_path, "rb") as f:            # 用二进制更稳
#                 while chunk := f.read(1024 * 1024):
#                     copy.write(chunk)

#         cur.execute(upsert_sql)
#     conn.commit()
# print('Insertion Completed, Script Execution Successful')



