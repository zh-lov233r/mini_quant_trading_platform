from datetime import date
import unittest

from backend.utils.backfill_sic_from_massive import (
    SicCandidate,
    candidate_asof_date,
    details_match_instrument,
    normalize_sic_payload,
)


class SicBackfillTest(unittest.TestCase):
    def test_normalizes_sic_and_preserves_empty_values(self):
        self.assertEqual(
            normalize_sic_payload(
                {
                    "sic_code": " 7372 ",
                    "sic_description": " Services-Prepackaged Software ",
                    "locale": "us",
                    "primary_exchange": "XNAS",
                    "list_date": "1986-03-13",
                }
            ),
            {
                "sic_code": "7372",
                "sic_description": "Services-Prepackaged Software",
                "primary_exchange": "XNAS",
                "country": "US",
                "list_date": "1986-03-13",
            },
        )
        self.assertIsNone(normalize_sic_payload({})["sic_code"])

    def test_inactive_candidate_uses_last_known_asof_date(self):
        inactive = SicCandidate(1, "OLD", "FIGI", False, date(2020, 1, 2))
        active = SicCandidate(2, "NOW", "FIGI2", True, date(2020, 1, 2))
        self.assertEqual(candidate_asof_date(inactive), date(2020, 1, 2))
        self.assertIsNone(candidate_asof_date(active))

    def test_figi_mismatch_is_rejected(self):
        self.assertTrue(details_match_instrument({"share_class_figi": "F1"}, "F1"))
        self.assertFalse(details_match_instrument({"share_class_figi": "F2"}, "F1"))


if __name__ == "__main__":
    unittest.main()
