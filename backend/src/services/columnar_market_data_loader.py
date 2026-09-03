"""Fixed-width binary COPY and instrument/year shards; no per-row Python values."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from pathlib import Path
import struct
from time import perf_counter
from typing import Any, Iterable

import numpy as np
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.services.prepared_dataset_service import (
    PREPARED_DATE_SENTINEL, PREPARED_FLOAT_FIELDS, PREPARED_READ_PATH_REVISION,
    PreparedDataset, PreparedDatasetCache,
)

READ_PATH_REVISION = PREPARED_READ_PATH_REVISION
INSTRUMENT_BUCKET_SIZE = 256
WIRE_FIELDS = ("instrument_id", "dt_ordinal", "ts_us", *PREPARED_FLOAT_FIELDS)
WIRE_ROW_BYTES = 2 + 12 * len(WIRE_FIELDS)
COPY_HEADER = b"PGCOPY\n\xff\r\n\x00" + struct.pack("!ii", 0, 0)
PREVIOUS_FIELDS = tuple(name.removeprefix("prev_") for name in PREPARED_FLOAT_FIELDS if name.startswith("prev_"))
BAR_FIELDS = {"open", "high", "low", "close", "close_unadjusted", "volume"}
FEATURE_FIELDS = tuple(dict.fromkeys(
    "adv_20" if name == "volume_sma_20" else name
    for name in PREPARED_FLOAT_FIELDS if name not in BAR_FIELDS and not name.startswith("prev_")
))


def feature_range_sql() -> str:
    # Compute LAG before joining bars, including each instrument's exact seed.
    columns = ", ".join(FEATURE_FIELDS)
    lag = ", ".join(f"lag(f.{name}) OVER w AS prev_{name}" for name in PREVIOUS_FIELDS)
    values = ["f.instrument_id::bigint", "(f.dt_ny - DATE '0001-01-01' + 1)::bigint",
              "trunc(extract(epoch FROM bars.ts_utc)::double precision * 1000000)::bigint"]
    for name in PREPARED_FLOAT_FIELDS:
        if name in {"open", "high", "low", "close"}:
            expression = f"COALESCE(bars.{name}_fa, bars.{name}_u)"
        elif name == "close_unadjusted":
            expression = "bars.close_u"
        elif name == "volume":
            expression = "bars.volume"
        else:
            expression = "f." + ("adv_20" if name == "volume_sma_20" else name)
        values.append(f"COALESCE(({expression})::double precision, 'NaN'::double precision) AS {name}")
    return f"""
WITH feature_rows AS (
    SELECT instrument_id, dt_ny, {columns} FROM daily_features
    WHERE instrument_id = ANY(%(instrument_ids)s)
      AND dt_ny BETWEEN %(start_date)s AND %(end_date)s
    UNION ALL
    SELECT seed.instrument_id, seed.dt_ny, {', '.join('seed.' + name for name in FEATURE_FIELDS)}
    FROM unnest(%(instrument_ids)s::bigint[]) AS ids(id)
    CROSS JOIN LATERAL (
        SELECT instrument_id, dt_ny, {columns} FROM daily_features
        WHERE instrument_id = ids.id AND dt_ny < %(start_date)s
        ORDER BY dt_ny DESC LIMIT 1
    ) seed
), windowed AS (
    SELECT f.*, {lag} FROM feature_rows f
    WINDOW w AS (PARTITION BY f.instrument_id ORDER BY f.dt_ny)
)
SELECT {', '.join(values)} FROM windowed f
JOIN eod_bars bars ON bars.instrument_id = f.instrument_id AND bars.dt_ny = f.dt_ny
  AND bars.instrument_id = ANY(%(instrument_ids)s)
  AND bars.dt_ny BETWEEN %(start_date)s AND %(end_date)s
WHERE f.dt_ny BETWEEN %(start_date)s AND %(end_date)s
ORDER BY f.instrument_id, f.dt_ny
""".strip()


FEATURE_RANGE_SQL = feature_range_sql()


def read_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def spool_copy(chunks: Iterable[bytes], path: Path) -> int:
    """Support arbitrary chunk boundaries, including split header/trailer."""
    header = bytearray()
    with path.open("w+b", buffering=1024 * 1024) as target:
        for chunk in chunks:
            if len(header) < len(COPY_HEADER):
                needed = len(COPY_HEADER) - len(header)
                header.extend(chunk[:needed])
                chunk = chunk[needed:]
                if len(header) == len(COPY_HEADER) and header != COPY_HEADER:
                    raise ValueError("unexpected binary COPY header")
            target.write(chunk)
        size = target.tell()
        if header != COPY_HEADER or size < 2:
            raise ValueError("incomplete binary COPY stream")
        target.seek(-2, 2)
        if target.read(2) != b"\xff\xff" or (size - 2) % WIRE_ROW_BYTES:
            raise ValueError("invalid binary COPY trailer or row width")
        target.truncate(size - 2)
    return (size - 2) // WIRE_ROW_BYTES


def wire_values(path: Path, rows: int) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        return np.empty((0, 3), dtype=np.int64), np.empty((0, len(PREPARED_FLOAT_FIELDS)))
    wire = np.memmap(path, mode="r", dtype=np.uint8)
    counts = np.ndarray((rows,), dtype=">i2", buffer=wire, strides=(WIRE_ROW_BYTES,))
    lengths = np.ndarray((rows, len(WIRE_FIELDS)), dtype=">i4", buffer=wire, offset=2,
                         strides=(WIRE_ROW_BYTES, 12))
    if np.any(counts != len(WIRE_FIELDS)) or np.any(lengths != 8):
        raise ValueError("COPY column count/type/NULL differs from fixed-width contract")
    return (np.ndarray((rows, 3), dtype=">i8", buffer=wire, offset=6, strides=(WIRE_ROW_BYTES, 12)),
            np.ndarray((rows, len(PREPARED_FLOAT_FIELDS)), dtype=">f8", buffer=wire,
                       offset=42, strides=(WIRE_ROW_BYTES, 12)))


def encode_wire(dataset: PreparedDataset, path: Path, metadata: dict[int, dict[str, Any]]) -> None:
    integers, floats = wire_values(path, len(dataset))
    dataset.floats[:] = floats
    if not len(dataset):
        return
    target = dataset.integers
    target[:, 1], target[:, 3], target[:, 2] = integers[:, 0], integers[:, 1], integers[:, 2]
    target[:, 0] = np.unique(integers[:, 1], return_inverse=True)[1]
    ids, starts, counts = np.unique(integers[:, 0], return_index=True, return_counts=True)
    for instrument_id, start, count in zip(ids, starts, counts):
        item = metadata[int(instrument_id)]
        selected = slice(int(start), int(start + count))
        dates = integers[selected, 1]
        symbol_id = dataset._dictionary_id(item["symbol"], dataset._symbols, dataset._symbol_ids)
        symbols = np.full(int(count), symbol_id, dtype=np.int64)
        # Last matching interval wins, preserving valid_from DESC/id DESC.
        for symbol, first, last in item["intervals"]:
            mask = (dates >= first) & (dates <= last)
            if mask.any():
                symbols[mask] = dataset._dictionary_id(symbol, dataset._symbols, dataset._symbol_ids)
        target[selected, 4] = symbols
        target[selected, 5] = dataset._dictionary_id(item["asset_type"], dataset._asset_types, dataset._asset_type_ids)
        target[selected, 6] = dataset._dictionary_id(item["exchange"], dataset._exchanges, dataset._exchange_ids)
        target[selected, 7], target[selected, 8] = item["listed"], item["delisted"]


def shard_manifests(instrument_ids: list[int], start: date, end: date) -> list[dict[str, Any]]:
    buckets: dict[int, list[int]] = {}
    for identity in sorted(set(instrument_ids)):
        buckets.setdefault(identity // INSTRUMENT_BUCKET_SIZE, []).append(identity)
    return [{"read_path_revision": READ_PATH_REVISION, "instrument_ids": ids,
             "date_range": [date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()]}
            for year in range(start.year, end.year + 1) for ids in buckets.values()]


def canonicalize_rows(dataset: PreparedDataset) -> dict[str, Any]:
    """Restore physical day-major layout, not merely a date-offset sidecar."""
    ints = dataset.integers
    order = np.lexsort((ints[:, 1], ints[:, 3]))
    for column in range(ints.shape[1]):
        ints[:, column] = ints[order, column]
    for column in range(dataset.floats.shape[1]):
        dataset.floats[:, column] = dataset.floats[order, column]
    days, starts, counts = np.unique(ints[:, 3], return_index=True, return_counts=True)
    ints[:, 0] = np.repeat(np.arange(len(days)), counts)
    if len(ints) > 1 and np.any((ints[1:, 3] == ints[:-1, 3]) & (ints[1:, 1] == ints[:-1, 1])):
        raise ValueError("duplicate prepared instrument/date")
    for column, values, mapping in ((4, dataset._symbols, dataset._symbol_ids),
                                    (5, dataset._asset_types, dataset._asset_type_ids),
                                    (6, dataset._exchanges, dataset._exchange_ids)):
        used, first = np.unique(ints[:, column], return_index=True)
        ordered = used[np.argsort(first)]
        remap = np.zeros(len(values), dtype=np.int64)
        remap[ordered] = np.arange(len(ordered))
        new_values = [values[int(index)] for index in ordered]
        ints[:, column] = remap[ints[:, column]]
        values[:] = new_values
        mapping.clear()
        mapping.update({value: index for index, value in enumerate(values)})
    symbol_order = np.lexsort((ints[:, 4], ints[:, 3]))
    dates, symbols = ints[symbol_order, 3], ints[symbol_order, 4]
    if len(ints) > 1 and np.any((dates[1:] == dates[:-1]) & (symbols[1:] == symbols[:-1])):
        raise ValueError("symbol resolves to multiple instruments on the same day")
    identity_order = np.lexsort((ints[:, 3], ints[:, 4], ints[:, 1]))
    ids, symbols, dates = ints[identity_order, 1], ints[identity_order, 4], ints[identity_order, 3]
    boundaries = np.r_[0, np.flatnonzero((ids[1:] != ids[:-1]) | (symbols[1:] != symbols[:-1])) + 1, len(ints)]
    intervals = [[int(ids[a]), dataset._symbols[int(symbols[a])], date.fromordinal(int(dates[a])).isoformat(),
                  date.fromordinal(int(dates[b-1])).isoformat()] for a, b in zip(boundaries[:-1], boundaries[1:])]
    return {"date_offsets": [[date.fromordinal(int(day)).isoformat(), int(start), int(count)]
                             for day, start, count in zip(days, starts, counts)],
            "instrument_symbol_intervals": sorted(intervals)}


def build_market_dataset(db: Session, cache: PreparedDatasetCache, manifest: dict[str, Any],
                         performance: dict[str, Any], corporate_actions: list[Any]) -> PreparedDataset:
    start, end = map(date.fromisoformat, manifest["date_range"])
    ids = [int(value) for value in manifest["instrument_ids"]]
    parts = shard_manifests(ids, start, end)
    shards = PreparedDatasetCache(cache.root / "shards")
    workers = read_setting("BACKTEST_READ_WORKERS", 4, 1, 4)
    work_mem = read_setting("BACKTEST_READ_WORK_MEM_MB", 128, 4, 512)
    read_started = perf_counter()
    engine = db.get_bind()
    # Parallel readers import one snapshot. Caller holds the maintenance lock.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        metadata = {}
        for row in connection.execute(text("""
            SELECT id, ticker_canonical, asset_type, exchange, listed_at, delisted_at
            FROM instruments WHERE id = ANY(:ids) ORDER BY id
        """), {"ids": ids}).mappings():
            metadata[int(row["id"])] = {
                "symbol": str(row["ticker_canonical"]).upper(), "asset_type": row["asset_type"],
                "exchange": row["exchange"], "listed": row["listed_at"].toordinal() if row["listed_at"] else PREPARED_DATE_SENTINEL,
                "delisted": row["delisted_at"].toordinal() if row["delisted_at"] else PREPARED_DATE_SENTINEL, "intervals": [],
            }
        if set(metadata) != set(ids):
            raise ValueError("prepared instrument metadata is incomplete")
        for row in connection.execute(text("""
            SELECT instrument_id, symbol, valid_from, valid_to FROM symbol_history
            WHERE instrument_id = ANY(:ids) AND is_primary = TRUE
            ORDER BY instrument_id, valid_from, id
        """), {"ids": ids}).mappings():
            metadata[int(row["instrument_id"])]["intervals"].append((str(row["symbol"]).upper(),
                row["valid_from"].toordinal(), row["valid_to"].toordinal() if row["valid_to"] else date.max.toordinal()))
        snapshot = connection.exec_driver_sql("SELECT pg_export_snapshot()").scalar_one()
        performance["sql_read_ms"] = float(performance.get("sql_read_ms", 0.0)) + (perf_counter() - read_started) * 1000
        def load_part(part):
            existing = shards.open(part)
            if existing is not None:
                return existing, True
            def prepare(temporary):
                path = temporary / "wire.bin"
                with engine.connect().execution_options(isolation_level="REPEATABLE READ") as reader:
                    reader.exec_driver_sql("SET TRANSACTION READ ONLY")
                    with reader.connection.driver_connection.cursor() as cursor:
                        cursor.execute(sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(snapshot)))
                        cursor.execute(sql.SQL("SET LOCAL work_mem = {}").format(sql.Literal(f"{work_mem}MB")))
                        with cursor.copy("COPY (" + FEATURE_RANGE_SQL + ") TO STDOUT (FORMAT BINARY)",
                                         {"instrument_ids": part["instrument_ids"],
                                          "start_date": date.fromisoformat(part["date_range"][0]),
                                          "end_date": date.fromisoformat(part["date_range"][1])}) as stream:
                            count = spool_copy(stream, path)
                def writer(dataset):
                    encode_wire(dataset, path, metadata)
                    path.unlink()
                return count, writer
            return shards.build(part, prepare=prepare), False
        shards_started = perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            loaded = list(executor.map(load_part, parts))
        performance["shard_load_ms"] = (perf_counter() - shards_started) * 1000
    performance["read_workers"], performance["read_work_mem_mb"] = workers, work_mem
    performance["shards_hit"] = sum(hit for _, hit in loaded)
    performance["shards_built"] = sum(not hit for _, hit in loaded)
    selected = [(part, np.flatnonzero((part.integers[:, 3] >= start.toordinal()) &
                                    (part.integers[:, 3] <= end.toordinal()))) for part, _ in loaded]
    row_count = sum(len(indices) for _, indices in selected)
    if not row_count:
        raise ValueError("no daily feature data found for the backtest universe and window")
    def writer(dataset):
        write_started = perf_counter()
        index = 0
        for part, indices in selected:
            end_index = index + len(indices)
            dataset.integers[index:end_index], dataset.floats[index:end_index] = part.integers[indices], part.floats[indices]
            for column, name, values, mapping in ((4, "symbols", dataset._symbols, dataset._symbol_ids),
                                                  (5, "asset_types", dataset._asset_types, dataset._asset_type_ids),
                                                  (6, "exchanges", dataset._exchanges, dataset._exchange_ids)):
                remap = np.array([dataset._dictionary_id(value, values, mapping) for value in part.sidecar[name]], dtype=np.int64)
                dataset.integers[index:end_index, column] = remap[dataset.integers[index:end_index, column]]
            index = end_index
            part.integers._mmap.close()
            part.floats._mmap.close()
        sidecar = canonicalize_rows(dataset)
        sidecar["corporate_actions"] = corporate_actions
        performance["array_write_ms"] = float(performance.get("array_write_ms", 0.0)) + (perf_counter() - write_started) * 1000
        return sidecar
    try:
        return cache.build(manifest, row_count=row_count, writer=writer, performance=performance)
    finally:
        for part, _ in loaded:
            part.integers._mmap.close()
            part.floats._mmap.close()
