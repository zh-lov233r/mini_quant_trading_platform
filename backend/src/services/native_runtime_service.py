from __future__ import annotations

import quant_kernel
from sqlalchemy.engine import Engine


EXPECTED_KERNEL_VERSION = "cpp-v1"
EXPECTED_ABI_VERSION = 3
EXPECTED_STRATEGY_TYPES = {
    "trend",
    "mean_reversion",
    "momentum_breakout",
    "island_reversal",
    "double_bottom",
    "head_shoulders_bottom",
    "rounded_bottom",
    "v_reversal",
    "support_resistance",
}


def validate_native_runtime(engine: Engine) -> None:
    """Fail startup before workers run when the native wheel or COPY driver is incompatible."""
    if quant_kernel.KERNEL_VERSION != EXPECTED_KERNEL_VERSION:
        raise RuntimeError(
            f"native kernel version mismatch: {quant_kernel.KERNEL_VERSION!r}"
        )
    if int(quant_kernel.ABI_VERSION) != EXPECTED_ABI_VERSION:
        raise RuntimeError(f"native kernel ABI mismatch: {quant_kernel.ABI_VERSION!r}")
    catalog = quant_kernel.catalog()
    actual = [str(item["strategy_type"]) for item in catalog]
    if len(actual) != len(set(actual)) or set(actual) != EXPECTED_STRATEGY_TYPES:
        raise RuntimeError(f"native strategy catalog mismatch: {actual!r}")
    if engine.dialect.name == "postgresql" and engine.dialect.driver != "psycopg":
        raise RuntimeError("native result persistence requires postgresql+psycopg")
