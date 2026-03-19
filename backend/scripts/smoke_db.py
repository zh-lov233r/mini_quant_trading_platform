from __future__ import annotations

import csv
import gzip
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False

try:
    import psycopg
    from psycopg import sql
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency `psycopg`. Install it first, for example: "
        "`pip install psycopg[binary]`"
    ) from exc

from backend.src.services.data_service import import_massive_day_agg_file  # noqa: E402


SQL_DIR = REPO_ROOT / "backend" / "utils"
CREATE_SQL_FILES = (
    "create_instrument.sql",
    "create_stock_daily.sql",
    "create_trans_strategy.sql",
)


def _database_url() -> str:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")
    return database_url


@contextmanager
def isolated_schema(conn: psycopg.Connection, schema_name: str, keep: bool = False):
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        cur.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))
    conn.commit()

    try:
        yield schema_name
    finally:
        with conn.cursor() as cur:
            cur.execute("RESET search_path")
            if not keep:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
        conn.commit()


def run_schema_sql(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for filename in CREATE_SQL_FILES:
            sql_text = (SQL_DIR / filename).read_text(encoding="utf-8")
            cur.execute(sql_text)
    conn.commit()


def assert_tables_exist(conn: psycopg.Connection) -> None:
    expected = {
        "instruments",
        "symbol_history",
        "symbol_reference",
        "eod_bars",
        "strategies",
        "transactions",
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = current_schema()
            """
        )
        found = {row[0] for row in cur.fetchall()}
    missing = expected - found
    if missing:
        raise AssertionError(f"Missing tables after schema creation: {sorted(missing)}")


def seed_reference_data(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO instruments (
                share_class_figi,
                composite_figi,
                cik,
                ticker_canonical,
                exchange,
                asset_type,
                share_class,
                name,
                currency,
                listed_at,
                is_active,
                vendor_source
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
            """,
            (
                "BBG001S5N8V8",
                "BBG000B9XRY4",
                "0000320193",
                "AAPL",
                "XNAS",
                "CS",
                None,
                "Apple Inc.",
                "USD",
                date(1980, 12, 12),
                True,
                "massive",
            ),
        )
        instrument_id = int(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO symbol_history (instrument_id, exchange, symbol, valid_from)
            VALUES (%s, %s, %s, %s)
            """,
            (instrument_id, "XNAS", "AAPL", date(1980, 12, 12)),
        )
        cur.execute(
            """
            INSERT INTO symbol_reference (
                symbol, exchange, asset_type, market, locale, is_common_stock, name, source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            ("AAPL", "XNAS", "CS", "stocks", "us", True, "Apple Inc.", "massive"),
        )
    conn.commit()
    return instrument_id


def _window_start_ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def build_sample_flat_file(temp_dir: Path) -> Path:
    dated_dir = temp_dir / "2025-01-03"
    dated_dir.mkdir(parents=True, exist_ok=True)
    file_path = dated_dir / "2025-01-03.csv.gz"

    rows = [
        {
            "ticker": "AAPL",
            "volume": "123456",
            "open": "250.10",
            "close": "252.25",
            "high": "253.00",
            "low": "249.80",
            "window_start": str(_window_start_ns(datetime(2025, 1, 3, 21, 0, tzinfo=timezone.utc))),
            "transactions": "1000",
        }
    ]

    with gzip.open(file_path, mode="wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "volume",
                "open",
                "close",
                "high",
                "low",
                "window_start",
                "transactions",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    return file_path


def assert_imported_bar(conn: psycopg.Connection, instrument_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT instrument_id, dt_ny, open_u, high_u, low_u, close_u, volume, trades, vendor
            FROM eod_bars
            WHERE instrument_id = %s
            """,
            (instrument_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise AssertionError("Expected one imported eod_bars row, found none")

    assert int(row[0]) == instrument_id
    assert row[1] == date(2025, 1, 3)
    assert float(row[2]) == 250.10
    assert float(row[3]) == 253.00
    assert float(row[4]) == 249.80
    assert float(row[5]) == 252.25
    assert int(row[6]) == 123456
    assert int(row[7]) == 1000
    assert row[8] == "massive"


def main() -> None:
    database_url = _database_url()
    schema_name = f"smoke_{uuid.uuid4().hex[:8]}"
    keep_schema = os.getenv("SMOKE_KEEP_SCHEMA", "").lower() in {"1", "true", "yes"}

    print(f"Connecting to database: {database_url}")
    print(f"Using isolated schema: {schema_name}")

    with psycopg.connect(database_url) as conn:
        with isolated_schema(conn, schema_name, keep=keep_schema):
            run_schema_sql(conn)
            assert_tables_exist(conn)
            print("Schema creation check passed")

            instrument_id = seed_reference_data(conn)
            with tempfile.TemporaryDirectory(prefix="massive_smoke_") as temp_dir:
                sample_file = build_sample_flat_file(Path(temp_dir))
                stats = import_massive_day_agg_file(conn, sample_file)

                if (
                    stats.staged_rows != 1
                    or stats.upserted_rows != 1
                    or stats.filtered_rows != 0
                    or stats.unresolved_symbols != 0
                ):
                    raise AssertionError(
                        "Unexpected import stats: "
                        f"staged={stats.staged_rows}, "
                        f"upserted={stats.upserted_rows}, "
                        f"filtered={stats.filtered_rows}, "
                        f"unresolved={stats.unresolved_symbols}"
                    )

                assert_imported_bar(conn, instrument_id)
                print(
                    "Historical import check passed "
                    f"(trade_date={stats.trade_date}, rows={stats.upserted_rows})"
                )

    print("Smoke test passed")


if __name__ == "__main__":
    main()
