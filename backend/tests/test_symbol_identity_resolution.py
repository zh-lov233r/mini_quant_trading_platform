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

    def test_supported_symbol_map_only_uses_active_primary_intervals(self) -> None:
        from src.services import data_service

        self.assertIn("AND sh.is_primary", data_service.SUPPORTED_SYMBOL_MAP_SQL)
        self.assertIn("AND instr.is_active = TRUE", data_service.SUPPORTED_SYMBOL_MAP_SQL)


if __name__ == "__main__":
    unittest.main()
