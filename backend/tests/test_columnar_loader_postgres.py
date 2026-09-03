"""Opt-in PostgreSQL contract test; temporary tables are rolled back, never source tables."""
from datetime import date, datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import psycopg
from psycopg import sql

from src.services.columnar_market_data_loader import (
    FEATURE_FIELDS, FEATURE_RANGE_SQL, PREVIOUS_FIELDS, spool_copy, wire_values,
)
from src.services.prepared_dataset_service import PREPARED_FLOAT_INDEX


@unittest.skipUnless(os.getenv('TEST_POSTGRESQL_URL'), 'set TEST_POSTGRESQL_URL for isolated temporary-table contract test')
class PostgresColumnarContractTests(unittest.TestCase):
    def test_exact_seed_missing_bar_nulls_and_binary_conversion(self):
        with psycopg.connect(os.environ['TEST_POSTGRESQL_URL']) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL('CREATE TEMP TABLE daily_features (instrument_id bigint, dt_ny date, {}) ON COMMIT DROP').format(
                        sql.SQL(',').join(sql.SQL('{} double precision').format(sql.Identifier(name)) for name in FEATURE_FIELDS)))
                    cursor.execute('CREATE TEMP TABLE eod_bars (instrument_id bigint, dt_ny date, ts_utc timestamptz, open_fa float8, open_u float8, high_fa float8, high_u float8, low_fa float8, low_u float8, close_fa float8, close_u float8, volume bigint) ON COMMIT DROP')
                    # Instrument 1's exact predecessor is years before the window.
                    # Jan 3 has a feature but no bar; Jan 4 must still use it.
                    for identity, day, value in [(1,'2020-01-01',10),(1,'2025-01-02',20),(1,'2025-01-03',30),(1,'2025-01-04',40),(2,'2025-01-02',50)]:
                        cursor.execute(sql.SQL('INSERT INTO daily_features (instrument_id,dt_ny,{}) VALUES (%s,%s,{})').format(
                            sql.SQL(',').join(map(sql.Identifier, PREVIOUS_FIELDS)),
                            sql.SQL(',').join(sql.Placeholder() for _ in PREVIOUS_FIELDS)),
                            (identity,day,*([value]*len(PREVIOUS_FIELDS))))
                    for identity, day in [(1,date(2025,1,2)),(1,date(2025,1,4)),(2,date(2025,1,2))]:
                        timestamp=datetime(day.year,day.month,day.day,21,0,0,123456,tzinfo=timezone.utc)
                        cursor.execute('INSERT INTO eod_bars (instrument_id,dt_ny,ts_utc,open_u,close_fa,close_u,volume) VALUES (%s,%s,%s,2,3,4,123456789)', (identity,day,timestamp))
                    with tempfile.TemporaryDirectory() as root:
                        path=Path(root)/'wire.bin'
                        with cursor.copy('COPY ('+FEATURE_RANGE_SQL+') TO STDOUT (FORMAT BINARY)',
                                         {'instrument_ids':[1,2],'start_date':date(2025,1,2),'end_date':date(2025,1,4)}) as stream:
                            count=spool_copy(stream,path)
                        self.assertEqual(count,3)
                        ints,floats=wire_values(path,count)
                        for name in PREVIOUS_FIELDS:
                            np.testing.assert_equal(floats[:,PREPARED_FLOAT_INDEX['prev_'+name]], [10,30,np.nan])
                        np.testing.assert_equal(floats[:,PREPARED_FLOAT_INDEX['open']], [2,2,2])
                        np.testing.assert_equal(floats[:,PREPARED_FLOAT_INDEX['close']], [3,3,3])
                        np.testing.assert_equal(floats[:,PREPARED_FLOAT_INDEX['close_unadjusted']], [4,4,4])
                        self.assertEqual(int(ints[0,2]),int(datetime(2025,1,2,21,0,0,123456,tzinfo=timezone.utc).timestamp()*1_000_000))
            finally:
                connection.rollback()


if __name__ == '__main__':
    unittest.main()
