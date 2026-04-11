from __future__ import annotations

"""Daily scheduler for portfolio-level paper trading runs.

This module is responsible for turning the paper-trading service into a
background daily job:

1. Wait until feature data for a trade date is fully materialized.
2. Wait until the configured New York run time has passed.
3. Find active portfolios that opted into auto-run.
4. Trigger one multi-strategy paper-trading run per eligible portfolio.

The scheduler is intentionally conservative. It will not run if the feature
pipeline is only partially loaded, and it will skip a portfolio that already
has a scheduler-triggered run recorded for the same trade date.
"""

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from src.core.db import SessionLocal
from src.models.tables import PaperTradingAccount, StrategyPortfolio, StrategyRun
from src.services.paper_trading_service import (
    PAPER_TRADING_TRIGGER_SCHEDULER,
    run_multi_strategy_paper_trading,
)
from src.services.strategy_allocation_service import list_allocated_strategies, normalize_portfolio_name


log = logging.getLogger("paper_trading_scheduler")
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(slots=True)
class PaperTradingSchedulerConfig:
    """Runtime configuration for the background scheduler.

    These values are typically loaded from environment variables at process
    startup so operators can control when the scheduler runs and whether it
    should place real paper orders or remain in dry-run mode.
    """

    enabled: bool
    run_time_ny: time
    poll_seconds: float
    submit_orders: bool
    continue_on_error: bool

    @classmethod
    def from_env(cls) -> "PaperTradingSchedulerConfig":
        """Build scheduler config from environment variables."""
        return cls(
            enabled=_env_bool("PAPER_TRADING_SCHEDULER_ENABLED", default=True),
            run_time_ny=_env_time("PAPER_TRADING_SCHEDULER_RUN_TIME_NY", default=time(hour=23, minute=30)),
            poll_seconds=_env_float("PAPER_TRADING_SCHEDULER_POLL_SECONDS", default=60.0, minimum=5.0),
            submit_orders=_env_bool("PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS", default=False),
            continue_on_error=_env_bool("PAPER_TRADING_SCHEDULER_CONTINUE_ON_ERROR", default=True),
        )


@dataclass(slots=True)
class SchedulablePortfolio:
    """Minimal payload describing a portfolio the scheduler can run."""

    account_id: str
    account_name: str
    portfolio_name: str


class PaperTradingDailyScheduler:
    """Background loop that runs eligible paper-trading portfolios once per day.

    The scheduler polls on a short interval, but it only performs real work when
    all gating conditions are satisfied for a given trade date.
    """

    def __init__(self, config: PaperTradingSchedulerConfig | None = None) -> None:
        """Initialize scheduler state and remember the effective configuration."""
        self.config = config or PaperTradingSchedulerConfig.from_env()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_no_data_log_date: date | None = None
        self._last_summary_log_date: date | None = None

    async def start(self) -> None:
        """Start the background polling task if the scheduler is enabled."""
        if not self.config.enabled:
            log.info("Paper trading daily scheduler disabled")
            return
        if self._task is not None and not self._task.done():
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="paper-trading-daily-scheduler",
        )
        log.info(
            "Paper trading daily scheduler started (run_time_ny=%s, submit_orders=%s, continue_on_error=%s, poll_seconds=%s)",
            self.config.run_time_ny.isoformat(),
            self.config.submit_orders,
            self.config.continue_on_error,
            self.config.poll_seconds,
        )

    async def stop(self) -> None:
        """Stop the polling task and wait briefly for it to exit cleanly."""
        task = self._task
        if task is None:
            return

        self._stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None
            log.info("Paper trading daily scheduler stopped")

    async def _run_loop(self) -> None:
        """Poll scheduler state until asked to stop.

        The heavy work is delegated to `_poll_once()` on a worker thread so the
        async app event loop stays responsive.
        """
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception:
                log.exception("Unexpected error while polling paper trading scheduler")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.poll_seconds)
            except asyncio.TimeoutError:
                continue

    def _poll_once(self) -> None:
        """Evaluate the current scheduler state and trigger due portfolio runs.

        This method is intentionally idempotent across repeated polls:
        - it waits for a fully ready trade date
        - it waits until the configured run time
        - it skips portfolios already run by the scheduler for that trade date
        """
        now_ny = datetime.now(NEW_YORK)
        latest_ready_trade_date = _latest_ready_daily_features_trade_date(now_ny.date())
        if latest_ready_trade_date is None:
            if self._last_no_data_log_date != now_ny.date():
                log.info(
                    "Daily scheduler is waiting for complete daily_features coverage on or before %s before running paper trading portfolios",
                    now_ny.date().isoformat(),
                )
                self._last_no_data_log_date = now_ny.date()
            return

        trade_date = latest_ready_trade_date
        # Use the trade date's scheduled wall-clock time in New York so a
        # late-arriving feature load can still trigger the missed date once the
        # configured run time has already passed.
        scheduled_at = datetime.combine(trade_date, self.config.run_time_ny, tzinfo=NEW_YORK)
        if now_ny < scheduled_at:
            return

        self._last_no_data_log_date = None
        targets = _load_schedulable_portfolios()
        if not targets:
            if self._last_summary_log_date != trade_date:
                log.info(
                    "Daily scheduler found no active portfolios with auto-run-enabled allocations for %s",
                    trade_date.isoformat(),
                )
                self._last_summary_log_date = trade_date
            return

        completed = 0
        skipped = 0
        failed = 0

        for target in targets:
            if _has_scheduler_run_for_trade_date(target.portfolio_name, trade_date):
                skipped += 1
                continue

            try:
                with SessionLocal() as db:
                    result = run_multi_strategy_paper_trading(
                        db,
                        trade_date,
                        portfolio_name=target.portfolio_name,
                        submit_orders=self.config.submit_orders,
                        continue_on_error=self.config.continue_on_error,
                        auto_run_only=True,
                        trigger=PAPER_TRADING_TRIGGER_SCHEDULER,
                    )
                completed += 1
                log.info(
                    "Scheduled paper trading run completed for account=%s portfolio=%s on %s (%s/%s strategies completed, submit_orders=%s)",
                    target.account_name,
                    target.portfolio_name,
                    trade_date.isoformat(),
                    result.completed_runs,
                    result.total_runs,
                    self.config.submit_orders,
                )
            except Exception:
                failed += 1
                log.exception(
                    "Scheduled paper trading run failed for account=%s portfolio=%s on %s",
                    target.account_name,
                    target.portfolio_name,
                    trade_date.isoformat(),
                )

        if self._last_summary_log_date != trade_date or completed or failed:
            log.info(
                "Daily scheduler finished pass for %s: completed=%s skipped=%s failed=%s",
                trade_date.isoformat(),
                completed,
                skipped,
                failed,
            )
            self._last_summary_log_date = trade_date


def _load_schedulable_portfolios() -> list[SchedulablePortfolio]:
    """Return active portfolios that have at least one auto-run allocation.

    The scheduler operates at the portfolio level, so this helper filters out
    archived accounts, archived portfolios, and portfolios whose strategies have
    not opted into automatic daily execution.
    """
    with SessionLocal() as db:
        rows = db.execute(
            select(StrategyPortfolio, PaperTradingAccount)
            .join(PaperTradingAccount, PaperTradingAccount.id == StrategyPortfolio.paper_account_id)
            .where(PaperTradingAccount.status == "active")
            .where(StrategyPortfolio.status == "active")
            .order_by(PaperTradingAccount.created_at.asc(), StrategyPortfolio.created_at.asc())
        ).all()

        targets: list[SchedulablePortfolio] = []
        for portfolio, account in rows:
            allocated = list_allocated_strategies(
                db,
                portfolio_name=portfolio.name,
                auto_run_enabled=True,
            )
            if not allocated:
                continue
            targets.append(
                SchedulablePortfolio(
                    account_id=str(account.id),
                    account_name=account.name,
                    portfolio_name=portfolio.name,
                )
            )
        return targets


def _latest_ready_daily_features_trade_date(max_trade_date: date) -> date | None:
    """Find the newest trade date whose feature coverage is complete.

    A date is considered ready only when every `eod_bars` row for that date has
    a matching `daily_features` row. This prevents the scheduler from running on
    partially populated feature snapshots.
    """
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT MAX(ready_dates.trade_date)
                FROM (
                    SELECT e.dt_ny AS trade_date
                    FROM eod_bars e
                    LEFT JOIN daily_features f
                      ON f.instrument_id = e.instrument_id
                     AND f.dt_ny = e.dt_ny
                    WHERE e.dt_ny <= :max_trade_date
                    GROUP BY e.dt_ny
                    HAVING COUNT(*) > 0
                       AND COUNT(f.instrument_id) = COUNT(*)
                ) AS ready_dates
                """
            ),
            {"max_trade_date": max_trade_date},
        ).scalar()
    return row


def _has_scheduler_run_for_trade_date(portfolio_name: str, trade_date: date) -> bool:
    """Check whether a portfolio already has a scheduler-triggered run that day.

    This dedupes repeated polls so the scheduler can wake up many times without
    submitting duplicate orders for the same portfolio and trade date.
    """
    normalized_portfolio = normalize_portfolio_name(portfolio_name)
    with SessionLocal() as db:
        runs = db.execute(
            select(StrategyRun)
            .where(StrategyRun.mode == "paper")
            .where(StrategyRun.window_start == trade_date)
            .order_by(StrategyRun.requested_at.desc())
        ).scalars().all()

    for run in runs:
        paper_cfg = (run.config_snapshot or {}).get("paper_trading")
        if not isinstance(paper_cfg, dict):
            continue
        run_portfolio = normalize_portfolio_name(paper_cfg.get("portfolio_name"))
        if run_portfolio != normalized_portfolio:
            continue
        if str(paper_cfg.get("trigger") or "").strip() == PAPER_TRADING_TRIGGER_SCHEDULER:
            return True
    return False


def _env_bool(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable with a safe fallback."""
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float, minimum: float) -> float:
    """Parse a positive-ish float environment variable with validation."""
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        log.warning("Invalid %s=%r, falling back to %s", name, value, default)
        return default
    if parsed < minimum:
        log.warning("%s=%s is below minimum %s, falling back to %s", name, parsed, minimum, default)
        return default
    return parsed


def _env_time(name: str, *, default: time) -> time:
    """Parse an `HH:MM` or `HH:MM:SS` environment variable into a `time`."""
    value = os.getenv(name, "").strip()
    if not value:
        return default

    for separator_count in (1, 2):
        if value.count(":") != separator_count:
            continue
        try:
            parts = [int(part) for part in value.split(":")]
        except ValueError:
            break
        if separator_count == 1:
            hour, minute = parts
            second = 0
        else:
            hour, minute, second = parts
        try:
            return time(hour=hour, minute=minute, second=second)
        except ValueError:
            break

    log.warning("Invalid %s=%r, falling back to %s", name, value, default.isoformat())
    return default
