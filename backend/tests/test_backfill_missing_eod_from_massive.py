from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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
    def test_weekend_only_range_succeeds_without_eod_work(self) -> None:
        module = _load_module()
        argv = ["backfill", "--start-date", "2026-09-05", "--end-date", "2026-09-06"]
        with patch.object(sys, "argv", argv), patch.object(module, "load_dotenv"), patch.dict(
            "os.environ", {"MASSIVE_API_KEY": "test-key", "DATABASE_URL": "postgresql://localhost/test"}
        ), patch.object(module, "require_market_data_maintenance_owner") as guard, patch(
            "psycopg.connect"
        ) as connect, patch.object(module.subprocess, "run") as run, patch("builtins.print") as output:
            module.main()

        guard.assert_called_once_with("postgresql://localhost/test")
        connect.assert_not_called()
        run.assert_not_called()
        output.assert_called_once_with(
            "No weekday trade dates in the requested range; skipping EOD gap fill.", flush=True
        )

    def test_missing_symbols_sql_targets_active_primary_symbols_only(self) -> None:
        module = _load_module()

        self.assertIn("AND sh.is_primary", module.MISSING_SYMBOLS_SQL)
        self.assertIn("AND instr.is_active = TRUE", module.MISSING_SYMBOLS_SQL)
        self.assertIn("WHERE map.match_count = 1", module.MISSING_SYMBOLS_SQL)

    def test_flat_file_import_uses_point_in_time_symbols_not_current_active_flag(self) -> None:
        data_service_path = Path(__file__).resolve().parents[1] / "src" / "services" / "data_service.py"
        source = data_service_path.read_text(encoding="utf-8")
        supported_map_sql = source.split('SUPPORTED_SYMBOL_MAP_SQL = """', 1)[1].split('"""', 1)[0]

        self.assertIn("sh.valid_from <= %(trade_date)s::date", supported_map_sql)
        self.assertIn("sh.valid_to IS NULL OR sh.valid_to >= %(trade_date)s::date", supported_map_sql)
        self.assertNotIn("instr.is_active = TRUE", supported_map_sql)


if __name__ == "__main__":
    unittest.main()
