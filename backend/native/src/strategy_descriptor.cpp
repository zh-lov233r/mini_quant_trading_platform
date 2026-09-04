#include "strategy_descriptor.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

struct Descriptor {
    const char* strategy_type;
    const char* label;
    const char* description;
    int revision;
    int history_length;
    std::vector<std::string> features;
    const char* defaults_json;
};

const std::vector<Descriptor>& descriptors() {
    static const std::vector<Descriptor> values = {
        {
            "trend", "Trend Following",
            "双均线趋势策略，带成交量过滤、ATR 风控和调仓配置。", 1, 0,
            {"open", "close", "volume", "volume_sma_20", "atr_14", "ema_15", "sma_200"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"description":"","schema_version":1},"risk":{"max_positions":10,"position_size_pct":0.1,"stop_loss_atr":2.0,"stop_loss_pct":0.1,"take_profit_atr":4.0},"signal":{"atr_multiplier":2.0,"fast_indicator":{"kind":"ema","window":15},"min_strength_score":50.0,"price_field":"close","slow_indicator":{"kind":"sma","window":200},"trigger":"cross_over","volume_multiplier":1.5},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "mean_reversion", "Mean Reversion",
            "均值回归配置模板，基于 z-score / ATR / 流动性特征做日线信号。", 1, 0,
            {"open", "close", "atr_14", "zscore_5", "zscore_10", "zscore_20"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"description":"","schema_version":1},"risk":{"max_holding_days":0,"max_positions":10,"position_size_pct":0.1,"stop_loss_pct":0.1,"take_profit_pct":0.1},"signal":{"lookback_window":20,"min_strength_score":50.0,"price_field":"close","zscore_entry":2.0,"zscore_exit":0.5},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "momentum_breakout", "Momentum Breakout",
            "动量突破策略，使用 20 日收益、20 日均线和成交量确认日线突破。", 1, 0,
            {"open", "close", "sma_20", "ret_20d", "volume", "volume_sma_20", "atr_14"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"description":"","schema_version":1},"risk":{"max_positions":10,"position_size_pct":0.1,"stop_loss_pct":0.08,"take_profit_pct":0.2},"signal":{"breakout_buffer_pct":0.02,"exit_return_20d":0.0,"min_strength_score":50.0,"minimum_return_20d":0.1,"price_field":"close","volume_multiplier":1.5},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "island_reversal", "Island Reversal Bottom",
            "底部岛形反转策略，识别缩量向下衰竭缺口、放量向上突破缺口和缩量回踩缺口。", 2, 80,
            {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14", "ret_60d"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"description":"","schema_version":1},"risk":{"max_loss_pct":0.1,"max_positions":6,"position_size_pct":0.15,"stage_1_target_pct":0.2,"stage_2_target_pct":0.5,"stage_3_target_pct":1.0,"stop_loss_atr":1.5,"take_profit_atr":3.0},"signal":{"breakout_body_atr_min":0.5,"downtrend_lookback":60,"downtrend_min_drop_pct":0.15,"exhaustion_body_atr_max":1.0,"island_body_atr_max":0.5,"left_gap_min_pct":0.02,"left_volume_ratio_max":0.8,"max_island_bars":8,"min_island_bars":1,"min_strength_score":50.0,"previous_body_atr_min":0.5,"retest_volume_ratio_max":0.7,"retest_window":10,"right_gap_min_pct":0.02,"right_volume_ratio_min":1.5,"support_tolerance_pct":0.01},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "double_bottom", "Double Bottom",
            "双底三阶段策略，第二底不破前低，温和放量反弹、缩量回踩，并以收盘放量突破颈线确认。", 2, 151,
            {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14", "ret_60d"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"description":"","schema_version":1},"risk":{"max_loss_pct":0.08,"max_positions":6,"position_size_pct":0.15,"stage_1_target_pct":0.2,"stage_2_target_pct":0.5,"stage_3_target_pct":1.0,"stop_loss_atr":1.5,"take_profit_atr":3.0},"signal":{"bottom_tolerance_pct":0.03,"breakout_buffer_pct":0.005,"breakout_volume_ratio_min":1.5,"downtrend_lookback":60,"downtrend_max_up_day_ratio":0.35,"downtrend_min_drop_pct":0.2,"downtrend_min_r_squared":0.65,"left_bottom_after_bars":1,"left_bottom_before_bars":1,"max_bottom_spacing":30,"max_breakout_bars_after_right_bottom":40,"min_bottom_spacing":5,"min_strength_score":50.0,"neckline_min_rebound_pct":0.06,"rebound_up_day_ratio_min":0.6,"rebound_volume_ratio_max":1.5,"rebound_volume_ratio_min":1.0,"retest_volume_ratio_max":0.8,"retest_window":10,"second_bottom_volume_ratio_max":0.9,"support_tolerance_pct":0.02},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "head_shoulders_bottom", "Head and Shoulders Bottom",
            "头肩底反转策略，分阶段识别缩量头部、右肩回踩与放量突破动态颈线。", 2, 152,
            {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14", "ret_60d"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"algorithm_version":"confirmed-pivots-v2","description":"","schema_version":1},"risk":{"max_loss_pct":0.08,"max_positions":6,"position_size_pct":0.15,"stage_1_target_pct":0.2,"stage_2_target_pct":0.5,"stage_3_target_pct":1.0,"stop_loss_atr":1.5,"take_profit_atr":3.0},"signal":{"breakout_buffer_pct":0.005,"breakout_volume_ratio_min":1.5,"downtrend_lookback":60,"downtrend_min_drop_pct":0.2,"head_depth_min_pct":0.05,"head_volume_ratio_max":0.8,"max_segment_bars":40,"min_segment_bars":5,"min_strength_score":50.0,"pivot_left_bars":2,"pivot_right_bars":2,"platform_bars":5,"platform_drift_atr_max":1.0,"platform_range_atr_max":3.0,"rebound_volume_ratio_max":1.5,"rebound_volume_ratio_min":1.0,"right_shoulder_volume_ratio_max":0.9,"shoulder_tolerance_pct":0.05},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "rounded_bottom", "Rounded Bottom",
            "圆弧底反转策略，使用因果二次曲线拟合、右侧更高回踩与放量突破碗口。", 2, 252,
            {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14", "ret_60d"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"algorithm_version":"log-quadratic-v2","description":"","schema_version":1},"risk":{"max_loss_pct":0.08,"max_positions":6,"position_size_pct":0.15,"stage_1_target_pct":0.2,"stage_2_target_pct":0.5,"stage_3_target_pct":1.0,"stop_loss_atr":1.5,"take_profit_atr":3.0},"signal":{"breakout_buffer_pct":0.005,"breakout_volume_ratio_min":1.5,"max_lookback":240,"min_depth_pct":0.2,"min_lookback":80,"min_pullback_spacing":5,"min_r_squared":0.75,"min_strength_score":50.0,"pivot_left_bars":2,"pivot_right_bars":2,"pullback_volume_ratio_max":0.8,"right_volume_ratio_min":1.3,"vertex_position_max":0.65,"vertex_position_min":0.35,"weakening_buffer_pct":0.005},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "v_reversal", "V Reversal",
            "V 型反转策略，识别底部放量转折、连续上升与顶部突破回踩。", 2, 90,
            {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14", "ret_60d"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"algorithm_version":"volume-v-reversal-v2","description":"","schema_version":1},"risk":{"max_loss_pct":0.08,"max_positions":6,"position_size_pct":0.15,"stage_1_target_pct":0.2,"stage_2_target_pct":0.5,"stage_3_target_pct":1.0,"stop_loss_atr":1.5,"take_profit_atr":3.0},"signal":{"bearish_body_atr_min":0.5,"bearish_reversal_volume_ratio_min":2.0,"breakout_buffer_pct":0.005,"breakout_volume_ratio_min":1.5,"consolidation_drift_atr_max":1.0,"consolidation_max_bars":10,"consolidation_min_bars":3,"consolidation_range_atr_max":3.0,"continuation_volume_ratio_min":1.2,"continuation_window":5,"downtrend_lookback":60,"downtrend_min_drop_pct":0.2,"min_strength_score":50.0,"pivot_max_bars":3,"pivot_volume_ratio_min":2.0,"retest_volume_ratio_max":0.8,"retest_window":5,"reversal_min_atr":1.5,"reversal_min_return_pct":0.05,"support_tolerance_pct":0.02},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
        {
            "support_resistance", "Support / Resistance Zones",
            "使用已确认 Pivot 与 ATR 聚类识别动态支撑/压力区，并交易反弹和突破回踩；直接突破仅审计。", 11, 163,
            {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14"},
            R"json({"execution":{"rebalance":"daily","run_at":"close","timeframe":"1d"},"metadata":{"algorithm_version":"pivot-slope-regime-v3","description":"","price_semantics":"forward_adjusted_preferred_unadjusted_fallback","schema_version":1},"risk":{"break_even_at_r":1.0,"market_filter_enabled":false,"market_filter_symbol":"SPY","max_holding_days":40,"max_loss_pct":0.08,"max_positions":6,"min_reward_risk":1.5,"position_size_pct":0.15,"risk_per_trade_pct":0.005,"stop_cooldown_sessions":5,"stop_loss_atr":1.5,"take_profit_atr":3.0},"signal":{"bounce_confirmation_atr":0.25,"breakout_confirmation_atr":0.5,"breakout_retest_enabled":true,"breakout_volume_ratio_min":1.5,"decay_half_life":60,"detection_window":120,"line_inlier_tolerance_atr":0.75,"max_abs_slope_atr_per_session":0.25,"max_zones_per_kind":3,"min_line_pivots":3,"min_line_span_sessions":10,"min_strength_score":50.0,"pivot_left_bars":3,"pivot_right_bars":3,"pivot_tolerance_atr":0.05,"resistance_breakout_enabled":true,"retest_volume_ratio_max":0.8,"retest_window":10,"support_bounce_enabled":true,"zone_half_width_atr":0.5},"universe":{"selection_mode":"all_common_stock","symbols":[]}})json"
        },
    };
    return values;
}

const Descriptor& descriptor(const std::string& type) {
    const auto found = std::find_if(
        descriptors().begin(), descriptors().end(),
        [&type](const Descriptor& item) { return type == item.strategy_type; }
    );
    if (found == descriptors().end()) {
        throw std::invalid_argument("native strategy is not implemented: " + type);
    }
    return *found;
}

py::dict parse_json_object(const char* value) {
    return py::module_::import("json").attr("loads")(value).cast<py::dict>();
}

void merge_dict(py::dict& target, const py::dict& incoming) {
    for (const auto& [key, raw] : incoming) {
        if (target.contains(key) && py::isinstance<py::dict>(target[key])
            && py::isinstance<py::dict>(raw)) {
            py::dict nested = py::cast<py::dict>(target[key]);
            merge_dict(nested, py::cast<py::dict>(raw));
            target[key] = std::move(nested);
        } else {
            target[key] = py::module_::import("copy").attr("deepcopy")(raw);
        }
    }
}

double finite_number(const py::dict& value, const char* key, const std::string& label) {
    if (!value.contains(key) || value[key].is_none()) {
        throw std::invalid_argument(label + " must be a number");
    }
    double result = 0.0;
    try {
        result = py::cast<double>(value[key]);
    } catch (const py::cast_error&) {
        throw std::invalid_argument(label + " must be a number");
    }
    if (!std::isfinite(result)) throw std::invalid_argument(label + " must be finite");
    return result;
}

int positive_integer(const py::dict& value, const char* key, const std::string& label) {
    int result = 0;
    try {
        result = py::cast<int>(value[key]);
    } catch (const py::cast_error&) {
        throw std::invalid_argument(label + " must be a positive integer");
    }
    if (result <= 0) throw std::invalid_argument(label + " must be a positive integer");
    return result;
}

py::list normalized_symbols(const py::handle& raw) {
    py::list values;
    if (raw.is_none()) return values;
    py::object iterable;
    if (py::isinstance<py::str>(raw)) {
        iterable = py::str(raw).attr("split")(",");
    } else if (py::isinstance<py::list>(raw) || py::isinstance<py::tuple>(raw)
        || py::isinstance<py::set>(raw)) {
        iterable = py::reinterpret_borrow<py::object>(raw);
    } else {
        throw std::invalid_argument("symbols must be a string or array");
    }
    std::set<std::string> seen;
    for (const py::handle item : py::reinterpret_borrow<py::iterable>(iterable)) {
        std::string symbol = py::cast<std::string>(py::str(item).attr("strip")().attr("upper")());
        if (!symbol.empty() && seen.insert(symbol).second) values.append(symbol);
    }
    return values;
}

py::dict schema_for(const py::dict& defaults, const std::string& path = "", bool staged = false) {
    py::dict properties;
    for (const auto& [raw_key, raw_value] : defaults) {
        const std::string key = py::cast<std::string>(py::str(raw_key));
        const std::string field = path.empty() ? key : path + "." + key;
        py::dict property;
        if (py::isinstance<py::dict>(raw_value)) {
            property = schema_for(py::cast<py::dict>(raw_value), field, staged);
        } else if (py::isinstance<py::bool_>(raw_value)) {
            property["type"] = "boolean";
        } else if (py::isinstance<py::int_>(raw_value)) {
            property["type"] = "integer";
        } else if (py::isinstance<py::float_>(raw_value)) {
            property["type"] = "number";
        } else if (py::isinstance<py::list>(raw_value)) {
            property["type"] = "array";
            py::dict items;
            items["type"] = "string";
            property["items"] = std::move(items);
        } else {
            property["type"] = "string";
        }
        if (staged && path == "signal" && key != "min_strength_score") {
            const bool unit_interval = key == "downtrend_max_up_day_ratio" || key == "downtrend_min_r_squared"
                || key == "rebound_up_day_ratio_min" || key == "min_r_squared" || key.starts_with("vertex_position_");
            if (py::isinstance<py::int_>(raw_value)) property["minimum"] = 1;
            else if (unit_interval || key == "breakout_buffer_pct") property["minimum"] = 0.0;
            else property["exclusiveMinimum"] = 0.0;
            if (unit_interval || key.ends_with("_pct")) property["maximum"] = 1.0;
        }
        if (field == "signal.min_strength_score") {
            property["minimum"] = 0.0;
            property["maximum"] = 100.0;
        } else if (field == "risk.position_size_pct") {
            property["exclusiveMinimum"] = 0.0;
            property["maximum"] = 1.0;
        } else if (field.starts_with("risk.stage_") && field.ends_with("_target_pct")) {
            property["exclusiveMinimum"] = 0.0;
            property["maximum"] = 1.0;
        } else if (field == "risk.max_positions" || field == "metadata.schema_version") {
            property["minimum"] = 1;
        }
        if (field == "signal.max_zones_per_kind") { property["minimum"] = 1; property["maximum"] = 5; }
        if (field == "signal.pivot_tolerance_atr") { property["minimum"] = 0.0; property["maximum"] = 0.1; }
        if (field == "risk.risk_per_trade_pct") { property["exclusiveMinimum"] = 0.0; property["maximum"] = 1.0; }
        if (field == "risk.stop_cooldown_sessions") property["minimum"] = 0;
        if (field == "risk.break_even_at_r") property["exclusiveMinimum"] = 0.0;
        if (field == "signal.breakout_buffer_pct") {
            property["minimum"] = 0.0;
            property["exclusiveMaximum"] = 1.0;
        }
        if (field == "signal.platform_bars") property["minimum"] = 3;
        if (field.starts_with("signal.") && (field.ends_with("_atr_min") || field.ends_with("_atr_max")
            || field.starts_with("signal.rebound_volume_ratio_"))) property["exclusiveMinimum"] = 0.0;
        if (field == "signal.weakening_buffer_pct") {
            property["exclusiveMinimum"] = 0.0;
            property["exclusiveMaximum"] = 1.0;
        }
        if (field == "execution.timeframe") property["enum"] = py::make_tuple("1d");
        if (field == "execution.rebalance") property["enum"] = py::make_tuple("daily");
        if (field == "execution.run_at") property["enum"] = py::make_tuple("close");
        if (field == "universe.selection_mode") {
            property["enum"] = py::make_tuple(
                "manual", "all_common_stock", "stock_basket", "point_in_time_liquid"
            );
        }
        if (field == "signal.lookback_window") property["enum"] = py::make_tuple(5, 10, 20);
        if (field == "signal.price_field") property["enum"] = py::make_tuple("close");
        if (field.ends_with("_indicator.kind")) property["enum"] = py::make_tuple("ema", "sma");
        if (field == "signal.trigger") property["enum"] = py::make_tuple("cross_over");
        property["default"] = py::module_::import("copy").attr("deepcopy")(raw_value);
        properties[py::str(key)] = std::move(property);
    }
    py::dict result;
    result["type"] = "object";
    result["properties"] = std::move(properties);
    result["additionalProperties"] = path == "metadata" || path == "universe";
    return result;
}

void validate_declared_types(
    const py::dict& defaults,
    const py::dict& values,
    const std::string& path = ""
) {
    for (const auto& [raw_key, expected] : defaults) {
        const std::string key = py::cast<std::string>(py::str(raw_key));
        const std::string field = path.empty() ? key : path + "." + key;
        if (!values.contains(raw_key)) throw std::invalid_argument(field + " is required");
        const py::handle actual = values[raw_key];
        if (py::isinstance<py::dict>(expected)) {
            if (!py::isinstance<py::dict>(actual)) {
                throw std::invalid_argument(field + " must be an object");
            }
            validate_declared_types(
                py::cast<py::dict>(expected), py::cast<py::dict>(actual), field
            );
        } else if (py::isinstance<py::bool_>(expected)) {
            if (!py::isinstance<py::bool_>(actual)) {
                throw std::invalid_argument(field + " must be a boolean");
            }
        } else if (py::isinstance<py::int_>(expected)) {
            if (!py::isinstance<py::int_>(actual) || py::isinstance<py::bool_>(actual)) {
                throw std::invalid_argument(field + " must be an integer");
            }
        } else if (py::isinstance<py::float_>(expected)) {
            if ((!py::isinstance<py::float_>(actual) && !py::isinstance<py::int_>(actual))
                || py::isinstance<py::bool_>(actual)) {
                throw std::invalid_argument(field + " must be a number");
            }
            if (!std::isfinite(py::cast<double>(actual))) {
                throw std::invalid_argument(field + " must be finite");
            }
        } else if (py::isinstance<py::list>(expected)) {
            if (!py::isinstance<py::list>(actual) && !py::isinstance<py::tuple>(actual)
                && !py::isinstance<py::set>(actual) && !py::isinstance<py::str>(actual)) {
                throw std::invalid_argument(field + " must be an array or comma-separated string");
            }
        } else if (!py::isinstance<py::str>(actual)) {
            throw std::invalid_argument(field + " must be a string");
        }
    }
}

void validate_common(const std::string& type, py::dict& normalized, const py::dict& defaults) {
    py::dict signal = py::cast<py::dict>(normalized["signal"]);
    py::dict universe = py::cast<py::dict>(normalized["universe"]);
    py::dict risk = py::cast<py::dict>(normalized["risk"]);
    py::dict execution = py::cast<py::dict>(normalized["execution"]);
    const double strength = finite_number(signal, "min_strength_score", "signal.min_strength_score");
    if (strength < 0.0 || strength > 100.0) {
        throw std::invalid_argument("signal.min_strength_score must be within [0, 100]");
    }
    positive_integer(risk, "max_positions", "risk.max_positions");
    const double position_size = finite_number(risk, "position_size_pct", "risk.position_size_pct");
    if (position_size <= 0.0 || position_size > 1.0) {
        throw std::invalid_argument("risk.position_size_pct must be within (0, 1]");
    }
    universe["symbols"] = normalized_symbols(universe["symbols"]);
    std::string selection = py::cast<std::string>(py::str(universe["selection_mode"]));
    if (selection != "manual" && selection != "all_common_stock" && selection != "stock_basket"
        && selection != "point_in_time_liquid") {
        selection = py::len(universe["symbols"]) > 0 ? "manual" : "all_common_stock";
    }
    if (selection == "manual" && py::len(universe["symbols"]) == 0) {
        selection = "all_common_stock";
    }
    universe["selection_mode"] = selection;
    if (py::cast<std::string>(py::str(execution["timeframe"])) != "1d") {
        throw std::invalid_argument("execution.timeframe must be 1d for " + type);
    }
    if (py::cast<std::string>(py::str(execution["rebalance"])) != "daily") {
        throw std::invalid_argument("execution.rebalance must be daily for " + type);
    }
    if (py::cast<std::string>(py::str(execution["run_at"])) != "close") {
        throw std::invalid_argument("execution.run_at must be close for " + type);
    }
    if (type == "mean_reversion") {
        const int lookback = positive_integer(signal, "lookback_window", "signal.lookback_window");
        if (lookback != 5 && lookback != 10 && lookback != 20) {
            throw std::invalid_argument("signal.lookback_window must be one of: 5, 10, 20");
        }
    }
    if (type == "support_resistance") {
        for (const char* key : {"min_line_pivots", "min_line_span_sessions", "decay_half_life", "retest_window"}) {
            positive_integer(signal, key, std::string("signal.") + key);
        }
        if (py::cast<int>(signal["min_line_pivots"]) < 3) throw std::invalid_argument("signal.min_line_pivots must be at least 3");
        positive_integer(risk, "max_holding_days", "risk.max_holding_days");
        for (const char* key : {"line_inlier_tolerance_atr", "max_abs_slope_atr_per_session", "zone_half_width_atr",
            "bounce_confirmation_atr", "breakout_confirmation_atr", "breakout_volume_ratio_min", "retest_volume_ratio_max"}) {
            if (finite_number(signal, key, std::string("signal.") + key) <= 0.0) {
                throw std::invalid_argument(std::string("signal.") + key + " must be positive");
            }
        }
        for (const char* key : {"stop_loss_atr", "take_profit_atr", "min_reward_risk", "max_loss_pct"}) {
            if (finite_number(risk, key, std::string("risk.") + key) <= 0.0) {
                throw std::invalid_argument(std::string("risk.") + key + " must be positive");
            }
        }
        if (py::cast<double>(risk["max_loss_pct"]) > 1.0) throw std::invalid_argument("risk.max_loss_pct must not exceed 1");
        const int zones = positive_integer(signal, "max_zones_per_kind", "signal.max_zones_per_kind");
        if (zones > 5) throw std::invalid_argument("signal.max_zones_per_kind must not exceed 5");
        const double tolerance = finite_number(signal, "pivot_tolerance_atr", "signal.pivot_tolerance_atr");
        if (tolerance < 0.0 || tolerance > 0.1) throw std::invalid_argument("signal.pivot_tolerance_atr must be within [0, 0.1]");
        const double risk_budget = finite_number(risk, "risk_per_trade_pct", "risk.risk_per_trade_pct");
        if (risk_budget <= 0.0 || risk_budget > 1.0) throw std::invalid_argument("risk.risk_per_trade_pct must be within (0, 1]");
        if (py::cast<int>(risk["stop_cooldown_sessions"]) < 0) {
            throw std::invalid_argument("risk.stop_cooldown_sessions must be a nonnegative integer");
        }
        if (finite_number(risk, "break_even_at_r", "risk.break_even_at_r") <= 0.0) {
            throw std::invalid_argument("risk.break_even_at_r must be positive");
        }
        const auto benchmark = normalized_symbols(py::make_tuple(risk["market_filter_symbol"]));
        if (py::len(benchmark) != 1) throw std::invalid_argument("risk.market_filter_symbol must be one symbol");
        risk["market_filter_symbol"] = benchmark[0];
        for (const char* key : {
                "support_bounce_enabled", "resistance_breakout_enabled", "breakout_retest_enabled"
            }) {
            if (!py::isinstance<py::bool_>(signal[key])) {
                throw std::invalid_argument(std::string("signal.") + key + " must be a boolean");
            }
        }
        if (!py::cast<bool>(signal["support_bounce_enabled"])
            && !py::cast<bool>(signal["breakout_retest_enabled"])) {
            throw std::invalid_argument("at least one tradable support/resistance mode (bounce or retest) must be enabled");
        }
        const int left = positive_integer(signal, "pivot_left_bars", "signal.pivot_left_bars");
        const int right = positive_integer(signal, "pivot_right_bars", "signal.pivot_right_bars");
        const int window = positive_integer(signal, "detection_window", "signal.detection_window");
        if (window < left + right + 1) {
            throw std::invalid_argument(
                "signal.detection_window must cover pivot_left_bars + pivot_right_bars + 1"
            );
        }
        const py::dict metadata = py::cast<py::dict>(normalized["metadata"]);
        if (py::cast<std::string>(py::str(metadata["price_semantics"]))
            != "forward_adjusted_preferred_unadjusted_fallback") {
            throw std::invalid_argument(
                "metadata.price_semantics must be "
                "forward_adjusted_preferred_unadjusted_fallback"
            );
        }
    }
    static const std::set<std::string> staged = {
        "island_reversal", "double_bottom", "head_shoulders_bottom",
        "rounded_bottom", "v_reversal"
    };
    if (staged.contains(type)) {
        const auto declared_metadata = py::cast<py::dict>(defaults["metadata"]);
        if (declared_metadata.contains("algorithm_version")) {
            py::cast<py::dict>(normalized["metadata"])["algorithm_version"] = declared_metadata["algorithm_version"];
        }
        const auto declared_signal = py::cast<py::dict>(defaults["signal"]);
        for (const auto& [raw_key, value] : signal) {
            const std::string key = py::cast<std::string>(raw_key);
            if (key == "min_strength_score") continue;
            if (py::isinstance<py::int_>(declared_signal[raw_key])) {
                positive_integer(signal, key.c_str(), "signal." + key);
                continue;
            }
            const double number = py::cast<double>(value);
            const bool unit_interval = key == "downtrend_max_up_day_ratio" || key == "downtrend_min_r_squared"
                || key == "rebound_up_day_ratio_min" || key == "min_r_squared" || key.starts_with("vertex_position_");
            const bool zero_allowed = unit_interval || key == "breakout_buffer_pct";
            if (number < 0.0 || (!zero_allowed && number == 0.0)) {
                throw std::invalid_argument("signal." + key + (zero_allowed ? " must be nonnegative" : " must be positive"));
            }
            if ((unit_interval || key.ends_with("_pct")) && number > 1.0) {
                throw std::invalid_argument("signal." + key + " must not exceed 1");
            }
        }
        if (signal.contains("platform_bars") && py::cast<int>(signal["platform_bars"]) < 3) {
            throw std::invalid_argument("signal.platform_bars must be at least 3");
        }
        if (signal.contains("weakening_buffer_pct")) {
            const double buffer = py::cast<double>(signal["weakening_buffer_pct"]);
            if (buffer <= 0.0 || buffer >= 1.0) throw std::invalid_argument("signal.weakening_buffer_pct must be within (0, 1)");
        }
        for (const auto& [minimum, maximum] : std::vector<std::pair<const char*, const char*>>{
            {"rebound_volume_ratio_min", "rebound_volume_ratio_max"}, {"min_island_bars", "max_island_bars"},
            {"min_bottom_spacing", "max_bottom_spacing"}, {"min_segment_bars", "max_segment_bars"},
            {"min_lookback", "max_lookback"}, {"consolidation_min_bars", "consolidation_max_bars"},
            {"vertex_position_min", "vertex_position_max"}
        }) {
            if (signal.contains(minimum) && py::cast<double>(signal[minimum]) > py::cast<double>(signal[maximum])) {
                throw std::invalid_argument(std::string("signal.") + minimum + " must not exceed signal." + maximum);
            }
        }
        if (signal.contains("breakout_buffer_pct")) {
            const double buffer = py::cast<double>(signal["breakout_buffer_pct"]);
            if (buffer < 0.0 || buffer >= 1.0) throw std::invalid_argument("signal.breakout_buffer_pct must be within [0, 1)");
        }
        const double first = finite_number(risk, "stage_1_target_pct", "risk.stage_1_target_pct");
        const double second = finite_number(risk, "stage_2_target_pct", "risk.stage_2_target_pct");
        const double third = finite_number(risk, "stage_3_target_pct", "risk.stage_3_target_pct");
        if (!(0.0 < first && first < second && second < third)) {
            throw std::invalid_argument("staged entry targets must be strictly increasing");
        }
        if (std::abs(third - 1.0) > 1e-9) {
            throw std::invalid_argument("risk.stage_3_target_pct must equal 1");
        }
    }
}

}  // namespace

py::list strategy_catalog() {
    py::list result;
    for (const Descriptor& value : descriptors()) {
        py::dict defaults = parse_json_object(value.defaults_json);
        py::dict item;
        item["strategy_type"] = value.strategy_type;
        item["label"] = value.label;
        item["description"] = value.description;
        item["engine_ready"] = true;
        item["defaults"] = defaults;
        item["algorithm_revision"] = value.revision;
        item["history_length"] = value.history_length;
        item["required_features"] = value.features;
        item["parameter_schema"] = schema_for(defaults, "", value.revision == 2);
        result.append(std::move(item));
    }
    return result;
}

py::dict normalize_strategy_params(
    const std::string& strategy_type,
    const py::dict& params
) {
    py::dict defaults = parse_json_object(descriptor(strategy_type).defaults_json);
    py::dict normalized = py::module_::import("copy").attr("deepcopy")(defaults).cast<py::dict>();
    merge_dict(normalized, params);
    validate_declared_types(defaults, normalized);
    validate_common(strategy_type, normalized, defaults);
    return normalized;
}

}  // namespace quant_kernel
