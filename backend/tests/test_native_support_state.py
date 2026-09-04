from __future__ import annotations

from datetime import date
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from src.services.native_support_state import NativeJson, NativeSupportState
from src.services.prepared_dataset_service import PREPARED_INTEGER_FIELDS
from src.services.backtest_engine import _native_support_state
from src.services.prepared_dataset_service import PREPARED_INTEGER_INDEX


class _FakeResult:
    """Mimics ``quant_kernel.KernelResult`` for the support/resistance columns.

    The native kernel exports ``symbol_id`` / ``instrument_id`` as read-only
    numpy views, so any boolean use of those columns raises
    ``ValueError: The truth value of an array with more than one element is
    ambiguous``.
    """

    def __init__(self, symbols, support_resistance):
        self.symbols = symbols
        self.support_resistance = None if support_resistance is None else {
            **{
                "events": _event_collection([], [], []),
                "zone_versions": {"instrument_id": _numpy_column([]), "symbol_id": _numpy_column([]), "payload_json": []},
                "regime_versions": {"instrument_id": _numpy_column([]), "symbol_id": _numpy_column([]), "payload_json": []},
            },
            **support_resistance,
        }


class _FakeDataset:
    def __init__(self, integers):
        self.integers = integers


def _numpy_column(values):
    array = np.array(values, dtype=np.int32)
    array.setflags(write=False)
    return array


def _event_collection(instrument_ids, symbol_ids, payloads):
    count = len(instrument_ids)
    event_types = []
    for index in range(count):
        payload = json.loads(payloads[index])
        event_types.append(str(payload.get("event_type") or payload.get("event") or "candidate"))
    return {
        "instrument_id": _numpy_column(instrument_ids),
        "symbol_id": _numpy_column(symbol_ids),
        "materialization_event": np.array(
            [event_type in {"touch", "invalidation"} for event_type in event_types],
            dtype=np.uint8,
        ),
        "event_date_ordinal": np.full(count, date(2026, 7, 31).toordinal(), dtype=np.int32),
        "event_type": event_types,
        "zone_key": [""] * count,
        "setup": [""] * count,
        "score": np.full(count, np.nan, dtype=np.float64),
        "posterior_sample_count": np.full(count, -1, dtype=np.int32),
        "lower_price": np.full(count, np.nan, dtype=np.float64),
        "upper_price": np.full(count, np.nan, dtype=np.float64),
        "payload_json": payloads,
    }


class NativeSupportStateTests(unittest.TestCase):
    maxDiff = None

    def _dataset(self):
        width = max(PREPARED_INTEGER_INDEX.values()) + 1
        rows = np.zeros((2, width), dtype=np.int64)
        rows[0, PREPARED_INTEGER_INDEX["instrument_id"]] = 11
        rows[0, PREPARED_INTEGER_INDEX["symbol_id"]] = 0
        rows[0, PREPARED_INTEGER_INDEX["dt_ordinal"]] = date(2026, 7, 30).toordinal()
        rows[1, PREPARED_INTEGER_INDEX["instrument_id"]] = 12
        rows[1, PREPARED_INTEGER_INDEX["symbol_id"]] = 1
        rows[1, PREPARED_INTEGER_INDEX["dt_ordinal"]] = date(2026, 7, 31).toordinal()
        return _FakeDataset(rows)

    def test_multi_element_numpy_columns_do_not_raise(self):
        support = {
            "events": _event_collection(
                [11, 12, 11],
                [0, 1, 0],
                [
                    json.dumps({"event": "zone_created"}),
                    json.dumps({"event": "zone_broken"}),
                    json.dumps({"event": "zone_retested"}),
                ],
            ),
            "zone_versions": {
                "instrument_id": _numpy_column([12]),
                "symbol_id": _numpy_column([1]),
                "payload_json": [json.dumps({"zone": "support"})],
            },
            "regime_versions": {
                "instrument_id": _numpy_column([]),
                "symbol_id": _numpy_column([]),
                "payload_json": [],
            },
        }
        state = _native_support_state(
            _FakeResult(["AAPL", "MSFT"], support), self._dataset()
        )
        self.assertEqual(len(state.symbols["11"].events), 2)
        self.assertEqual(len(state.symbols["12"].events), 1)
        self.assertEqual(len(state.symbols["12"].zone_versions), 1)
        self.assertEqual(list(state.symbols["11"].regime_versions), [])
        self.assertEqual(state.symbols["11"].instrument_id, 11)
        self.assertEqual(state.symbols["11"].symbol, "AAPL")
        self.assertEqual(
            list(state.symbols["11"].history), [{"dt_ny": date(2026, 7, 30)}]
        )

    def test_missing_support_resistance_payload(self):
        state = _native_support_state(
            _FakeResult(["AAPL", "MSFT"], None), self._dataset()
        )
        self.assertEqual(list(state.symbols["11"].events), [])
        self.assertEqual(list(state.symbols["12"].events), [])

    def test_reused_ticker_keeps_instrument_states_separate(self):
        dataset = self._dataset()
        dataset.integers[1, PREPARED_INTEGER_INDEX["symbol_id"]] = 0
        support = {
            "events": _event_collection(
                [11, 12],
                [0, 0],
                [
                    json.dumps({"event": "old identity"}),
                    json.dumps({"event": "new identity"}),
                ],
            )
        }

        state = _native_support_state(_FakeResult(["SAME"], support), dataset)

        self.assertEqual(set(state.symbols), {"11", "12"})
        self.assertEqual(
            json.loads(state.symbols["11"].events[0].payload.text),
            {"event": "old identity"},
        )
        self.assertEqual(
            json.loads(state.symbols["12"].events[0].payload.text),
            {"event": "new identity"},
        )


class NativeSupportStateLazyTests(unittest.TestCase):
    def _fixture(self):
        integers = np.zeros((4, len(PREPARED_INTEGER_FIELDS)), dtype=np.int64, order="F")
        integers[:, PREPARED_INTEGER_INDEX["instrument_id"]] = [1, 2, 1, 2]
        integers[:, PREPARED_INTEGER_INDEX["symbol_id"]] = [0, 0, 1, 0]
        integers[:, PREPARED_INTEGER_INDEX["dt_ordinal"]] = [date(2025, 1, 1).toordinal()] * 2 + [date(2025, 1, 3).toordinal()] * 2
        payloads = Mock()
        payloads.__len__ = Mock(return_value=3)
        payloads.__getitem__ = Mock(side_effect=['{"value":1}', '{"value":2}', '{"value":3}'])
        empty = {"instrument_id": np.array([], dtype=np.int64), "symbol_id": [], "payload_json": []}
        result = SimpleNamespace(symbols=["REUSED", "RENAMED"], support_resistance={
            "events": {
                "instrument_id": np.array([1, 1, 2]),
                "symbol_id": [1, 1, 0],
                "materialization_event": np.array([0, 0, 0], dtype=np.uint8),
                "event_date_ordinal": np.array([date(2025, 1, 1).toordinal()] * 3),
                "event_type": ["candidate"] * 3,
                "zone_key": [""] * 3,
                "setup": [""] * 3,
                "score": np.array([np.nan] * 3),
                "posterior_sample_count": np.array([-1] * 3),
                "lower_price": np.array([np.nan] * 3),
                "upper_price": np.array([np.nan] * 3),
                "payload_json": payloads,
            },
            "zone_versions": empty, "regime_versions": empty,
        })
        return result, SimpleNamespace(integers=integers), payloads

    def test_audit_decoding_is_lazy_and_preserves_identity_and_session_order(self):
        result, dataset, payloads = self._fixture()
        state = NativeSupportState(result, dataset)
        self.assertEqual(set(state.symbols), {"1", "2"})
        self.assertEqual(state.symbols["1"].symbol, "RENAMED")
        self.assertEqual(state.symbols["2"].symbol, "REUSED")
        self.assertEqual(len(state.symbols["1"].events), 2)
        payloads.__getitem__.assert_not_called()
        events_1 = list(state.symbols["1"].events)
        events_2 = list(state.symbols["2"].events)
        self.assertTrue(all(isinstance(event.payload, NativeJson) for event in events_1 + events_2))
        self.assertEqual([json.loads(event.payload.text) for event in events_1], [{"value": 1}, {"value": 2}])
        self.assertEqual([json.loads(event.payload.text) for event in events_2], [{"value": 3}])
        self.assertEqual(list(state.symbols["1"].history), [
            {"dt_ny": date(2025, 1, 1)}, {"dt_ny": date(2025, 1, 3)},
        ])

    def test_cancel_is_checked_before_decode_and_misaligned_columns_fail(self):
        result, dataset, payloads = self._fixture()
        cancel = Mock()
        state = NativeSupportState(result, dataset, cancel)
        cancel.side_effect = RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            next(iter(state.symbols["1"].events))
        payloads.__getitem__.assert_not_called()
        result.support_resistance["events"]["symbol_id"] = [1]
        with self.assertRaisesRegex(ValueError, "column lengths differ"):
            NativeSupportState(result, dataset)


if __name__ == "__main__":
    unittest.main()
