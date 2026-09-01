#include "pattern_kernel.hpp"
#include "double_bottom_kernel.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

struct Decision {
    std::string action;
    std::string reason;
    py::dict setup;
    std::optional<double> score;
    py::dict strength_inputs;
};

struct IslandPattern {
    int left_gap_idx;
    int breakout_idx;
    double island_low;
    double island_high;
    double breakout_gap_low;
    double breakout_close;
    double breakout_volume;
    double breakout_volume_ratio;
    double left_gap_pct;
    double breakout_gap_pct;
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

std::optional<double> volume_ratio(const py::dict& bar) {
    const auto volume = number(bar, "volume");
    const auto average = number(bar, "volume_sma_20");
    if (!volume || !average || *average <= 0.0) return std::nullopt;
    return *volume / *average;
}

std::vector<std::string> universe(const py::dict& params, const py::dict& market) {
    const py::dict config = py::cast<py::dict>(params["universe"]);
    if (config.contains("symbols") && !config["symbols"].is_none()) {
        auto symbols = py::cast<std::vector<std::string>>(config["symbols"]);
        if (!symbols.empty()) return symbols;
    }
    const std::string selection_mode = config.contains("selection_mode")
        ? py::cast<std::string>(config["selection_mode"])
        : "";
    std::vector<std::string> symbols;
    for (const auto& [raw_symbol, raw_snapshot] : market) {
        const py::dict snapshot = py::cast<py::dict>(raw_snapshot);
        if (selection_mode == "all_common_stock") {
            std::string asset_type = snapshot.contains("asset_type") && !snapshot["asset_type"].is_none()
                ? py::cast<std::string>(snapshot["asset_type"])
                : "";
            std::transform(
                asset_type.begin(),
                asset_type.end(),
                asset_type.begin(),
                [](unsigned char character) { return static_cast<char>(std::toupper(character)); }
            );
            if (asset_type != "CS") continue;
        }
        symbols.push_back(py::cast<std::string>(raw_symbol));
    }
    std::sort(symbols.begin(), symbols.end());
    return symbols;
}

py::object raw_timestamp(const py::dict& snapshot) {
    if (snapshot.contains("ts") && !snapshot["ts"].is_none()) {
        return py::reinterpret_borrow<py::object>(snapshot["ts"]);
    }
    const py::module_ datetime_module = py::module_::import("datetime");
    return datetime_module.attr("datetime").attr("now")(datetime_module.attr("timezone").attr("utc"));
}

std::string date_text(const py::dict& bar) {
    return py::str(item_or_none(bar, "dt_ny"));
}

double unit(double value) {
    value = std::min(std::max(value, 0.0), 1.0);
    return std::round(value * 1'000'000.0) / 1'000'000.0;
}

std::string setup_id(
    const std::string& pattern_type,
    const std::string& symbol,
    const py::tuple& anchors
) {
    py::list payload;
    payload.append(pattern_type);
    std::string upper_symbol = symbol;
    std::transform(
        upper_symbol.begin(),
        upper_symbol.end(),
        upper_symbol.begin(),
        [](unsigned char character) { return static_cast<char>(std::toupper(character)); }
    );
    payload.append(upper_symbol);
    for (const py::handle anchor : anchors) payload.append(py::str(anchor));
    const py::object serialized = py::module_::import("json").attr("dumps")(
        payload,
        py::arg("ensure_ascii") = true,
        py::arg("separators") = py::make_tuple(",", ":")
    );
    const std::string digest = py::str(
        py::module_::import("hashlib").attr("sha256")(py::bytes(py::str(serialized))).attr("hexdigest")()
    ).cast<std::string>().substr(0, 16);
    return pattern_type + ":" + upper_symbol + ":" + digest;
}

py::dict build_setup(
    const std::string& pattern_type,
    const std::string& symbol,
    int stage_index,
    const std::string& stage_key,
    const py::dict& risk,
    const py::dict& anchors,
    double invalidation_price,
    const py::tuple& setup_anchors,
    const py::dict& extra
) {
    const std::string target_key = "stage_" + std::to_string(stage_index) + "_target_pct";
    py::dict setup;
    setup["pattern_type"] = pattern_type;
    setup["setup_id"] = setup_id(pattern_type, symbol, setup_anchors);
    setup["stage_index"] = stage_index;
    setup["stage_key"] = stage_key;
    setup["stage_target_pct"] = risk[py::str(target_key)];
    setup["anchors"] = py::dict(anchors);
    setup["invalidation_price"] = invalidation_price;
    for (const auto& [key, value] : extra) setup[key] = value;
    setup["stage"] = stage_key;
    return setup;
}

Decision buy_decision(
    const std::string& reason,
    py::dict setup,
    double structure,
    double price,
    double volume,
    double stage,
    std::optional<double> score = std::nullopt
) {
    py::dict strength;
    strength["structure_quality"] = unit(structure);
    strength["price_confirmation"] = unit(price);
    strength["volume_quality"] = unit(volume);
    strength["stage_confirmation"] = unit(stage);
    return {"BUY", reason, std::move(setup), score, std::move(strength)};
}

py::dict stored_setup(const py::dict& snapshot) {
    if (!snapshot.contains("entry_signal_features") || snapshot["entry_signal_features"].is_none()) {
        return py::dict();
    }
    const py::dict features = py::cast<py::dict>(snapshot["entry_signal_features"]);
    if (!features.contains("setup") || features["setup"].is_none()) return py::dict();
    return py::dict(py::cast<py::dict>(features["setup"]));
}

std::optional<Decision> position_exit(
    const std::string& pattern_type,
    const std::string& symbol,
    const py::list& bars,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg,
    double position,
    std::optional<double> average_entry,
    py::dict setup
) {
    if (position <= 0.0 || py::len(bars) == 0) return std::nullopt;
    const py::dict current = bar_at(bars, static_cast<int>(py::len(bars)) - 1);
    const auto close = number(current, "close");
    const auto low = number(current, "low");
    const auto atr = number(current, "atr_14");
    if (py::len(setup) == 0) {
        setup["pattern_type"] = pattern_type;
        setup["setup_id"] = pattern_type + ":" + symbol + ":position";
        setup["stage_index"] = 3;
        setup["stage_key"] = "position";
        setup["stage_target_pct"] = 1.0;
        setup["stage"] = "position";
        setup["anchors"] = py::dict();
        setup["invalidation_price"] = py::none();
    }
    std::optional<std::string> reason;
    std::optional<std::string> stage_key;
    const auto invalidation = number(setup, "invalidation_price");
    if (invalidation && low && *low < *invalidation) {
        reason = "pattern invalidation price was breached";
        stage_key = "pattern_invalidation";
    } else if (close && average_entry && *average_entry > 0.0
               && *close <= *average_entry * (1.0 - py::cast<double>(risk_cfg["max_loss_pct"]))) {
        reason = "price fell through the configured maximum loss";
        stage_key = "max_loss_stop";
    } else if (close && atr && average_entry
               && *close <= *average_entry - py::cast<double>(risk_cfg["stop_loss_atr"]) * *atr) {
        reason = "price hit the ATR stop";
        stage_key = "atr_stop";
    } else if (close && atr && average_entry
               && *close >= *average_entry + py::cast<double>(risk_cfg["take_profit_atr"]) * *atr) {
        reason = "price reached the ATR take-profit target";
        stage_key = "take_profit";
    } else if (pattern_type == "v_reversal") {
        const auto open = number(current, "open");
        const auto ratio = volume_ratio(current);
        const int current_stage = setup.contains("stage_index") && !setup["stage_index"].is_none()
            ? py::cast<int>(setup["stage_index"])
            : 0;
        if (current_stage < 3 && close && open && *close < *open && ratio
            && *ratio >= py::cast<double>(signal_cfg["bearish_reversal_volume_ratio_min"])) {
            reason = "high-volume bearish reversal invalidated the V setup";
            stage_key = "bearish_volume_failure";
        }
    }
    if (!reason) return std::nullopt;
    setup["exit_stage"] = *stage_key;
    py::dict strength;
    strength["structure_quality"] = 1.0;
    strength["price_confirmation"] = 1.0;
    strength["volume_quality"] = 1.0;
    strength["stage_confirmation"] = 1.0;
    return Decision{"SELL", *reason, std::move(setup), std::nullopt, std::move(strength)};
}

std::vector<int> confirmed_pivot_lows(const py::list& bars, int left, int right) {
    std::vector<int> pivots;
    const int size = static_cast<int>(py::len(bars));
    for (int index = left; index < size - right; ++index) {
        const auto low = bar_number(bars, index, "low");
        if (!low) continue;
        bool all = true;
        bool any = false;
        for (int position = index - left; position <= index + right; ++position) {
            if (position == index) continue;
            const auto neighbor = bar_number(bars, position, "low");
            if (!neighbor || *low > *neighbor) all = false;
            if (neighbor && *low < *neighbor) any = true;
        }
        if (all && any) pivots.push_back(index);
    }
    return pivots;
}

bool downtrend_context(
    const py::list& bars,
    int index,
    int lookback,
    double minimum_drop
) {
    const auto current = bar_number(bars, index, "close");
    if (!current) return false;
    std::optional<double> maximum;
    for (int position = std::max(0, index - lookback); position < index; ++position) {
        const auto close = bar_number(bars, position, "close");
        if (close) maximum = maximum ? std::max(*maximum, *close) : *close;
    }
    return maximum && (*maximum - *current) / *maximum >= minimum_drop;
}

std::optional<int> highest_index(const py::list& bars, int start, int end) {
    if (end <= start) return std::nullopt;
    std::optional<int> selected;
    std::optional<double> maximum;
    for (int index = start; index <= end; ++index) {
        const auto high = bar_number(bars, index, "high");
        if (high && (!maximum || *high > *maximum)) {
            maximum = *high;
            selected = index;
        }
    }
    return selected;
}

double project_line(int x1, double y1, int x2, double y2, int x) {
    return y1 + ((y2 - y1) / static_cast<double>(x2 - x1)) * static_cast<double>(x - x1);
}

std::optional<std::tuple<double, double, double>> quadratic_fit(const std::vector<double>& values) {
    const int count = static_cast<int>(values.size());
    if (count < 3) return std::nullopt;
    std::vector<double> xs;
    xs.reserve(count);
    for (int index = 0; index < count; ++index) xs.push_back(static_cast<double>(index) / (count - 1));
    double sums[5] = {};
    double rhs[3] = {};
    for (int power = 0; power < 5; ++power) {
        for (const double x : xs) sums[power] += std::pow(x, power);
    }
    for (int power = 0; power < 3; ++power) {
        for (int index = 0; index < count; ++index) rhs[power] += std::pow(xs[index], power) * values[index];
    }
    double matrix[3][4] = {
        {sums[0], sums[1], sums[2], rhs[0]},
        {sums[1], sums[2], sums[3], rhs[1]},
        {sums[2], sums[3], sums[4], rhs[2]},
    };
    for (int column = 0; column < 3; ++column) {
        int pivot = column;
        for (int row = column + 1; row < 3; ++row) {
            if (std::abs(matrix[row][column]) > std::abs(matrix[pivot][column])) pivot = row;
        }
        if (std::abs(matrix[pivot][column]) < 1e-12) return std::nullopt;
        for (int item = 0; item < 4; ++item) std::swap(matrix[column][item], matrix[pivot][item]);
        const double divisor = matrix[column][column];
        for (double& value : matrix[column]) value /= divisor;
        for (int row = 0; row < 3; ++row) {
            if (row == column) continue;
            const double factor = matrix[row][column];
            for (int item = 0; item < 4; ++item) matrix[row][item] -= factor * matrix[column][item];
        }
    }
    const double intercept = matrix[0][3];
    const double linear = matrix[1][3];
    const double quadratic = matrix[2][3];
    if (quadratic == 0.0) return std::nullopt;
    const double vertex = -linear / (2.0 * quadratic);
    double mean = 0.0;
    for (const double value : values) mean += value;
    mean /= count;
    double total = 0.0;
    double residual = 0.0;
    for (int index = 0; index < count; ++index) {
        const double predicted = intercept + linear * xs[index] + quadratic * xs[index] * xs[index];
        total += std::pow(values[index] - mean, 2.0);
        residual += std::pow(values[index] - predicted, 2.0);
    }
    return std::tuple{quadratic, vertex, total > 0.0 ? 1.0 - residual / total : 0.0};
}

std::optional<Decision> evaluate_head_shoulders(
    const std::string& symbol,
    const py::dict& snapshot,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    const py::list bars = py::cast<py::list>(snapshot["recent_bars"]);
    if (py::len(bars) == 0) return std::nullopt;
    const double position = number_or(snapshot, "position");
    const auto average_entry = number(snapshot, "avg_entry_price");
    if (auto exit = position_exit(
            "head_shoulders_bottom", symbol, bars, signal_cfg, risk_cfg,
            position, average_entry, stored_setup(snapshot))) return exit;
    const int left = py::cast<int>(signal_cfg["pivot_left_bars"]);
    const int right = py::cast<int>(signal_cfg["pivot_right_bars"]);
    const auto pivots = confirmed_pivot_lows(bars, left, right);
    if (pivots.size() < 2) return std::nullopt;
    const int current_index = static_cast<int>(py::len(bars)) - 1;
    const int min_gap = py::cast<int>(signal_cfg["min_segment_bars"]);
    const int max_gap = py::cast<int>(signal_cfg["max_segment_bars"]);
    const double shoulder_tolerance = py::cast<double>(signal_cfg["shoulder_tolerance_pct"]);
    const double head_depth = py::cast<double>(signal_cfg["head_depth_min_pct"]);
    for (int offset = static_cast<int>(pivots.size()) - 2; offset >= 0; --offset) {
        const int left_index = pivots[offset];
        const int head_index = pivots[offset + 1];
        const int gap = head_index - left_index;
        const auto left_low = bar_number(bars, left_index, "low");
        const auto head_low = bar_number(bars, head_index, "low");
        if (gap < min_gap || gap > max_gap || !left_low || !head_low
            || *head_low > *left_low * (1.0 - head_depth)) continue;
        if (!downtrend_context(
                bars,
                left_index,
                py::cast<int>(signal_cfg["downtrend_lookback"]),
                py::cast<double>(signal_cfg["downtrend_min_drop_pct"]))) continue;
        const auto head_volume = volume_ratio(bar_at(bars, head_index));
        if (!head_volume || *head_volume > py::cast<double>(signal_cfg["head_volume_ratio_max"])) continue;
        if (current_index == head_index + right) {
            const double quality = std::min(
                (*left_low - *head_low) / std::max(*left_low * head_depth, 1e-12),
                1.0
            );
            py::dict anchors;
            anchors["left_shoulder"] = date_text(bar_at(bars, left_index));
            anchors["head"] = date_text(bar_at(bars, head_index));
            py::dict extra;
            extra["left_shoulder_low"] = *left_low;
            extra["head_low"] = *head_low;
            py::dict setup = build_setup(
                "head_shoulders_bottom", symbol, 1, "head_candidate", risk_cfg,
                anchors, *head_low,
                py::make_tuple(item_or_none(bar_at(bars, left_index), "dt_ny"), item_or_none(bar_at(bars, head_index), "dt_ny")),
                extra
            );
            return buy_decision(
                "confirmed a low-volume head candidate", setup, quality, quality,
                1.0 - *head_volume / py::cast<double>(signal_cfg["head_volume_ratio_max"]),
                1.0 / 3.0
            );
        }
    }
    if (pivots.size() < 3) return std::nullopt;
    for (int offset = static_cast<int>(pivots.size()) - 3; offset >= 0; --offset) {
        const int left_index = pivots[offset];
        const int head_index = pivots[offset + 1];
        const int shoulder_index = pivots[offset + 2];
        if (head_index - left_index < min_gap || head_index - left_index > max_gap
            || shoulder_index - head_index < min_gap || shoulder_index - head_index > max_gap) continue;
        const auto left_low = bar_number(bars, left_index, "low");
        const auto head_low = bar_number(bars, head_index, "low");
        const auto shoulder_low = bar_number(bars, shoulder_index, "low");
        if (!left_low || !head_low || !shoulder_low) continue;
        const double shoulder_distance = std::abs(*shoulder_low - *left_low) / *left_low;
        if (shoulder_distance > shoulder_tolerance
            || *head_low > std::min(*left_low, *shoulder_low) * (1.0 - head_depth)) continue;
        const auto shoulder_volume = volume_ratio(bar_at(bars, shoulder_index));
        if (!shoulder_volume
            || *shoulder_volume > py::cast<double>(signal_cfg["right_shoulder_volume_ratio_max"])) continue;
        const auto first_high_index = highest_index(bars, left_index, head_index);
        const auto second_high_index = highest_index(bars, head_index, shoulder_index);
        if (!first_high_index || !second_high_index || *first_high_index == *second_high_index) continue;
        const auto first_high = bar_number(bars, *first_high_index, "high");
        const auto second_high = bar_number(bars, *second_high_index, "high");
        if (!first_high || !second_high) continue;
        const double neckline = project_line(
            *first_high_index, *first_high, *second_high_index, *second_high, current_index
        );
        py::dict anchors;
        anchors["left_shoulder"] = date_text(bar_at(bars, left_index));
        anchors["head"] = date_text(bar_at(bars, head_index));
        anchors["right_shoulder"] = date_text(bar_at(bars, shoulder_index));
        anchors["neckline_1"] = date_text(bar_at(bars, *first_high_index));
        anchors["neckline_2"] = date_text(bar_at(bars, *second_high_index));
        const py::tuple id_anchors = py::make_tuple(
            item_or_none(bar_at(bars, left_index), "dt_ny"),
            item_or_none(bar_at(bars, head_index), "dt_ny")
        );
        const double structure = std::max(0.0, 1.0 - shoulder_distance / shoulder_tolerance);
        py::dict extra;
        extra["neckline_price"] = neckline;
        extra["head_low"] = *head_low;
        if (current_index == shoulder_index + right) {
            py::dict setup = build_setup(
                "head_shoulders_bottom", symbol, 2, "right_shoulder", risk_cfg,
                anchors, *head_low, id_anchors, extra
            );
            return buy_decision(
                "confirmed a low-volume right shoulder", setup, structure, structure,
                std::max(
                    0.0,
                    1.0 - *shoulder_volume / py::cast<double>(signal_cfg["right_shoulder_volume_ratio_max"])
                ),
                2.0 / 3.0
            );
        }
        const auto close = bar_number(bars, current_index, "close");
        const auto current_volume = volume_ratio(bar_at(bars, current_index));
        const double buffer = py::cast<double>(signal_cfg["breakout_buffer_pct"]);
        if (close && current_volume && *close >= neckline * (1.0 + buffer)
            && *current_volume >= py::cast<double>(signal_cfg["breakout_volume_ratio_min"])) {
            py::dict setup = build_setup(
                "head_shoulders_bottom", symbol, 3, "neckline_breakout", risk_cfg,
                anchors, *head_low, id_anchors, extra
            );
            return buy_decision(
                "broke above the projected neckline on confirming volume", setup, structure,
                std::min(std::max((*close / neckline - 1.0) / std::max(buffer * 2.0, 1e-12), 0.0), 1.0),
                std::min(
                    *current_volume / (py::cast<double>(signal_cfg["breakout_volume_ratio_min"]) * 2.0),
                    1.0
                ),
                1.0
            );
        }
    }
    return std::nullopt;
}

std::optional<int> v_anchor(const py::list& bars, const py::dict& config) {
    const int lookback = py::cast<int>(config["downtrend_lookback"]);
    const int pivot_bars = py::cast<int>(config["pivot_max_bars"]);
    for (int index = static_cast<int>(py::len(bars)) - 1; index >= std::max(lookback - 1, 0); --index) {
        const py::dict bar = bar_at(bars, index);
        const auto open = number(bar, "open");
        const auto close = number(bar, "close");
        const auto low = number(bar, "low");
        const auto atr = number(bar, "atr_14");
        const auto ratio = volume_ratio(bar);
        if (!open || !close || !low || !atr || !ratio || *atr == 0.0 || *low == 0.0) continue;
        if (*close <= *open
            || (*close - *low) / *low < py::cast<double>(config["reversal_min_return_pct"])
            || (*close - *low) / *atr < py::cast<double>(config["reversal_min_atr"])
            || *ratio < py::cast<double>(config["pivot_volume_ratio_min"])) continue;
        std::optional<double> minimum_low;
        for (int item = std::max(0, index - pivot_bars + 1); item <= index; ++item) {
            const auto value = bar_number(bars, item, "low");
            if (value) minimum_low = minimum_low ? std::min(*minimum_low, *value) : *value;
        }
        if (!minimum_low || *low > *minimum_low) continue;
        std::optional<double> maximum_close;
        for (int item = std::max(0, index - lookback); item < index; ++item) {
            const auto value = bar_number(bars, item, "close");
            if (value) maximum_close = maximum_close ? std::max(*maximum_close, *value) : *value;
        }
        if (!maximum_close
            || (*maximum_close - *low) / *maximum_close < py::cast<double>(config["downtrend_min_drop_pct"])) continue;
        return index;
    }
    return std::nullopt;
}

std::optional<std::pair<int, double>> v_retest(
    const py::list& bars,
    int anchor_index,
    const py::dict& config
) {
    const int current_index = static_cast<int>(py::len(bars)) - 1;
    const int min_bars = py::cast<int>(config["consolidation_min_bars"]);
    const int max_bars = py::cast<int>(config["consolidation_max_bars"]);
    const int retest_window = py::cast<int>(config["retest_window"]);
    const double tolerance = py::cast<double>(config["support_tolerance_pct"]);
    const auto current_low = bar_number(bars, current_index, "low");
    const auto current_close = bar_number(bars, current_index, "close");
    const auto current_volume = bar_number(bars, current_index, "volume");
    if (!current_low || !current_close || !current_volume) return std::nullopt;
    const int first_breakout = std::max(anchor_index + min_bars + 1, current_index - retest_window);
    for (int breakout_index = first_breakout; breakout_index < current_index; ++breakout_index) {
        const int start = std::max(anchor_index + 1, breakout_index - max_bars);
        const int size = breakout_index - start;
        if (size < min_bars || size > max_bars) continue;
        double top = -std::numeric_limits<double>::infinity();
        bool missing = false;
        for (int item = start; item < breakout_index; ++item) {
            const auto high = bar_number(bars, item, "high");
            if (!high) { missing = true; break; }
            top = std::max(top, *high);
        }
        if (missing) continue;
        const py::dict breakout = bar_at(bars, breakout_index);
        const auto breakout_close = number(breakout, "close");
        const auto breakout_ratio = volume_ratio(breakout);
        const auto breakout_volume = number(breakout, "volume");
        if (!breakout_close || !breakout_ratio || !breakout_volume
            || *breakout_close <= top
            || *breakout_ratio < py::cast<double>(config["breakout_volume_ratio_min"])) continue;
        if (*current_low >= top * (1.0 - tolerance) && *current_close >= top
            && *current_volume <= *breakout_volume * py::cast<double>(config["retest_volume_ratio_max"])) {
            return std::pair{breakout_index, top};
        }
    }
    return std::nullopt;
}

std::optional<Decision> evaluate_v_reversal(
    const std::string& symbol,
    const py::dict& snapshot,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    const py::list bars = py::cast<py::list>(snapshot["recent_bars"]);
    if (py::len(bars) == 0) return std::nullopt;
    const double position = number_or(snapshot, "position");
    const auto average_entry = number(snapshot, "avg_entry_price");
    if (auto exit = position_exit(
            "v_reversal", symbol, bars, signal_cfg, risk_cfg,
            position, average_entry, stored_setup(snapshot))) return exit;
    const auto anchor_index = v_anchor(bars, signal_cfg);
    if (!anchor_index) return std::nullopt;
    const int current_index = static_cast<int>(py::len(bars)) - 1;
    const py::dict anchor = bar_at(bars, *anchor_index);
    const double anchor_low = *number(anchor, "low");
    const double anchor_close = *number(anchor, "close");
    py::dict anchors;
    anchors["pivot"] = date_text(anchor);
    const py::tuple id_anchors = py::make_tuple(item_or_none(anchor, "dt_ny"));
    const double reversal_return = (anchor_close - anchor_low) / anchor_low;
    const double anchor_volume = volume_ratio(anchor).value_or(py::cast<double>(signal_cfg["pivot_volume_ratio_min"]));
    py::dict extra;
    extra["pivot_low"] = anchor_low;
    if (current_index == *anchor_index) {
        py::dict setup = build_setup(
            "v_reversal", symbol, 1, "volume_pivot", risk_cfg, anchors,
            anchor_low, id_anchors, extra
        );
        return buy_decision(
            "confirmed a high-volume V reversal pivot", setup,
            std::min(reversal_return / (py::cast<double>(signal_cfg["reversal_min_return_pct"]) * 2.0), 1.0),
            std::min(reversal_return / (py::cast<double>(signal_cfg["reversal_min_return_pct"]) * 2.0), 1.0),
            std::min(anchor_volume / (py::cast<double>(signal_cfg["pivot_volume_ratio_min"]) * 2.0), 1.0),
            1.0 / 3.0
        );
    }
    const int distance = current_index - *anchor_index;
    const py::dict current = bar_at(bars, current_index);
    const auto close = number(current, "close");
    const auto open = number(current, "open");
    const auto current_ratio = volume_ratio(current);
    bool continuous = current_index - *anchor_index >= 2;
    std::optional<double> previous_close;
    for (int item = *anchor_index + 1; continuous && item <= current_index; ++item) {
        const py::dict bar = bar_at(bars, item);
        const auto item_close = number(bar, "close");
        const auto item_open = number(bar, "open");
        const auto ratio = volume_ratio(bar);
        if (!item_close || !item_open || *item_close <= *item_open || !ratio
            || *ratio < py::cast<double>(signal_cfg["continuation_volume_ratio_min"])
            || (previous_close && *item_close <= *previous_close)) continuous = false;
        previous_close = item_close;
    }
    if (distance >= 2 && distance <= py::cast<int>(signal_cfg["continuation_window"])
        && continuous && close && open && *close > *open && *close > anchor_close && current_ratio
        && *current_ratio >= py::cast<double>(signal_cfg["continuation_volume_ratio_min"])) {
        py::dict setup = build_setup(
            "v_reversal", symbol, 2, "continuation", risk_cfg, anchors,
            anchor_low, id_anchors, extra
        );
        const double price_quality = std::min(
            (*close / anchor_close - 1.0) / std::max(py::cast<double>(signal_cfg["reversal_min_return_pct"]), 1e-12),
            1.0
        );
        return buy_decision(
            "continued higher with confirming volume after the V pivot", setup,
            price_quality, price_quality,
            std::min(
                *current_ratio / (py::cast<double>(signal_cfg["continuation_volume_ratio_min"]) * 2.0),
                1.0
            ),
            2.0 / 3.0
        );
    }
    const auto retest = v_retest(bars, *anchor_index, signal_cfg);
    if (!retest) return std::nullopt;
    const int breakout_index = retest->first;
    const double top = retest->second;
    anchors["breakout"] = date_text(bar_at(bars, breakout_index));
    extra["consolidation_top"] = top;
    py::dict setup = build_setup(
        "v_reversal", symbol, 3, "top_breakout_retest", risk_cfg, anchors,
        anchor_low, id_anchors, extra
    );
    const py::dict breakout = bar_at(bars, breakout_index);
    const double breakout_ratio = volume_ratio(breakout).value_or(
        py::cast<double>(signal_cfg["breakout_volume_ratio_min"])
    );
    const double current_volume = number_or(current, "volume");
    const double breakout_raw_volume = number_or(breakout, "volume", 1.0);
    const double volume_quality = std::max(
        0.0,
        1.0 - current_volume / std::max(
            breakout_raw_volume * py::cast<double>(signal_cfg["retest_volume_ratio_max"]),
            1e-12
        )
    );
    const double price_quality = std::min(
        std::max(
            (*close - top) / std::max(top * py::cast<double>(signal_cfg["support_tolerance_pct"]), 1e-12),
            0.0
        ),
        1.0
    );
    return buy_decision(
        "low-volume retest held the V consolidation top", setup,
        std::min(
            breakout_ratio / (py::cast<double>(signal_cfg["breakout_volume_ratio_min"]) * 2.0),
            1.0
        ),
        price_quality,
        volume_quality,
        1.0
    );
}

std::optional<Decision> evaluate_rounded_bottom(
    const std::string& symbol,
    const py::dict& snapshot,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    const py::list bars = py::cast<py::list>(snapshot["recent_bars"]);
    if (py::len(bars) == 0) return std::nullopt;
    const double position = number_or(snapshot, "position");
    const auto average_entry = number(snapshot, "avg_entry_price");
    if (auto exit = position_exit(
            "rounded_bottom", symbol, bars, signal_cfg, risk_cfg,
            position, average_entry, stored_setup(snapshot))) return exit;
    const int count = static_cast<int>(py::len(bars));
    const int minimum = py::cast<int>(signal_cfg["min_lookback"]);
    if (count < minimum) return std::nullopt;
    const int window_size = std::min(count, py::cast<int>(signal_cfg["max_lookback"]));
    const int window_start = count - window_size;
    std::vector<double> closes;
    closes.reserve(window_size);
    for (int index = window_start; index < count; ++index) {
        const auto close = bar_number(bars, index, "close");
        if (!close || *close <= 0.0) return std::nullopt;
        closes.push_back(*close);
    }
    std::vector<double> logs;
    logs.reserve(window_size);
    for (const double close : closes) logs.push_back(std::log(close));
    const auto fit = quadratic_fit(logs);
    if (!fit) return std::nullopt;
    const auto [curvature, vertex, r_squared] = *fit;
    if (curvature <= 0.0 || r_squared < py::cast<double>(signal_cfg["min_r_squared"])
        || vertex < py::cast<double>(signal_cfg["vertex_position_min"])
        || vertex > py::cast<double>(signal_cfg["vertex_position_max"])) return std::nullopt;
    const int bottom_local = static_cast<int>(std::min_element(closes.begin(), closes.end()) - closes.begin());
    const int bottom_index = window_start + bottom_local;
    const double bottom_close = closes[bottom_local];
    double left_rim = -std::numeric_limits<double>::infinity();
    const int rim_count = std::max(3, window_size / 10);
    for (int index = window_start; index < window_start + rim_count; ++index) {
        const auto high = bar_number(bars, index, "high");
        if (!high) return std::nullopt;
        left_rim = std::max(left_rim, *high);
    }
    const double depth = left_rim > 0.0 ? (left_rim - bottom_close) / left_rim : 0.0;
    if (depth < py::cast<double>(signal_cfg["min_depth_pct"])) return std::nullopt;
    const int right = py::cast<int>(signal_cfg["pivot_right_bars"]);
    const auto all_pivots = confirmed_pivot_lows(
        bars,
        py::cast<int>(signal_cfg["pivot_left_bars"]),
        right
    );
    std::vector<int> qualified;
    for (const int pivot : all_pivots) {
        if (pivot <= bottom_index) continue;
        const auto pivot_volume = volume_ratio(bar_at(bars, pivot));
        double surge = 0.0;
        for (int item = std::max(bottom_index + 1, pivot - 5); item < pivot; ++item) {
            surge = std::max(surge, volume_ratio(bar_at(bars, item)).value_or(0.0));
        }
        if (!pivot_volume || *pivot_volume > py::cast<double>(signal_cfg["pullback_volume_ratio_max"])
            || surge < py::cast<double>(signal_cfg["right_volume_ratio_min"])) continue;
        if (!qualified.empty()) {
            if (pivot - qualified.back() < py::cast<int>(signal_cfg["min_pullback_spacing"])) continue;
            if (*bar_number(bars, pivot, "low") <= *bar_number(bars, qualified.back(), "low")) continue;
        }
        qualified.push_back(pivot);
    }
    py::dict anchors;
    anchors["bottom"] = date_text(bar_at(bars, bottom_index));
    py::list pullbacks;
    for (int index = 0; index < std::min<int>(2, qualified.size()); ++index) {
        pullbacks.append(date_text(bar_at(bars, qualified[index])));
    }
    anchors["pullbacks"] = pullbacks;
    const py::tuple id_anchors = py::make_tuple(item_or_none(bar_at(bars, bottom_index), "dt_ny"));
    const double structure = std::min(
        std::max(
            (r_squared - py::cast<double>(signal_cfg["min_r_squared"]))
                / std::max(1.0 - py::cast<double>(signal_cfg["min_r_squared"]), 1e-12),
            0.0
        ),
        1.0
    );
    py::dict extra;
    extra["r_squared"] = r_squared;
    extra["depth_pct"] = depth;
    extra["rim_price"] = left_rim;
    extra["vertex_position"] = vertex;
    const int current_index = count - 1;
    for (int stage = 1; stage <= std::min<int>(2, qualified.size()); ++stage) {
        const int pivot = qualified[stage - 1];
        if (current_index != pivot + right) continue;
        const std::string key = stage == 1 ? "first_right_pullback" : "second_right_pullback";
        py::dict setup = build_setup(
            "rounded_bottom", symbol, stage, key, risk_cfg, anchors,
            bottom_close, id_anchors, extra
        );
        const double ratio = volume_ratio(bar_at(bars, pivot)).value_or(
            py::cast<double>(signal_cfg["pullback_volume_ratio_max"])
        );
        return buy_decision(
            "confirmed a higher low-volume pullback on the bowl's right side", setup,
            structure,
            std::min(depth / (py::cast<double>(signal_cfg["min_depth_pct"]) * 2.0), 1.0),
            std::max(0.0, 1.0 - ratio / py::cast<double>(signal_cfg["pullback_volume_ratio_max"])),
            static_cast<double>(stage) / 3.0
        );
    }
    const py::dict current = bar_at(bars, current_index);
    const auto close = number(current, "close");
    const auto current_ratio = volume_ratio(current);
    const double buffer = py::cast<double>(signal_cfg["breakout_buffer_pct"]);
    if (qualified.size() >= 2 && close && current_ratio && *close >= left_rim * (1.0 + buffer)
        && *current_ratio >= py::cast<double>(signal_cfg["breakout_volume_ratio_min"])) {
        py::dict setup = build_setup(
            "rounded_bottom", symbol, 3, "rim_breakout", risk_cfg, anchors,
            bottom_close, id_anchors, extra
        );
        return buy_decision(
            "broke above the rounded-bottom rim on confirming volume", setup, structure,
            std::min(std::max((*close / left_rim - 1.0) / std::max(buffer * 2.0, 1e-12), 0.0), 1.0),
            std::min(
                *current_ratio / (py::cast<double>(signal_cfg["breakout_volume_ratio_min"]) * 2.0),
                1.0
            ),
            1.0
        );
    }
    return std::nullopt;
}

bool island_downtrend(const py::list& bars, int index, int lookback, double minimum_drop) {
    const py::dict bar = bar_at(bars, index);
    const auto close = number(bar, "close");
    std::optional<double> lookback_return;
    const int anchor = index - lookback;
    if (close && anchor >= 0) {
        const auto anchor_close = bar_number(bars, anchor, "close");
        if (anchor_close && *anchor_close > 0.0) lookback_return = *close / *anchor_close - 1.0;
    }
    const auto sma = number(bar, "sma_50");
    return (lookback_return && *lookback_return <= -minimum_drop)
        || (close && sma && *close < *sma);
}

std::optional<IslandPattern> latest_island(const py::list& bars, const py::dict& config) {
    const int count = static_cast<int>(py::len(bars));
    if (count < 4) return std::nullopt;
    const int minimum_bars = py::cast<int>(config["min_island_bars"]);
    const int maximum_bars = py::cast<int>(config["max_island_bars"]);
    for (int breakout_index = count - 1; breakout_index >= minimum_bars + 1; --breakout_index) {
        const py::dict breakout = bar_at(bars, breakout_index);
        const auto breakout_open = number(breakout, "open");
        const auto breakout_close = number(breakout, "close");
        const auto breakout_low = number(breakout, "low");
        const auto breakout_volume = number(breakout, "volume");
        const auto breakout_average = number(breakout, "volume_sma_20");
        if (!breakout_open || !breakout_close || !breakout_low || !breakout_volume
            || !breakout_average || *breakout_average <= 0.0 || *breakout_close <= *breakout_open) continue;
        const double breakout_ratio = *breakout_volume / *breakout_average;
        if (breakout_ratio < py::cast<double>(config["right_volume_ratio_min"])) continue;
        const int latest_left = breakout_index - minimum_bars;
        const int earliest_left = std::max(1, breakout_index - maximum_bars);
        if (latest_left < earliest_left) continue;
        for (int left_index = latest_left; left_index >= earliest_left; --left_index) {
            const py::dict left = bar_at(bars, left_index);
            const auto left_high = number(left, "high");
            const auto left_open = number(left, "open");
            const auto left_close = number(left, "close");
            const auto left_volume = number(left, "volume");
            const auto left_average = number(left, "volume_sma_20");
            const auto previous_low = bar_number(bars, left_index - 1, "low");
            if (!left_high || !left_open || !left_close || !left_volume || !left_average
                || *left_average <= 0.0 || !previous_low || *left_close >= *left_open) continue;
            const double left_gap = *previous_low > 0.0 ? (*previous_low - *left_high) / *previous_low : 0.0;
            if (left_gap < py::cast<double>(config["left_gap_min_pct"])
                || *left_volume / *left_average > py::cast<double>(config["left_volume_ratio_max"])
                || !island_downtrend(
                    bars,
                    left_index,
                    py::cast<int>(config["downtrend_lookback"]),
                    py::cast<double>(config["downtrend_min_drop_pct"]))) continue;
            if (breakout_index - left_index < minimum_bars) continue;
            double island_high = -std::numeric_limits<double>::infinity();
            double island_low = std::numeric_limits<double>::infinity();
            bool invalid = false;
            for (int item = left_index; item < breakout_index; ++item) {
                const auto high = bar_number(bars, item, "high");
                const auto low = bar_number(bars, item, "low");
                island_high = std::max(island_high, high.value_or(-std::numeric_limits<double>::infinity()));
                island_low = std::min(island_low, low.value_or(std::numeric_limits<double>::infinity()));
                if (high.value_or(std::numeric_limits<double>::infinity()) >= *previous_low) invalid = true;
            }
            if (!std::isfinite(island_high) || !std::isfinite(island_low) || invalid) continue;
            const double breakout_gap = island_high > 0.0 ? (*breakout_low - island_high) / island_high : 0.0;
            if (breakout_gap < py::cast<double>(config["right_gap_min_pct"])) continue;
            return IslandPattern{
                left_index, breakout_index, island_low, island_high, *breakout_low,
                *breakout_close, *breakout_volume, breakout_ratio, left_gap, breakout_gap
            };
        }
    }
    return std::nullopt;
}

std::optional<py::dict> island_exhaustion(const py::list& bars, const py::dict& config) {
    const int count = static_cast<int>(py::len(bars));
    if (count < 2) return std::nullopt;
    const int index = count - 1;
    const py::dict bar = bar_at(bars, index);
    const auto high = number(bar, "high");
    const auto low = number(bar, "low");
    const auto open = number(bar, "open");
    const auto close = number(bar, "close");
    const auto volume = number(bar, "volume");
    const auto average = number(bar, "volume_sma_20");
    const auto previous_low = bar_number(bars, index - 1, "low");
    if (!high || !low || !open || !close || !volume || !average || !previous_low
        || *average <= 0.0 || *previous_low <= 0.0 || *close >= *open) return std::nullopt;
    const double gap = (*previous_low - *high) / *previous_low;
    const double ratio = *volume / *average;
    if (gap < py::cast<double>(config["left_gap_min_pct"])
        || ratio > py::cast<double>(config["left_volume_ratio_max"])
        || !island_downtrend(
            bars,
            index,
            py::cast<int>(config["downtrend_lookback"]),
            py::cast<double>(config["downtrend_min_drop_pct"]))) return std::nullopt;
    py::dict result;
    result["trade_date"] = item_or_none(bar, "dt_ny");
    result["high"] = *high;
    result["low"] = *low;
    result["left_gap_pct"] = gap;
    result["volume_ratio"] = ratio;
    return result;
}

std::optional<double> recent_atr(const py::list& bars, int end, int window) {
    std::vector<double> ranges;
    for (int index = 0; index < end; ++index) {
        const auto high = bar_number(bars, index, "high");
        const auto low = bar_number(bars, index, "low");
        if (!high || !low) continue;
        double range = *high - *low;
        if (index > 0) {
            const auto previous_close = bar_number(bars, index - 1, "close");
            if (previous_close) range = std::max({range, std::abs(*high - *previous_close), std::abs(*low - *previous_close)});
        }
        ranges.push_back(range);
    }
    if (static_cast<int>(ranges.size()) < window) return std::nullopt;
    double total = 0.0;
    for (int index = static_cast<int>(ranges.size()) - window; index < static_cast<int>(ranges.size()); ++index) {
        total += ranges[index];
    }
    return total / window;
}

std::optional<Decision> evaluate_island(
    const std::string& symbol,
    const py::dict& snapshot,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    const py::list bars = py::cast<py::list>(snapshot["recent_bars"]);
    if (py::len(bars) == 0) return std::nullopt;
    py::dict stored = stored_setup(snapshot);
    const double position = number_or(snapshot, "position");
    const auto average_entry = number(snapshot, "avg_entry_price");
    if (position > 0.0 && py::len(stored) > 0) {
        const py::dict current = bar_at(bars, static_cast<int>(py::len(bars)) - 1);
        const auto close = number(current, "close");
        const auto low = number(current, "low");
        auto atr = number(current, "atr_14");
        if (!atr || *atr == 0.0) atr = recent_atr(bars, static_cast<int>(py::len(bars)), 20);
        std::optional<std::string> reason;
        std::optional<std::string> stage;
        const auto invalidation = number(stored, "invalidation_price");
        if (invalidation && low && *low < *invalidation) {
            reason = "price broke the staged pattern invalidation level"; stage = "pattern_invalidation";
        } else if (close && average_entry
                   && *close <= *average_entry * (1.0 - py::cast<double>(risk_cfg["max_loss_pct"]))) {
            reason = "price fell more than the configured max-loss threshold from entry"; stage = "max_loss_stop";
        } else if (close && atr && average_entry
                   && *close <= *average_entry - py::cast<double>(risk_cfg["stop_loss_atr"]) * *atr) {
            reason = "price hit the ATR stop"; stage = "atr_stop";
        } else if (close && atr && average_entry
                   && *close >= *average_entry + py::cast<double>(risk_cfg["take_profit_atr"]) * *atr) {
            reason = "price reached the ATR take-profit target"; stage = "take_profit";
        }
        if (reason) {
            stored["exit_stage"] = *stage;
            py::dict strength;
            strength["stage"] = *stage;
            strength["left_gap_pct"] = item_or_none(stored, "left_gap_pct");
            strength["right_gap_pct"] = item_or_none(stored, "breakout_gap_pct");
            strength["breakout_volume_ratio"] = item_or_none(stored, "breakout_volume_ratio");
            strength["left_volume_ratio"] = item_or_none(stored, "left_volume_ratio");
            strength["retest_volume_ratio"] = py::none();
            strength["hold_margin_atr"] = py::none();
            return Decision{"SELL", *reason, stored, std::nullopt, strength};
        }
    }
    const auto pattern = latest_island(bars, signal_cfg);
    const auto exhaustion = pattern ? std::optional<py::dict>() : island_exhaustion(bars, signal_cfg);
    std::string action;
    std::string reason;
    std::string stage;
    if (pattern) {
        const int current_index = static_cast<int>(py::len(bars)) - 1;
        if (current_index == pattern->breakout_idx) {
            action = "BUY";
            reason = "confirmed the island reversal with a volume-backed upside gap";
            stage = "breakout";
        } else if (current_index > pattern->breakout_idx
                   && current_index <= pattern->breakout_idx + py::cast<int>(signal_cfg["retest_window"])) {
            const py::dict current = bar_at(bars, current_index);
            const auto low = number(current, "low");
            const auto close = number(current, "close");
            const auto volume = number(current, "volume");
            const double tolerance = py::cast<double>(signal_cfg["support_tolerance_pct"]);
            const double support_floor = pattern->island_high * (1.0 - tolerance);
            bool prior_held = true;
            for (int index = pattern->breakout_idx + 1; index < current_index; ++index) {
                const auto prior_close = bar_number(bars, index, "close");
                if (!prior_close || *prior_close < support_floor) prior_held = false;
            }
            if (low && close && volume && prior_held
                && *low <= pattern->breakout_gap_low * (1.0 + tolerance)
                && *low >= support_floor && *close >= pattern->island_high
                && *volume <= pattern->breakout_volume * py::cast<double>(signal_cfg["retest_volume_ratio_max"])) {
                action = "BUY";
                reason = "low-volume retest held the upside gap after the island reversal";
                stage = "retest";
            }
        }
    } else if (exhaustion) {
        action = "BUY";
        reason = "confirmed a low-volume downside exhaustion gap";
        stage = "exhaustion_gap";
    }
    if (action.empty()) return std::nullopt;
    py::dict setup;
    std::optional<double> score;
    if (pattern) {
        const int stage_index = stage == "breakout" ? 2 : 3;
        py::dict anchors;
        anchors["left_gap_trade_date"] = date_text(bar_at(bars, pattern->left_gap_idx));
        anchors["breakout_trade_date"] = date_text(bar_at(bars, pattern->breakout_idx));
        anchors["left_gap_price"] = pattern->island_high;
        anchors["breakout_price"] = pattern->breakout_close;
        py::dict extra;
        extra["island_low"] = pattern->island_low;
        extra["island_high"] = pattern->island_high;
        extra["breakout_gap_low"] = pattern->breakout_gap_low;
        extra["left_gap_pct"] = pattern->left_gap_pct;
        extra["breakout_gap_pct"] = pattern->breakout_gap_pct;
        extra["breakout_volume"] = pattern->breakout_volume;
        extra["breakout_volume_ratio"] = pattern->breakout_volume_ratio;
        setup = build_setup(
            "island_reversal", symbol, stage_index,
            stage_index == 2 ? "upside_gap" : "gap_retest", risk_cfg,
            anchors,
            pattern->island_low * (1.0 - py::cast<double>(signal_cfg["support_tolerance_pct"])),
            py::make_tuple(item_or_none(bar_at(bars, pattern->left_gap_idx), "dt_ny")),
            extra
        );
        score = pattern->left_gap_pct * 100.0 + pattern->breakout_gap_pct * 100.0 + pattern->breakout_volume_ratio;
    } else {
        const py::dict value = *exhaustion;
        py::dict anchors;
        anchors["left_gap_trade_date"] = py::str(value["trade_date"]);
        anchors["left_gap_price"] = value["high"];
        py::dict extra;
        extra["island_low"] = value["low"];
        extra["island_high"] = value["high"];
        extra["left_gap_pct"] = value["left_gap_pct"];
        extra["left_volume_ratio"] = value["volume_ratio"];
        setup = build_setup(
            "island_reversal", symbol, 1, "exhaustion_gap", risk_cfg,
            anchors,
            py::cast<double>(value["low"]) * (1.0 - py::cast<double>(signal_cfg["support_tolerance_pct"])),
            py::make_tuple(value["trade_date"]),
            extra
        );
        score = py::cast<double>(value["left_gap_pct"]) * 100.0;
    }
    const py::dict current = bar_at(bars, static_cast<int>(py::len(bars)) - 1);
    py::dict strength;
    strength["stage"] = stage;
    strength["left_gap_pct"] = item_or_none(setup, "left_gap_pct");
    strength["right_gap_pct"] = item_or_none(setup, "breakout_gap_pct");
    strength["breakout_volume_ratio"] = item_or_none(setup, "breakout_volume_ratio");
    strength["left_volume_ratio"] = item_or_none(setup, "left_volume_ratio");
    const auto current_volume = number(current, "volume");
    const auto breakout_volume = number(setup, "breakout_volume");
    strength["retest_volume_ratio"] = current_volume && breakout_volume && *breakout_volume > 0.0
        ? py::object(py::cast(*current_volume / *breakout_volume))
        : py::object(py::none());
    const auto current_close = number(current, "close");
    const auto current_atr = number(current, "atr_14");
    const auto island_high = number(setup, "island_high");
    strength["hold_margin_atr"] = current_close && current_atr && *current_atr > 0.0 && island_high
        ? py::object(py::cast((*current_close - *island_high) / *current_atr))
        : py::object(py::none());
    return Decision{action, reason, setup, score, strength};
}

py::dict event_from_decision(
    const std::string& pattern_type,
    const py::dict& runtime,
    const std::string& symbol,
    const py::dict& snapshot,
    const Decision& decision,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    py::dict metadata;
    for (const char* key : {"close", "open", "high", "low", "volume", "atr_14"}) {
        metadata[key] = item_or_none(snapshot, key);
    }
    metadata["position"] = number_or(snapshot, "position");
    metadata["avg_entry_price"] = item_or_none(snapshot, "avg_entry_price");
    metadata["setup"] = decision.setup;
    metadata["strength_inputs"] = decision.strength_inputs;
    if (pattern_type == "island_reversal") {
        py::dict config;
        for (const char* key : {
            "downtrend_min_drop_pct", "left_gap_min_pct", "right_gap_min_pct",
            "retest_window", "support_tolerance_pct"
        }) config[key] = signal_cfg[key];
        config["max_loss_pct"] = risk_cfg["max_loss_pct"];
        config["take_profit_atr"] = risk_cfg["take_profit_atr"];
        metadata["config"] = config;
    } else {
        metadata["price_semantics"] = "forward_adjusted_fallback_unadjusted";
    }
    py::dict event;
    event["strategy_id"] = runtime["strategy_id"];
    event["ts"] = raw_timestamp(snapshot);
    event["symbol"] = symbol;
    event["action"] = decision.action;
    event["reason"] = decision.reason;
    event["score"] = decision.score
        ? py::object(py::cast(*decision.score))
        : py::object(py::none());
    event["metadata"] = metadata;
    event["instrument_id"] = py::none();
    return event;
}

}  // namespace

py::list evaluate_pattern_day(const py::dict& runtime, const py::dict& market) {
    const std::string type = py::cast<std::string>(runtime["strategy_type"]);
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::dict signal_cfg = py::cast<py::dict>(params["signal"]);
    const py::dict risk_cfg = py::cast<py::dict>(params["risk"]);
    py::list results;
    for (const std::string& symbol : universe(params, market)) {
        if (!market.contains(py::str(symbol))) continue;
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol)]);
        if (!snapshot.contains("recent_bars") || snapshot["recent_bars"].is_none()) continue;
        if (type == "double_bottom") {
            py::object event = evaluate_double_bottom_event(
                runtime, symbol, snapshot, signal_cfg, risk_cfg
            );
            if (!event.is_none()) results.append(event);
            continue;
        }
        std::optional<Decision> decision;
        if (type == "island_reversal") {
            decision = evaluate_island(symbol, snapshot, signal_cfg, risk_cfg);
        } else if (type == "head_shoulders_bottom") {
            decision = evaluate_head_shoulders(symbol, snapshot, signal_cfg, risk_cfg);
        } else if (type == "rounded_bottom") {
            decision = evaluate_rounded_bottom(symbol, snapshot, signal_cfg, risk_cfg);
        } else if (type == "v_reversal") {
            decision = evaluate_v_reversal(symbol, snapshot, signal_cfg, risk_cfg);
        } else {
            throw std::invalid_argument("native pattern strategy is not implemented: " + type);
        }
        if (decision) {
            results.append(event_from_decision(
                type, runtime, symbol, snapshot, *decision, signal_cfg, risk_cfg
            ));
        }
    }
    return results;
}

}  // namespace quant_kernel
