from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
import json
from typing import Any

import numpy as np

from src.services.prepared_dataset_service import PREPARED_INTEGER_INDEX


class _JsonRows(Sequence[dict[str, Any]]):
    """Decode one audit row at a time, retaining the native column's owner."""

    def __init__(self, payloads: Any, indices: np.ndarray, check_cancel: Callable[[], None] | None):
        self.payloads = payloads
        self.indices = indices
        self.check_cancel = check_cancel

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _JsonRows(self.payloads, self.indices[index], self.check_cancel)
        if self.check_cancel is not None and index % 256 == 0:
            self.check_cancel()
        return json.loads(self.payloads[self.indices[index]])


class _SessionRows(Sequence[dict[str, date]]):
    def __init__(self, ordinals: np.ndarray):
        self.ordinals = ordinals

    def __len__(self) -> int:
        return len(self.ordinals)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _SessionRows(self.ordinals[index])
        return {"dt_ny": date.fromordinal(int(self.ordinals[index]))}


@dataclass(slots=True)
class NativeSupportSymbol:
    instrument_id: int
    symbol: str
    history: Sequence[dict[str, date]]
    events: Sequence[dict[str, Any]] = ()
    zone_versions: Sequence[dict[str, Any]] = ()
    regime_versions: Sequence[dict[str, Any]] = ()


class NativeSupportState:
    """Read-only persistence view; no second, fully decoded audit tree."""

    def __init__(self, result: Any, dataset: Any, check_cancel: Callable[[], None] | None = None):
        self.symbols: dict[str, NativeSupportSymbol] = {}
        symbols = list(result.symbols)
        integers = dataset.integers
        instrument_ids = integers[:, PREPARED_INTEGER_INDEX["instrument_id"]]
        order = np.argsort(instrument_ids, kind="stable")
        ordered_ids = instrument_ids[order]
        boundaries = np.r_[0, np.flatnonzero(np.diff(ordered_ids)) + 1, len(order)]
        for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
            if check_cancel is not None:
                check_cancel()
            rows = order[start:end]
            instrument_id = int(ordered_ids[start])
            symbol_id = int(integers[rows[-1], PREPARED_INTEGER_INDEX["symbol_id"]])
            self.symbols[str(instrument_id)] = NativeSupportSymbol(
                instrument_id, symbols[symbol_id],
                _SessionRows(integers[rows, PREPARED_INTEGER_INDEX["dt_ordinal"]]),
            )

        support = result.support_resistance
        if support is None:
            return
        for key in ("events", "zone_versions", "regime_versions"):
            collection = support[key]
            ids = collection["instrument_id"]
            symbol_ids = collection["symbol_id"]
            payloads = collection["payload_json"]
            if not len(ids) == len(symbol_ids) == len(payloads):
                raise ValueError(f"native support {key} column lengths differ")
            order = np.argsort(ids, kind="stable")
            ordered_ids = ids[order]
            boundaries = np.r_[0, np.flatnonzero(np.diff(ordered_ids)) + 1, len(ids)]
            for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
                if start == end:
                    continue
                indices = order[start:end]
                symbol = self.symbols[str(int(ordered_ids[start]))]
                setattr(symbol, key, _JsonRows(payloads, indices, check_cancel))
