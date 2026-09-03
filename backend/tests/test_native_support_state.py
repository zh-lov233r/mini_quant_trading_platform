from __future__ import annotations

from datetime import date
import json
import unittest

import numpy as np

from src.services.native_backtest_service import _native_support_state
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
        self.support_resistance = support_resistance


class _FakeDataset:
    def __init__(self, integers):
        self.integers = integers


def _numpy_column(values):
    array = np.array(values, dtype=np.int32)
    array.setflags(write=False)
    return array


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
            "events": {
                "instrument_id": _numpy_column([11, 12, 11]),
                "symbol_id": _numpy_column([0, 1, 0]),
                "payload_json": [
                    json.dumps({"event": "zone_created"}),
                    json.dumps({"event": "zone_broken"}),
                    json.dumps({"event": "zone_retested"}),
                ],
            },
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
        self.assertEqual(state.symbols["11"].regime_versions, [])
        self.assertEqual(state.symbols["11"].instrument_id, 11)
        self.assertEqual(state.symbols["11"].symbol, "AAPL")
        self.assertEqual(
            state.symbols["11"].history, [{"dt_ny": date(2026, 7, 30)}]
        )

    def test_missing_support_resistance_payload(self):
        state = _native_support_state(
            _FakeResult(["AAPL", "MSFT"], None), self._dataset()
        )
        self.assertEqual(state.symbols["11"].events, [])
        self.assertEqual(state.symbols["12"].events, [])

    def test_reused_ticker_keeps_instrument_states_separate(self):
        dataset = self._dataset()
        dataset.integers[1, PREPARED_INTEGER_INDEX["symbol_id"]] = 0
        support = {
            "events": {
                "instrument_id": _numpy_column([11, 12]),
                "symbol_id": _numpy_column([0, 0]),
                "payload_json": [
                    json.dumps({"event": "old identity"}),
                    json.dumps({"event": "new identity"}),
                ],
            }
        }

        state = _native_support_state(_FakeResult(["SAME"], support), dataset)

        self.assertEqual(set(state.symbols), {"11", "12"})
        self.assertEqual(state.symbols["11"].events, [{"event": "old identity"}])
        self.assertEqual(state.symbols["12"].events, [{"event": "new identity"}])


if __name__ == "__main__":
    unittest.main()
