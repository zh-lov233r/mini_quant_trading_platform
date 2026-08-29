from datetime import date
import unittest

from backend.utils.backfill_vwap_from_massive import build_stage_rows


class VwapBackfillTest(unittest.TestCase):
    def test_only_provider_values_are_staged_and_instrument_is_deduplicated(self):
        trade_date = date(2026, 8, 26)

        rows = build_stage_rows(
            [(1, "META"), (1, "FB"), (2, "MISSING"), (3, None)],
            {"META": 512.25, "FB": 200.0},
            trade_date,
        )

        self.assertEqual(rows, [(1, trade_date, 512.25)])


if __name__ == "__main__":
    unittest.main()
