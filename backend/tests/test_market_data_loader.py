from datetime import date
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from src.services.columnar_market_data_loader import (
    COPY_HEADER, WIRE_FIELDS, canonicalize_rows, encode_wire,
    shard_manifests, spool_copy, wire_values, read_setting,
)
from src.services.prepared_dataset_service import (
    PREPARED_DATE_SENTINEL, PREPARED_FLOAT_FIELDS, PreparedDatasetCache, prepared_dataset_key,
)
from src.services.backtest_engine import _coverage_start


def copy_row(identity, day, floats=None):
    values = [identity, day.toordinal(), 1735830000000000, *(floats or [np.nan] * len(PREPARED_FLOAT_FIELDS))]
    return struct.pack('!h', len(values)) + b''.join(
        struct.pack('!i', 8) + struct.pack('!q' if index < 3 else '!d', value)
        for index, value in enumerate(values)
    )


class ColumnarLoaderTests(unittest.TestCase):
    def test_fragmented_wire_nulls_symbols_and_day_major_roundtrip(self):
        first, second = date(2025, 1, 2), date(2025, 1, 3)
        floats = [float(index) for index in range(len(PREPARED_FLOAT_FIELDS))]
        content = COPY_HEADER + copy_row(1, first, floats) + copy_row(1, second) + copy_row(2, first) + b'\xff\xff'
        metadata = {identity: dict(symbol=f'S{identity}', asset_type='CS', exchange='XSHE',
                    listed=PREPARED_DATE_SENTINEL, delisted=PREPARED_DATE_SENTINEL, intervals=[])
                    for identity in (1, 2)}
        metadata[1]['intervals'] = [('OLD', first.toordinal(), first.toordinal()),
                                    ('NEW', second.toordinal(), date.max.toordinal())]
        with tempfile.TemporaryDirectory() as root:
            cache = PreparedDatasetCache(Path(root))
            def prepare(temporary):
                path = temporary / 'wire.bin'
                count = spool_copy([content[i:i+7] for i in range(0, len(content), 7)], path)
                self.assertEqual(count, 3)
                def writer(dataset):
                    encode_wire(dataset, path, metadata)
                    path.unlink()
                    return canonicalize_rows(dataset)
                return count, writer
            result = cache.build({'case': 'wire'}, prepare=prepare)
            np.testing.assert_array_equal(result.integers[:, 1], [1, 2, 1])
            np.testing.assert_array_equal(result.integers[:, 0], [0, 0, 1])
            np.testing.assert_array_equal(result.floats[0], floats)
            self.assertTrue(np.isnan(result.floats[1:]).all())
            self.assertEqual(result.sidecar['symbols'], ['OLD', 'S2', 'NEW'])
            self.assertEqual(result.sidecar['date_offsets'], [['2025-01-02', 0, 2], ['2025-01-03', 2, 1]])
            self.assertFalse(result.writeable)
            self.assertTrue(result.floats.flags.f_contiguous)
            self.assertIsNotNone(cache.open({'case': 'wire'}))

    def test_wire_rejects_corruption_and_incomplete_stream(self):
        content = COPY_HEADER + copy_row(1, date(2025, 1, 2)) + b'\xff\xff'
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'wire.bin'
            for bad in (b'bad', content[:-1], b'X' + content[1:]):
                with self.subTest(length=len(bad)), self.assertRaises(ValueError):
                    spool_copy([bad], path)
            malformed = bytearray(content)
            struct.pack_into('!i', malformed, len(COPY_HEADER) + 2, -1)
            count = spool_copy([malformed], path)
            with self.assertRaises(ValueError):
                wire_values(path, count)

    def test_empty_shard_is_cacheable_and_invalidated_with_parent(self):
        with tempfile.TemporaryDirectory() as root:
            parent = PreparedDatasetCache(Path(root))
            cache = PreparedDatasetCache(Path(root) / 'shards')
            result = cache.build({'empty': True}, row_count=0, writer=lambda dataset: {})
            self.assertEqual(len(result), 0)
            self.assertIsNotNone(cache.open({'empty': True}))
            self.assertEqual(parent.invalidate_all(), 1)
            self.assertIsNone(cache.open({'empty': True}))

    def test_shard_keys_reuse_years_and_unaffected_instrument_buckets(self):
        start, end = date(2025, 6, 1), date(2025, 7, 1)
        keys = lambda ids, end: {prepared_dataset_key(p) for p in shard_manifests(ids, start, end)}
        self.assertEqual(keys([1, 256], end), keys([1, 256], date(2025, 7, 2)))
        self.assertEqual(len(keys([1, 256], end) & keys([1, 2, 256], end)), 1)
        self.assertEqual(len(keys([1, 256], date(2026, 1, 1))), 4)

    def test_settings_and_warmup_boundaries(self):
        start = date(2025, 1, 1)
        self.assertEqual(_coverage_start('trend', start, None), start)
        self.assertEqual((start - _coverage_start('double_bottom', start, None)).days, 400)
        self.assertEqual((start - _coverage_start('trend', start, {'minHistorySessions': 200})).days, 400)
        with patch.dict('os.environ', {'BACKTEST_READ_WORKERS': '5'}), self.assertRaises(ValueError):
            read_setting('BACKTEST_READ_WORKERS', 4, 1, 4)

    def test_interval_ties_and_symbol_collision_are_not_silently_overwritten(self):
        day = date(2025, 1, 2)
        content = COPY_HEADER + copy_row(1, day) + copy_row(2, day) + b'\xff\xff'
        metadata = {identity: dict(symbol=f'S{identity}', asset_type='CS', exchange='XSHE',
                    listed=PREPARED_DATE_SENTINEL, delisted=PREPARED_DATE_SENTINEL,
                    intervals=[('OLDER', day.toordinal(), day.toordinal()),
                               ('SHARED', day.toordinal(), day.toordinal())]) for identity in (1, 2)}
        with tempfile.TemporaryDirectory() as root:
            cache = PreparedDatasetCache(Path(root))
            def prepare(temporary):
                path = temporary / 'wire.bin'
                count = spool_copy([content], path)
                def writer(dataset):
                    encode_wire(dataset, path, metadata)
                    self.assertEqual([dataset._symbols[int(i)] for i in dataset.integers[:, 4]], ['SHARED', 'SHARED'])
                    path.unlink()
                    canonicalize_rows(dataset)
                return count, writer
            with self.assertRaisesRegex(ValueError, 'multiple instruments'):
                cache.build({'case': 'collision'}, prepare=prepare)
            self.assertIsNone(cache.open({'case': 'collision'}))


if __name__ == '__main__':
    unittest.main()
