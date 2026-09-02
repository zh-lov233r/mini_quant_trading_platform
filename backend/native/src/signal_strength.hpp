#pragma once

#include <pybind11/pybind11.h>

#include <string>

namespace quant_kernel {

void annotate_signal_strength(
    const std::string& strategy_type,
    const pybind11::dict& runtime,
    pybind11::list& signals
);

}  // namespace quant_kernel
