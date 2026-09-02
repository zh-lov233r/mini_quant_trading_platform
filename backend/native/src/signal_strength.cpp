#include "signal_strength.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

double finite_number(const py::handle value, const std::string& label) {
    if (value.is_none()) throw std::invalid_argument(label + " must be a finite number");
    double result;
    try {
        result = py::cast<double>(value);
    } catch (const py::cast_error&) {
        throw std::invalid_argument(label + " must be a finite number");
    }
    if (!std::isfinite(result)) throw std::invalid_argument(label + " must be a finite number");
    return result;
}

double dictionary_number(const py::dict& value, const char* key) {
    if (!value.contains(key)) throw std::invalid_argument(std::string(key) + " must be a finite number");
    return finite_number(value[key], key);
}

double rounded_2(double value) {
    return py::cast<double>(py::module_::import("builtins").attr("round")(value, 2));
}

py::dict component(
    const std::string& key,
    const py::dict& inputs,
    double weight,
    double gate,
    double cap_or_ideal,
    bool falling = false
) {
    if (!inputs.contains(py::str(key))) {
        throw std::invalid_argument(key + ".raw_value must be a finite number");
    }
    const double raw = finite_number(inputs[py::str(key)], key + ".raw_value");
    if ((!falling && cap_or_ideal <= gate) || (falling && gate <= cap_or_ideal)) {
        throw std::invalid_argument(falling
            ? "strength gate must be greater than ideal"
            : "strength cap must be greater than gate");
    }
    const double ratio = falling
        ? (gate - raw) / (gate - cap_or_ideal)
        : (raw - gate) / (cap_or_ideal - gate);
    py::dict result;
    result["key"] = key;
    result["raw_value"] = raw;
    result["normalized_score"] = rounded_2(100.0 * std::clamp(ratio, 0.0, 1.0));
    result["weight"] = weight;
    return result;
}

py::dict strength_record(
    const std::string& model_version,
    double threshold,
    const std::vector<py::dict>& components
) {
    if (threshold < 0.0 || threshold > 100.0) {
        throw std::invalid_argument("signal.min_strength_score must be within [0, 100]");
    }
    double weighted = 0.0;
    double total_weight = 0.0;
    py::list output_components;
    for (const py::dict& item : components) {
        const double weight = dictionary_number(item, "weight");
        weighted += dictionary_number(item, "normalized_score") * weight;
        total_weight += weight;
        output_components.append(item);
    }
    if (total_weight <= 0.0) throw std::invalid_argument("strength component weights must sum to a positive number");
    const double score = rounded_2(weighted / total_weight);
    py::dict result;
    result["score"] = score;
    result["level"] = score < 50.0 ? "weak" : score < 70.0 ? "medium" : score < 85.0 ? "strong" : "very_strong";
    result["threshold"] = threshold;
    result["passes_threshold"] = score >= threshold;
    result["rank"] = py::none();
    result["model_version"] = model_version;
    result["components"] = std::move(output_components);
    return result;
}

std::string string_or(const py::dict& value, const char* key, const std::string& fallback = "") {
    if (!value.contains(key) || value[key].is_none()) return fallback;
    return py::cast<std::string>(value[key]);
}

py::dict strength_for(
    const std::string& type,
    const py::dict& signal,
    const py::dict& risk,
    const py::dict& metadata
) {
    const double threshold = signal.contains("min_strength_score")
        ? finite_number(signal["min_strength_score"], "signal.min_strength_score")
        : 50.0;
    const py::dict inputs = metadata.contains("strength_inputs") && !metadata["strength_inputs"].is_none()
        ? py::cast<py::dict>(metadata["strength_inputs"])
        : py::dict();
    if (type == "trend") {
        const double volume = dictionary_number(signal, "volume_multiplier");
        return strength_record("trend:v1", threshold, {
            component("separation_atr", inputs, 0.60, 0.0, 0.5),
            component("crossover_impulse_atr", inputs, 0.20, 0.0, 0.5),
            component("volume_ratio", inputs, 0.20, volume, volume * 2.0),
        });
    }
    if (type == "mean_reversion") {
        const double entry = dictionary_number(signal, "zscore_entry");
        return strength_record("mean_reversion:v1", threshold, {
            component("absolute_zscore", inputs, 1.0, entry, entry * 2.0),
        });
    }
    if (type == "momentum_breakout") {
        const double minimum_return = dictionary_number(signal, "minimum_return_20d");
        const double breakout_buffer = dictionary_number(signal, "breakout_buffer_pct");
        const double volume = dictionary_number(signal, "volume_multiplier");
        return strength_record("momentum_breakout:v1", threshold, {
            component("return_20d", inputs, 0.40, minimum_return, minimum_return * 2.0),
            component("price_extension", inputs, 0.35, breakout_buffer, breakout_buffer * 2.0),
            component("volume_ratio", inputs, 0.25, volume, volume * 2.0),
        });
    }
    if (type == "island_reversal") {
        const std::string stage = string_or(inputs, "stage");
        const double left_gap = dictionary_number(signal, "left_gap_min_pct");
        const double left_volume = dictionary_number(signal, "left_volume_ratio_max");
        if (stage == "exhaustion_gap") {
            return strength_record("island_reversal:exhaustion_gap:v1", threshold, {
                component("left_gap_pct", inputs, 0.60, left_gap, left_gap * 2.0),
                component("left_volume_ratio", inputs, 0.40, left_volume, 0.0, true),
            });
        }
        const std::string normalized = stage == "upside_gap" ? "breakout" : stage == "gap_retest" ? "retest" : stage;
        if (normalized != "breakout" && normalized != "retest") {
            throw std::invalid_argument("unsupported island reversal stage: " + stage);
        }
        const double right_gap = dictionary_number(signal, "right_gap_min_pct");
        const double breakout_volume = dictionary_number(signal, "right_volume_ratio_min");
        std::vector<py::dict> components{
            component("left_gap_pct", inputs, normalized == "breakout" ? 0.30 : 0.15, left_gap, left_gap * 2.0),
            component("right_gap_pct", inputs, normalized == "breakout" ? 0.40 : 0.20, right_gap, right_gap * 2.0),
            component("breakout_volume_ratio", inputs, normalized == "breakout" ? 0.30 : 0.20, breakout_volume, breakout_volume * 2.0),
        };
        if (normalized == "retest") {
            components.push_back(component(
                "retest_volume_ratio", inputs, 0.25,
                dictionary_number(signal, "retest_volume_ratio_max"), 0.0, true
            ));
            components.push_back(component("hold_margin_atr", inputs, 0.20, 0.0, 1.0));
        }
        return strength_record("island_reversal:" + stage + ":v1", threshold, components);
    }
    if (type == "double_bottom") {
        const std::string stage = string_or(inputs, "stage", "retest");
        const double tolerance = dictionary_number(signal, "bottom_tolerance_pct");
        const double rebound = dictionary_number(signal, "rebound_up_day_ratio_min");
        const double breakout_volume = dictionary_number(signal, "breakout_volume_ratio_min");
        const double buffer = dictionary_number(signal, "breakout_buffer_pct");
        if (stage == "second_bottom") {
            return strength_record("double_bottom:second_bottom:v1", threshold, {
                component("bottom_distance_pct", inputs, 0.40, tolerance, 0.0, true),
                component("rebound_up_day_ratio", inputs, 0.30, rebound, 1.0),
                component("current_volume_ratio", inputs, 0.30, dictionary_number(signal, "second_bottom_volume_ratio_max"), 0.0, true),
            });
        }
        if (stage == "right_side_pullback") {
            return strength_record("double_bottom:right_side_pullback:v1", threshold, {
                component("bottom_distance_pct", inputs, 0.25, tolerance, 0.0, true),
                component("rebound_up_day_ratio", inputs, 0.25, rebound, 1.0),
                component("current_volume_ratio", inputs, 0.25, dictionary_number(signal, "second_bottom_volume_ratio_max"), 0.0, true),
                component("pullback_hold_pct", inputs, 0.25, 0.0, 1.0),
            });
        }
        if (stage == "neckline_breakout") {
            return strength_record("double_bottom:neckline_breakout:v1", threshold, {
                component("bottom_distance_pct", inputs, 0.25, tolerance, 0.0, true),
                component("rebound_up_day_ratio", inputs, 0.25, rebound, 1.0),
                component("breakout_volume_ratio", inputs, 0.25, breakout_volume, breakout_volume * 2.0),
                component("breakout_extension_pct", inputs, 0.25, buffer, buffer * 2.0),
            });
        }
        return strength_record("double_bottom:retest:v1", threshold, {
            component("bottom_distance_pct", inputs, 0.25, tolerance, 0.0, true),
            component("rebound_up_day_ratio", inputs, 0.20, rebound, 1.0),
            component("breakout_volume_ratio", inputs, 0.20, breakout_volume, breakout_volume * 2.0),
            component("breakout_extension_pct", inputs, 0.15, buffer, buffer * 2.0),
            component("retest_volume_ratio", inputs, 0.20, dictionary_number(signal, "retest_volume_ratio_max"), 0.0, true),
        });
    }
    if (type == "head_shoulders_bottom" || type == "rounded_bottom" || type == "v_reversal") {
        std::string stage = "unknown";
        if (metadata.contains("setup") && !metadata["setup"].is_none()) {
            stage = string_or(py::cast<py::dict>(metadata["setup"]), "stage_key", "unknown");
        }
        return strength_record(type + ":" + stage + ":v1", threshold, {
            component("structure_quality", inputs, 0.25, 0.0, 1.0),
            component("price_confirmation", inputs, 0.25, 0.0, 1.0),
            component("volume_quality", inputs, 0.25, 0.0, 1.0),
            component("stage_confirmation", inputs, 0.25, 0.0, 1.0),
        });
    }
    if (type == "support_resistance") {
        if (!metadata.contains("support_resistance") || metadata["support_resistance"].is_none()) {
            throw std::invalid_argument("support/resistance BUY signal is missing strength");
        }
        const py::dict support = py::cast<py::dict>(metadata["support_resistance"]);
        if (!support.contains("strength") || support["strength"].is_none()) {
            throw std::invalid_argument("support/resistance BUY signal is missing strength");
        }
        return py::module_::import("builtins").attr("dict")(
            support["strength"]
        ).cast<py::dict>();
    }
    throw std::invalid_argument("unsupported engine-ready strategy type: " + type);
}

bool entry_buy(const py::dict& event) {
    if (string_or(event, "action") != "BUY") return false;
    const py::dict metadata = event.contains("metadata") && !event["metadata"].is_none()
        ? py::cast<py::dict>(event["metadata"])
        : py::dict();
    if (!metadata.contains("position") || metadata["position"].is_none()) return true;
    return finite_number(metadata["position"], "signal position") >= 0.0;
}

std::int64_t stable_instrument_id(const py::dict& event) {
    if (!event.contains("instrument_id") || event["instrument_id"].is_none()) {
        return std::numeric_limits<std::int64_t>::max();
    }
    const std::int64_t value = py::cast<std::int64_t>(event["instrument_id"]);
    return value < 0 ? std::numeric_limits<std::int64_t>::max() : value;
}

}  // namespace

void annotate_signal_strength(
    const std::string& strategy_type,
    const py::dict& runtime,
    py::list& signals
) {
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::dict signal = py::cast<py::dict>(params["signal"]);
    const py::dict risk = py::cast<py::dict>(params["risk"]);
    std::vector<py::dict> entries;
    for (const py::handle raw : signals) {
        py::dict event = py::cast<py::dict>(raw);
        if (!entry_buy(event)) continue;
        py::dict metadata = py::cast<py::dict>(event["metadata"]);
        metadata["strength"] = strength_for(strategy_type, signal, risk, metadata);
        entries.push_back(event);
    }
    std::sort(entries.begin(), entries.end(), [](const py::dict& left, const py::dict& right) {
        const py::dict left_meta = py::cast<py::dict>(left["metadata"]);
        const py::dict right_meta = py::cast<py::dict>(right["metadata"]);
        const py::dict left_strength = py::cast<py::dict>(left_meta["strength"]);
        const py::dict right_strength = py::cast<py::dict>(right_meta["strength"]);
        const double left_score = dictionary_number(left_strength, "score");
        const double right_score = dictionary_number(right_strength, "score");
        if (left_score != right_score) return left_score > right_score;
        const std::int64_t left_id = stable_instrument_id(left);
        const std::int64_t right_id = stable_instrument_id(right);
        if (left_id != right_id) return left_id < right_id;
        std::string left_symbol = string_or(left, "symbol");
        std::string right_symbol = string_or(right, "symbol");
        std::transform(left_symbol.begin(), left_symbol.end(), left_symbol.begin(), ::toupper);
        std::transform(right_symbol.begin(), right_symbol.end(), right_symbol.begin(), ::toupper);
        return left_symbol < right_symbol;
    });
    int rank = 1;
    for (py::dict& event : entries) {
        py::dict metadata = py::cast<py::dict>(event["metadata"]);
        py::dict strength = py::cast<py::dict>(metadata["strength"]);
        strength["rank"] = rank++;
    }
}

}  // namespace quant_kernel
