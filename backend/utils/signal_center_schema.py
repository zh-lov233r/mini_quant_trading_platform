"""Read-only preflight or explicitly apply ONLY additive Signal Center tables."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine,inspect,text
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects import postgresql
from src.models.tables import Base


def ddl():
    statements=[]
    for table in Base.metadata.sorted_tables:
        if not table.name.startswith("signal_"):continue
        statements.append(str(CreateTable(table,if_not_exists=True).compile(dialect=postgresql.dialect())).strip()+";")
        statements.extend(str(CreateIndex(index,if_not_exists=True).compile(dialect=postgresql.dialect()))+";" for index in sorted(table.indexes,key=lambda i:i.name))
    return "-- Additive Signal Center schema. Apply only after target preflight and authorization.\nBEGIN;\n"+"\n\n".join("\n".join(line.rstrip() for line in statement.splitlines()) for statement in statements)+"\nCOMMIT;\n"


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--database-url");parser.add_argument("--apply",action="store_true");parser.add_argument("--sql",action="store_true");args=parser.parse_args()
    if args.sql:print(ddl());return
    import os
    url=args.database_url or os.getenv("DATABASE_URL")
    if not url:parser.error("--database-url or DATABASE_URL is required")
    engine=create_engine(url)
    print(f"Target: host={engine.url.host} port={engine.url.port} database={engine.url.database}")
    names=[t.name for t in Base.metadata.sorted_tables if t.name.startswith("signal_")]
    with engine.connect() as conn:
        existing=set(inspect(conn).get_table_names());counts={}
        for name in names:
            counts[name]=conn.scalar(text(f'SELECT count(*) FROM "{name}"')) if name in existing else "missing"
        print(counts)
        if not args.apply:print("Preflight only; no schema or data changed.");return
        conn.rollback()
        with conn.begin():
            for table in Base.metadata.sorted_tables:
                if table.name in names:table.create(conn,checkfirst=True)
    print("Additive schema applied; no pre-existing rows changed.")

if __name__=="__main__":main()
