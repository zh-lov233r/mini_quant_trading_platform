#pragma once

#include <pybind11/pybind11.h>

namespace quant_kernel {

pybind11::object evaluate_double_bottom_event(
    const pybind11::dict& runtime,
    const std::string& symbol,
    const pybind11::dict& snapshot,
    const pybind11::dict& signal_cfg,
    const pybind11::dict& risk_cfg
);

}  // namespace quant_kernel
