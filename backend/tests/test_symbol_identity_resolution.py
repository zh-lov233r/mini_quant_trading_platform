from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


BACKFILL_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "utils" / "backfill_instruments_and_symbol.py"
)


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location("backfill_instruments_and_symbol", BACKFILL_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {BACKFILL_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SymbolIdentityResolutionTests(unittest.TestCase):
    def test_active_rows_close_conflicting_symbol_owners(self) -> None:
        module = _load_backfill_module()

        self.assertTrue(module.should_close_conflicting_symbol_owners({"active": True}))
        self.assertFalse(module.should_close_conflicting_symbol_owners({"active": False}))
        self.assertFalse(module.should_close_conflicting_symbol_owners({}))

    def test_stale_inactive_upsert_preserves_current_identity(self) -> None:
        module = _load_backfill_module()
        sql = module.UPSERT_INSTR
        self.assertIn(
            "EXCLUDED.ticker_canonical IS DISTINCT FROM instruments.ticker_canonical",
            sql,
        )
        self.assertIn("EXCLUDED.delisted_at < instruments.listed_at", sql)
        self.assertIn("RETURNING id, is_active", sql)

    def test_symbol_history_params_use_unknown_start_when_list_date_missing(self) -> None:
        module = _load_backfill_module()

        params = module.build_symbol_history_params(
            {
                "exchange": "XASE",
                "ticker": "NINE",
                "list_date": None,
            },
            instrument_id=17552,
        )

        self.assertEqual(params["iid"], 17552)
        self.assertEqual(params["exchange"], "XASE")
        self.assertEqual(params["symbol"], "NINE")
        self.assertEqual(params["start_date"], module.UNKNOWN_VALID_FROM)
        self.assertEqual(params["valid_from_precision"], "unknown")

    def test_conflicting_symbol_owner_sql_closes_other_open_instruments(self) -> None:
        module = _load_backfill_module()

        self.assertIn("instrument_id <> %(iid)s", module.SQL_CLOSE_CONFLICTING_SYMBOL_OWNERS)
        self.assertIn("symbol = %(symbol)s", module.SQL_CLOSE_CONFLICTING_SYMBOL_OWNERS)
        self.assertIn("later_primary.instrument_id = symbol_history.instrument_id", module.SQL_CLOSE_CONFLICTING_SYMBOL_OWNERS)
        self.assertIn("later_primary.valid_from > symbol_history.valid_from", module.SQL_CLOSE_CONFLICTING_SYMBOL_OWNERS)

    def test_open_new_sql_blocks_same_symbol_on_other_instrument(self) -> None:
        module = _load_backfill_module()

        self.assertIn("sh_conflict.symbol = %(symbol)s", module.SQL_OPEN_NEW)
        self.assertIn("sh_conflict.instrument_id <> %(iid)s", module.SQL_OPEN_NEW)
        self.assertNotIn("sh_conflict.exchange = %(exchange)s", module.SQL_OPEN_NEW)

    def test_open_new_sql_infers_non_overlapping_start_for_unknown_reopens(self) -> None:
        module = _load_backfill_module()

        self.assertIn("latest_same_symbol.latest_valid_to", module.SQL_OPEN_NEW)
        self.assertIn("THEN latest_same_symbol.latest_valid_to + 1", module.SQL_OPEN_NEW)
        self.assertIn("THEN 'inferred'", module.SQL_OPEN_NEW)

    def test_aan_reopen_considers_all_closed_intervals_for_the_instrument(self) -> None:
        module = _load_backfill_module()

        self.assertIn("OR instrument_id = %(iid)s", module.SQL_OPEN_NEW)
        self.assertIn("%(allow_reopen)s", module.SQL_OPEN_NEW)

        inactive = module.build_symbol_history_params(
            {
                "exchange": "XNYS",
                "ticker": "AAN",
                "list_date": None,
                "active": False,
            },
            instrument_id=4211,
        )
        active = module.build_symbol_history_params(
            {
                "exchange": "XNYS",
                "ticker": "AAN",
                "list_date": None,
                "active": True,
            },
            instrument_id=4211,
        )

        self.assertFalse(inactive["allow_reopen"])
        self.assertTrue(active["allow_reopen"])

    def test_active_alias_is_promoted_after_the_old_primary_closes(self) -> None:
        module = _load_backfill_module()

        self.assertIn("SET valid_from = GREATEST", module.SQL_PROMOTE_OPEN_SYMBOL)
        self.assertIn("is_primary = TRUE", module.SQL_PROMOTE_OPEN_SYMBOL)
        self.assertIn("sh.valid_from_precision <> 'exact'", module.SQL_PROMOTE_OPEN_SYMBOL)
        self.assertIn("MAX(valid_to) + 1", module.SQL_PROMOTE_OPEN_SYMBOL)

    def test_supported_symbol_map_uses_point_in_time_primary_intervals(self) -> None:
        data_service_path = Path(__file__).resolve().parents[1] / "src" / "services" / "data_service.py"
        source = data_service_path.read_text(encoding="utf-8")
        supported_symbol_map_sql = source.split(
            'SUPPORTED_SYMBOL_MAP_SQL = """', 1
        )[1].split('"""', 1)[0]

        self.assertIn("AND sh.is_primary", supported_symbol_map_sql)
        self.assertIn("sh.valid_from <= %(trade_date)s::date", supported_symbol_map_sql)
        self.assertIn(
            "sh.valid_to IS NULL OR sh.valid_to >= %(trade_date)s::date",
            supported_symbol_map_sql,
        )
        self.assertNotIn("instr.is_active = TRUE", supported_symbol_map_sql)

    def test_flat_file_replay_preserves_existing_vwap_when_source_has_none(self) -> None:
        data_service_path = Path(__file__).resolve().parents[1] / "src" / "services" / "data_service.py"
        source = data_service_path.read_text(encoding="utf-8")

        self.assertIn("vwap = COALESCE(EXCLUDED.vwap, eod_bars.vwap)", source)


if __name__ == "__main__":
    unittest.main()
