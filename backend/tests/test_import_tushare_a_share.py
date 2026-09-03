from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from backend.utils.import_tushare_a_share import (
    TushareClient,
    TushareError,
    _daily_rows,
    normalize_bar,
    normalize_index,
    normalize_instrument,
)
from backend.utils.backfill_adjusted_prices import IDENTITY_UPDATE_SQL


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def post(self, url, *, json, timeout):
        self.request = (url, json, timeout)
        return _Response(self.payload)


class _Client:
    def __init__(self):
        self.params = []

    def query(self, api_name, *, params, fields):
        self.params.append((api_name, params))
        if api_name == "daily":
            return [
                {"ts_code": "000001.SZ"},
                {"ts_code": "600000.SH"},
                {"ts_code": "920000.BJ"},
            ]
        return [
            {"ts_code": "000001.SZ", "adj_factor": 1.0},
            {"ts_code": "600000.SH", "adj_factor": 2.0},
            {"ts_code": "920000.BJ", "adj_factor": 3.0},
        ]


class TushareAShareImportTests(unittest.TestCase):
    def test_multiple_selected_symbols_are_filtered_locally(self) -> None:
        client = _Client()

        daily, factors = _daily_rows(
            client,  # type: ignore[arg-type]
            date(2026, 1, 5),
            {"000001.SZ", "600000.SH"},
        )

        self.assertEqual(
            client.params,
            [
                ("daily", {"trade_date": "20260105"}),
                ("adj_factor", {"trade_date": "20260105"}),
            ],
        )
        self.assertEqual([row["ts_code"] for row in daily], ["000001.SZ", "600000.SH"])
        self.assertEqual(factors, {"000001.SZ": 1.0, "600000.SH": 2.0})

    def test_generic_adjustment_backfill_preserves_tushare_factors(self) -> None:
        self.assertIn("COALESCE(vendor, '') <> 'tushare'", IDENTITY_UPDATE_SQL)

    def test_normalizes_a_share_identity_without_losing_exchange_suffix(self) -> None:
        row = normalize_instrument(
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "exchange": "SZSE",
                "curr_type": "CNY",
                "list_status": "L",
                "list_date": "19910403",
                "delist_date": None,
            }
        )

        self.assertEqual(row.vendor_key, "TUSHARE:000001.SZ")
        self.assertEqual(row.ts_code, "000001.SZ")
        self.assertEqual(row.exchange, "XSHE")
        self.assertEqual(row.currency, "CNY")
        self.assertEqual(row.listed_at, date(1991, 4, 3))
        self.assertTrue(row.is_active)

    def test_normalizes_a_share_benchmark_index(self) -> None:
        row = normalize_index(
            {
                "ts_code": "000001.SH",
                "name": "上证指数",
                "market": "SSE",
                "list_date": "19910715",
                "exp_date": None,
            }
        )

        self.assertEqual(row.vendor_key, "TUSHARE_INDEX:000001.SH")
        self.assertEqual(row.exchange, "XSHG")
        self.assertEqual(row.asset_type, "INDEX")
        self.assertEqual(row.market, "indices")
        self.assertTrue(row.is_active)

    def test_normalizes_daily_units_timestamp_and_adjustment_factor(self) -> None:
        row = normalize_bar(
            {
                "ts_code": "600000.SH",
                "trade_date": "20260105",
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "vol": 123.45,
                "amount": 129.6225,
            },
            instrument_id=8,
            adj_factor=2.5,
        )

        self.assertEqual(row.trade_date, date(2026, 1, 5))
        self.assertEqual(row.ts_utc, datetime(2026, 1, 5, 7, tzinfo=timezone.utc))
        self.assertEqual(row.volume, 12_345)
        self.assertAlmostEqual(row.vwap or 0.0, 10.5)
        self.assertEqual(row.adj_factor, 2.5)

    def test_invalid_ohlc_fails_at_the_vendor_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "inverted OHLC"):
            normalize_bar(
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260105",
                    "open": 10.0,
                    "high": 9.0,
                    "low": 9.5,
                    "close": 10.5,
                    "vol": 1,
                    "amount": 1,
                },
                instrument_id=8,
                adj_factor=1.0,
            )

    def test_missing_amount_keeps_vwap_null(self) -> None:
        row = normalize_bar(
            {
                "ts_code": "600000.SH",
                "trade_date": "20260105",
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "vol": 123.45,
                "amount": None,
            },
            instrument_id=8,
            adj_factor=1.0,
        )

        self.assertIsNone(row.vwap)

    def test_http_client_maps_fields_and_never_puts_token_in_an_error(self) -> None:
        session = _Session(
            {"code": 0, "msg": None, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}}
        )
        client = TushareClient(
            "top-secret",
            request_interval_seconds=0,
            session=session,  # type: ignore[arg-type]
        )

        self.assertEqual(client.query("daily"), [{"ts_code": "000001.SZ"}])
        self.assertEqual(session.request[1]["token"], "top-secret")

        failing = TushareClient(
            "top-secret",
            request_interval_seconds=0,
            session=_Session({"code": 2002, "msg": "permission denied"}),  # type: ignore[arg-type]
        )
        with self.assertRaises(TushareError) as raised:
            failing.query("adj_factor")
        self.assertNotIn("top-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
