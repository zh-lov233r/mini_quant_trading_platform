from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine

from src.services.native_runtime_service import validate_native_runtime


class NativeRuntimeServiceTests(unittest.TestCase):
    def test_current_wheel_and_non_postgres_driver_are_accepted(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            validate_native_runtime(engine)
        finally:
            engine.dispose()

    def test_kernel_version_and_catalog_mismatches_fail_fast(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with patch("src.services.native_runtime_service.quant_kernel.KERNEL_VERSION", "old"):
                with self.assertRaisesRegex(RuntimeError, "version mismatch"):
                    validate_native_runtime(engine)
            with patch("src.services.native_runtime_service.quant_kernel.catalog", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "catalog mismatch"):
                    validate_native_runtime(engine)
        finally:
            engine.dispose()

    def test_postgresql_requires_psycopg3_driver(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with patch.object(engine.dialect, "name", "postgresql"), patch.object(
                engine.dialect, "driver", "psycopg2"
            ):
                with self.assertRaisesRegex(RuntimeError, r"postgresql\+psycopg"):
                    validate_native_runtime(engine)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
