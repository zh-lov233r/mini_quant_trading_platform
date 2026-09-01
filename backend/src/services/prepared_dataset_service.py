from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session


PREPARED_DATASET_SCHEMA_VERSION = "v2"
PREPARED_STRING_FIELDS = {"symbol": 64, "asset_type": 24, "exchange": 24}
PREPARED_DATE_FIELDS = ("dt_ny", "listed_at", "delisted_at")
PREPARED_FLOAT_FIELDS = (
    "open", "high", "low", "close", "close_unadjusted", "volume", "atr_14",
    "volume_sma_20", "dollar_volume_20", "ret_20d", "ret_60d", "sma_10",
    "sma_20", "sma_50", "sma_100", "sma_200", "ema_12", "ema_15", "ema_20",
    "ema_50", "rsi_2", "rsi_5", "rsi_14", "zscore_5", "zscore_10", "zscore_20",
    "prev_sma_10", "prev_sma_20", "prev_sma_50", "prev_sma_100", "prev_sma_200",
    "prev_ema_12", "prev_ema_15", "prev_ema_20", "prev_ema_50",
)
PREPARED_DATASET_DTYPE = np.dtype(
    [
        ("instrument_id", "<i8"),
        ("ts_us", "<i8"),
        *((field, f"<U{width}") for field, width in PREPARED_STRING_FIELDS.items()),
        *((field, "<U10") for field in PREPARED_DATE_FIELDS),
        *((field, "<f8") for field in PREPARED_FLOAT_FIELDS),
    ]
)


class PreparedDatasetDataChangedError(RuntimeError):
    pass


def prepared_dataset_key(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_prepared_dataset_manifest(
    *,
    data_fingerprint: dict[str, Any],
    strategy_type: str,
    universe: dict[str, Any],
    requested_date_range: tuple[date, date],
) -> dict[str, Any]:
    """Build the stable, parameter-independent identity for a research dataset."""
    return {
        "loader_schema_version": PREPARED_DATASET_SCHEMA_VERSION,
        "data_fingerprint": data_fingerprint["sha256"],
        "row_count": int(data_fingerprint["rowCount"]),
        "instrument_ids": sorted(int(value) for value in data_fingerprint["instrumentIds"]),
        "date_range": [data_fingerprint["startDate"], data_fingerprint["endDate"]],
        "fingerprint_request_range": [
            requested_date_range[0].isoformat(),
            requested_date_range[1].isoformat(),
        ],
        "feature_set": ["daily_features", "adjusted_ohlcv", strategy_type],
        "price_semantics": "forward_adjusted_when_available",
        "corporate_action_semantics": "split_reverse_split_stock_dividend",
        "symbol_identity_semantics": "point_in_time_primary_symbol",
        "universe_membership_semantics": (
            "point_in_time_liquid" if data_fingerprint.get("universePolicy") else "resolved_instrument_set"
        ),
        "universe_policy": data_fingerprint.get("universePolicy"),
        "universe": universe,
    }


def default_cache_root() -> Path:
    configured = os.getenv("BACKTEST_PREPARED_DATASET_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "data" / "backtest_prepared"


class PreparedDatasetCache:
    """Atomic, fingerprint-addressed NumPy memmap cache for one experiment dataset."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_cache_root()).resolve()

    def _paths(self, key: str) -> tuple[Path, Path, Path]:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("prepared dataset key must be a lowercase sha256")
        return (
            self.root / f"{key}.npy",
            self.root / f"{key}.json",
            self.root / f"{key}.lock",
        )

    @contextmanager
    def _lock(self, lock_path: Path) -> Iterator[None]:
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def open(
        self,
        manifest: dict[str, Any],
        *,
        expected_dtype: np.dtype[Any] | None = None,
    ) -> np.memmap | None:
        key = prepared_dataset_key(manifest)
        data_path, metadata_path, _ = self._paths(key)
        if not data_path.exists() or not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("key") != key or metadata.get("manifest") != manifest:
                return None
            array = np.load(data_path, mmap_mode="r", allow_pickle=False)
            if expected_dtype is not None and array.dtype != np.dtype(expected_dtype):
                return None
            if list(array.shape) != metadata.get("shape") or array.dtype.descr != [
                tuple(item) for item in metadata.get("dtype", [])
            ]:
                return None
            array.flags.writeable = False
            return array
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def metadata(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        key = prepared_dataset_key(manifest)
        _, metadata_path, _ = self._paths(key)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return metadata if metadata.get("key") == key and metadata.get("manifest") == manifest else None

    def build(
        self,
        manifest: dict[str, Any],
        *,
        shape: tuple[int, ...],
        dtype: np.dtype[Any],
        writer: Callable[[np.memmap], dict[str, Any] | None],
    ) -> np.memmap:
        normalized_manifest = {
            **manifest,
            "loader_schema_version": manifest.get(
                "loader_schema_version", PREPARED_DATASET_SCHEMA_VERSION
            ),
        }
        key = prepared_dataset_key(normalized_manifest)
        data_path, metadata_path, lock_path = self._paths(key)
        with self._lock(lock_path):
            existing = self.open(normalized_manifest, expected_dtype=dtype)
            if existing is not None:
                return existing
            token = uuid4().hex
            temp_data = self.root / f".{key}.{token}.npy"
            temp_metadata = self.root / f".{key}.{token}.json"
            try:
                array = np.lib.format.open_memmap(
                    temp_data,
                    mode="w+",
                    dtype=dtype,
                    shape=shape,
                )
                sidecar = writer(array) or {}
                array.flush()
                del array
                metadata = {
                    "key": key,
                    "manifest": normalized_manifest,
                    "shape": list(shape),
                    "dtype": [list(item) for item in np.dtype(dtype).descr],
                    "sidecar": sidecar,
                }
                temp_metadata.write_text(
                    json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(temp_data, data_path)
                os.replace(temp_metadata, metadata_path)
            finally:
                temp_data.unlink(missing_ok=True)
                temp_metadata.unlink(missing_ok=True)
            opened = self.open(normalized_manifest, expected_dtype=dtype)
            if opened is None:
                raise RuntimeError("prepared dataset cache failed validation after atomic publish")
            return opened

    def cleanup(self, manifest: dict[str, Any], *, active_lease_count: int) -> bool:
        if active_lease_count > 0:
            return False
        normalized_manifest = {
            **manifest,
            "loader_schema_version": manifest.get(
                "loader_schema_version", PREPARED_DATASET_SCHEMA_VERSION
            ),
        }
        key = prepared_dataset_key(normalized_manifest)
        data_path, metadata_path, lock_path = self._paths(key)
        with self._lock(lock_path):
            data_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)
        return True

    def cleanup_if_unused(self, db: Session, manifest: dict[str, Any]) -> bool:
        key = prepared_dataset_key(
            {
                **manifest,
                "loader_schema_version": manifest.get(
                    "loader_schema_version", PREPARED_DATASET_SCHEMA_VERSION
                ),
            }
        )
        active_lease_count = int(
            db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM backtest_jobs
                    WHERE status IN ('queued', 'running')
                      AND payload -> 'prepared_dataset' ->> 'key' = :key
                    """
                ),
                {"key": key},
            ).scalar_one()
        )
        return self.cleanup(manifest, active_lease_count=active_lease_count)


def encode_prepared_snapshot(array: np.memmap, index: int, snapshot: dict[str, Any]) -> None:
    array[index]["instrument_id"] = int(snapshot["instrument_id"])
    timestamp = snapshot.get("ts")
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        array[index]["ts_us"] = int(timestamp.timestamp() * 1_000_000)
    else:
        array[index]["ts_us"] = 0
    for field in PREPARED_STRING_FIELDS:
        array[index][field] = str(snapshot.get(field) or "")
    for field in PREPARED_DATE_FIELDS:
        value = snapshot.get(field)
        array[index][field] = value.isoformat() if isinstance(value, date) else ""
    for field in PREPARED_FLOAT_FIELDS:
        value = snapshot.get(field)
        array[index][field] = float(value) if value is not None else np.nan


def decode_prepared_snapshot(row: np.void) -> tuple[date, str, dict[str, Any]]:
    trade_date = date.fromisoformat(str(row["dt_ny"]))
    symbol = str(row["symbol"]).upper()
    ts_us = int(row["ts_us"])
    snapshot: dict[str, Any] = {
        "instrument_id": int(row["instrument_id"]),
        "symbol": symbol,
        "dt_ny": trade_date,
        "ts": datetime.fromtimestamp(ts_us / 1_000_000, tz=timezone.utc),
        "position": 0.0,
        "avg_entry_price": None,
        "entry_trade_date": None,
        "entry_signal_features": None,
        "position_holding_days": None,
        "recent_bars": [],
    }
    for field in ("asset_type", "exchange"):
        snapshot[field] = str(row[field]) or None
    for field in ("listed_at", "delisted_at"):
        value = str(row[field])
        snapshot[field] = date.fromisoformat(value) if value else None
    for field in PREPARED_FLOAT_FIELDS:
        value = float(row[field])
        snapshot[field] = value if np.isfinite(value) else None
    return trade_date, symbol, snapshot


class PreparedDatasetDayLoader:
    """Read-only day iterator over a prepared structured NumPy array."""

    def __init__(
        self,
        array: np.memmap,
        *,
        start_date: date,
        end_date: date,
        performance: dict[str, Any],
    ) -> None:
        self.array = array
        self.start_date = start_date
        self.end_date = end_date
        self.performance = performance
        self.rows_loaded = 0
        self.loaded_symbols: set[str] = set()
        self._history_sessions_by_instrument: dict[int, int] = {}

    def iter_days(self) -> Iterator[tuple[date, dict[str, dict[str, Any]]]]:
        from time import perf_counter

        current_date: date | None = None
        current: dict[str, dict[str, Any]] = {}
        decode_ms = 0.0
        grouping_ms = 0.0
        for row in self.array:
            raw_date = str(row["dt_ny"])
            if not raw_date:
                continue
            trade_date = date.fromisoformat(raw_date)
            if trade_date < self.start_date or trade_date > self.end_date:
                continue
            started = perf_counter()
            decoded_date, symbol, snapshot = decode_prepared_snapshot(row)
            instrument_id = int(snapshot["instrument_id"])
            history_sessions = self._history_sessions_by_instrument.get(instrument_id, 0) + 1
            self._history_sessions_by_instrument[instrument_id] = history_sessions
            snapshot["history_sessions"] = history_sessions
            decode_ms += (perf_counter() - started) * 1000.0
            if current_date is not None and decoded_date != current_date:
                self.performance["row_decode_ms"] += round(decode_ms, 3)
                self.performance["day_grouping_ms"] += round(grouping_ms, 3)
                decode_ms = 0.0
                grouping_ms = 0.0
                yield current_date, current
                current = {}
            started = perf_counter()
            current_date = decoded_date
            current[symbol] = snapshot
            self.rows_loaded += 1
            self.loaded_symbols.add(symbol)
            grouping_ms += (perf_counter() - started) * 1000.0
        if current_date is not None:
            self.performance["row_decode_ms"] += round(decode_ms, 3)
            self.performance["day_grouping_ms"] += round(grouping_ms, 3)
            yield current_date, current
