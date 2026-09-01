#pragma once

#include <pybind11/pybind11.h>

namespace quant_kernel {

pybind11::list evaluate_pattern_day(
    const pybind11::dict& runtime,
    const pybind11::dict& market
);

}  // namespace quant_kernel
