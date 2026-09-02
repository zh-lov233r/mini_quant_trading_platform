#pragma once

#include <pybind11/pybind11.h>

#include "pattern_core.hpp"

namespace quant_kernel {

PatternConfig parse_pattern_config(const pybind11::dict& runtime);

pybind11::list evaluate_pattern_day(
    const pybind11::dict& runtime,
    const pybind11::dict& market
);

}  // namespace quant_kernel
