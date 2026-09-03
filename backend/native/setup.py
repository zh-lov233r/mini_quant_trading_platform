from __future__ import annotations

import os
import sys

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup


THREAD_FLAGS = [] if sys.platform == "win32" else ["-pthread"]


setup(
    packages=["quant_kernel"],
    ext_modules=[
        Pybind11Extension(
            "quant_kernel._native",
            [
                "src/quant_kernel.cpp",
                "src/native_utils.cpp",
                "src/strategy_descriptor.cpp",
                "src/pattern_core.cpp",
                "src/backtest_kernel.cpp",
                "src/pattern_kernel.cpp",
                "src/signal_strength.cpp",
                "src/support_resistance_core.cpp",
                "src/support_resistance_kernel.cpp",
            ],
            cxx_std=20,
            define_macros=[
                (
                    "QUANT_KERNEL_BUILD_ID",
                    f'"{os.getenv("QUANT_KERNEL_BUILD_ID", "local")}"',
                )
            ],
            extra_compile_args=[
                "-O3",
                "-DNDEBUG",
                *THREAD_FLAGS,
            ],
            extra_link_args=THREAD_FLAGS,
        )
    ],
    cmdclass={"build_ext": build_ext},
)
