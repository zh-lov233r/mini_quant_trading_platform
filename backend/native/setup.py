from __future__ import annotations

import os

from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup


setup(
    packages=["quant_kernel"],
    ext_modules=[
        Pybind11Extension(
            "quant_kernel._native",
            [
                "src/quant_kernel.cpp",
                "src/pattern_kernel.cpp",
                "src/double_bottom_kernel.cpp",
                "src/support_resistance_kernel.cpp",
            ],
            cxx_std=20,
            define_macros=[
                (
                    "QUANT_KERNEL_BUILD_ID",
                    f'"{os.getenv("QUANT_KERNEL_BUILD_ID", "local")}"',
                )
            ],
            extra_compile_args=["-O3", "-DNDEBUG"],
        )
    ],
    cmdclass={"build_ext": build_ext},
)
