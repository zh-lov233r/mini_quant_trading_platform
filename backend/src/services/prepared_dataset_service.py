from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
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


PREPARED_DATASET_SCHEMA_VERSION = "v3"
PREPARED_INTEGER_FIELDS = (
    "session_index", "instrument_id", "ts_us", "dt_ordinal", "symbol_id",
    "asset_type_id", "exchange_id", "listed_ordinal", "delisted_ordinal",
)
PREPARED_FLOAT_FIELDS = (
    "open", "high", "low", "close", "close_unadjusted", "volume", "atr_14",
    "volume_sma_20", "dollar_volume_20", "ret_20d", "ret_60d", "sma_10",
    "sma_20", "sma_50", "sma_100", "sma_200", "ema_12", "ema_15", "ema_20",
    "ema_50", "rsi_2", "rsi_5", "rsi_14", "zscore_5", "zscore_10", "zscore_20",
    "prev_sma_10", "prev_sma_20", "prev_sma_50", "prev_sma_100", "prev_sma_200",
    "prev_ema_12", "prev_ema_15", "prev_ema_20", "prev_ema_50",
)
PREPARED_INTEGER_INDEX = {name: index for index, name in enumerate(PREPARED_INTEGER_FIELDS)}
PREPARED_FLOAT_INDEX = {name: index for index, name in enumerate(PREPARED_FLOAT_FIELDS)}
PREPARED_DATE_SENTINEL = np.iinfo(np.int64).min


class PreparedDatasetDataChangedError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedDataset:
    """Two Fortran-order memmaps and dictionary sidecars for the v3 dataset."""

    integers: np.memmap
    floats: np.memmap
    sidecar: dict[str, Any]
    _symbols: list[str] = field(default_factory=list)
    _asset_types: list[str] = field(default_factory=list)
    _exchanges: list[str] = field(default_factory=list)
    _symbol_ids: dict[str, int] = field(default_factory=dict)
    _asset_type_ids: dict[str, int] = field(default_factory=dict)
    _exchange_ids: dict[str, int] = field(default_factory=dict)
    _session_by_ordinal: dict[int, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.integers.shape[0])

    @property
    def writeable(self) -> bool:
        return bool(self.integers.flags.writeable or self.floats.flags.writeable)

    def mapping_sidecar(self) -> dict[str, list[str]]:
        return {
            "symbols": list(self._symbols),
            "asset_types": list(self._asset_types),
            "exchanges": list(self._exchanges),
        }

    @classmethod
    def opened(
        cls,
        integers: np.memmap,
        floats: np.memmap,
        sidecar: dict[str, Any],
    ) -> PreparedDataset:
        symbols = [str(value) for value in sidecar.get("symbols") or []]
        asset_types = [str(value) for value in sidecar.get("asset_types") or []]
        exchanges = [str(value) for value in sidecar.get("exchanges") or []]
        return cls(
            integers,
            floats,
            sidecar,
            symbols,
            asset_types,
            exchanges,
            {value: index for index, value in enumerate(symbols)},
            {value: index for index, value in enumerate(asset_types)},
            {value: index for index, value in enumerate(exchanges)},
        )

    @staticmethod
    def _dictionary_id(value: Any, values: list[str], mapping: dict[str, int]) -> int:
        normalized = str(value or "")
        existing = mapping.get(normalized)
        if existing is not None:
            return existing
        index = len(values)
        values.append(normalized)
        mapping[normalized] = index
        return index

    def encode(self, index: int, snapshot: dict[str, Any]) -> None:
        trade_date = snapshot.get("dt_ny")
        if not isinstance(trade_date, date):
            raise ValueError("prepared snapshot requires dt_ny")
        trade_ordinal = trade_date.toordinal()
        session_index = self._session_by_ordinal.setdefault(
            trade_ordinal,
            len(self._session_by_ordinal),
        )
        row = self.integers[index]
        row[PREPARED_INTEGER_INDEX["session_index"]] = session_index
        row[PREPARED_INTEGER_INDEX["instrument_id"]] = int(snapshot["instrument_id"])
        timestamp = snapshot.get("ts")
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp_us = int(timestamp.timestamp() * 1_000_000)
        else:
            timestamp_us = 0
        row[PREPARED_INTEGER_INDEX["ts_us"]] = timestamp_us
        row[PREPARED_INTEGER_INDEX["dt_ordinal"]] = trade_ordinal
        row[PREPARED_INTEGER_INDEX["symbol_id"]] = self._dictionary_id(
            str(snapshot.get("symbol") or "").upper(), self._symbols, self._symbol_ids
        )
        row[PREPARED_INTEGER_INDEX["asset_type_id"]] = self._dictionary_id(
            snapshot.get("asset_type"), self._asset_types, self._asset_type_ids
        )
        row[PREPARED_INTEGER_INDEX["exchange_id"]] = self._dictionary_id(
            snapshot.get("exchange"), self._exchanges, self._exchange_ids
        )
        for name in ("listed", "delisted"):
            value = snapshot.get(f"{name}_at")
            row[PREPARED_INTEGER_INDEX[f"{name}_ordinal"]] = (
                value.toordinal() if isinstance(value, date) else PREPARED_DATE_SENTINEL
            )
        for name, column in PREPARED_FLOAT_INDEX.items():
            value = snapshot.get(name)
            self.floats[index, column] = float(value) if value is not None else np.nan


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
    return {
        "loader_schema_version": PREPARED_DATASET_SCHEMA_VERSION,
        "data_fingerprint": data_fingerprint["sha256"],
        "row_count": int(data_fingerprint["rowCount"]),
        "instrument_ids": sorted(int(value) for value in data_fingerprint["instrumentIds"]),
        "date_range": [data_fingerprint["startDate"], data_fingerprint["endDate"]],
        "fingerprint_request_range": [
            requested_date_range[0].isoformat(), requested_date_range[1].isoformat()
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
    """Atomic fingerprint-addressed columnar cache; v2 files remain untouched."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_cache_root()).resolve()

    def _paths(self, key: str) -> tuple[Path, Path]:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("prepared dataset key must be a lowercase sha256")
        return self.root / f"{key}.v3", self.root / f"{key}.lock"

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

    @staticmethod
    def _remove_directory(directory: Path) -> None:
        if not directory.exists():
            return
        for name in ("integers.npy", "floats.npy", "metadata.json"):
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()

    @staticmethod
    def _normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            **manifest,
            "loader_schema_version": manifest.get(
                "loader_schema_version", PREPARED_DATASET_SCHEMA_VERSION
            ),
        }

    def open(self, manifest: dict[str, Any]) -> PreparedDataset | None:
        normalized = self._normalize_manifest(manifest)
        key = prepared_dataset_key(normalized)
        directory, _ = self._paths(key)
        try:
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            if metadata.get("key") != key or metadata.get("manifest") != normalized:
                return None
            integers = np.load(directory / "integers.npy", mmap_mode="r", allow_pickle=False)
            floats = np.load(directory / "floats.npy", mmap_mode="r", allow_pickle=False)
            rows = int(metadata["row_count"])
            if (
                integers.dtype != np.dtype("<i8")
                or floats.dtype != np.dtype("<f8")
                or tuple(integers.shape) != (rows, len(PREPARED_INTEGER_FIELDS))
                or tuple(floats.shape) != (rows, len(PREPARED_FLOAT_FIELDS))
                or not np.isfortran(integers)
                or not np.isfortran(floats)
                or metadata.get("integer_fields") != list(PREPARED_INTEGER_FIELDS)
                or metadata.get("float_fields") != list(PREPARED_FLOAT_FIELDS)
            ):
                return None
            integers.flags.writeable = False
            floats.flags.writeable = False
            return PreparedDataset.opened(integers, floats, metadata.get("sidecar") or {})
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def metadata(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        normalized = self._normalize_manifest(manifest)
        key = prepared_dataset_key(normalized)
        directory, _ = self._paths(key)
        try:
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return metadata if metadata.get("key") == key and metadata.get("manifest") == normalized else None

    def build(
        self,
        manifest: dict[str, Any],
        *,
        row_count: int,
        writer: Callable[[PreparedDataset], dict[str, Any] | None],
    ) -> PreparedDataset:
        normalized = self._normalize_manifest(manifest)
        key = prepared_dataset_key(normalized)
        directory, lock_path = self._paths(key)
        with self._lock(lock_path):
            existing = self.open(normalized)
            if existing is not None:
                return existing
            temporary = self.root / f".{key}.{uuid4().hex}.v3"
            temporary.mkdir()
            try:
                integers = np.lib.format.open_memmap(
                    temporary / "integers.npy", mode="w+", dtype="<i8",
                    shape=(row_count, len(PREPARED_INTEGER_FIELDS)), fortran_order=True,
                )
                floats = np.lib.format.open_memmap(
                    temporary / "floats.npy", mode="w+", dtype="<f8",
                    shape=(row_count, len(PREPARED_FLOAT_FIELDS)), fortran_order=True,
                )
                integers[:] = PREPARED_DATE_SENTINEL
                floats[:] = np.nan
                dataset = PreparedDataset(integers, floats, {})
                supplied_sidecar = writer(dataset) or {}
                sidecar = {**dataset.mapping_sidecar(), **supplied_sidecar}
                integers.flush()
                floats.flush()
                (temporary / "metadata.json").write_text(
                    json.dumps(
                        {
                            "key": key,
                            "manifest": normalized,
                            "row_count": row_count,
                            "integer_fields": list(PREPARED_INTEGER_FIELDS),
                            "float_fields": list(PREPARED_FLOAT_FIELDS),
                            "storage_order": "column_major_fortran",
                            "sidecar": sidecar,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                del dataset, integers, floats
                if directory.exists():
                    self._remove_directory(directory)
                os.replace(temporary, directory)
            finally:
                if temporary.exists():
                    self._remove_directory(temporary)
            opened = self.open(normalized)
            if opened is None:
                raise RuntimeError("prepared dataset cache failed validation after atomic publish")
            return opened

    def cleanup(self, manifest: dict[str, Any], *, active_lease_count: int) -> bool:
        if active_lease_count > 0:
            return False
        normalized = self._normalize_manifest(manifest)
        directory, lock_path = self._paths(prepared_dataset_key(normalized))
        with self._lock(lock_path):
            self._remove_directory(directory)
        lock_path.unlink(missing_ok=True)
        return True

    def cleanup_if_unused(self, db: Session, manifest: dict[str, Any]) -> bool:
        normalized = self._normalize_manifest(manifest)
        key = prepared_dataset_key(normalized)
        active_lease_count = int(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM backtest_jobs
                    WHERE status IN ('queued', 'running')
                      AND payload -> 'prepared_dataset' ->> 'key' = :key
                    """
                ),
                {"key": key},
            ).scalar_one()
        )
        return self.cleanup(normalized, active_lease_count=active_lease_count)


def encode_prepared_snapshot(dataset: PreparedDataset, index: int, snapshot: dict[str, Any]) -> None:
    dataset.encode(index, snapshot)


def decode_prepared_snapshot(
    dataset: PreparedDataset,
    index: int,
) -> tuple[date, str, dict[str, Any]]:
    integer_row = dataset.integers[index]
    float_row = dataset.floats[index]
    trade_date = date.fromordinal(int(integer_row[PREPARED_INTEGER_INDEX["dt_ordinal"]]))
    symbol = dataset._symbols[int(integer_row[PREPARED_INTEGER_INDEX["symbol_id"]])]
    timestamp_us = int(integer_row[PREPARED_INTEGER_INDEX["ts_us"]])
    snapshot: dict[str, Any] = {
        "instrument_id": int(integer_row[PREPARED_INTEGER_INDEX["instrument_id"]]),
        "symbol": symbol,
        "dt_ny": trade_date,
        "ts": datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc),
        "asset_type": dataset._asset_types[
            int(integer_row[PREPARED_INTEGER_INDEX["asset_type_id"]])
        ] or None,
        "exchange": dataset._exchanges[
            int(integer_row[PREPARED_INTEGER_INDEX["exchange_id"]])
        ] or None,
        "position": 0.0,
        "avg_entry_price": None,
        "entry_trade_date": None,
        "entry_signal_features": None,
        "position_holding_days": None,
        "recent_bars": [],
    }
    for name in ("listed", "delisted"):
        ordinal = int(integer_row[PREPARED_INTEGER_INDEX[f"{name}_ordinal"]])
        snapshot[f"{name}_at"] = date.fromordinal(ordinal) if ordinal != PREPARED_DATE_SENTINEL else None
    for name, column in PREPARED_FLOAT_INDEX.items():
        value = float(float_row[column])
        snapshot[name] = value if np.isfinite(value) else None
    return trade_date, symbol, snapshot


class PreparedDatasetDayLoader:
    def __init__(
        self,
        dataset: PreparedDataset,
        *,
        start_date: date,
        end_date: date,
        performance: dict[str, Any],
    ) -> None:
        self.dataset = dataset
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
        for index in range(len(self.dataset)):
            ordinal = int(self.dataset.integers[index, PREPARED_INTEGER_INDEX["dt_ordinal"]])
            if ordinal == PREPARED_DATE_SENTINEL:
                continue
            trade_date = date.fromordinal(ordinal)
            if trade_date < self.start_date or trade_date > self.end_date:
                continue
            started = perf_counter()
            decoded_date, symbol, snapshot = decode_prepared_snapshot(self.dataset, index)
            instrument_id = int(snapshot["instrument_id"])
            history_sessions = self._history_sessions_by_instrument.get(instrument_id, 0) + 1
            self._history_sessions_by_instrument[instrument_id] = history_sessions
            snapshot["history_sessions"] = history_sessions
            decode_ms += (perf_counter() - started) * 1000.0
            if current_date is not None and decoded_date != current_date:
                self.performance["row_decode_ms"] += round(decode_ms, 3)
                self.performance["day_grouping_ms"] += round(grouping_ms, 3)
                decode_ms = grouping_ms = 0.0
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
