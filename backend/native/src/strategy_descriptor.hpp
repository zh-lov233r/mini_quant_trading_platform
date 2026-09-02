#pragma once

#include <pybind11/pybind11.h>

#include <string>

namespace quant_kernel {

pybind11::list strategy_catalog();
pybind11::dict normalize_strategy_params(
    const std::string& strategy_type,
    const pybind11::dict& params
);

}  // namespace quant_kernel
