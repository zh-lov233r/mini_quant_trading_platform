#pragma once

#include "support_resistance_core.hpp"

#include <pybind11/pybind11.h>

namespace quant_kernel {

support_resistance::Config parse_support_resistance_config(
    const pybind11::dict& signal,
    const pybind11::dict& risk
);
void hydrate_support_resistance_symbol_state(
    support_resistance::SymbolState& state,
    const pybind11::dict& payload
);
pybind11::list evaluate_support_resistance_day(
    const pybind11::dict& runtime,
    const pybind11::dict& market,
    pybind11::dict audit
);
void bind_support_resistance(pybind11::module_& module);

}  // namespace quant_kernel
