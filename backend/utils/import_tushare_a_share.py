from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

try:
    from .backfill_daily_features import backfill_daily_features
except ImportError:
    from backfill_daily_features import backfill_daily_features


REPO_ROOT = Path(__file__).resolve().parents[2]
TUSHARE_API_URL = "https://api.tushare.pro"
SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGE_MIC = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "XBSE"}
LIST_STATUSES = ("L", "D", "P")
A_SHARE_BASKET_NAME = "All A Shares (Tushare)"
A_SHARE_BASKET_DESCRIPTION = "Tushare 导入的当前正常上市 A 股；仅用于本地研究和回测。"
STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,market,exchange,curr_type,"
    "list_status,list_date,delist_date,is_hs"
)
DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount"
ADJ_FACTOR_FIELDS = "ts_code,trade_date,adj_factor"
REQUIRED_TABLES = ("instruments", "symbol_history", "eod_bars", "daily_features", "stock_baskets")


class TushareError(RuntimeError):
    pass


class TushareClient:
    def __init__(
        self,
        token: str,
        *,
        request_interval_seconds: float = 0.2,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("TUSHARE_TOKEN is required")
        self._token = token.strip()
        self._interval = request_interval_seconds
        self._timeout = timeout_seconds
        self._session = session or requests.Session()
        self._last_request_at = 0.0

    def query(
        self,
        api_name: str,
        *,
        params: dict[str, Any] | None = None,
        fields: str = "",
    ) -> list[dict[str, Any]]:
        wait_seconds = self._interval - (time.monotonic() - self._last_request_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        response = self._session.post(
            TUSHARE_API_URL,
            json={
                "api_name": api_name,
                "token": self._token,
                "params": params or {},
                "fields": fields,
            },
            timeout=self._timeout,
        )
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            code = payload.get("code") if isinstance(payload, dict) else "invalid_response"
            message = payload.get("msg") if isinstance(payload, dict) else "response is not an object"
            raise TushareError(f"Tushare {api_name} failed: code={code} message={message}")
        data = payload.get("data") or {}
        returned_fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(returned_fields, list) or not isinstance(items, list):
            raise TushareError(f"Tushare {api_name} returned malformed data")
        return [dict(zip(returned_fields, item, strict=True)) for item in items]


@dataclass(frozen=True, slots=True)
class InstrumentRow:
    vendor_key: str
    ts_code: str
    exchange: str
    name: str | None
    currency: str
    listed_at: date | None
    delisted_at: date | None
    is_active: bool
    vendor_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BarRow:
    instrument_id: int
    trade_date: date
    ts_utc: datetime
    open_u: float
    high_u: float
    low_u: float
    close_u: float
    volume: int
    vwap: float | None
    adj_factor: float


def _parse_tushare_date(value: Any) -> date | None:
    normalized = str(value or "").strip()
    return datetime.strptime(normalized, "%Y%m%d").date() if normalized else None


def normalize_instrument(item: dict[str, Any]) -> InstrumentRow:
    ts_code = str(item.get("ts_code") or "").strip().upper()
    exchange_code = str(item.get("exchange") or "").strip().upper()
    if not ts_code or exchange_code not in EXCHANGE_MIC:
        raise ValueError("Tushare instrument requires a supported ts_code and exchange")
    currency = str(item.get("curr_type") or "CNY").strip().upper()
    if currency in {"RMB", ""}:
        currency = "CNY"
    list_status = str(item.get("list_status") or "").strip().upper()
    listed_at = _parse_tushare_date(item.get("list_date"))
    delisted_at = _parse_tushare_date(item.get("delist_date"))
    if listed_at and delisted_at and delisted_at < listed_at:
        raise ValueError(f"Tushare instrument {ts_code} has an invalid listing window")
    return InstrumentRow(
        vendor_key=f"TUSHARE:{ts_code}",
        ts_code=ts_code,
        exchange=EXCHANGE_MIC[exchange_code],
        name=str(item.get("name") or "").strip() or None,
        currency=currency,
        listed_at=listed_at,
        delisted_at=delisted_at,
        is_active=list_status == "L",
        vendor_payload=dict(item),
    )


def _finite_positive(value: Any, field: str, ts_code: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Tushare {ts_code} has invalid {field}")
    return number


def normalize_bar(
    item: dict[str, Any],
    *,
    instrument_id: int,
    adj_factor: float,
) -> BarRow:
    ts_code = str(item.get("ts_code") or "").strip().upper()
    trade_date = _parse_tushare_date(item.get("trade_date"))
    if not ts_code or trade_date is None:
        raise ValueError("Tushare daily row requires ts_code and trade_date")
    open_u = _finite_positive(item.get("open"), "open", ts_code)
    high_u = _finite_positive(item.get("high"), "high", ts_code)
    low_u = _finite_positive(item.get("low"), "low", ts_code)
    close_u = _finite_positive(item.get("close"), "close", ts_code)
    if high_u < max(open_u, low_u, close_u) or low_u > min(open_u, high_u, close_u):
        raise ValueError(f"Tushare {ts_code} has inverted OHLC geometry")
    volume_lots = float(item.get("vol") or 0.0)
    raw_amount = item.get("amount")
    amount_thousand = None if raw_amount is None else float(raw_amount)
    if not math.isfinite(volume_lots) or volume_lots < 0:
        raise ValueError(f"Tushare {ts_code} has invalid volume")
    if amount_thousand is not None and (
        not math.isfinite(amount_thousand) or amount_thousand < 0
    ):
        raise ValueError(f"Tushare {ts_code} has invalid amount")
    factor = _finite_positive(adj_factor, "adj_factor", ts_code)
    volume = int(round(volume_lots * 100.0))
    vwap = amount_thousand * 1000.0 / volume if volume and amount_thousand else None
    close_shanghai = datetime.combine(trade_date, datetime_time(15, 0), SHANGHAI)
    return BarRow(
        instrument_id=instrument_id,
        trade_date=trade_date,
        ts_utc=close_shanghai.astimezone(timezone.utc),
        open_u=open_u,
        high_u=high_u,
        low_u=low_u,
        close_u=close_u,
        volume=volume,
        vwap=vwap,
        adj_factor=factor,
    )


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def _database_label(url: str) -> str:
    parsed = urlsplit(_psycopg_dsn(url))
    return f"{parsed.username or ''}@{parsed.hostname or ''}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"


def _validate_schema(conn: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in REQUIRED_TABLES:
            cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            if cur.fetchone()[0] is None:
                raise RuntimeError(f"required table public.{table} does not exist")
        cur.execute("SELECT COUNT(*) FROM instruments WHERE vendor_source = 'tushare'")
        counts["instruments"] = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM eod_bars WHERE vendor = 'tushare'")
        counts["bars"] = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT COUNT(*)
            FROM daily_features df
            JOIN instruments i ON i.id = df.instrument_id
            WHERE i.vendor_source = 'tushare'
            """
        )
        counts["features"] = int(cur.fetchone()[0])
    return counts


def _fetch_instruments(client: TushareClient, selected: set[str]) -> list[InstrumentRow]:
    by_code: dict[str, InstrumentRow] = {}
    for status in LIST_STATUSES:
        for exchange in EXCHANGE_MIC:
            rows = client.query(
                "stock_basic",
                params={"exchange": exchange, "list_status": status},
                fields=STOCK_BASIC_FIELDS,
            )
            for item in rows:
                instrument = normalize_instrument(item)
                if not selected or instrument.ts_code in selected:
                    by_code[instrument.ts_code] = instrument
    missing = sorted(selected - set(by_code))
    if missing:
        raise ValueError(f"unknown or unsupported Tushare ts_code: {', '.join(missing)}")
    return [by_code[key] for key in sorted(by_code)]


def _upsert_instruments(
    conn: psycopg.Connection,
    instruments: list[InstrumentRow],
) -> dict[str, int]:
    instrument_sql = """
    INSERT INTO instruments (
      share_class_figi, ticker_canonical, exchange, mic, asset_type, name,
      currency, country, locale, market, listed_at, delisted_at, is_active,
      vendor_source, vendor_payload
    ) VALUES (
      %(vendor_key)s, %(ts_code)s, %(exchange)s, %(exchange)s, 'CS', %(name)s,
      %(currency)s, 'CN', 'cn', 'stocks', %(listed_at)s, %(delisted_at)s,
      %(is_active)s, 'tushare', %(vendor_payload)s
    )
    ON CONFLICT (share_class_figi) DO UPDATE SET
      ticker_canonical = EXCLUDED.ticker_canonical,
      exchange = EXCLUDED.exchange,
      mic = EXCLUDED.mic,
      name = EXCLUDED.name,
      currency = EXCLUDED.currency,
      listed_at = EXCLUDED.listed_at,
      delisted_at = EXCLUDED.delisted_at,
      is_active = EXCLUDED.is_active,
      vendor_payload = EXCLUDED.vendor_payload,
      updated_at = now()
    """
    history_update_sql = """
    UPDATE symbol_history
    SET exchange = %(exchange)s,
        mic = %(exchange)s,
        currency = %(currency)s,
        valid_from = %(valid_from)s,
        valid_to = %(valid_to)s,
        vendor_payload = %(vendor_payload)s,
        asof = now(),
        updated_at = now()
    WHERE instrument_id = %(instrument_id)s
      AND source = 'tushare'
      AND symbol = %(ts_code)s
    """
    history_insert_sql = """
    INSERT INTO symbol_history (
      instrument_id, exchange, mic, symbol, currency, valid_from, valid_to,
      is_primary, valid_from_precision, source, vendor_payload
    ) VALUES (
      %(instrument_id)s, %(exchange)s, %(exchange)s, %(ts_code)s, %(currency)s,
      %(valid_from)s, %(valid_to)s, TRUE, %(valid_from_precision)s,
      'tushare', %(vendor_payload)s
    )
    """
    with conn.cursor() as cur:
        for instrument in instruments:
            params = {
                **asdict(instrument),
                "vendor_payload": Jsonb(instrument.vendor_payload),
            }
            cur.execute(instrument_sql, params)
        cur.execute(
            """
            SELECT id, ticker_canonical
            FROM instruments
            WHERE share_class_figi = ANY(%s)
            """,
            ([item.vendor_key for item in instruments],),
        )
        instrument_ids = {str(symbol): int(instrument_id) for instrument_id, symbol in cur.fetchall()}
        for instrument in instruments:
            valid_from = instrument.listed_at or date(1990, 1, 1)
            params = {
                "instrument_id": instrument_ids[instrument.ts_code],
                "exchange": instrument.exchange,
                "currency": instrument.currency,
                "valid_from": valid_from,
                "valid_to": instrument.delisted_at,
                "valid_from_precision": "exact" if instrument.listed_at else "unknown",
                "ts_code": instrument.ts_code,
                "vendor_payload": Jsonb(instrument.vendor_payload),
            }
            cur.execute(history_update_sql, params)
            if cur.rowcount == 0:
                cur.execute(history_insert_sql, params)
    conn.commit()
    return instrument_ids


def _trade_dates(client: TushareClient, start_date: date, end_date: date) -> list[date]:
    rows = client.query(
        "trade_cal",
        params={
            "exchange": "SSE",
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
            "is_open": "1",
        },
        fields="cal_date,is_open",
    )
    return sorted(
        parsed
        for row in rows
        if str(row.get("is_open")) == "1"
        and (parsed := _parse_tushare_date(row.get("cal_date"))) is not None
    )


def _daily_rows(
    client: TushareClient,
    trade_date: date,
    selected: set[str],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    query_date = trade_date.strftime("%Y%m%d")
    params = {"trade_date": query_date}
    if len(selected) == 1:
        params["ts_code"] = next(iter(selected))
    daily = client.query("daily", params=params, fields=DAILY_FIELDS)
    factors = client.query(
        "adj_factor",
        params=params,
        fields=ADJ_FACTOR_FIELDS,
    )
    if len(daily) >= 6000 or len(factors) >= 6000:
        raise TushareError(f"Tushare row limit reached on {trade_date}; refusing a truncated import")
    if selected:
        daily = [row for row in daily if str(row.get("ts_code") or "").upper() in selected]
        factors = [row for row in factors if str(row.get("ts_code") or "").upper() in selected]
    factor_by_code = {
        str(row.get("ts_code") or "").strip().upper(): float(row["adj_factor"])
        for row in factors
    }
    missing = sorted(
        str(row.get("ts_code") or "").strip().upper()
        for row in daily
        if str(row.get("ts_code") or "").strip().upper() not in factor_by_code
    )
    if missing:
        raise TushareError(
            f"adj_factor missing for {len(missing)} daily rows on {trade_date}; first={missing[0]}"
        )
    return daily, factor_by_code


def _upsert_bars(conn: psycopg.Connection, bars: Iterable[BarRow]) -> int:
    create_stage_sql = """
    CREATE TEMP TABLE IF NOT EXISTS tushare_eod_stage (
      instrument_id BIGINT NOT NULL,
      ts_utc TIMESTAMPTZ NOT NULL,
      open_u DOUBLE PRECISION NOT NULL,
      high_u DOUBLE PRECISION NOT NULL,
      low_u DOUBLE PRECISION NOT NULL,
      close_u DOUBLE PRECISION NOT NULL,
      volume BIGINT NOT NULL,
      vwap DOUBLE PRECISION,
      adj_factor DOUBLE PRECISION NOT NULL,
      open_ba DOUBLE PRECISION NOT NULL,
      high_ba DOUBLE PRECISION NOT NULL,
      low_ba DOUBLE PRECISION NOT NULL,
      close_ba DOUBLE PRECISION NOT NULL
    ) ON COMMIT PRESERVE ROWS
    """
    copy_sql = """
    COPY tushare_eod_stage (
      instrument_id, ts_utc, open_u, high_u, low_u, close_u, volume, vwap,
      adj_factor, open_ba, high_ba, low_ba, close_ba
    ) FROM STDIN
    """
    upsert_sql = """
    INSERT INTO eod_bars (
      instrument_id, ts_utc, open_u, high_u, low_u, close_u, volume, vwap,
      fwd_factor, open_fa, high_fa, low_fa, close_fa,
      bwd_factor, open_ba, high_ba, low_ba, close_ba, vendor
    )
    SELECT
      instrument_id, ts_utc, open_u, high_u, low_u, close_u, volume, vwap,
      NULL, NULL, NULL, NULL, NULL,
      adj_factor, open_ba, high_ba, low_ba, close_ba, 'tushare'
    FROM tushare_eod_stage
    ON CONFLICT (instrument_id, dt_ny) DO UPDATE SET
      ts_utc = EXCLUDED.ts_utc,
      open_u = EXCLUDED.open_u,
      high_u = EXCLUDED.high_u,
      low_u = EXCLUDED.low_u,
      close_u = EXCLUDED.close_u,
      volume = EXCLUDED.volume,
      vwap = EXCLUDED.vwap,
      fwd_factor = NULL,
      open_fa = NULL,
      high_fa = NULL,
      low_fa = NULL,
      close_fa = NULL,
      bwd_factor = EXCLUDED.bwd_factor,
      open_ba = EXCLUDED.open_ba,
      high_ba = EXCLUDED.high_ba,
      low_ba = EXCLUDED.low_ba,
      close_ba = EXCLUDED.close_ba,
      vendor = 'tushare',
      asof = now()
    """
    rows = list(bars)
    with conn.cursor() as cur:
        cur.execute(create_stage_sql)
        with cur.copy(copy_sql) as copy:
            for row in rows:
                copy.write_row(
                    (
                        row.instrument_id,
                        row.ts_utc,
                        row.open_u,
                        row.high_u,
                        row.low_u,
                        row.close_u,
                        row.volume,
                        row.vwap,
                        row.adj_factor,
                        row.open_u * row.adj_factor,
                        row.high_u * row.adj_factor,
                        row.low_u * row.adj_factor,
                        row.close_u * row.adj_factor,
                    )
                )
        cur.execute(upsert_sql)
        cur.execute("TRUNCATE tushare_eod_stage")
    conn.commit()
    return len(rows)


def _refresh_forward_adjustment(conn: psycopg.Connection, instrument_ids: list[int]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
              SELECT DISTINCT ON (instrument_id) instrument_id, bwd_factor AS latest_factor
              FROM eod_bars
              WHERE instrument_id = ANY(%s)
                AND vendor = 'tushare'
                AND bwd_factor > 0
              ORDER BY instrument_id, dt_ny DESC
            )
            UPDATE eod_bars e
            SET fwd_factor = e.bwd_factor / latest.latest_factor,
                open_fa = e.open_u * e.bwd_factor / latest.latest_factor,
                high_fa = e.high_u * e.bwd_factor / latest.latest_factor,
                low_fa = e.low_u * e.bwd_factor / latest.latest_factor,
                close_fa = e.close_u * e.bwd_factor / latest.latest_factor,
                asof = now()
            FROM latest
            WHERE e.instrument_id = latest.instrument_id
              AND e.vendor = 'tushare'
              AND e.bwd_factor > 0
            """,
            (instrument_ids,),
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def _sync_a_share_basket(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker_canonical
            FROM instruments
            WHERE vendor_source = 'tushare'
              AND locale = 'cn'
              AND asset_type = 'CS'
              AND is_active = TRUE
            ORDER BY ticker_canonical
            """
        )
        symbols = [str(row[0]) for row in cur.fetchall()]
        cur.execute(
            """
            INSERT INTO stock_baskets (name, description, symbols, status)
            VALUES (%s, %s, %s, 'active')
            ON CONFLICT (name) DO UPDATE SET
              description = EXCLUDED.description,
              symbols = EXCLUDED.symbols,
              status = 'active',
              updated_at = now()
            """,
            (A_SHARE_BASKET_NAME, A_SHARE_BASKET_DESCRIPTION, Jsonb(symbols)),
        )
    conn.commit()
    return len(symbols)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or import Tushare A-share daily bars into PostgreSQL for backtesting."
    )
    parser.add_argument("mode", choices=("plan", "apply"))
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD end date.")
    parser.add_argument(
        "--ts-code",
        action="append",
        default=[],
        help="Optional Tushare code such as 000001.SZ; repeat for a subset.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
    )
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=0.2,
        help="Minimum delay between Tushare requests.",
    )
    parser.add_argument(
        "--skip-features",
        action="store_true",
        help="Import bars without rebuilding daily_features (not backtest-ready).",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        raise SystemExit("end date must be on or after start date")
    if args.request_interval_seconds < 0:
        raise SystemExit("request interval must be non-negative")
    selected = {str(value).strip().upper() for value in args.ts_code if str(value).strip()}

    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        counts = _validate_schema(conn)
    print(f"Database target: {_database_label(args.database_url)}")
    print(
        f"Existing Tushare rows: instruments={counts['instruments']} "
        f"bars={counts['bars']} features={counts['features']}"
    )
    print(
        f"Requested window: {start_date.isoformat()}..{end_date.isoformat()} "
        f"universe={'all A shares' if not selected else ','.join(sorted(selected))}"
    )
    if args.mode == "plan":
        print("Plan only: no network request or database write was performed.")
        return

    token = str(os.getenv("TUSHARE_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Missing TUSHARE_TOKEN")
    client = TushareClient(
        token,
        request_interval_seconds=args.request_interval_seconds,
    )
    instruments = _fetch_instruments(client, selected)
    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        instrument_ids = _upsert_instruments(conn, instruments)
    dates = _trade_dates(client, start_date, end_date)
    print(f"Resolved instruments={len(instruments)} open_sessions={len(dates)}")

    imported_rows = 0
    with psycopg.connect(_psycopg_dsn(args.database_url)) as conn:
        for index, trade_date in enumerate(dates, start=1):
            daily, factors = _daily_rows(client, trade_date, selected)
            bars = [
                normalize_bar(
                    row,
                    instrument_id=instrument_ids[str(row["ts_code"]).strip().upper()],
                    adj_factor=factors[str(row["ts_code"]).strip().upper()],
                )
                for row in daily
                if str(row.get("ts_code") or "").strip().upper() in instrument_ids
            ]
            imported_rows += _upsert_bars(conn, bars)
            if index == 1 or index == len(dates) or index % 20 == 0:
                print(
                    f"Imported sessions={index}/{len(dates)} bars={imported_rows} "
                    f"last_date={trade_date.isoformat()}",
                    flush=True,
                )
        adjusted_rows = _refresh_forward_adjustment(conn, list(instrument_ids.values()))
        basket_symbols = _sync_a_share_basket(conn)

    feature_instruments = 0
    feature_rows = 0
    if not args.skip_features:
        feature_instruments, feature_rows = backfill_daily_features(
            args.database_url,
            start_date=args.start_date,
            end_date=args.end_date,
            instrument_ids=list(instrument_ids.values()),
        )
    print(
        f"Tushare import completed. instruments={len(instruments)} bars={imported_rows} "
        f"adjusted_rows={adjusted_rows} feature_instruments={feature_instruments} "
        f"feature_rows={feature_rows} basket_symbols={basket_symbols}"
    )


if __name__ == "__main__":
    main()
