#pragma once

#include <pybind11/pybind11.h>

namespace quant_kernel {

void bind_backtest(pybind11::module_& module);

}  // namespace quant_kernel
