#pragma once
namespace quant_kernel::market {
// Pure market predicates shared by daily execution, backtests and observations.
inline int crossover(double previous_fast, double previous_slow, double fast, double slow) {
    if (previous_fast <= previous_slow && fast > slow) return 1;
    if (previous_fast >= previous_slow && fast < slow) return -1;
    return 0;
}
inline int deviation(double zscore, double entry) {
    if (zscore <= -entry) return 1;
    if (zscore >= entry) return -1;
    return 0;
}
inline bool momentum(double close, double threshold, double return_20d, double minimum_return,
                     double volume_ratio, double volume_multiplier) {
    return close >= threshold && return_20d >= minimum_return && volume_ratio >= volume_multiplier;
}
}
