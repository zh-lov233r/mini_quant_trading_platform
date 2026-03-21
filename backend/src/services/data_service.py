from __future__ import annotations

import csv
import gzip
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

import psycopg
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


DATE_IN_PATH_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
EXCLUDED_TEST_SYMBOLS = (
    "NTEST",
    "NTEST.H",
    "NTEST.I",
    "ZBZX",
    "ZEXIT",
    "ZIEXT",
    "ZTEST",
    "ZTST",
    "ZVZZT",
    "ZXIET",
)
EXCLUDED_TEST_SYMBOLS_SQL = ", ".join(f"'{symbol}'" for symbol in EXCLUDED_TEST_SYMBOLS)

STAGE_TABLE_SQL = """
CREATE TEMP TABLE massive_day_aggs_stage (
    symbol TEXT NOT NULL,
    ts_utc TIMESTAMPTZ NOT NULL,
    open_u DOUBLE PRECISION,
    high_u DOUBLE PRECISION,
    low_u DOUBLE PRECISION,
    close_u DOUBLE PRECISION,
    volume BIGINT,
    vwap DOUBLE PRECISION,
    trades INT
) ON COMMIT DROP;
"""

COPY_STAGE_SQL = """
COPY massive_day_aggs_stage (
    symbol, ts_utc, open_u, high_u, low_u, close_u, volume, vwap, trades
) FROM STDIN
"""

COMMON_STOCK_SYMBOL_MAP_SQL = """
CREATE TEMP TABLE common_stock_symbol_map ON COMMIT DROP AS
SELECT
    sh.symbol,
    MIN(instrument_id) AS instrument_id,
    COUNT(*) AS match_count
FROM symbol_history sh
JOIN instruments instr
  ON instr.id = sh.instrument_id
WHERE sh.valid_from <= %(trade_date)s::date
  AND (sh.valid_to IS NULL OR sh.valid_to >= %(trade_date)s::date)
  AND instr.asset_type = 'CS'
GROUP BY sh.symbol;
"""

SYMBOL_CLASSIFICATION_MAP_SQL = """
CREATE TEMP TABLE symbol_classification_map ON COMMIT DROP AS
SELECT
    symbol,
    BOOL_OR(is_common_stock) AS has_common_stock,
    BOOL_OR(NOT is_common_stock) AS has_non_common_stock
FROM symbol_reference
GROUP BY symbol;
"""

FILTERED_COUNT_SQL = """
SELECT COUNT(*)
FROM massive_day_aggs_stage stage
LEFT JOIN common_stock_symbol_map map ON map.symbol = stage.symbol
LEFT JOIN symbol_classification_map cls ON cls.symbol = stage.symbol
WHERE map.symbol IS NULL
  AND (
    stage.symbol IN (""" + EXCLUDED_TEST_SYMBOLS_SQL + """)
    OR
    COALESCE(cls.has_non_common_stock, FALSE)
    OR stage.symbol LIKE '%%.WS'
    OR stage.symbol LIKE '%%.W'
    OR stage.symbol LIKE '%%.U'
    OR stage.symbol LIKE '%%.R'
    OR stage.symbol LIKE '%%.RT'
    OR stage.symbol LIKE '%%.WT'
  )
  AND NOT COALESCE(cls.has_common_stock, FALSE);
"""

UNRESOLVED_COUNT_SQL = """
SELECT COUNT(*)
FROM massive_day_aggs_stage stage
LEFT JOIN common_stock_symbol_map map ON map.symbol = stage.symbol
LEFT JOIN symbol_classification_map cls ON cls.symbol = stage.symbol
WHERE (map.symbol IS NULL OR map.match_count <> 1)
  AND NOT (
    map.symbol IS NULL
    AND (
      stage.symbol IN (""" + EXCLUDED_TEST_SYMBOLS_SQL + """)
      OR
      COALESCE(cls.has_non_common_stock, FALSE)
      OR stage.symbol LIKE '%%.WS'
      OR stage.symbol LIKE '%%.W'
      OR stage.symbol LIKE '%%.U'
      OR stage.symbol LIKE '%%.R'
      OR stage.symbol LIKE '%%.RT'
      OR stage.symbol LIKE '%%.WT'
    )
    AND NOT COALESCE(cls.has_common_stock, FALSE)
  );
"""

UPSERT_EOD_SQL = """
WITH ranked_stage AS (
    SELECT
        map.instrument_id,
        stage.ts_utc,
        stage.open_u,
        stage.high_u,
        stage.low_u,
        stage.close_u,
        stage.volume,
        stage.vwap,
        stage.trades,
        ROW_NUMBER() OVER (
            PARTITION BY map.instrument_id, ((stage.ts_utc AT TIME ZONE 'America/New_York')::date)
            ORDER BY
                CASE WHEN stage.symbol = instruments.ticker_canonical THEN 0 ELSE 1 END,
                stage.symbol
        ) AS rn
    FROM massive_day_aggs_stage stage
    JOIN common_stock_symbol_map map
      ON map.symbol = stage.symbol
     AND map.match_count = 1
    JOIN instruments
      ON instruments.id = map.instrument_id
), upserted AS (
    INSERT INTO eod_bars (
        instrument_id,
        ts_utc,
        open_u,
        high_u,
        low_u,
        close_u,
        volume,
        vwap,
        trades,
        vendor
    )
    SELECT
        ranked_stage.instrument_id,
        ranked_stage.ts_utc,
        ranked_stage.open_u,
        ranked_stage.high_u,
        ranked_stage.low_u,
        ranked_stage.close_u,
        ranked_stage.volume,
        ranked_stage.vwap,
        ranked_stage.trades,
        'massive'
    FROM ranked_stage
    WHERE ranked_stage.rn = 1
    ON CONFLICT (instrument_id, dt_ny) DO UPDATE SET
        ts_utc = EXCLUDED.ts_utc,
        open_u = EXCLUDED.open_u,
        high_u = EXCLUDED.high_u,
        low_u = EXCLUDED.low_u,
        close_u = EXCLUDED.close_u,
        volume = EXCLUDED.volume,
        vwap = EXCLUDED.vwap,
        trades = EXCLUDED.trades,
        vendor = EXCLUDED.vendor,
        asof = now()
    RETURNING 1
)
SELECT COUNT(*) FROM upserted;
"""


@dataclass(frozen=True)
class FlatFileImportStats:
    file_path: Path
    trade_date: date
    staged_rows: int
    upserted_rows: int
    filtered_rows: int
    unresolved_symbols: int


@dataclass(frozen=True)
class HistoricalBar:
    symbol: str
    instrument_id: int
    ts_utc: datetime
    trade_date: date
    open_px: float | None
    high_px: float | None
    low_px: float | None
    close_px: float | None
    volume: int | None
    trades: int | None


def subscribe_market_data():
    pass


def get_historical_data(
    db: Session,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    adjusted: bool = False,
) -> dict[str, list[HistoricalBar]]:
    normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()})
    if not normalized_symbols:
        return {}

    px_open = "COALESCE(e.open_fa, e.open_u)" if adjusted else "e.open_u"
    px_high = "COALESCE(e.high_fa, e.high_u)" if adjusted else "e.high_u"
    px_low = "COALESCE(e.low_fa, e.low_u)" if adjusted else "e.low_u"
    px_close = "COALESCE(e.close_fa, e.close_u)" if adjusted else "e.close_u"

    stmt = text(
        f"""
        SELECT
            COALESCE(sh.symbol, instr.ticker_canonical) AS symbol,
            e.instrument_id,
            e.ts_utc,
            e.dt_ny,
            {px_open} AS open_px,
            {px_high} AS high_px,
            {px_low} AS low_px,
            {px_close} AS close_px,
            e.volume,
            e.trades
        FROM eod_bars e
        JOIN instruments instr
          ON instr.id = e.instrument_id
        LEFT JOIN symbol_history sh
          ON sh.instrument_id = instr.id
         AND sh.symbol IN :symbols
         AND sh.valid_from <= e.dt_ny
         AND (sh.valid_to IS NULL OR sh.valid_to >= e.dt_ny)
        WHERE e.dt_ny BETWEEN :start_date AND :end_date
          AND (
            instr.ticker_canonical IN :symbols
            OR sh.symbol IS NOT NULL
          )
        ORDER BY COALESCE(sh.symbol, instr.ticker_canonical), e.dt_ny, sh.valid_from DESC NULLS LAST
        """
    ).bindparams(bindparam("symbols", expanding=True))

    rows = db.execute(
        stmt,
        {
            "symbols": normalized_symbols,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings()

    bars_by_symbol: dict[str, list[HistoricalBar]] = {symbol: [] for symbol in normalized_symbols}
    seen: set[tuple[str, date]] = set()

    for row in rows:
        symbol = str(row["symbol"]).upper()
        trade_date = row["dt_ny"]
        key = (symbol, trade_date)
        if key in seen:
            continue
        seen.add(key)

        bars_by_symbol.setdefault(symbol, []).append(
            HistoricalBar(
                symbol=symbol,
                instrument_id=int(row["instrument_id"]),
                ts_utc=row["ts_utc"],
                trade_date=trade_date,
                open_px=_to_float_or_none(row["open_px"]),
                high_px=_to_float_or_none(row["high_px"]),
                low_px=_to_float_or_none(row["low_px"]),
                close_px=_to_float_or_none(row["close_px"]),
                volume=_to_int_or_none(row["volume"]),
                trades=_to_int_or_none(row["trades"]),
            )
        )

    return {symbol: bars for symbol, bars in bars_by_symbol.items() if bars}


def _read_text_value(row: dict[str, str], *keys: str, required: bool = False) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        value = value.strip()
        if value:
            return value
    if required:
        raise ValueError(f"Missing required CSV columns among: {', '.join(keys)}")
    return None


def _to_float(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


def _to_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Could not parse integer-like value: {value}") from exc


def _to_float_or_none(value: object) -> float | None:
    return None if value is None else float(value)


def _to_int_or_none(value: object) -> int | None:
    return None if value is None else int(value)


def _ns_to_utc_datetime(ns_value: str) -> datetime:
    timestamp_ns = int(ns_value)
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=nanos // 1_000
    )


def infer_trade_date_from_path(file_path: Path) -> date:
    match = DATE_IN_PATH_RE.search(str(file_path))
    if not match:
        raise ValueError(f"Could not infer trade date from path: {file_path}")
    return date.fromisoformat(match.group("date"))


def iter_massive_day_agg_rows(file_path: Path) -> Iterator[tuple]:
    with gzip.open(file_path, mode="rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Flat file has no header: {file_path}")

        for row in reader:
            symbol = _read_text_value(row, "ticker", "symbol", required=True)
            window_start = _read_text_value(row, "window_start", required=True)
            yield (
                symbol.upper(),
                _ns_to_utc_datetime(window_start),
                _to_float(_read_text_value(row, "open", "o")),
                _to_float(_read_text_value(row, "high", "h")),
                _to_float(_read_text_value(row, "low", "l")),
                _to_float(_read_text_value(row, "close", "c")),
                _to_int(_read_text_value(row, "volume", "v")),
                _to_float(_read_text_value(row, "vwap", "vw")),
                _to_int(_read_text_value(row, "transactions", "trade_count", "n")),
            )


def find_massive_day_agg_files(
    root_dir: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Path]:
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"Flat file directory does not exist: {root}")

    files: list[Path] = []
    for file_path in sorted(root.rglob("*.csv.gz")):
        trade_date = infer_trade_date_from_path(file_path)
        if start_date and trade_date < start_date:
            continue
        if end_date and trade_date > end_date:
            continue
        files.append(file_path)
    return files


def import_massive_day_agg_file(
    conn: psycopg.Connection,
    file_path: str | Path,
) -> FlatFileImportStats:
    file_path = Path(file_path)
    trade_date = infer_trade_date_from_path(file_path)

    with conn.cursor() as cur:
        cur.execute(STAGE_TABLE_SQL)

        staged_rows = 0
        with cur.copy(COPY_STAGE_SQL) as copy:
            for row in iter_massive_day_agg_rows(file_path):
                copy.write_row(row)
                staged_rows += 1

        cur.execute(COMMON_STOCK_SYMBOL_MAP_SQL, {"trade_date": trade_date.isoformat()})
        cur.execute(SYMBOL_CLASSIFICATION_MAP_SQL)
        cur.execute(FILTERED_COUNT_SQL)
        filtered_rows = int(cur.fetchone()[0])
        cur.execute(UNRESOLVED_COUNT_SQL)
        unresolved_symbols = int(cur.fetchone()[0])

        cur.execute(UPSERT_EOD_SQL)
        upserted_rows = int(cur.fetchone()[0])

    conn.commit()
    return FlatFileImportStats(
        file_path=file_path,
        trade_date=trade_date,
        staged_rows=staged_rows,
        upserted_rows=upserted_rows,
        filtered_rows=filtered_rows,
        unresolved_symbols=unresolved_symbols,
    )


def backfill_massive_day_aggs(
    conn: psycopg.Connection,
    files: Sequence[str | Path],
) -> list[FlatFileImportStats]:
    stats: list[FlatFileImportStats] = []
    for file_path in files:
        stats.append(import_massive_day_agg_file(conn, file_path))
    return stats
