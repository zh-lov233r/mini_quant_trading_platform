from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

if __package__:
    from .massive_enrichment_common import MASSIVE_API_BASE, fetch_json, normalize_dsn
else:
    from massive_enrichment_common import MASSIVE_API_BASE, fetch_json, normalize_dsn

try:
    from backend.utils.market_data_maintenance_guard import require_market_data_maintenance_owner
except ModuleNotFoundError:
    from market_data_maintenance_guard import require_market_data_maintenance_owner


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = "ticker_events"

CANDIDATES_SQL = """
SELECT
  i.id,
  i.ticker_canonical,
  i.exchange,
  i.composite_figi,
  i.share_class_figi,
  bars.first_bar,
  i.is_active,
  i.delisted_at::date,
  bars.last_bar
FROM instruments i
LEFT JOIN LATERAL (
  SELECT min(e.dt_ny) AS first_bar, max(e.dt_ny) AS last_bar
  FROM eod_bars e
  WHERE e.instrument_id = i.id
) bars ON TRUE
LEFT JOIN instrument_vendor_sync_state state
  ON state.instrument_id = i.id
 AND state.dataset = 'ticker_events'
 AND state.vendor_source = 'massive'
WHERE i.ticker_canonical IS NOT NULL
  AND i.composite_figi IS NOT NULL
  AND (
    %(full_refresh)s
    OR state.last_success_at IS NULL
    OR state.last_success_at < now() - make_interval(days => %(stale_days)s)
  )
ORDER BY i.ticker_canonical
"""

UPSERT_EVENT_SQL = """
INSERT INTO security_ticker_events (
  instrument_id,
  event_date,
  event_type,
  ticker,
  composite_figi,
  resolution_status,
  vendor_source,
  vendor_payload,
  asof
) VALUES (
  %(instrument_id)s,
  %(event_date)s,
  'ticker_change',
  %(ticker)s,
  %(composite_figi)s,
  'pending',
  'massive',
  %(vendor_payload)s::jsonb,
  now()
)
ON CONFLICT (instrument_id, event_date, event_type, ticker, vendor_source) DO UPDATE SET
  composite_figi = EXCLUDED.composite_figi,
  vendor_payload = EXCLUDED.vendor_payload,
  asof = now()
"""

UPSERT_STATE_SQL = """
INSERT INTO instrument_vendor_sync_state (
  instrument_id, dataset, vendor_source, last_attempt_at, last_success_at, last_error
) VALUES (%s, 'ticker_events', 'massive', now(), now(), NULL)
ON CONFLICT (instrument_id, dataset, vendor_source) DO UPDATE SET
  last_attempt_at = now(), last_success_at = now(), last_error = NULL
"""

UPSERT_ERROR_STATE_SQL = """
INSERT INTO instrument_vendor_sync_state (
  instrument_id, dataset, vendor_source, last_attempt_at, last_error
) VALUES (%s, 'ticker_events', 'massive', now(), %s)
ON CONFLICT (instrument_id, dataset, vendor_source) DO UPDATE SET
  last_attempt_at = now(), last_error = EXCLUDED.last_error
"""


@dataclass(frozen=True)
class InstrumentCandidate:
    instrument_id: int
    canonical_ticker: str
    current_exchange: str
    composite_figi: str
    share_class_figi: str
    first_bar: date | None
    is_active: bool = True
    delisted_at: date | None = None
    last_bar: date | None = None


@dataclass(frozen=True)
class NormalizedEvent:
    event_date: date
    ticker: str
    vendor_payload: dict
    exchange: str | None = None
    composite_figi: str | None = None
    share_class_figi: str | None = None


@dataclass(frozen=True)
class SymbolInterval:
    exchange: str
    symbol: str
    valid_from: date
    valid_to: date | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Store and safely apply Massive ticker-change events.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"))
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--warning-sample-limit", type=int, default=50)
    parser.add_argument("--store-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_events(raw_events: list[dict]) -> list[NormalizedEvent]:
    normalized: list[NormalizedEvent] = []
    for item in raw_events:
        if item.get("type") != "ticker_change" or not item.get("date"):
            continue
        ticker_change = item.get("ticker_change") or {}
        ticker = str(ticker_change.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        normalized.append(
            NormalizedEvent(
                event_date=date.fromisoformat(str(item["date"])),
                ticker=ticker,
                vendor_payload=item,
            )
        )
    normalized.sort(key=lambda event: (event.event_date, event.ticker))
    return normalized


def build_intervals(
    events: list[NormalizedEvent],
    canonical_ticker: str,
    *,
    terminal_valid_to: date | None = None,
) -> tuple[list[SymbolInterval], str | None]:
    if not events:
        return [], None
    by_date: dict[date, set[str]] = {}
    for event in events:
        by_date.setdefault(event.event_date, set()).add(event.ticker)
    conflicting_dates = [event_date for event_date, tickers in by_date.items() if len(tickers) > 1]
    if conflicting_dates:
        return [], f"multiple tickers on event date {conflicting_dates[0]}"
    seen_tickers: set[str] = set()
    for event in events:
        if event.ticker in seen_tickers:
            return [], f"ticker {event.ticker} is reused within the event chain"
        seen_tickers.add(event.ticker)
    if events[-1].ticker != canonical_ticker:
        return [], f"latest event ticker {events[-1].ticker} != canonical {canonical_ticker}"
    if any(event.exchange is None for event in events):
        return [], "one or more events have no validated exchange"

    intervals: list[SymbolInterval] = []
    for index, event in enumerate(events):
        valid_to = (
            events[index + 1].event_date - timedelta(days=1)
            if index + 1 < len(events)
            else terminal_valid_to
        )
        intervals.append(
            SymbolInterval(
                exchange=str(event.exchange),
                symbol=event.ticker,
                valid_from=event.event_date,
                valid_to=valid_to,
            )
        )
    return intervals, None


def inactive_terminal_valid_to(candidate: InstrumentCandidate, final_event_date: date) -> date | None:
    if candidate.is_active:
        return None
    preferred = (
        candidate.delisted_at - timedelta(days=1)
        if candidate.delisted_at is not None
        else candidate.last_bar
    )
    return max(final_event_date, preferred or final_event_date)


def details_match_candidate(item: dict, candidate: InstrumentCandidate) -> bool:
    composite = item.get("composite_figi")
    share_class = item.get("share_class_figi")
    return composite == candidate.composite_figi and share_class in (None, "", candidate.share_class_figi)


def _load_candidates(conn: psycopg.Connection, *, full_refresh: bool, stale_days: int) -> list[InstrumentCandidate]:
    rows = conn.execute(
        CANDIDATES_SQL,
        {"full_refresh": full_refresh, "stale_days": stale_days},
    ).fetchall()
    return [
        InstrumentCandidate(
            instrument_id=int(row[0]),
            canonical_ticker=str(row[1]),
            current_exchange=str(row[2]),
            composite_figi=str(row[3]),
            share_class_figi=str(row[4]),
            first_bar=row[5],
            is_active=bool(row[6]),
            delisted_at=row[7],
            last_bar=row[8],
        )
        for row in rows
    ]


def _fetch_events(api_key: str, candidate: InstrumentCandidate) -> list[NormalizedEvent]:
    payload = fetch_json(
        f"{MASSIVE_API_BASE}/vX/reference/tickers/{candidate.composite_figi}/events",
        api_key=api_key,
    )
    result = payload.get("results") or {}
    return normalize_events(result.get("events") or [])


def _validate_events(
    api_key: str,
    candidate: InstrumentCandidate,
    events: list[NormalizedEvent],
) -> tuple[list[NormalizedEvent], str | None]:
    validated: list[NormalizedEvent] = []
    for index, event in enumerate(events):
        payload = fetch_json(
            f"{MASSIVE_API_BASE}/v3/reference/tickers/{event.ticker}",
            api_key=api_key,
            params={"date": event.event_date.isoformat()},
        )
        details = payload.get("results") or {}
        if not details:
            return [], f"no ticker overview for {event.ticker} on {event.event_date}"
        if not details_match_candidate(details, candidate):
            return [], f"FIGI mismatch for {event.ticker} on {event.event_date}"
        exchange = details.get("primary_exchange")
        if not exchange:
            fallback_date = (
                events[index + 1].event_date - timedelta(days=1)
                if index + 1 < len(events)
                else candidate.delisted_at or candidate.last_bar or date.today()
            )
            fallback_payload = fetch_json(
                f"{MASSIVE_API_BASE}/v3/reference/tickers/{event.ticker}",
                api_key=api_key,
                params={"date": fallback_date.isoformat()},
            )
            fallback_details = fallback_payload.get("results") or {}
            if not details_match_candidate(fallback_details, candidate):
                return [], f"FIGI mismatch for {event.ticker} on fallback {fallback_date}"
            exchange = fallback_details.get("primary_exchange")
        if not exchange:
            return [], f"missing exchange for {event.ticker} on {event.event_date}"
        validated.append(
            NormalizedEvent(
                event_date=event.event_date,
                ticker=event.ticker,
                vendor_payload=event.vendor_payload,
                exchange=str(exchange),
                composite_figi=details.get("composite_figi"),
                share_class_figi=details.get("share_class_figi"),
            )
        )
    return validated, None


def _fetch_and_validate(
    api_key: str,
    candidate: InstrumentCandidate,
) -> tuple[list[NormalizedEvent], list[NormalizedEvent], str | None]:
    events = _fetch_events(api_key, candidate)
    if not events:
        return [], [], None
    validated, reason = _validate_events(api_key, candidate, events)
    return events, validated, reason


def fetch_candidate_events(
    api_key: str,
    candidates: list[InstrumentCandidate],
    *,
    workers: int,
):
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_fetch_and_validate, api_key, candidate)
            for candidate in candidates
        ]
        for candidate, future in zip(candidates, futures):
            try:
                events, validated, reason = future.result()
                yield candidate, events, validated, reason, None
            except Exception as exc:
                yield candidate, [], [], None, exc


def print_limited_warning(ticker: str, message: str, *, number: int, limit: int) -> None:
    if number <= limit:
        print(f"WARN ticker-events {ticker}: {message}", flush=True)
    elif number == limit + 1:
        print(
            f"WARN ticker-events: additional warnings suppressed after {limit} samples",
            flush=True,
        )


def _snapshot_intervals(conn: psycopg.Connection, instrument_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, exchange, symbol, valid_from, valid_to, is_primary,
               valid_from_precision, source
        FROM symbol_history
        WHERE instrument_id = %s
        ORDER BY valid_from, symbol, id
        """,
        (instrument_id,),
    ).fetchall()
    keys = ("id", "exchange", "symbol", "valid_from", "valid_to", "is_primary", "valid_from_precision", "source")
    return [dict(zip(keys, row)) for row in rows]


def _preflight_intervals(
    conn: psycopg.Connection,
    candidate: InstrumentCandidate,
    intervals: list[SymbolInterval],
) -> str | None:
    if candidate.first_bar and candidate.first_bar < intervals[0].valid_from:
        return f"first EOD {candidate.first_bar} predates earliest event {intervals[0].valid_from}"

    before = _snapshot_intervals(conn, candidate.instrument_id)
    exact = [row for row in before if row["is_primary"] and row["valid_from_precision"] == "exact"]
    expected = {
        (item.exchange, item.symbol, item.valid_from): item.valid_to
        for item in intervals
    }
    for row in exact:
        key = (row["exchange"], row["symbol"], row["valid_from"])
        if key not in expected:
            return "existing exact primary intervals disagree with vendor event chain"
        expected_valid_to = expected[key]
        if (
            row["valid_to"] != expected_valid_to
            and not (row["valid_to"] is None and expected_valid_to is not None)
        ):
            return "existing exact primary intervals disagree with vendor event chain"

    for interval in intervals:
        conflicts = conn.execute(
            """
            SELECT count(*)
            FROM symbol_history sh
            WHERE sh.instrument_id <> %(instrument_id)s
              AND sh.exchange = %(exchange)s
              AND sh.symbol = %(symbol)s
              AND daterange(sh.valid_from, COALESCE(sh.valid_to, 'infinity'::date), '[]')
                  && daterange(%(valid_from)s, COALESCE(%(valid_to)s, 'infinity'::date), '[]')
            """,
            {
                "instrument_id": candidate.instrument_id,
                **asdict(interval),
            },
        ).fetchone()[0]
        if conflicts:
            return f"{interval.exchange}:{interval.symbol} conflicts with another instrument"
        same_rows = conn.execute(
            """
            SELECT count(*) FROM symbol_history
            WHERE instrument_id=%s AND exchange=%s AND symbol=%s
            """,
            (candidate.instrument_id, interval.exchange, interval.symbol),
        ).fetchone()[0]
        if same_rows > 1:
            return f"multiple existing rows for {interval.exchange}:{interval.symbol}"
    return None


def _record_events(conn: psycopg.Connection, candidate: InstrumentCandidate, events: list[NormalizedEvent]) -> None:
    for event in events:
        conn.execute(
            UPSERT_EVENT_SQL,
            {
                "instrument_id": candidate.instrument_id,
                "event_date": event.event_date,
                "ticker": event.ticker,
                "composite_figi": candidate.composite_figi,
                "vendor_payload": json.dumps(event.vendor_payload),
            },
        )
    conn.commit()


def _mark_resolution(
    conn: psycopg.Connection,
    candidate: InstrumentCandidate,
    events: list[NormalizedEvent],
    *,
    status: str,
    reason: str | None,
    before: list[dict] | None = None,
    after: list[dict] | None = None,
) -> None:
    for event in events:
        conn.execute(
            """
            UPDATE security_ticker_events
            SET exchange=%(exchange)s,
                share_class_figi=%(share_class_figi)s,
                resolution_status=%(status)s,
                resolution_reason=%(reason)s,
                before_intervals=%(before)s::jsonb,
                after_intervals=%(after)s::jsonb,
                applied_at=CASE WHEN %(status)s='applied' THEN now() ELSE applied_at END,
                asof=now()
            WHERE instrument_id=%(instrument_id)s
              AND event_date=%(event_date)s
              AND event_type='ticker_change'
              AND ticker=%(ticker)s
              AND vendor_source='massive'
            """,
            {
                "instrument_id": candidate.instrument_id,
                "event_date": event.event_date,
                "ticker": event.ticker,
                "exchange": event.exchange,
                "share_class_figi": event.share_class_figi,
                "status": status,
                "reason": reason,
                "before": json.dumps(before, default=str) if before is not None else None,
                "after": json.dumps(after, default=str) if after is not None else None,
            },
        )


def _apply_intervals(
    conn: psycopg.Connection,
    candidate: InstrumentCandidate,
    events: list[NormalizedEvent],
    intervals: list[SymbolInterval],
) -> tuple[list[dict], list[dict]]:
    before = _snapshot_intervals(conn, candidate.instrument_id)
    conn.execute(
        "UPDATE symbol_history SET is_primary=FALSE WHERE instrument_id=%s AND is_primary",
        (candidate.instrument_id,),
    )
    event_by_ticker = {event.ticker: event for event in events}
    for interval in intervals:
        existing = conn.execute(
            """
            SELECT id FROM symbol_history
            WHERE instrument_id=%s AND exchange=%s AND symbol=%s
            """,
            (candidate.instrument_id, interval.exchange, interval.symbol),
        ).fetchall()
        payload = json.dumps(event_by_ticker[interval.symbol].vendor_payload)
        if existing:
            conn.execute(
                """
                UPDATE symbol_history
                SET valid_from=%s,
                    valid_to=%s,
                    is_primary=TRUE,
                    valid_from_precision='exact',
                    source='massive_ticker_events',
                    vendor_payload=%s::jsonb,
                    asof=now()
                WHERE id=%s
                """,
                (interval.valid_from, interval.valid_to, payload, existing[0][0]),
            )
        else:
            conn.execute(
                """
                INSERT INTO symbol_history (
                  instrument_id, exchange, symbol, valid_from, valid_to,
                  is_primary, valid_from_precision, source, vendor_payload
                ) VALUES (%s,%s,%s,%s,%s,TRUE,'exact','massive_ticker_events',%s::jsonb)
                """,
                (
                    candidate.instrument_id,
                    interval.exchange,
                    interval.symbol,
                    interval.valid_from,
                    interval.valid_to,
                    payload,
                ),
            )
    after = _snapshot_intervals(conn, candidate.instrument_id)
    _mark_resolution(
        conn,
        candidate,
        events,
        status="applied",
        reason=None,
        before=before,
        after=after,
    )
    return before, after


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if args.stale_days < 1:
        raise SystemExit("--stale-days must be >= 1")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.warning_sample_limit < 0:
        raise SystemExit("--warning-sample-limit must be >= 0")
    database_url = args.database_url or os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    api_key = os.getenv("MASSIVE_API_KEY")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    if not args.dry_run:
        require_market_data_maintenance_owner(database_url)
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY")

    fetched = with_events = applicable = applied = unresolved = failures = warning_count = 0
    with psycopg.connect(normalize_dsn(database_url)) as conn:
        candidates = _load_candidates(conn, full_refresh=args.full_refresh, stale_days=args.stale_days)
        print(
            f"Ticker-event candidates={len(candidates)} full_refresh={args.full_refresh} "
            f"store_only={args.store_only} dry_run={args.dry_run}",
            flush=True,
        )
        for index, (candidate, events, validated, reason, fetch_error) in enumerate(
            fetch_candidate_events(api_key, candidates, workers=args.workers),
            start=1,
        ):
            try:
                if fetch_error is not None:
                    raise fetch_error
                fetched += 1
                if not events:
                    if not args.dry_run:
                        conn.execute(UPSERT_STATE_SQL, (candidate.instrument_id,))
                        conn.commit()
                    continue
                with_events += 1
                if not args.dry_run:
                    _record_events(conn, candidate, events)

                intervals: list[SymbolInterval] = []
                if (
                    not reason
                    and validated
                    and validated[-1].exchange != candidate.current_exchange
                ):
                    reason = (
                        f"latest event exchange {validated[-1].exchange} "
                        f"!= current exchange {candidate.current_exchange}"
                    )
                if not reason:
                    terminal_valid_to = (
                        inactive_terminal_valid_to(candidate, validated[-1].event_date)
                        if validated
                        else None
                    )
                    intervals, reason = build_intervals(
                        validated,
                        candidate.canonical_ticker,
                        terminal_valid_to=terminal_valid_to,
                    )
                if not reason and intervals:
                    reason = _preflight_intervals(conn, candidate, intervals)

                if reason:
                    unresolved += 1
                    warning_count += 1
                    if not args.dry_run:
                        _mark_resolution(
                            conn,
                            candidate,
                            validated or events,
                            status="unresolved",
                            reason=reason,
                        )
                        conn.execute(UPSERT_STATE_SQL, (candidate.instrument_id,))
                        conn.commit()
                    print_limited_warning(
                        candidate.canonical_ticker,
                        reason,
                        number=warning_count,
                        limit=args.warning_sample_limit,
                    )
                else:
                    applicable += 1
                    if not args.dry_run and not args.store_only:
                        _apply_intervals(conn, candidate, validated, intervals)
                        applied += 1
                    elif not args.dry_run:
                        _mark_resolution(
                            conn,
                            candidate,
                            validated,
                            status="pending",
                            reason="store-only mode",
                        )
                    if not args.dry_run:
                        conn.execute(UPSERT_STATE_SQL, (candidate.instrument_id,))
                        conn.commit()
            except Exception as exc:
                conn.rollback()
                failures += 1
                warning_count += 1
                message = f"{type(exc).__name__}: {str(exc)[:300]}"
                if not args.dry_run:
                    conn.execute(UPSERT_ERROR_STATE_SQL, (candidate.instrument_id, message))
                    conn.commit()
                print_limited_warning(
                    candidate.canonical_ticker,
                    message,
                    number=warning_count,
                    limit=args.warning_sample_limit,
                )
            if index % 100 == 0 or index == len(candidates):
                print(
                    f"Ticker-event progress={index}/{len(candidates)} with_events={with_events} "
                    f"applicable={applicable} applied={applied} unresolved={unresolved} failures={failures}",
                    flush=True,
                )

    print(
        "Ticker-event sync complete: "
        f"fetched={fetched} with_events={with_events} applicable={applicable} "
        f"applied={applied} unresolved={unresolved} failures={failures}",
        flush=True,
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
