from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

import numpy as np


PREPARED_DATASET_SCHEMA_VERSION = "v1"


def prepared_dataset_key(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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

    def open(self, manifest: dict[str, Any]) -> np.memmap | None:
        key = prepared_dataset_key(manifest)
        data_path, metadata_path, _ = self._paths(key)
        if not data_path.exists() or not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("key") != key or metadata.get("manifest") != manifest:
                return None
            array = np.load(data_path, mmap_mode="r", allow_pickle=False)
            if list(array.shape) != metadata.get("shape") or array.dtype.descr != [
                tuple(item) for item in metadata.get("dtype", [])
            ]:
                return None
            array.flags.writeable = False
            return array
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def build(
        self,
        manifest: dict[str, Any],
        *,
        shape: tuple[int, ...],
        dtype: np.dtype[Any],
        writer: Callable[[np.memmap], None],
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
            existing = self.open(normalized_manifest)
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
                writer(array)
                array.flush()
                del array
                metadata = {
                    "key": key,
                    "manifest": normalized_manifest,
                    "shape": list(shape),
                    "dtype": [list(item) for item in np.dtype(dtype).descr],
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
            opened = self.open(normalized_manifest)
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
