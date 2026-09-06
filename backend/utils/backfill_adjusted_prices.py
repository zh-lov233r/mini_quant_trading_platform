from __future__ import annotations

import argparse
import os
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

try:
    from backend.utils.market_data_maintenance_guard import require_market_data_maintenance_owner
except ModuleNotFoundError:
    from market_data_maintenance_guard import require_market_data_maintenance_owner


REPO_ROOT = Path(__file__).resolve().parents[2]

ACTION_INSTRUMENTS_SQL = """
SELECT i.id FROM instruments i
WHERE i.currency = 'USD' AND i.vendor_source <> 'tushare'
  AND EXISTS (SELECT 1 FROM eod_bars b WHERE b.instrument_id=i.id)
ORDER BY i.id;
"""

STORED_ADJUSTMENTS_SQL = """
SELECT dt_ny, fwd_factor, bwd_factor, open_fa, high_fa, low_fa, close_fa,
       open_ba, high_ba, low_ba, close_ba
FROM eod_bars WHERE instrument_id=%(instrument_id)s ORDER BY dt_ny
"""


BARS_SQL = """
SELECT dt_ny, open_u, high_u, low_u, close_u
FROM eod_bars
WHERE instrument_id = %(instrument_id)s
ORDER BY dt_ny;
"""

ACTIONS_SQL = """
SELECT ex_date, action_type, split_from, split_to, cash_amount
FROM corporate_actions
WHERE instrument_id = %(instrument_id)s
  AND action_type IN ('split', 'reverse_split', 'cash_dividend', 'stock_dividend')
ORDER BY ex_date, id;
"""

CREATE_STAGE_SQL = """
CREATE TEMP TABLE adjusted_eod_stage (
  instrument_id BIGINT NOT NULL,
  dt_ny DATE NOT NULL,
  fwd_factor DOUBLE PRECISION NOT NULL,
  bwd_factor DOUBLE PRECISION NOT NULL,
  open_fa DOUBLE PRECISION,
  high_fa DOUBLE PRECISION,
  low_fa DOUBLE PRECISION,
  close_fa DOUBLE PRECISION,
  open_ba DOUBLE PRECISION,
  high_ba DOUBLE PRECISION,
  low_ba DOUBLE PRECISION,
  close_ba DOUBLE PRECISION,
  PRIMARY KEY (instrument_id, dt_ny)
);
"""

COPY_STAGE_SQL = """
COPY adjusted_eod_stage (
  instrument_id,
  dt_ny,
  fwd_factor,
  bwd_factor,
  open_fa,
  high_fa,
  low_fa,
  close_fa,
  open_ba,
  high_ba,
  low_ba,
  close_ba
) FROM STDIN
"""

APPLY_STAGE_SQL = """
UPDATE eod_bars e
SET
  fwd_factor = s.fwd_factor,
  bwd_factor = s.bwd_factor,
  open_fa = s.open_fa,
  high_fa = s.high_fa,
  low_fa = s.low_fa,
  close_fa = s.close_fa,
  open_ba = s.open_ba,
  high_ba = s.high_ba,
  low_ba = s.low_ba,
  close_ba = s.close_ba,
  asof = now()
FROM adjusted_eod_stage s
WHERE e.instrument_id = s.instrument_id
  AND e.dt_ny = s.dt_ny;
"""

TRUNCATE_STAGE_SQL = "TRUNCATE adjusted_eod_stage;"


@dataclass(frozen=True)
class BarRow:
    dt_ny: date
    open_u: float | None
    high_u: float | None
    low_u: float | None
    close_u: float | None


@dataclass(frozen=True)
class ActionRow:
    ex_date: date
    action_type: str
    split_from: float | None
    split_to: float | None
    cash_amount: float | None


def _to_float_or_none(value) -> float | None:
    return None if value is None else float(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute adjustment factors and adjusted OHLC prices from corporate_actions."
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Ingestion start date for audit context; corrections cover full history.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Ingestion end date for audit context; corrections cover full history.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--batch-rows",
        type=int,
        default=100_000,
        help="How many adjusted rows to stage before flushing updates.",
    )
    parser.add_argument(
        "--instrument-limit",
        type=int,
        default=None,
        help="Optional limit for smoke testing.",
    )
    parser.add_argument(
        "--instrument-id",
        type=int,
        default=None,
        help="Optional single instrument_id for targeted validation.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count inconsistent adjusted rows without writing.")
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return normalized.replace("postgresql+psycopg2://", "postgresql://", 1)


def _safe_price(price: float | None, factor: float) -> float | None:
    return None if price is None else price * factor


def _event_factor_for_date(cash_amount: float, prev_close: float | None) -> float:
    if prev_close is None or prev_close <= 0:
        return 1.0
    dividend_factor = (prev_close - cash_amount) / prev_close
    return dividend_factor if dividend_factor > 0 else 1.0


def _build_event_factor_map(bars: list[BarRow], actions: list[ActionRow]) -> dict[date, float]:
    split_components: dict[date, float] = defaultdict(lambda: 1.0)
    cash_components: dict[date, float] = defaultdict(float)

    for action in actions:
        if action.action_type in {"split", "reverse_split", "stock_dividend"}:
            if action.split_from and action.split_to and action.split_from > 0 and action.split_to > 0:
                split_components[action.ex_date] *= action.split_from / action.split_to
        elif action.action_type == "cash_dividend":
            if action.cash_amount and action.cash_amount > 0:
                cash_components[action.ex_date] += action.cash_amount

    event_factor: dict[date, float] = {}
    prev_close: float | None = None
    for bar in bars:
        factor = split_components[bar.dt_ny]
        cash_amount = cash_components[bar.dt_ny]
        if cash_amount > 0:
            factor *= _event_factor_for_date(cash_amount, prev_close)
        if factor <= 0:
            factor = 1.0
        event_factor[bar.dt_ny] = factor
        prev_close = bar.close_u
    return event_factor


def _compute_adjusted_rows(
    instrument_id: int,
    bars: list[BarRow],
    actions: list[ActionRow],
) -> list[tuple]:
    if not bars:
        return []

    event_factor = _build_event_factor_map(bars, actions)
    fwd_factors = [1.0] * len(bars)
    bwd_factors = [1.0] * len(bars)

    future_cum = 1.0
    for idx in range(len(bars) - 1, -1, -1):
        bar = bars[idx]
        fwd_factors[idx] = future_cum
        future_cum *= event_factor.get(bar.dt_ny, 1.0)

    past_cum = 1.0
    for idx, bar in enumerate(bars):
        past_cum /= event_factor.get(bar.dt_ny, 1.0)
        bwd_factors[idx] = past_cum

    adjusted_rows: list[tuple] = []
    for idx, bar in enumerate(bars):
        fwd = fwd_factors[idx]
        bwd = bwd_factors[idx]
        adjusted_rows.append(
            (
                instrument_id,
                bar.dt_ny,
                fwd,
                bwd,
                _safe_price(bar.open_u, fwd),
                _safe_price(bar.high_u, fwd),
                _safe_price(bar.low_u, fwd),
                _safe_price(bar.close_u, fwd),
                _safe_price(bar.open_u, bwd),
                _safe_price(bar.high_u, bwd),
                _safe_price(bar.low_u, bwd),
                _safe_price(bar.close_u, bwd),
            )
        )
    return adjusted_rows


def _fetch_bars(conn: psycopg.Connection, instrument_id: int) -> list[BarRow]:
    with conn.cursor() as cur:
        cur.execute(BARS_SQL, {"instrument_id": instrument_id})
        return [BarRow(*row) for row in cur.fetchall()]


def _fetch_actions(
    conn: psycopg.Connection,
    instrument_id: int,
) -> list[ActionRow]:
    with conn.cursor() as cur:
        cur.execute(ACTIONS_SQL, {"instrument_id": instrument_id})
        return [
            ActionRow(
                ex_date=row[0],
                action_type=row[1],
                split_from=_to_float_or_none(row[2]),
                split_to=_to_float_or_none(row[3]),
                cash_amount=_to_float_or_none(row[4]),
            )
            for row in cur.fetchall()
        ]


def _load_action_instruments(conn: psycopg.Connection) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(ACTION_INSTRUMENTS_SQL)
        return [row[0] for row in cur.fetchall()]


def _flush_stage(conn: psycopg.Connection, stage_rows: list[tuple]) -> None:
    if not stage_rows:
        return
    with conn.cursor() as cur:
        with cur.copy(COPY_STAGE_SQL) as copy:
            for row in stage_rows:
                copy.write_row(row)
        cur.execute(APPLY_STAGE_SQL)
        cur.execute(TRUNCATE_STAGE_SQL)
    conn.commit()


def changed_adjustment_rows(expected: list[tuple], stored: dict[date, tuple]) -> list[tuple]:
    def equal(left, right):
        if left is None or right is None:
            return left is right
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return [row for row in expected if not all(
        equal(left, right) for left, right in zip(row[2:], stored[row[1]])
    )]


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    if not args.dry_run:
        require_market_data_maintenance_owner(args.database_url)
    if args.batch_rows < 1 or (args.instrument_limit is not None and args.instrument_limit < 1):
        raise SystemExit("batch rows and instrument limit must be positive")
    if args.start_date and args.end_date and date.fromisoformat(args.start_date) > date.fromisoformat(args.end_date):
        raise SystemExit("start date must be on or before end date")
    changed_instruments = changed_rows = 0
    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        if args.dry_run:
            conn.execute("SET TRANSACTION READ ONLY")
        else:
            conn.execute(CREATE_STAGE_SQL)
            conn.commit()
        instrument_ids = _load_action_instruments(conn)
        if args.instrument_id is not None:
            if args.instrument_id not in instrument_ids:
                raise SystemExit("instrument must have US market data and cannot be Tushare-owned")
            instrument_ids = [args.instrument_id]
        elif args.instrument_limit is not None:
            instrument_ids = instrument_ids[:args.instrument_limit]
        for index, instrument_id in enumerate(instrument_ids, 1):
            # New/corrected actions change prices outside the ingestion window.
            bars = _fetch_bars(conn, instrument_id)
            expected = _compute_adjusted_rows(instrument_id, bars, _fetch_actions(conn, instrument_id))
            stored = {r[0]: r[1:] for r in conn.execute(STORED_ADJUSTMENTS_SQL, {"instrument_id": instrument_id})}
            changed = changed_adjustment_rows(expected, stored)
            if changed:
                changed_instruments += 1
                changed_rows += len(changed)
                if not args.dry_run:
                    for offset in range(0, len(changed), args.batch_rows):
                        _flush_stage(conn, changed[offset:offset + args.batch_rows])
            if index % 500 == 0:
                print(f"Adjustment progress={index}/{len(instrument_ids)} changed_rows={changed_rows}", flush=True)
        print(f"Adjustment audit complete: instruments={len(instrument_ids)} changed_instruments={changed_instruments} changed_rows={changed_rows} dry_run={args.dry_run}", flush=True)


if __name__ == "__main__":
    main()
