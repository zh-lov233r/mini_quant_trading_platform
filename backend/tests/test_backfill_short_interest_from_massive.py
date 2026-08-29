from datetime import date
import unittest

from backend.utils.backfill_short_interest_from_massive import (
    RESOLVE_SQL,
    STAGE_SQL,
    UPSERT_SQL,
    normalize_item,
)


class ShortInterestBackfillTest(unittest.TestCase):
    def test_normalizes_valid_fact_and_rejects_negative_values(self):
        row = normalize_item(
            {
                "ticker": " meta ",
                "settlement_date": "2026-08-14",
                "short_interest": 123,
                "avg_daily_volume": 100,
                "days_to_cover": 1.23,
            }
        )
        self.assertEqual(row[:5], ("META", date(2026, 8, 14), 123, 100, 1.23))
        self.assertIsNone(
            normalize_item(
                {"ticker": "META", "settlement_date": "2026-08-14", "short_interest": -1}
            )
        )

    def test_mapping_is_point_in_time_and_upsert_is_idempotent(self):
        self.assertNotIn("short_interest_resolved", STAGE_SQL)
        self.assertIn("sh.valid_from <= stage.settlement_date", RESOLVE_SQL)
        self.assertNotIn("is_active", RESOLVE_SQL)
        self.assertIn("count(DISTINCT ROW(short_interest", RESOLVE_SQL)
        self.assertIn("GROUP BY instrument_id, settlement_date", RESOLVE_SQL)
        self.assertIn("FROM short_interest_dedup", UPSERT_SQL)
        self.assertIn("ON CONFLICT (instrument_id, settlement_date, vendor_source)", UPSERT_SQL)


if __name__ == "__main__":
    unittest.main()
