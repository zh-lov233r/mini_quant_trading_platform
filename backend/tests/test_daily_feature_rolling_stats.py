from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "backfill_daily_features.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_daily_features", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RollingStatsTests(unittest.TestCase):
    def test_mean_rebases_after_large_values_leave_window(self) -> None:
        module = _load_module()
        stats = module.RollingStats(window=2)

        stats.push(1e20)
        stats.push(1e20)
        stats.push(1.0)
        stats.push(1.0)

        self.assertEqual(list(stats.values), [1.0, 1.0])
        self.assertEqual(stats.mean(), 1.0)
        self.assertTrue(math.isclose(stats.sum, math.fsum(stats.values)))

    def test_mean_keeps_normal_sliding_window_behavior(self) -> None:
        module = _load_module()
        stats = module.RollingStats(window=3)

        for value in (1.0, 2.0, 3.0, 4.0):
            stats.push(value)

        self.assertEqual(list(stats.values), [2.0, 3.0, 4.0])
        self.assertEqual(stats.mean(), 3.0)

    def test_mean_repairs_accumulated_residual_outside_window_bounds(self) -> None:
        module = _load_module()
        stats = module.RollingStats(window=3)
        for value in (2.0, 3.0, 4.0):
            stats.push(value)
        # Model a residual left by a much older extreme observation. The
        # active window remains authoritative and must produce a valid mean.
        stats.sum = -12_000.0

        self.assertEqual(stats.mean(), 3.0)
        self.assertEqual(stats.sum, math.fsum(stats.values))


if __name__ == "__main__":
    unittest.main()
