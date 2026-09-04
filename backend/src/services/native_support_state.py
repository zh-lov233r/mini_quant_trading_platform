from __future__ import annotations

from collections.abc import Callable, Sequence
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import json
import math
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


@dataclass(frozen=True, slots=True)
class NativeJson:
    text: str


@dataclass(frozen=True, slots=True)
class NativeSupportEvent:
    event_date: date
    event_type: str
    zone_key: str | None
    setup: str | None
    score: float | None
    posterior_sample_count: int | None
    lower_price: float | None
    upper_price: float | None
    payload: NativeJson


class _EventRows(Sequence[NativeSupportEvent]):
    def __init__(self, columns: Any, indices: np.ndarray, check_cancel: Callable[[], None] | None):
        self.columns = columns
        self.indices = indices
        self.check_cancel = check_cancel

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _EventRows(self.columns, self.indices[index], self.check_cancel)
        if self.check_cancel is not None and index % 256 == 0:
            self.check_cancel()
        row = int(self.indices[index])
        optional_text = lambda value: str(value) or None
        optional_float = lambda value: None if math.isnan(float(value)) else float(value)
        sample_count = int(self.columns["posterior_sample_count"][row])
        return NativeSupportEvent(
            event_date=date.fromordinal(int(self.columns["event_date_ordinal"][row])),
            event_type=str(self.columns["event_type"][row]),
            zone_key=optional_text(self.columns["zone_key"][row]),
            setup=optional_text(self.columns["setup"][row]),
            score=optional_float(self.columns["score"][row]),
            posterior_sample_count=sample_count if sample_count >= 0 else None,
            lower_price=optional_float(self.columns["lower_price"][row]),
            upper_price=optional_float(self.columns["upper_price"][row]),
            payload=NativeJson(str(self.columns["payload_json"][row])),
        )


class _SessionRows(Sequence[dict[str, date]]):
    def __init__(self, ordinals: np.ndarray):
        self.ordinals = ordinals

    def __len__(self) -> int:
        return len(self.ordinals)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _SessionRows(self.ordinals[index])
        return {"dt_ny": date.fromordinal(int(self.ordinals[index]))}


class NativeSupportHistory:
    """Compact session identity retained after each prepared chunk is unmapped."""

    def __init__(self) -> None:
        self.chunks: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    def consume(self, dataset: Any) -> None:
        integers = dataset.integers
        self.chunks.append((
            np.array(integers[:, PREPARED_INTEGER_INDEX["instrument_id"]], copy=True),
            np.array(integers[:, PREPARED_INTEGER_INDEX["symbol_id"]], copy=True),
            np.array(integers[:, PREPARED_INTEGER_INDEX["dt_ordinal"]], copy=True),
        ))


@dataclass(slots=True)
class NativeSupportSymbol:
    instrument_id: int
    symbol: str
    history: Sequence[dict[str, date]]
    events: Sequence[dict[str, Any] | NativeSupportEvent] = ()
    materialization_events: Sequence[NativeSupportEvent] = ()
    zone_versions: Sequence[dict[str, Any]] = ()
    regime_versions: Sequence[dict[str, Any]] = ()


class NativeSupportState:
    """Read-only persistence view; no second, fully decoded audit tree."""

    def __init__(self, result: Any, dataset: Any, check_cancel: Callable[[], None] | None = None):
        self.symbols: dict[str, NativeSupportSymbol] = {}
        symbols = list(result.symbols)
        if isinstance(dataset, NativeSupportHistory):
            history_chunks = dataset.chunks
        else:
            datasets = dataset if isinstance(dataset, (list, tuple)) else [dataset]
            history_chunks = [
                (
                    chunk.integers[:, PREPARED_INTEGER_INDEX["instrument_id"]],
                    chunk.integers[:, PREPARED_INTEGER_INDEX["symbol_id"]],
                    chunk.integers[:, PREPARED_INTEGER_INDEX["dt_ordinal"]],
                )
                for chunk in datasets
            ]
        history_parts: dict[int, list[np.ndarray]] = defaultdict(list)
        latest_symbol_ids: dict[int, int] = {}
        for instrument_ids, symbol_ids, ordinals in history_chunks:
            order = np.argsort(instrument_ids, kind="stable")
            ordered_ids = instrument_ids[order]
            boundaries = np.r_[0, np.flatnonzero(np.diff(ordered_ids)) + 1, len(order)]
            for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
                rows = order[start:end]
                instrument_id = int(ordered_ids[start])
                history_parts[instrument_id].append(ordinals[rows])
                latest_symbol_ids[instrument_id] = int(symbol_ids[rows[-1]])
        for instrument_id in sorted(history_parts):
            if check_cancel is not None:
                check_cancel()
            symbol_id = latest_symbol_ids[instrument_id]
            self.symbols[str(instrument_id)] = NativeSupportSymbol(
                instrument_id, symbols[symbol_id],
                _SessionRows(np.concatenate(history_parts[instrument_id])),
            )

        support = result.support_resistance
        if support is None:
            return

        def attach_rows(
            key: str,
            collection: Any,
            indices: np.ndarray,
            attribute: str,
        ) -> None:
            if not len(indices):
                return
            ids = collection["instrument_id"]
            order = indices[np.argsort(ids[indices], kind="stable")]
            ordered_ids = ids[order]
            boundaries = np.r_[
                0,
                np.flatnonzero(np.diff(ordered_ids)) + 1,
                len(order),
            ]
            for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
                symbol = self.symbols[str(int(ordered_ids[start]))]
                setattr(
                    symbol,
                    attribute,
                    _EventRows(collection, order[start:end], check_cancel)
                    if key == "events"
                    else _JsonRows(collection["payload_json"], order[start:end], check_cancel),
                )

        for key in ("events", "zone_versions", "regime_versions"):
            collection = support[key]
            ids = collection["instrument_id"]
            symbol_ids = collection["symbol_id"]
            payloads = collection["payload_json"]
            lengths = [len(ids), len(symbol_ids), len(payloads)]
            if key == "events":
                lengths.extend(
                    len(collection[column])
                    for column in (
                        "event_date_ordinal",
                        "event_type",
                        "zone_key",
                        "setup",
                        "score",
                        "posterior_sample_count",
                        "lower_price",
                        "upper_price",
                        "materialization_event",
                    )
                )
            if len(set(lengths)) != 1:
                raise ValueError(f"native support {key} column lengths differ")
            if key == "events":
                shared = np.asarray(collection["materialization_event"], dtype=np.uint8)
                attach_rows(key, collection, np.flatnonzero(shared == 0), "events")
                attach_rows(
                    key,
                    collection,
                    np.flatnonzero(shared != 0),
                    "materialization_events",
                )
            else:
                attach_rows(key, collection, np.arange(len(ids)), key)
