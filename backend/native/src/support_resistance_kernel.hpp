#pragma once

#include <pybind11/pybind11.h>

namespace quant_kernel {

pybind11::list evaluate_support_resistance_day(
    const pybind11::dict& runtime,
    const pybind11::dict& market
);
void bind_support_resistance(pybind11::module_& module);

}  // namespace quant_kernel
