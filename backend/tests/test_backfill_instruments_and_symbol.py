import unittest

from backend.utils.backfill_instruments_and_symbol import (
    ACTIVE_SYNC_ORDER,
    is_common_stock_reference,
    is_supported_common_stock,
    norm_item,
    should_update_symbol_history,
)


class InstrumentReferenceNormalizationTest(unittest.TestCase):
    def test_current_active_records_are_applied_after_historical_records(self):
        self.assertEqual(ACTIVE_SYNC_ORDER, ("false", "true"))

    def test_inactive_common_stock_without_figi_is_still_a_reference_match(self):
        row = norm_item(
            {
                "ticker": "mdv",
                "primary_exchange": "XNYS",
                "type": "CS",
                "market": "stocks",
                "locale": "us",
                "active": False,
                "cik": "0001645873",
                "delisted_utc": "2026-08-13T00:00:00Z",
            }
        )

        self.assertTrue(is_common_stock_reference(row))
        self.assertFalse(is_supported_common_stock(row))
        self.assertEqual(row["ticker"], "MDV")
        self.assertEqual(row["delisted_at"], "2026-08-13")

    def test_figi_backed_us_common_stock_is_supported(self):
        row = norm_item(
            {
                "ticker": "NVRI",
                "primary_exchange": "XNYS",
                "type": "CS",
                "market": "stocks",
                "locale": "us",
                "active": True,
                "share_class_figi": "BBG01YQ37BJ7",
            }
        )

        self.assertTrue(is_common_stock_reference(row))
        self.assertTrue(is_supported_common_stock(row))
        self.assertIsNone(row["delisted_at"])

    def test_stale_inactive_snapshot_does_not_touch_active_symbol_history(self):
        self.assertFalse(
            should_update_symbol_history(
                {"active": False},
                effective_is_active=True,
            )
        )
        self.assertTrue(
            should_update_symbol_history(
                {"active": False},
                effective_is_active=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
