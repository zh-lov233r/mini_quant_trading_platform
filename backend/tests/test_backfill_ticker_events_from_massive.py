from datetime import date
import unittest

from backend.utils.backfill_ticker_events_from_massive import (
    InstrumentCandidate,
    NormalizedEvent,
    build_intervals,
    details_match_candidate,
    inactive_terminal_valid_to,
    normalize_events,
    print_limited_warning,
)
from unittest.mock import patch


class TickerEventBackfillTest(unittest.TestCase):
    def event(self, when: str, ticker: str, exchange: str = "XNAS") -> NormalizedEvent:
        return NormalizedEvent(date.fromisoformat(when), ticker, {}, exchange=exchange)

    def test_meta_chain_builds_exact_nonoverlapping_intervals(self):
        intervals, reason = build_intervals(
            [self.event("2012-05-18", "FB"), self.event("2022-06-09", "META")],
            "META",
        )
        self.assertIsNone(reason)
        self.assertEqual(intervals[0].valid_to, date(2022, 6, 8))
        self.assertEqual(intervals[1].valid_from, date(2022, 6, 9))
        self.assertIsNone(intervals[1].valid_to)

    def test_inactive_chain_closes_at_delisting_boundary(self):
        candidate = InstrumentCandidate(
            1,
            "OLD",
            "XNAS",
            "COMP",
            "SHARE",
            date(2020, 1, 2),
            is_active=False,
            delisted_at=date(2024, 6, 10),
            last_bar=date(2024, 6, 7),
        )
        terminal = inactive_terminal_valid_to(candidate, date(2020, 1, 2))
        intervals, reason = build_intervals(
            [self.event("2020-01-02", "OLD")],
            "OLD",
            terminal_valid_to=terminal,
        )

        self.assertIsNone(reason)
        self.assertEqual(intervals[0].valid_to, date(2024, 6, 9))

    def test_incomplete_chain_and_conflicting_event_date_stay_unresolved(self):
        intervals, reason = build_intervals([self.event("2003-01-01", "BRK")], "BRK.A")
        self.assertEqual(intervals, [])
        self.assertIn("canonical", reason)

        raw = [
            {"type": "ticker_change", "date": "2020-01-01", "ticker_change": {"ticker": "AAA"}},
            {"type": "ticker_change", "date": "2020-01-01", "ticker_change": {"ticker": "BBB"}},
        ]
        events = normalize_events(raw)
        intervals, reason = build_intervals(
            [NormalizedEvent(e.event_date, e.ticker, e.vendor_payload, exchange="XNYS") for e in events],
            "BBB",
        )
        self.assertEqual(intervals, [])
        self.assertIn("multiple tickers", reason)

        intervals, reason = build_intervals(
            [
                self.event("2010-01-01", "AAA"),
                self.event("2015-01-01", "BBB"),
                self.event("2020-01-01", "AAA"),
            ],
            "AAA",
        )
        self.assertEqual(intervals, [])
        self.assertIn("reused", reason)

    def test_figi_validation_rejects_cross_security_match(self):
        candidate = InstrumentCandidate(1, "META", "XNAS", "COMP", "SHARE", None)
        self.assertTrue(details_match_candidate({"composite_figi": "COMP", "share_class_figi": "SHARE"}, candidate))
        self.assertFalse(details_match_candidate({"composite_figi": "OTHER", "share_class_figi": "SHARE"}, candidate))

    def test_warning_output_is_sampled(self):
        with patch("builtins.print") as print_mock:
            print_limited_warning("AAA", "one", number=1, limit=1)
            print_limited_warning("BBB", "two", number=2, limit=1)
            print_limited_warning("CCC", "three", number=3, limit=1)
        self.assertEqual(print_mock.call_count, 2)

    @patch("backend.utils.backfill_ticker_events_from_massive.fetch_json")
    def test_missing_event_day_exchange_uses_interval_end_with_figi_revalidation(
        self, fetch_json_mock
    ):
        from backend.utils.backfill_ticker_events_from_massive import _validate_events

        candidate = InstrumentCandidate(1, "META", "XNAS", "COMP", "SHARE", None)
        events = [
            self.event("2012-05-18", "FB", exchange=None),
            self.event("2022-06-09", "META", exchange=None),
        ]
        fetch_json_mock.side_effect = [
            {"results": {"composite_figi": "COMP", "share_class_figi": "SHARE"}},
            {
                "results": {
                    "composite_figi": "COMP",
                    "share_class_figi": "SHARE",
                    "primary_exchange": "XNAS",
                }
            },
            {
                "results": {
                    "composite_figi": "COMP",
                    "share_class_figi": "SHARE",
                    "primary_exchange": "XNAS",
                }
            },
        ]

        validated, reason = _validate_events("key", candidate, events)

        self.assertIsNone(reason)
        self.assertEqual([item.exchange for item in validated], ["XNAS", "XNAS"])
        self.assertEqual(
            fetch_json_mock.call_args_list[1].kwargs["params"]["date"],
            "2022-06-08",
        )


if __name__ == "__main__":
    unittest.main()
