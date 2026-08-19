import unittest
from datetime import date

from backend.utils.check_market_data_quality import (
    DailyCount,
    choose_latest_complete_day,
    find_daily_count_shocks,
)


class MarketDataQualityHelpersTest(unittest.TestCase):
    def test_partial_latest_day_is_not_treated_as_complete(self):
        counts = [
            DailyCount(date(2026, 8, 14), 4200),
            DailyCount(date(2026, 8, 17), 4210),
            DailyCount(date(2026, 8, 18), 3500),
        ]

        latest, baseline = choose_latest_complete_day(counts)

        self.assertEqual(latest.trade_date, date(2026, 8, 17))
        self.assertEqual(baseline, 4205.0)

    def test_daily_count_shock_excludes_partial_day_after_cutoff(self):
        counts = [
            DailyCount(date(2026, 8, 13), 4200),
            DailyCount(date(2026, 8, 14), 3900),
            DailyCount(date(2026, 8, 17), 4200),
            DailyCount(date(2026, 8, 18), 3500),
        ]

        shocks = find_daily_count_shocks(counts, through=date(2026, 8, 17))

        self.assertEqual(len(shocks), 2)
        self.assertEqual(shocks[0]["current_date"], date(2026, 8, 14))
        self.assertTrue(all(item["current_date"] != date(2026, 8, 18) for item in shocks))


if __name__ == "__main__":
    unittest.main()
