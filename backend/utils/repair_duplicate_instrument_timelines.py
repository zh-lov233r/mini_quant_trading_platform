from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TimelineRepair:
    ticker: str
    old_instrument_id: int
    current_instrument_id: int
    cutover_date: date


# These transitions were verified against the instrument metadata, symbol
# histories, and overlapping OHLCV rows in the local database on 2026-08-18.
# Rows on the wrong side of a cutover are duplicate imports of the same vendor
# ticker row, not two simultaneously tradable exchange listings.
TIMELINE_REPAIRS = (
    TimelineRepair("BBUC", 453, 15398, date(2026, 4, 8)),
    TimelineRepair("CLBK", 831, 984061, date(2026, 7, 9)),
    TimelineRepair("DMRC", 1152, 420550, date(2026, 5, 8)),
    TimelineRepair("FAC", 5263, 562346, date(2026, 6, 8)),
    TimelineRepair("GORO", 1665, 977302, date(2026, 7, 9)),
    TimelineRepair("MRLN", 6169, 17418, date(2026, 4, 8)),
    TimelineRepair("NINE", 6248, 17552, date(2026, 4, 1)),
    TimelineRepair("OPI", 6392, 662060, date(2026, 6, 22)),
    TimelineRepair("UROY", 3892, 1074519, date(2026, 7, 24)),
    TimelineRepair("XOM", 4146, 810226, date(2026, 6, 23)),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair the audited duplicate-instrument point-in-time timelines."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the repair. Without this flag all changes are rolled back after validation.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def _scalar(conn: psycopg.Connection, query: str, params: dict[str, Any]) -> Any:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return None if row is None else row[0]


def _assert_instrument_owner(
    conn: psycopg.Connection,
    *,
    instrument_id: int,
    ticker: str,
) -> None:
    row = conn.execute(
        "SELECT ticker_canonical FROM instruments WHERE id = %s",
        (instrument_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"instrument {instrument_id} does not exist")
    if row[0] != ticker:
        raise RuntimeError(
            f"instrument {instrument_id} expected ticker {ticker}, found {row[0]!r}"
        )


def _repair_timeline(
    conn: psycopg.Connection,
    repair: TimelineRepair,
) -> dict[str, Any]:
    params = {
        "ticker": repair.ticker,
        "old_id": repair.old_instrument_id,
        "current_id": repair.current_instrument_id,
        "cutover": repair.cutover_date,
    }
    _assert_instrument_owner(
        conn,
        instrument_id=repair.old_instrument_id,
        ticker=repair.ticker,
    )
    _assert_instrument_owner(
        conn,
        instrument_id=repair.current_instrument_id,
        ticker=repair.ticker,
    )

    old_first_date = _scalar(
        conn,
        "SELECT min(dt_ny) FROM eod_bars WHERE instrument_id=%(old_id)s AND dt_ny < %(cutover)s",
        params,
    )
    old_last_date = _scalar(
        conn,
        "SELECT max(dt_ny) FROM eod_bars WHERE instrument_id=%(old_id)s AND dt_ny < %(cutover)s",
        params,
    )
    current_last_date = _scalar(
        conn,
        "SELECT max(dt_ny) FROM eod_bars WHERE instrument_id=%(current_id)s AND dt_ny >= %(cutover)s",
        params,
    )
    if old_first_date is None or old_last_date is None or current_last_date is None:
        raise RuntimeError(f"incomplete EOD timeline for {repair.ticker}: {asdict(repair)}")

    # Move the current interval first. The exchange+symbol exclusion constraint
    # is immediate, so narrowing the open interval must precede expanding the
    # historical interval to its real dates.
    current_history_updates = conn.execute(
        """
        UPDATE symbol_history
        SET valid_from=%(cutover)s, valid_to=NULL
        WHERE instrument_id=%(current_id)s AND is_primary
        """,
        params,
    ).rowcount
    if current_history_updates != 1:
        raise RuntimeError(
            f"expected one current primary symbol history for {repair.ticker}, "
            f"updated {current_history_updates}"
        )

    deleted_old_features = conn.execute(
        "DELETE FROM daily_features WHERE instrument_id=%(old_id)s AND dt_ny >= %(cutover)s",
        params,
    ).rowcount
    deleted_current_features = conn.execute(
        "DELETE FROM daily_features WHERE instrument_id=%(current_id)s AND dt_ny < %(cutover)s",
        params,
    ).rowcount
    deleted_old_bars = conn.execute(
        "DELETE FROM eod_bars WHERE instrument_id=%(old_id)s AND dt_ny >= %(cutover)s",
        params,
    ).rowcount
    deleted_current_bars = conn.execute(
        "DELETE FROM eod_bars WHERE instrument_id=%(current_id)s AND dt_ny < %(cutover)s",
        params,
    ).rowcount

    moved_actions_to_current = conn.execute(
        """
        UPDATE corporate_actions
        SET instrument_id=%(current_id)s
        WHERE instrument_id=%(old_id)s AND ex_date >= %(cutover)s
        """,
        params,
    ).rowcount
    moved_actions_to_old = conn.execute(
        """
        UPDATE corporate_actions
        SET instrument_id=%(old_id)s
        WHERE instrument_id=%(current_id)s AND ex_date < %(cutover)s
        """,
        params,
    ).rowcount

    old_history_updates = conn.execute(
        """
        UPDATE symbol_history
        SET valid_from=%(old_first_date)s, valid_to=%(old_last_date)s
        WHERE instrument_id=%(old_id)s AND is_primary
        """,
        {**params, "old_first_date": old_first_date, "old_last_date": old_last_date},
    ).rowcount
    if old_history_updates != 1:
        raise RuntimeError(
            f"expected one old primary symbol history for {repair.ticker}, "
            f"updated {old_history_updates}"
        )

    conn.execute(
        """
        UPDATE instruments
        SET ticker_canonical=NULL,
            is_active=FALSE,
            listed_at=COALESCE(listed_at, %(old_first_date)s),
            delisted_at=%(old_last_date)s
        WHERE id=%(old_id)s
        """,
        {**params, "old_first_date": old_first_date, "old_last_date": old_last_date},
    )
    conn.execute(
        """
        UPDATE instruments
        SET is_active=TRUE,
            listed_at=COALESCE(listed_at, %(cutover)s),
            delisted_at=NULL
        WHERE id=%(current_id)s
        """,
        params,
    )

    return {
        **asdict(repair),
        "old_first_date": old_first_date,
        "old_last_date": old_last_date,
        "current_last_date": current_last_date,
        "deleted_old_features": deleted_old_features,
        "deleted_current_features": deleted_current_features,
        "deleted_old_bars": deleted_old_bars,
        "deleted_current_bars": deleted_current_bars,
        "moved_actions_to_current": moved_actions_to_current,
        "moved_actions_to_old": moved_actions_to_old,
    }


def _repair_alias_and_empty_records(conn: psycopg.Connection) -> dict[str, Any]:
    # Instrument 5595 is Hillenbrand (historical ticker HI), not Highwoods (HIW).
    hi_first = _scalar(
        conn,
        "SELECT min(dt_ny) FROM eod_bars WHERE instrument_id=5595",
        {},
    )
    hi_last = _scalar(
        conn,
        "SELECT max(dt_ny) FROM eod_bars WHERE instrument_id=5595",
        {},
    )
    if hi_first is None or hi_last is None:
        raise RuntimeError("missing Hillenbrand EOD timeline for instrument 5595")
    if _scalar(conn, "SELECT count(*) FROM instruments WHERE ticker_canonical='HI'", {}) != 0:
        raise RuntimeError("ticker HI already has a canonical owner")
    conn.execute(
        """
        UPDATE instruments
        SET ticker_canonical='HI', is_active=FALSE,
            listed_at=COALESCE(listed_at, %(first)s), delisted_at=%(last)s
        WHERE id=5595
        """,
        {"first": hi_first, "last": hi_last},
    )
    conn.execute(
        """
        UPDATE symbol_history
        SET valid_from=%(first)s, valid_to=%(last)s
        WHERE instrument_id=5595 AND is_primary
        """,
        {"first": hi_first, "last": hi_last},
    )

    # Keep the real Kennedy-Wilson history as KW and clear the empty duplicate
    # Kellanova record. Kellanova already has a separate canonical K owner.
    kw_first = _scalar(
        conn,
        "SELECT min(dt_ny) FROM eod_bars WHERE instrument_id=2137",
        {},
    )
    kw_last = _scalar(
        conn,
        "SELECT max(dt_ny) FROM eod_bars WHERE instrument_id=2137",
        {},
    )
    if kw_first is None or kw_last is None:
        raise RuntimeError("missing Kennedy-Wilson EOD timeline for instrument 2137")
    conn.execute(
        """
        UPDATE instruments
        SET is_active=FALSE, listed_at=COALESCE(listed_at, %(first)s), delisted_at=%(last)s
        WHERE id=2137
        """,
        {"first": kw_first, "last": kw_last},
    )
    conn.execute(
        """
        UPDATE symbol_history
        SET valid_from=%(first)s, valid_to=%(last)s
        WHERE instrument_id=2137 AND is_primary
        """,
        {"first": kw_first, "last": kw_last},
    )
    cleared_empty_kw = conn.execute(
        """
        UPDATE instruments
        SET ticker_canonical=NULL, is_active=FALSE
        WHERE id=5892
          AND NOT EXISTS (SELECT 1 FROM eod_bars WHERE instrument_id=5892)
          AND NOT EXISTS (SELECT 1 FROM symbol_history WHERE instrument_id=5892)
        """
    ).rowcount
    if cleared_empty_kw != 1:
        raise RuntimeError("empty Kellanova duplicate instrument 5892 did not match preconditions")

    return {
        "hillenbrand": {"instrument_id": 5595, "ticker": "HI", "first": hi_first, "last": hi_last},
        "kennedy_wilson": {
            "instrument_id": 2137,
            "ticker": "KW",
            "first": kw_first,
            "last": kw_last,
        },
        "cleared_empty_instrument_id": 5892,
    }


def _validation_summary(conn: psycopg.Connection) -> dict[str, int]:
    queries = {
        "duplicate_canonical_tickers": """
            SELECT count(*) FROM (
              SELECT ticker_canonical FROM instruments
              WHERE ticker_canonical IS NOT NULL
              GROUP BY ticker_canonical HAVING count(*) > 1
            ) duplicate_tickers
        """,
        "duplicate_canonical_bar_dates": """
            SELECT count(*) FROM (
              SELECT i.ticker_canonical, e.dt_ny
              FROM instruments i JOIN eod_bars e ON e.instrument_id=i.id
              WHERE i.ticker_canonical IS NOT NULL
              GROUP BY i.ticker_canonical, e.dt_ny HAVING count(*) > 1
            ) duplicate_dates
        """,
        "orphan_features": """
            SELECT count(*) FROM daily_features f
            LEFT JOIN eod_bars e USING (instrument_id, dt_ny)
            WHERE e.instrument_id IS NULL
        """,
        "symbol_history_overlap_pairs": """
            SELECT count(*) FROM symbol_history a JOIN symbol_history b
              ON a.id < b.id
             AND a.exchange=b.exchange
             AND a.symbol=b.symbol
             AND daterange(a.valid_from, COALESCE(a.valid_to, 'infinity'::date), '[]')
                 && daterange(b.valid_from, COALESCE(b.valid_to, 'infinity'::date), '[]')
        """,
    }
    return {name: int(_scalar(conn, query, {})) for name, query in queries.items()}


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(782641903)")
        repairs = [_repair_timeline(conn, repair) for repair in TIMELINE_REPAIRS]
        aliases = _repair_alias_and_empty_records(conn)
        validation = _validation_summary(conn)
        if any(validation.values()):
            raise RuntimeError(f"post-repair validation failed: {validation}")

        result = {
            "mode": "apply" if args.apply else "dry_run",
            "timeline_repairs": repairs,
            "alias_repairs": aliases,
            "validation": validation,
        }
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
            print("Dry-run only; transaction rolled back. Re-run with --apply to commit.")


if __name__ == "__main__":
    main()
