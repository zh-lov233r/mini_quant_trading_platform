"""Run with python -m src.workers.signal_worker; safe switches default off."""
import argparse
import logging
import os
from threading import Event
import signal
from src.core.db import SessionLocal
from src.services.signal_scan_job_service import run_once
from src.services.signal_report_retention import cleanup


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--once",action="store_true");args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    stop=Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        try:
            worked=run_once(SessionLocal)
            if os.getenv("SIGNAL_REPORT_RETENTION_ENABLED","false").lower()=="true":
                with SessionLocal() as db:cleanup(db,apply=True);db.commit()
        except Exception:
            logging.exception("Signal worker iteration failed")
            worked=False
        if args.once:break
        if not worked:stop.wait(5)

if __name__=="__main__":main()
