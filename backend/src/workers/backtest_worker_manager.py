from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from threading import Event
import time
from typing import Any
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.core.db import SessionLocal, engine
from src.models.tables import BacktestWorkerManager
from src.services.backtest_job_service import eligible_queued_job_count, recover_expired_jobs

log = logging.getLogger(__name__)
UTC = timezone.utc
ADVISORY_LOCK_KEY = 7_213_761_984_021_163
BACKOFF_SECONDS = (1, 2, 5, 10, 30)


def restart_delay_seconds(failure_count: int) -> int:
    return BACKOFF_SECONDS[min(max(1, failure_count), len(BACKOFF_SECONDS)) - 1]


class AdvisoryLeaderLock:
    def __init__(self) -> None:
        self.connection: Connection | None = None
        self.backend_pid: int | None = None

    def try_acquire(self) -> bool:
        if self.connection is not None and not self.connection.closed:
            if self.connection.dialect.name != "postgresql":
                return True
            try:
                current_backend_pid = int(
                    self.connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                )
                if current_backend_pid == self.backend_pid:
                    return True
                self.connection.close()
                self.connection = None
                self.backend_pid = None
            except Exception:
                try:
                    self.connection.close()
                finally:
                    self.connection = None
                    self.backend_pid = None
        connection = engine.connect()
        if connection.dialect.name != "postgresql":
            self.connection = connection
            return True
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": ADVISORY_LOCK_KEY},
            ).scalar_one()
        )
        if acquired:
            self.connection = connection
            self.backend_pid = int(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
            return True
        connection.close()
        return False

    def release(self) -> None:
        connection = self.connection
        self.connection = None
        self.backend_pid = None
        if connection is None:
            return
        try:
            if connection.dialect.name == "postgresql" and not connection.closed:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": ADVISORY_LOCK_KEY},
                )
        finally:
            connection.close()


class BacktestWorkerManagerRunner:
    def __init__(
        self,
        *,
        poll_seconds: float = 2.0,
        heartbeat_seconds: float = 5.0,
        lease_seconds: int = 120,
    ) -> None:
        self.poll_seconds = max(0.1, poll_seconds)
        self.heartbeat_seconds = max(0.5, heartbeat_seconds)
        self.lease_seconds = max(30, lease_seconds)
        self.hostname = socket.gethostname()
        self.manager_id = f"{self.hostname}:{os.getpid()}:{uuid.uuid4()}"
        self.started_at = datetime.now(UTC)
        self.stop_event = Event()
        self.leader_lock = AdvisoryLeaderLock()
        self.worker: subprocess.Popen[Any] | None = None
        self.failure_count = 0
        self.next_worker_start_at: datetime | None = None
        self.last_worker_exit_code: int | None = None
        self.last_worker_exit_at: datetime | None = None
        self.last_heartbeat_write = 0.0

    def request_stop(self, _signum: int | None = None, _frame: Any = None) -> None:
        self.stop_event.set()

    def _write_state(self, status: str, *, is_leader: bool, force: bool = False) -> None:
        now_monotonic = time.monotonic()
        if not force and now_monotonic - self.last_heartbeat_write < self.heartbeat_seconds:
            return
        observed_at = datetime.now(UTC)
        db = SessionLocal()
        try:
            row = db.get(BacktestWorkerManager, self.manager_id)
            if row is None:
                row = BacktestWorkerManager(
                    manager_id=self.manager_id,
                    hostname=self.hostname,
                    pid=os.getpid(),
                    started_at=self.started_at,
                )
                db.add(row)
            row.status = status
            row.is_leader = is_leader
            row.heartbeat_at = observed_at
            row.worker_pid = self.worker.pid if self.worker is not None else None
            row.worker_started_at = (
                row.worker_started_at
                if self.worker is not None and row.worker_started_at is not None
                else (observed_at if self.worker is not None else None)
            )
            row.last_worker_exit_at = self.last_worker_exit_at
            row.last_worker_exit_code = self.last_worker_exit_code
            row.next_worker_start_at = self.next_worker_start_at
            db.commit()
            self.last_heartbeat_write = now_monotonic
        finally:
            db.close()

    def _queue_has_work(self) -> bool:
        db = SessionLocal()
        try:
            recover_expired_jobs(db)
            return eligible_queued_job_count(db) > 0
        finally:
            db.close()

    def _start_worker(self) -> None:
        self._write_state("starting", is_leader=True, force=True)
        backend_dir = Path(__file__).resolve().parents[2]
        child_env = dict(os.environ)
        child_env["PAPER_TRADING_SCHEDULER_ENABLED"] = "false"
        child_env["PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS"] = "false"
        self.worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "src.workers.backtest_worker",
                "--once",
                "--concurrency",
                "1",
                "--lease-seconds",
                str(self.lease_seconds),
            ],
            cwd=backend_dir,
            env=child_env,
            start_new_session=True,
        )
        self.next_worker_start_at = None
        self._write_state("running", is_leader=True, force=True)
        log.info("Started on-demand backtest worker", extra={"worker_pid": self.worker.pid})

    def _observe_worker_exit(self, exit_code: int) -> None:
        self.worker = None
        self.last_worker_exit_code = exit_code
        self.last_worker_exit_at = datetime.now(UTC)
        if exit_code == 0:
            self.failure_count = 0
            self.next_worker_start_at = None
            return
        self.failure_count += 1
        delay = restart_delay_seconds(self.failure_count)
        self.next_worker_start_at = datetime.now(UTC) + timedelta(seconds=delay)
        log.error(
            "Backtest worker exited unexpectedly; restart scheduled",
            extra={"exit_code": exit_code, "restart_delay_seconds": delay},
        )

    def _stop_worker(self) -> None:
        worker = self.worker
        if worker is None:
            return
        if worker.poll() is None:
            try:
                os.killpg(worker.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                worker.wait(timeout=5)
        self._observe_worker_exit(int(worker.returncode or 0))

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    is_leader = self.leader_lock.try_acquire()
                    if not is_leader:
                        self._write_state("standby", is_leader=False)
                        self.stop_event.wait(self.poll_seconds)
                        continue

                    if self.worker is not None:
                        exit_code = self.worker.poll()
                        if exit_code is None:
                            self._write_state("running", is_leader=True)
                            self.stop_event.wait(self.poll_seconds)
                            continue
                        self._observe_worker_exit(int(exit_code))

                    has_work = self._queue_has_work()
                    now = datetime.now(UTC)
                    if has_work and self.next_worker_start_at is not None and now < self.next_worker_start_at:
                        self._write_state("backoff", is_leader=True)
                    elif has_work:
                        self._start_worker()
                    else:
                        self.failure_count = 0
                        self.next_worker_start_at = None
                        self._write_state("idle", is_leader=True)
                except Exception:
                    log.exception("Backtest worker manager iteration failed")
                    self.failure_count += 1
                    delay = restart_delay_seconds(self.failure_count)
                    self.next_worker_start_at = datetime.now(UTC) + timedelta(seconds=delay)
                    try:
                        self._write_state("backoff", is_leader=self.leader_lock.connection is not None, force=True)
                    except Exception:
                        log.exception("Backtest worker manager heartbeat failed")
                self.stop_event.wait(self.poll_seconds)
        finally:
            try:
                self._write_state(
                    "stopping",
                    is_leader=self.leader_lock.connection is not None,
                    force=True,
                )
                self._stop_worker()
                self._write_state("stopping", is_leader=False, force=True)
            finally:
                self.leader_lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start backtest workers only while durable jobs are queued.")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=120)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    runner = BacktestWorkerManagerRunner(
        poll_seconds=args.poll_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
        lease_seconds=args.lease_seconds,
    )
    signal.signal(signal.SIGINT, runner.request_stop)
    signal.signal(signal.SIGTERM, runner.request_stop)
    runner.run()


if __name__ == "__main__":
    main()
