#include "pattern_kernel.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

py::dict required_dict(const py::dict& parent, const char* key) {
    if (!parent.contains(key) || !py::isinstance<py::dict>(parent[key])) {
        throw std::invalid_argument(std::string("missing object: ") + key);
    }
    return py::cast<py::dict>(parent[key]);
}

double number_or(const py::dict& value, const char* key, double fallback = 0.0) {
    return value.contains(key) && !value[key].is_none() ? py::cast<double>(value[key]) : fallback;
}

int integer_or(const py::dict& value, const char* key, int fallback = 0) {
    return value.contains(key) && !value[key].is_none() ? py::cast<int>(value[key]) : fallback;
}

std::optional<double> optional_number(const py::dict& value, const char* key) {
    if (!value.contains(key) || value[key].is_none()) return std::nullopt;
    const double result = py::cast<double>(value[key]);
    return std::isfinite(result) ? std::optional<double>(result) : std::nullopt;
}

std::int64_t ordinal(const py::handle& value) {
    if (value.is_none()) return 0;
    if (py::isinstance<py::int_>(value)) return py::cast<std::int64_t>(value);
    if (py::hasattr(value, "toordinal")) return py::cast<std::int64_t>(value.attr("toordinal")());
    return py::cast<std::int64_t>(
        py::module_::import("datetime").attr("date").attr("fromisoformat")(py::str(value)).attr("toordinal")()
    );
}

PatternBar parse_bar(const py::dict& value) {
    PatternBar bar;
    if (value.contains("dt_ny")) bar.date_ordinal = ordinal(value["dt_ny"]);
    if (value.contains("ts") && !value["ts"].is_none()) {
        bar.timestamp_us = static_cast<std::int64_t>(
            py::cast<double>(py::reinterpret_borrow<py::object>(value["ts"]).attr("timestamp")()) * 1'000'000.0
        );
    }
    bar.open = optional_number(value, "open");
    bar.high = optional_number(value, "high");
    bar.low = optional_number(value, "low");
    bar.close = optional_number(value, "close");
    bar.volume = optional_number(value, "volume");
    bar.atr_14 = optional_number(value, "atr_14");
    bar.volume_sma_20 = optional_number(value, "volume_sma_20");
    bar.return_20d = optional_number(value, "ret_20d");
    bar.return_60d = optional_number(value, "ret_60d");
    bar.sma_20 = optional_number(value, "sma_20");
    bar.sma_50 = optional_number(value, "sma_50");
    return bar;
}

PatternValue parse_value(const py::handle& value) {
    if (value.is_none()) return std::monostate{};
    if (py::isinstance<py::bool_>(value)) return py::cast<bool>(value);
    if (py::isinstance<py::int_>(value)) return py::cast<std::int64_t>(value);
    if (py::isinstance<py::float_>(value)) return py::cast<double>(value);
    if (py::isinstance<py::str>(value)) return py::cast<std::string>(value);
    if (py::isinstance<py::list>(value) || py::isinstance<py::tuple>(value)) {
        std::vector<std::string> result;
        for (const py::handle item : py::reinterpret_borrow<py::iterable>(value)) {
            result.push_back(py::cast<std::string>(py::str(item)));
        }
        return result;
    }
    throw std::invalid_argument("pattern setup contains an unsupported value");
}

PatternObject parse_object(const py::dict& value) {
    PatternObject result;
    for (const auto& [raw_key, raw_value] : value) {
        result.emplace(py::cast<std::string>(py::str(raw_key)), parse_value(raw_value));
    }
    return result;
}

std::optional<PatternSetup> parse_stored_setup(PatternKind kind, const py::dict& snapshot) {
    if (!snapshot.contains("entry_signal_features") || snapshot["entry_signal_features"].is_none()) return std::nullopt;
    const py::dict features = py::cast<py::dict>(snapshot["entry_signal_features"]);
    if (!features.contains("setup") || features["setup"].is_none()) return std::nullopt;
    const py::dict raw = py::cast<py::dict>(features["setup"]);
    PatternSetup setup;
    setup.pattern_type = py::cast<std::string>(py::str(raw["pattern_type"]));
    setup.setup_id = py::cast<std::string>(py::str(raw["setup_id"]));
    setup.stage_index = integer_or(raw, "stage_index");
    setup.stage_key = raw.contains("stage_key") && !raw["stage_key"].is_none()
        ? py::cast<std::string>(py::str(raw["stage_key"])) : "";
    setup.stage_target_pct = number_or(raw, "stage_target_pct", 1.0);
    setup.invalidation_price = optional_number(raw, "invalidation_price");
    if (raw.contains("exit_stage") && !raw["exit_stage"].is_none()) {
        setup.exit_stage = py::cast<std::string>(py::str(raw["exit_stage"]));
    }
    if (raw.contains("anchors") && py::isinstance<py::dict>(raw["anchors"])) {
        setup.anchors = parse_object(py::cast<py::dict>(raw["anchors"]));
    }
    PatternObject fields;
    for (const auto& [raw_key, raw_value] : raw) {
        const std::string key = py::cast<std::string>(py::str(raw_key));
        if (key == "pattern_type" || key == "setup_id" || key == "stage_index"
            || key == "stage_key" || key == "stage_target_pct" || key == "anchors"
            || key == "invalidation_price" || key == "stage" || key == "exit_stage") continue;
        fields.emplace(key, parse_value(raw_value));
    }
    switch (kind) {
        case PatternKind::IslandReversal: setup.payload = IslandSetupPayload{std::move(fields)}; break;
        case PatternKind::DoubleBottom: setup.payload = DoubleBottomSetupPayload{std::move(fields)}; break;
        case PatternKind::HeadShouldersBottom: setup.payload = HeadShouldersSetupPayload{std::move(fields)}; break;
        case PatternKind::RoundedBottom: setup.payload = RoundedBottomSetupPayload{std::move(fields)}; break;
        case PatternKind::VReversal: setup.payload = VReversalSetupPayload{std::move(fields)}; break;
    }
    return setup;
}

std::vector<std::string> universe(const py::dict& params, const py::dict& market) {
    const py::dict config = py::cast<py::dict>(params["universe"]);
    if (config.contains("symbols") && !config["symbols"].is_none()) {
        auto symbols = py::cast<std::vector<std::string>>(config["symbols"]);
        if (!symbols.empty()) return symbols;
    }
    const std::string selection_mode = config.contains("selection_mode")
        ? py::cast<std::string>(config["selection_mode"]) : "";
    std::vector<std::string> symbols;
    for (const auto& [raw_symbol, raw_snapshot] : market) {
        const py::dict snapshot = py::cast<py::dict>(raw_snapshot);
        if (selection_mode == "all_common_stock") {
            std::string asset_type = snapshot.contains("asset_type") && !snapshot["asset_type"].is_none()
                ? py::cast<std::string>(snapshot["asset_type"]) : "";
            std::transform(asset_type.begin(), asset_type.end(), asset_type.begin(), [](unsigned char character) {
                return static_cast<char>(std::toupper(character));
            });
            if (asset_type != "CS") continue;
        }
        symbols.push_back(py::cast<std::string>(raw_symbol));
    }
    std::sort(symbols.begin(), symbols.end());
    return symbols;
}

py::object raw_timestamp(const py::dict& snapshot) {
    if (snapshot.contains("ts") && !snapshot["ts"].is_none()) return py::reinterpret_borrow<py::object>(snapshot["ts"]);
    const py::module_ datetime_module = py::module_::import("datetime");
    return datetime_module.attr("datetime").attr("now")(datetime_module.attr("timezone").attr("utc"));
}

}  // namespace

PatternConfig parse_pattern_config(const py::dict& runtime) {
    const std::string type = py::cast<std::string>(runtime["strategy_type"]);
    const py::dict params = required_dict(runtime, "params");
    const py::dict signal = required_dict(params, "signal");
    const py::dict risk = required_dict(params, "risk");
    PatternConfig config{};
    config.strategy_type = type;
    config.minimum_strength = number_or(signal, "min_strength_score", 50.0);
    config.risk.max_positions = integer_or(risk, "max_positions", 6);
    config.risk.position_size_pct = number_or(risk, "position_size_pct", 0.15);
    config.risk.stage_targets[0] = number_or(risk, "stage_1_target_pct", 0.20);
    config.risk.stage_targets[1] = number_or(risk, "stage_2_target_pct", 0.50);
    config.risk.stage_targets[2] = number_or(risk, "stage_3_target_pct", 1.00);
    config.risk.stop_loss_atr = number_or(risk, "stop_loss_atr", 1.5);
    config.risk.max_loss_pct = number_or(risk, "max_loss_pct", 0.08);
    config.risk.take_profit_atr = number_or(risk, "take_profit_atr", 3.0);
    if (type == "island_reversal") {
        config.kind = PatternKind::IslandReversal;
        IslandConfig value{
            integer_or(signal, "downtrend_lookback"), number_or(signal, "downtrend_min_drop_pct"),
            number_or(signal, "left_gap_min_pct"), number_or(signal, "right_gap_min_pct"),
            integer_or(signal, "min_island_bars"), integer_or(signal, "max_island_bars"),
            number_or(signal, "left_volume_ratio_max"), number_or(signal, "right_volume_ratio_min"),
            integer_or(signal, "retest_window"), number_or(signal, "retest_volume_ratio_max"),
            number_or(signal, "support_tolerance_pct"),
            number_or(signal, "previous_body_atr_min"),
            number_or(signal, "breakout_body_atr_min"),
            number_or(signal, "exhaustion_body_atr_max"),
            number_or(signal, "island_body_atr_max"),
        };
        config.history_limit = std::max(40, value.downtrend_lookback + value.max_island_bars + value.retest_window + 2);
        config.signal = value;
    } else if (type == "double_bottom") {
        config.kind = PatternKind::DoubleBottom;
        DoubleBottomConfig value{
            integer_or(signal, "downtrend_lookback"), number_or(signal, "downtrend_min_drop_pct"),
            number_or(signal, "downtrend_max_up_day_ratio"), number_or(signal, "downtrend_min_r_squared"),
            integer_or(signal, "min_bottom_spacing"), integer_or(signal, "max_bottom_spacing"),
            integer_or(signal, "left_bottom_before_bars"), integer_or(signal, "left_bottom_after_bars"),
            number_or(signal, "bottom_tolerance_pct"), number_or(signal, "neckline_min_rebound_pct"),
            number_or(signal, "rebound_up_day_ratio_min"), number_or(signal, "second_bottom_volume_ratio_max"),
            number_or(signal, "breakout_volume_ratio_min"), integer_or(signal, "max_breakout_bars_after_right_bottom"),
            number_or(signal, "breakout_buffer_pct"), integer_or(signal, "retest_window"),
            number_or(signal, "retest_volume_ratio_max"), number_or(signal, "support_tolerance_pct"),
            number_or(signal, "rebound_volume_ratio_min"),
            number_or(signal, "rebound_volume_ratio_max"),
        };
        config.history_limit = std::max(
            40, value.downtrend_lookback + value.max_bottom_spacing + value.left_bottom_before_bars
                + value.max_breakout_bars_after_right_bottom + value.retest_window + 10
        );
        config.signal = value;
    } else if (type == "head_shoulders_bottom") {
        config.kind = PatternKind::HeadShouldersBottom;
        HeadShouldersConfig value{
            integer_or(signal, "downtrend_lookback"), number_or(signal, "downtrend_min_drop_pct"),
            integer_or(signal, "pivot_left_bars"), integer_or(signal, "pivot_right_bars"),
            integer_or(signal, "min_segment_bars"), integer_or(signal, "max_segment_bars"),
            number_or(signal, "shoulder_tolerance_pct"), number_or(signal, "head_depth_min_pct"),
            number_or(signal, "head_volume_ratio_max"), number_or(signal, "right_shoulder_volume_ratio_max"),
            number_or(signal, "breakout_volume_ratio_min"), number_or(signal, "breakout_buffer_pct"),
            integer_or(signal, "platform_bars"),
            number_or(signal, "platform_range_atr_max"),
            number_or(signal, "platform_drift_atr_max"),
            number_or(signal, "rebound_volume_ratio_min"),
            number_or(signal, "rebound_volume_ratio_max"),
        };
        config.history_limit = std::max(40, value.downtrend_lookback + 2 * value.max_segment_bars + value.pivot_right_bars + 10);
        config.signal = value;
    } else if (type == "rounded_bottom") {
        config.kind = PatternKind::RoundedBottom;
        RoundedBottomConfig value{
            integer_or(signal, "min_lookback"), integer_or(signal, "max_lookback"),
            number_or(signal, "min_depth_pct"), number_or(signal, "min_r_squared"),
            number_or(signal, "vertex_position_min"), number_or(signal, "vertex_position_max"),
            integer_or(signal, "pivot_left_bars"), integer_or(signal, "pivot_right_bars"),
            integer_or(signal, "min_pullback_spacing"), number_or(signal, "right_volume_ratio_min"),
            number_or(signal, "pullback_volume_ratio_max"), number_or(signal, "breakout_volume_ratio_min"),
            number_or(signal, "breakout_buffer_pct"),
            number_or(signal, "weakening_buffer_pct"),
        };
        config.history_limit = std::max(40, value.max_lookback + value.pivot_right_bars + 10);
        config.signal = value;
    } else if (type == "v_reversal") {
        config.kind = PatternKind::VReversal;
        VReversalConfig value{
            integer_or(signal, "downtrend_lookback"), number_or(signal, "downtrend_min_drop_pct"),
            integer_or(signal, "pivot_max_bars"), number_or(signal, "reversal_min_return_pct"),
            number_or(signal, "reversal_min_atr"), number_or(signal, "pivot_volume_ratio_min"),
            integer_or(signal, "continuation_window"), number_or(signal, "continuation_volume_ratio_min"),
            integer_or(signal, "consolidation_min_bars"), integer_or(signal, "consolidation_max_bars"),
            number_or(signal, "breakout_volume_ratio_min"), integer_or(signal, "retest_window"),
            number_or(signal, "retest_volume_ratio_max"), number_or(signal, "support_tolerance_pct"),
            number_or(signal, "bearish_reversal_volume_ratio_min"),
            number_or(signal, "consolidation_range_atr_max"),
            number_or(signal, "consolidation_drift_atr_max"),
            number_or(signal, "breakout_buffer_pct"),
            number_or(signal, "bearish_body_atr_min"),
        };
        config.history_limit = std::max(
            40, value.downtrend_lookback + value.continuation_window + value.consolidation_max_bars
                + value.retest_window + 10
        );
        config.signal = value;
    } else {
        throw std::invalid_argument("native pattern strategy is not implemented: " + type);
    }
    return config;
}

py::list evaluate_pattern_day(const py::dict& runtime, const py::dict& market) {
    const PatternConfig config = parse_pattern_config(runtime);
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::object json_loads = py::module_::import("json").attr("loads");
    py::list results;
    for (const std::string& symbol : universe(params, market)) {
        if (!market.contains(py::str(symbol))) continue;
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol)]);
        if (!snapshot.contains("recent_bars") || snapshot["recent_bars"].is_none()) continue;
        PatternState state;
        for (const py::handle raw_bar : py::reinterpret_borrow<py::iterable>(snapshot["recent_bars"])) {
            append_pattern_bar(state, config, parse_bar(py::cast<py::dict>(raw_bar)));
        }
        const auto stored = parse_stored_setup(config.kind, snapshot);
        const PatternPositionView position{
            number_or(snapshot, "position"), optional_number(snapshot, "avg_entry_price"), stored ? &*stored : nullptr,
        };
        const auto decision = evaluate_pattern(config, symbol, state, position);
        if (!decision) continue;
        py::dict event;
        event["strategy_id"] = runtime["strategy_id"];
        event["ts"] = raw_timestamp(snapshot);
        event["symbol"] = symbol;
        event["action"] = decision->buy ? "BUY" : "SELL";
        event["reason"] = decision->reason;
        event["score"] = decision->score ? py::cast(*decision->score) : py::none();
        event["metadata"] = json_loads(pattern_metadata_json(config, state.bars.back(), position, *decision));
        event["instrument_id"] = snapshot.contains("instrument_id")
            ? py::reinterpret_borrow<py::object>(snapshot["instrument_id"])
            : py::object(py::none());
        results.append(std::move(event));
    }
    return results;
}

}  // namespace quant_kernel
