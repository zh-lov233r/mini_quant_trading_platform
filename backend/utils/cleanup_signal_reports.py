"""Dry-run by default; only the explicitly configured report retention scope is eligible."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.core.db import SessionLocal,engine
from src.services.signal_report_retention import cleanup

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--apply",action="store_true");args=parser.parse_args()
    print(f"Target: host={engine.url.host} port={engine.url.port} database={engine.url.database}")
    with SessionLocal() as db:
        print(cleanup(db,apply=args.apply))
        if args.apply:db.commit()
        else:db.rollback()
