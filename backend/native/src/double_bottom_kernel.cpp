#include "double_bottom_kernel.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

struct LeftCandidate {
    int left_index;
    double left_low;
};

struct RightCandidate {
    int left_index;
    int neckline_index;
    int right_index;
    double left_low;
    double right_low;
    double neckline;
    double distance;
    double rebound_ratio;
};

struct Pattern {
    int left_index;
    int neckline_index;
    int right_index;
    int breakout_index;
    double left_low;
    double right_low;
    double neckline;
    double breakout_close;
    double breakout_volume;
    double breakout_volume_ratio;
    double distance;
    double rebound_ratio;
};

struct ReplayState {
    std::vector<LeftCandidate> left;
    std::vector<RightCandidate> right;
    std::optional<Pattern> best;
};

std::optional<double> number(const py::dict& value, const char* key) {
    if (!value.contains(key) || value[key].is_none()) return std::nullopt;
    return py::cast<double>(value[key]);
}

double number_or(const py::dict& value, const char* key, double fallback = 0.0) {
    const auto result = number(value, key);
    return result ? *result : fallback;
}

py::object item_or_none(const py::dict& value, const char* key) {
    if (!value.contains(key)) return py::none();
    return py::reinterpret_borrow<py::object>(value[key]);
}

py::dict bar_at(const py::list& bars, int index) {
    return py::cast<py::dict>(bars[index]);
}

std::optional<double> bar_number(const py::list& bars, int index, const char* key) {
    return number(bar_at(bars, index), key);
}

std::optional<double> up_day_ratio(const py::list& bars, int start, int end) {
    if (end <= start) return std::nullopt;
    auto previous = bar_number(bars, start, "close");
    if (!previous) return std::nullopt;
    int up_days = 0;
    int directional_days = 0;
    for (int index = start + 1; index <= end; ++index) {
        const auto close = bar_number(bars, index, "close");
        if (!close) continue;
        if (*close > *previous) {
            ++up_days;
            ++directional_days;
        } else if (*close < *previous) {
            ++directional_days;
        }
        previous = close;
    }
    if (directional_days == 0) return std::nullopt;
    return static_cast<double>(up_days) / directional_days;
}

std::optional<std::pair<double, double>> linear_fit(const std::vector<double>& values) {
    if (values.size() < 2) return std::nullopt;
    const double count = static_cast<double>(values.size());
    const double x_mean = (count - 1.0) / 2.0;
    double y_mean = 0.0;
    for (const double value : values) y_mean += value;
    y_mean /= count;
    double squared_x = 0.0;
    double sum_xy = 0.0;
    double total_variance = 0.0;
    for (int index = 0; index < static_cast<int>(values.size()); ++index) {
        const double x_delta = index - x_mean;
        const double y_delta = values[index] - y_mean;
        squared_x += x_delta * x_delta;
        sum_xy += x_delta * y_delta;
        total_variance += y_delta * y_delta;
    }
    if (squared_x <= 0.0) return std::nullopt;
    const double slope = sum_xy / squared_x;
    if (total_variance <= 0.0) return std::pair{slope, 1.0};
    const double intercept = y_mean - slope * x_mean;
    double residual = 0.0;
    for (int index = 0; index < static_cast<int>(values.size()); ++index) {
        residual += std::pow(values[index] - (intercept + slope * index), 2.0);
    }
    return std::pair{slope, std::min(std::max(1.0 - residual / total_variance, 0.0), 1.0)};
}

bool local_minimum(const py::list& bars, int index, int before, int after) {
    const auto low = bar_number(bars, index, "low");
    const int count = static_cast<int>(py::len(bars));
    if (!low || before < 0 || after < 0 || index - before < 0 || index + after >= count) return false;
    for (int neighbor = index - before; neighbor <= index + after; ++neighbor) {
        if (neighbor == index) continue;
        const auto neighbor_low = bar_number(bars, neighbor, "low");
        if (neighbor_low && *neighbor_low < *low) return false;
    }
    return true;
}

bool downtrend(const py::list& bars, int left_index, int lookback, double minimum_drop) {
    const auto close = bar_number(bars, left_index, "close");
    const int anchor_index = left_index - lookback;
    if (!close || anchor_index < 0) return false;
    const auto anchor = bar_number(bars, anchor_index, "close");
    return anchor && *anchor > 0.0 && *close / *anchor - 1.0 <= -minimum_drop;
}

bool smooth_downtrend(
    const py::list& bars,
    int left_index,
    int lookback,
    double maximum_up_ratio,
    double minimum_r_squared
) {
    const int anchor_index = left_index - lookback;
    if (anchor_index < 0) return false;
    const auto ratio = up_day_ratio(bars, anchor_index, left_index);
    if (!ratio || *ratio > maximum_up_ratio) return false;
    std::vector<double> closes;
    for (int index = anchor_index; index <= left_index; ++index) {
        const auto close = bar_number(bars, index, "close");
        if (!close || *close <= 0.0) return false;
        closes.push_back(*close);
    }
    const auto fit = linear_fit(closes);
    return fit && fit->first < 0.0 && fit->second >= minimum_r_squared;
}

std::optional<LeftCandidate> build_left(
    const py::list& bars,
    int current_index,
    const py::dict& config
) {
    const int before = py::cast<int>(config["left_bottom_before_bars"]);
    const int after = py::cast<int>(config["left_bottom_after_bars"]);
    const int left_index = current_index - after;
    if (left_index < before || !local_minimum(bars, left_index, before, after)) return std::nullopt;
    const py::dict bar = bar_at(bars, left_index);
    const auto low = number(bar, "low");
    const auto volume = number(bar, "volume");
    const auto average = number(bar, "volume_sma_20");
    if (!low || *low <= 0.0 || !volume || !average || *average <= 0.0
        || *volume / *average > py::cast<double>(config["second_bottom_volume_ratio_max"])) return std::nullopt;
    const int lookback = py::cast<int>(config["downtrend_lookback"]);
    if (!downtrend(bars, left_index, lookback, py::cast<double>(config["downtrend_min_drop_pct"]))
        || !smooth_downtrend(
            bars,
            left_index,
            lookback,
            py::cast<double>(config["downtrend_max_up_day_ratio"]),
            py::cast<double>(config["downtrend_min_r_squared"]))) return std::nullopt;
    return LeftCandidate{left_index, *low};
}

bool intermediate_lows_hold(const py::list& bars, int left, int right, double floor) {
    if (right - left <= 1) return false;
    for (int index = left + 1; index < right; ++index) {
        const auto low = bar_number(bars, index, "low");
        if (!low || *low <= floor) return false;
    }
    return true;
}

std::optional<std::pair<int, double>> neckline(const py::list& bars, int left, int right) {
    if (right - left <= 1) return std::nullopt;
    std::optional<std::pair<int, double>> result;
    for (int index = left + 1; index < right; ++index) {
        const auto high = bar_number(bars, index, "high");
        if (high && (!result || *high > result->second)) result = std::pair{index, *high};
    }
    return result;
}

std::optional<RightCandidate> build_right(
    const py::list& bars,
    const LeftCandidate& left,
    int right_index,
    const py::dict& config
) {
    const py::dict right_bar = bar_at(bars, right_index);
    const auto right_low = number(right_bar, "low");
    const auto volume = number(right_bar, "volume");
    const auto average = number(right_bar, "volume_sma_20");
    if (!right_low || !volume || !average || *average <= 0.0
        || *volume / *average > py::cast<double>(config["second_bottom_volume_ratio_max"])) return std::nullopt;
    const double tolerance = py::cast<double>(config["bottom_tolerance_pct"]);
    const double distance = std::abs(*right_low - left.left_low) / std::max(left.left_low, *right_low);
    if (distance > tolerance || *right_low < left.left_low * (1.0 - tolerance)
        || !intermediate_lows_hold(bars, left.left_index, right_index, std::min(left.left_low, *right_low))) {
        return std::nullopt;
    }
    const auto line = neckline(bars, left.left_index, right_index);
    if (!line || line->second <= 0.0
        || line->second < std::max(left.left_low, *right_low)
            * (1.0 + py::cast<double>(config["neckline_min_rebound_pct"]))) return std::nullopt;
    const auto rebound = up_day_ratio(bars, left.left_index, right_index);
    if (!rebound || *rebound < py::cast<double>(config["rebound_up_day_ratio_min"])) return std::nullopt;
    return RightCandidate{
        left.left_index, line->first, right_index, left.left_low, *right_low,
        line->second, distance, *rebound
    };
}

std::optional<std::tuple<double, double, double>> breakout_match(
    const py::dict& bar,
    double neckline_price,
    double buffer,
    double minimum_volume_ratio
) {
    const auto high = number(bar, "high");
    const auto close = number(bar, "close");
    const auto volume = number(bar, "volume");
    const auto average = number(bar, "volume_sma_20");
    if (!high || !close || !volume || !average || *average <= 0.0
        || *high <= neckline_price * (1.0 + buffer)
        || *volume / *average < minimum_volume_ratio) return std::nullopt;
    return std::tuple{*close, *volume, *average};
}

std::optional<Pattern> build_pattern(
    const py::list& bars,
    const RightCandidate& candidate,
    int breakout_index,
    const py::dict& config
) {
    if (breakout_index <= candidate.right_index) return std::nullopt;
    const auto match = breakout_match(
        bar_at(bars, breakout_index),
        candidate.neckline,
        py::cast<double>(config["breakout_buffer_pct"]),
        py::cast<double>(config["breakout_volume_ratio_min"])
    );
    if (!match) return std::nullopt;
    const auto [close, volume, average] = *match;
    return Pattern{
        candidate.left_index, candidate.neckline_index, candidate.right_index,
        breakout_index, candidate.left_low, candidate.right_low, candidate.neckline,
        close, volume, volume / average, candidate.distance, candidate.rebound_ratio
    };
}

void advance(ReplayState& state, const py::list& bars, const py::dict& config) {
    const int count = static_cast<int>(py::len(bars));
    if (count < 2) return;
    const int current = count - 1;
    const int max_spacing = py::cast<int>(config["max_bottom_spacing"]);
    const auto candidate = build_left(bars, current, config);
    if (candidate && std::none_of(
            state.left.begin(), state.left.end(),
            [&](const LeftCandidate& existing) { return existing.left_index == candidate->left_index; })) {
        state.left.push_back(*candidate);
    }
    std::erase_if(
        state.left,
        [&](const LeftCandidate& value) { return current > value.left_index + max_spacing + 1; }
    );
    const int after = py::cast<int>(config["left_bottom_after_bars"]);
    const int before = py::cast<int>(config["left_bottom_before_bars"]);
    const int right_index = current - after;
    if (right_index >= 0 && local_minimum(bars, right_index, before, after)) {
        for (const LeftCandidate& left : state.left) {
            const int spacing = right_index - left.left_index;
            if (spacing < py::cast<int>(config["min_bottom_spacing"])
                || spacing > max_spacing) continue;
            const auto right = build_right(bars, left, right_index, config);
            if (!right) continue;
            const bool exists = std::any_of(
                state.right.begin(), state.right.end(),
                [&](const RightCandidate& existing) {
                    return existing.left_index == right->left_index && existing.right_index == right->right_index;
                }
            );
            if (!exists) state.right.push_back(*right);
        }
    }
    std::vector<RightCandidate> active;
    const int maximum_breakout = py::cast<int>(config["max_breakout_bars_after_right_bottom"]);
    for (const RightCandidate& right : state.right) {
        if (current > right.right_index + maximum_breakout) continue;
        const auto pattern = build_pattern(bars, right, current, config);
        if (!pattern) {
            active.push_back(right);
        } else if (!state.best || pattern->breakout_index > state.best->breakout_index
                   || (pattern->breakout_index == state.best->breakout_index
                       && pattern->right_index > state.best->right_index)) {
            state.best = pattern;
        }
    }
    state.right = std::move(active);
}

ReplayState replay(const py::list& all_bars, const py::dict& config) {
    ReplayState state;
    py::list progressive;
    for (const py::handle raw_bar : all_bars) {
        progressive.append(py::dict(py::cast<py::dict>(raw_bar)));
        advance(state, progressive, config);
    }
    return state;
}

std::optional<double> recent_atr(const py::list& bars, int end, int window = 20) {
    std::vector<double> ranges;
    for (int index = 0; index < end; ++index) {
        const auto high = bar_number(bars, index, "high");
        const auto low = bar_number(bars, index, "low");
        if (!high || !low) continue;
        double value = *high - *low;
        if (index > 0) {
            const auto previous = bar_number(bars, index - 1, "close");
            if (previous) value = std::max({value, std::abs(*high - *previous), std::abs(*low - *previous)});
        }
        ranges.push_back(value);
    }
    if (static_cast<int>(ranges.size()) < window) return std::nullopt;
    double total = 0.0;
    for (int index = static_cast<int>(ranges.size()) - window; index < static_cast<int>(ranges.size()); ++index) {
        total += ranges[index];
    }
    return total / window;
}

std::string build_setup_id(const std::string& symbol, const std::string& left_date, const std::string& right_date) {
    std::string upper = symbol;
    std::transform(
        upper.begin(), upper.end(), upper.begin(),
        [](unsigned char character) { return static_cast<char>(std::toupper(character)); }
    );
    py::list payload;
    payload.append("double_bottom");
    payload.append(upper);
    payload.append(left_date);
    payload.append(right_date);
    const py::object serialized = py::module_::import("json").attr("dumps")(
        payload,
        py::arg("ensure_ascii") = true,
        py::arg("separators") = py::make_tuple(",", ":")
    );
    const std::string digest = py::str(
        py::module_::import("hashlib").attr("sha256")(py::bytes(py::str(serialized))).attr("hexdigest")()
    ).cast<std::string>().substr(0, 16);
    return "double_bottom:" + upper + ":" + digest;
}

py::dict pattern_payload(const py::list& bars, const Pattern& pattern) {
    py::dict payload;
    payload["left_bottom_trade_date"] = py::str(item_or_none(bar_at(bars, pattern.left_index), "dt_ny"));
    payload["neckline_trade_date"] = py::str(item_or_none(bar_at(bars, pattern.neckline_index), "dt_ny"));
    payload["right_bottom_trade_date"] = py::str(item_or_none(bar_at(bars, pattern.right_index), "dt_ny"));
    payload["breakout_trade_date"] = py::str(item_or_none(bar_at(bars, pattern.breakout_index), "dt_ny"));
    payload["left_bottom_low"] = pattern.left_low;
    payload["right_bottom_low"] = pattern.right_low;
    payload["neckline_price"] = pattern.neckline;
    payload["breakout_close"] = pattern.breakout_close;
    payload["breakout_volume"] = pattern.breakout_volume;
    const auto atr = recent_atr(bars, pattern.breakout_index + 1);
    payload["breakout_atr"] = atr ? py::object(py::cast(*atr)) : py::object(py::none());
    payload["breakout_wait_bars"] = pattern.breakout_index - pattern.right_index;
    payload["bottom_distance_pct"] = pattern.distance;
    payload["breakout_volume_ratio"] = pattern.breakout_volume_ratio;
    payload["rebound_up_day_ratio"] = pattern.rebound_ratio;
    return payload;
}

py::dict staged_setup(
    const std::string& symbol,
    const py::list& bars,
    const RightCandidate& candidate,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg,
    int stage_index,
    const std::string& stage_key,
    const std::optional<Pattern>& pattern = std::nullopt
) {
    const std::string left_date = py::str(item_or_none(bar_at(bars, candidate.left_index), "dt_ny"));
    const std::string neckline_date = py::str(item_or_none(bar_at(bars, candidate.neckline_index), "dt_ny"));
    const std::string right_date = py::str(item_or_none(bar_at(bars, candidate.right_index), "dt_ny"));
    py::dict extra;
    extra["left_bottom_trade_date"] = left_date;
    extra["neckline_trade_date"] = neckline_date;
    extra["right_bottom_trade_date"] = right_date;
    extra["left_bottom_low"] = candidate.left_low;
    extra["right_bottom_low"] = candidate.right_low;
    extra["neckline_price"] = candidate.neckline;
    extra["bottom_distance_pct"] = candidate.distance;
    extra["rebound_up_day_ratio"] = candidate.rebound_ratio;
    if (pattern) {
        const py::dict payload = pattern_payload(bars, *pattern);
        for (const auto& [key, value] : payload) extra[key] = value;
    }
    py::dict anchors;
    anchors["left_bottom_trade_date"] = left_date;
    anchors["right_bottom_trade_date"] = right_date;
    anchors["left_bottom_price"] = candidate.left_low;
    anchors["right_bottom_price"] = candidate.right_low;
    anchors["neckline_price"] = candidate.neckline;
    py::dict setup;
    setup["pattern_type"] = "double_bottom";
    setup["setup_id"] = build_setup_id(symbol, left_date, right_date);
    setup["stage_index"] = stage_index;
    setup["stage_key"] = stage_key;
    const std::string target_key = "stage_" + std::to_string(stage_index) + "_target_pct";
    setup["stage_target_pct"] = risk_cfg[py::str(target_key)];
    setup["anchors"] = anchors;
    setup["invalidation_price"] = std::min(candidate.left_low, candidate.right_low)
        * (1.0 - py::cast<double>(signal_cfg["support_tolerance_pct"]));
    for (const auto& [key, value] : extra) setup[key] = value;
    setup["stage"] = stage_key;
    return setup;
}

bool right_pullback(const py::list& bars, const RightCandidate& candidate, const py::dict& config) {
    const int current = static_cast<int>(py::len(bars)) - 1;
    const int start = candidate.right_index + py::cast<int>(config["left_bottom_after_bars"]) + 1;
    if (current < start + 1) return false;
    const double halfway = candidate.right_low + (candidate.neckline - candidate.right_low) * 0.5;
    double maximum_close = 0.0;
    for (int index = start; index < current; ++index) {
        maximum_close = std::max(maximum_close, bar_number(bars, index, "close").value_or(0.0));
    }
    if (maximum_close < halfway) return false;
    const auto qualifies = [&](int index) {
        const py::dict bar = bar_at(bars, index);
        const auto close = number(bar, "close");
        const auto low = number(bar, "low");
        const auto previous_close = bar_number(bars, index - 1, "close");
        const auto volume = number(bar, "volume");
        const auto average = number(bar, "volume_sma_20");
        return close && low && previous_close && volume && average && *average > 0.0
            && *close < *previous_close && *low > candidate.right_low && *close > candidate.right_low
            && *volume / *average <= py::cast<double>(config["second_bottom_volume_ratio_max"]);
    };
    if (!qualifies(current)) return false;
    for (int index = start; index < current; ++index) if (qualifies(index)) return false;
    return true;
}

std::optional<py::dict> position_setup(const py::dict& snapshot) {
    if (!snapshot.contains("entry_signal_features") || snapshot["entry_signal_features"].is_none()) return std::nullopt;
    const py::dict features = py::cast<py::dict>(snapshot["entry_signal_features"]);
    if (!features.contains("setup") || features["setup"].is_none()) return std::nullopt;
    py::dict setup = py::dict(py::cast<py::dict>(features["setup"]));
    if (!setup.contains("left_bottom_low") || setup["left_bottom_low"].is_none()
        || !setup.contains("right_bottom_low") || setup["right_bottom_low"].is_none()
        || !setup.contains("neckline_price") || setup["neckline_price"].is_none()) return std::nullopt;
    for (const char* key : {
        "breakout_close", "breakout_atr", "breakout_wait_bars", "bottom_distance_pct",
        "breakout_volume_ratio", "rebound_up_day_ratio"
    }) if (!setup.contains(key)) setup[key] = py::none();
    return setup;
}

std::optional<std::tuple<std::string, std::string>> exit_action(
    const py::list& bars,
    const py::dict& setup,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg,
    std::optional<double> average_entry
) {
    const int current_index = static_cast<int>(py::len(bars)) - 1;
    const py::dict current = bar_at(bars, current_index);
    const auto close = number(current, "close");
    const auto low = number(current, "low");
    const auto current_atr = recent_atr(bars, static_cast<int>(py::len(bars)));
    const auto breakout_close = number(setup, "breakout_close");
    const auto breakout_atr = number(setup, "breakout_atr");
    const auto left_low = number(setup, "left_bottom_low");
    const auto right_low = number(setup, "right_bottom_low");
    if (!left_low || !right_low) return std::nullopt;
    const double hard_stop = std::min(*left_low, *right_low)
        * (1.0 - py::cast<double>(signal_cfg["support_tolerance_pct"]));
    if (close && *close < *right_low) {
        return std::tuple{"price closed below the right bottom after confirmation", "right_bottom_break"};
    }
    if (close && average_entry && *average_entry > 0.0
        && *close <= *average_entry * (1.0 - py::cast<double>(risk_cfg["max_loss_pct"]))) {
        return std::tuple{"price fell more than the configured max-loss threshold from entry", "max_loss_stop"};
    }
    if (low && *low < hard_stop) return std::tuple{"price broke below the double-bottom base", "base_break"};
    if (close && breakout_close && breakout_atr
        && *close >= *breakout_close + py::cast<double>(risk_cfg["take_profit_atr"]) * *breakout_atr) {
        return std::tuple{"price reached the ATR take-profit target from the breakout confirmation", "take_profit"};
    }
    const auto stop_anchor = breakout_close ? breakout_close : average_entry;
    if (close && current_atr && stop_anchor
        && *close < *stop_anchor - py::cast<double>(risk_cfg["stop_loss_atr"]) * *current_atr) {
        return std::tuple{"price hit the ATR stop from the breakout confirmation", "atr_stop"};
    }
    return std::nullopt;
}

double score(const py::dict& setup) {
    const double distance = number_or(setup, "bottom_distance_pct", 1.0);
    const double volume_ratio = number_or(setup, "breakout_volume_ratio");
    const double rebound_ratio = number_or(setup, "rebound_up_day_ratio");
    return (1.0 - distance) * 100.0 + volume_ratio + rebound_ratio * 10.0;
}

py::dict strength(const py::dict& snapshot, const py::dict& setup) {
    const auto neckline_price = number(setup, "neckline_price");
    const auto breakout_close = number(setup, "breakout_close");
    const auto breakout_volume = number(setup, "breakout_volume");
    const auto current_volume = number(snapshot, "volume");
    const auto close = number(snapshot, "close");
    const auto right_low = number(setup, "right_bottom_low");
    const auto average_volume = number(snapshot, "volume_sma_20");
    py::dict result;
    result["stage"] = setup.contains("stage_key") && !setup["stage_key"].is_none()
        ? py::reinterpret_borrow<py::object>(setup["stage_key"])
        : item_or_none(setup, "stage");
    result["bottom_distance_pct"] = item_or_none(setup, "bottom_distance_pct");
    result["rebound_up_day_ratio"] = item_or_none(setup, "rebound_up_day_ratio");
    result["current_volume_ratio"] = current_volume && average_volume && *average_volume != 0.0
        ? py::object(py::cast(*current_volume / *average_volume)) : py::object(py::none());
    result["pullback_hold_pct"] = close && right_low && neckline_price
        ? py::object(py::cast((*close - *right_low) / std::max(*neckline_price - *right_low, 1e-12)))
        : py::object(py::none());
    result["breakout_volume_ratio"] = item_or_none(setup, "breakout_volume_ratio");
    result["breakout_extension_pct"] = breakout_close && neckline_price && *neckline_price > 0.0
        ? py::object(py::cast(*breakout_close / *neckline_price - 1.0)) : py::object(py::none());
    result["retest_volume_ratio"] = current_volume && breakout_volume && *breakout_volume > 0.0
        ? py::object(py::cast(*current_volume / *breakout_volume)) : py::object(py::none());
    return result;
}

py::object timestamp(const py::dict& snapshot) {
    if (snapshot.contains("ts") && !snapshot["ts"].is_none()) {
        return py::reinterpret_borrow<py::object>(snapshot["ts"]);
    }
    const py::module_ datetime_module = py::module_::import("datetime");
    return datetime_module.attr("datetime").attr("now")(datetime_module.attr("timezone").attr("utc"));
}

}  // namespace

py::object evaluate_double_bottom_event(
    const py::dict& runtime,
    const std::string& symbol,
    const py::dict& snapshot,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    const py::list bars = py::cast<py::list>(snapshot["recent_bars"]);
    if (py::len(bars) == 0) return py::none();
    const ReplayState state = replay(bars, signal_cfg);
    const RightCandidate* candidate = state.right.empty() ? nullptr : &state.right.back();
    std::optional<Pattern> pattern = state.best;
    py::dict setup;
    std::optional<std::string> action;
    std::optional<std::string> reason;
    std::optional<std::string> stage;
    if (number_or(snapshot, "position") > 0.0) {
        const auto stored = position_setup(snapshot);
        if (stored) setup = *stored;
        else if (pattern) setup = pattern_payload(bars, *pattern);
        if (py::len(setup) > 0) {
            const auto exit = exit_action(
                bars, setup, signal_cfg, risk_cfg, number(snapshot, "avg_entry_price")
            );
            if (exit) {
                action = "SELL";
                reason = std::get<0>(*exit);
                stage = std::get<1>(*exit);
                setup["exit_stage"] = *stage;
            }
        }
    }
    if (!action) {
        const int current = static_cast<int>(py::len(bars)) - 1;
        if (pattern && current == pattern->breakout_index) {
            const RightCandidate converted{
                pattern->left_index, pattern->neckline_index, pattern->right_index,
                pattern->left_low, pattern->right_low, pattern->neckline,
                pattern->distance, pattern->rebound_ratio
            };
            setup = staged_setup(
                symbol, bars, converted, signal_cfg, risk_cfg, 3,
                "neckline_breakout", pattern
            );
            action = "BUY";
            reason = "broke above the double-bottom neckline on confirming volume";
            stage = "neckline_breakout";
        } else if (candidate && current == candidate->right_index
                   + py::cast<int>(signal_cfg["left_bottom_after_bars"])) {
            setup = staged_setup(
                symbol, bars, *candidate, signal_cfg, risk_cfg, 1, "second_bottom"
            );
            action = "BUY";
            reason = "confirmed a low-volume second bottom";
            stage = "second_bottom";
        } else if (candidate && right_pullback(bars, *candidate, signal_cfg)) {
            setup = staged_setup(
                symbol, bars, *candidate, signal_cfg, risk_cfg, 2, "right_side_pullback"
            );
            action = "BUY";
            reason = "confirmed the first low-volume right-side pullback above the second bottom";
            stage = "right_side_pullback";
        }
    }
    if (!action) return py::none();
    py::dict metadata;
    for (const char* key : {"close", "open", "high", "low", "volume", "atr_14"}) {
        metadata[key] = item_or_none(snapshot, key);
    }
    metadata["position"] = number_or(snapshot, "position");
    metadata["avg_entry_price"] = item_or_none(snapshot, "avg_entry_price");
    metadata["setup"] = setup;
    metadata["strength_inputs"] = strength(snapshot, setup);
    py::dict config;
    for (const char* key : {
        "downtrend_min_drop_pct", "downtrend_max_up_day_ratio", "downtrend_min_r_squared",
        "bottom_tolerance_pct", "left_bottom_before_bars", "left_bottom_after_bars",
        "neckline_min_rebound_pct", "breakout_buffer_pct", "breakout_volume_ratio_min",
        "max_breakout_bars_after_right_bottom", "retest_window", "support_tolerance_pct"
    }) config[key] = signal_cfg[key];
    config["max_loss_pct"] = risk_cfg["max_loss_pct"];
    config["take_profit_atr"] = risk_cfg["take_profit_atr"];
    metadata["config"] = config;
    py::dict event;
    event["strategy_id"] = runtime["strategy_id"];
    event["ts"] = timestamp(snapshot);
    event["symbol"] = symbol;
    event["action"] = *action;
    event["reason"] = *reason;
    event["score"] = score(setup);
    event["metadata"] = metadata;
    event["instrument_id"] = py::none();
    return std::move(event);
}

}  // namespace quant_kernel
