from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "backfill_missing_eod_from_massive.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_missing_eod_from_massive", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BackfillMissingEodFromMassiveTests(unittest.TestCase):
    def test_missing_symbols_sql_targets_active_primary_symbols_only(self) -> None:
        module = _load_module()

        self.assertIn("AND sh.is_primary", module.MISSING_SYMBOLS_SQL)
        self.assertIn("AND instr.is_active = TRUE", module.MISSING_SYMBOLS_SQL)
        self.assertIn("WHERE map.match_count = 1", module.MISSING_SYMBOLS_SQL)


if __name__ == "__main__":
    unittest.main()
