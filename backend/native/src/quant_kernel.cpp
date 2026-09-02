#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "pattern_kernel.hpp"
#include "support_resistance_kernel.hpp"

namespace py = pybind11;

namespace {

constexpr const char* kKernelVersion = "cpp-v1";
constexpr int kAbiVersion = 1;
#ifndef QUANT_KERNEL_BUILD_ID
#define QUANT_KERNEL_BUILD_ID "local"
#endif
constexpr const char* kBuildId = QUANT_KERNEL_BUILD_ID;

std::optional<double> number(const py::dict& value, const char* key) {
    if (!value.contains(key) || value[key].is_none()) {
        return std::nullopt;
    }
    return py::cast<double>(value[key]);
}

double number_or(const py::dict& value, const char* key, double fallback = 0.0) {
    const auto result = number(value, key);
    return result.has_value() ? *result : fallback;
}

int integer_or(const py::dict& value, const char* key, int fallback = 0) {
    if (!value.contains(key) || value[key].is_none()) {
        return fallback;
    }
    try {
        return py::cast<int>(value[key]);
    } catch (const py::cast_error&) {
        return fallback;
    }
}

py::object item_or_none(const py::dict& value, const std::string& key) {
    if (!value.contains(py::str(key))) {
        return py::none();
    }
    return py::reinterpret_borrow<py::object>(value[py::str(key)]);
}

std::optional<double> number(const py::dict& value, const std::string& key) {
    if (!value.contains(py::str(key)) || value[py::str(key)].is_none()) {
        return std::nullopt;
    }
    return py::cast<double>(value[py::str(key)]);
}

py::object optional_number(const std::optional<double>& value) {
    return value ? py::cast(*value) : py::none();
}

py::object raw_event_timestamp(const py::dict& snapshot) {
    if (snapshot.contains("ts") && !snapshot["ts"].is_none()) {
        return py::reinterpret_borrow<py::object>(snapshot["ts"]);
    }
    const py::module_ datetime_module = py::module_::import("datetime");
    return datetime_module.attr("datetime").attr("now")(datetime_module.attr("timezone").attr("utc"));
}

py::object normalized_event_timestamp(const py::dict& snapshot) {
    const py::module_ datetime_module = py::module_::import("datetime");
    const py::object utc = datetime_module.attr("timezone").attr("utc");
    if (snapshot.contains("ts") && !snapshot["ts"].is_none()) {
        py::object timestamp = py::reinterpret_borrow<py::object>(snapshot["ts"]);
        if (timestamp.attr("tzinfo").is_none()) {
            return timestamp.attr("replace")(py::arg("tzinfo") = utc);
        }
        return timestamp.attr("astimezone")(utc);
    }
    if (!snapshot.contains("dt_ny") || snapshot["dt_ny"].is_none()) {
        throw std::invalid_argument("daily strategy snapshot requires ts or dt_ny");
    }
    const py::object market_close = datetime_module.attr("datetime").attr("combine")(
        snapshot["dt_ny"],
        datetime_module.attr("time")(py::arg("hour") = 16),
        py::arg("tzinfo") = py::module_::import("zoneinfo").attr("ZoneInfo")("America/New_York")
    );
    return market_close.attr("astimezone")(utc);
}

std::optional<int> position_holding_days(const py::dict& snapshot) {
    if (snapshot.contains("position_holding_days") && !snapshot["position_holding_days"].is_none()) {
        try {
            const int days = py::cast<int>(snapshot["position_holding_days"]);
            if (days >= 0) return days;
        } catch (const py::cast_error&) {
        }
    }
    if (!snapshot.contains("entry_trade_date") || snapshot["entry_trade_date"].is_none()
        || !snapshot.contains("dt_ny") || snapshot["dt_ny"].is_none()) {
        return std::nullopt;
    }
    py::object entry = py::reinterpret_borrow<py::object>(snapshot["entry_trade_date"]);
    py::object current = py::reinterpret_borrow<py::object>(snapshot["dt_ny"]);
    if (PyObject_RichCompareBool(current.ptr(), entry.ptr(), Py_LT) == 1) {
        return std::nullopt;
    }
    if (snapshot.contains("recent_bars") && !snapshot["recent_bars"].is_none()) {
        py::set dates;
        for (const py::handle raw_bar : py::reinterpret_borrow<py::iterable>(snapshot["recent_bars"])) {
            const py::dict bar = py::cast<py::dict>(raw_bar);
            if (!bar.contains("dt_ny") || bar["dt_ny"].is_none()) continue;
            py::object bar_date = py::reinterpret_borrow<py::object>(bar["dt_ny"]);
            const bool after_entry = PyObject_RichCompareBool(entry.ptr(), bar_date.ptr(), Py_LE) == 1;
            const bool before_current = PyObject_RichCompareBool(bar_date.ptr(), current.ptr(), Py_LE) == 1;
            if (after_entry && before_current) dates.add(bar_date);
        }
        if (py::len(dates) > 0 && dates.contains(entry) && dates.contains(current)) {
            return static_cast<int>(py::len(dates)) - 1;
        }
    }
    return py::cast<int>((current - entry).attr("days"));
}

std::vector<std::string> universe(const py::dict& params, const py::dict& market) {
    const py::dict universe_cfg = py::cast<py::dict>(params["universe"]);
    std::vector<std::string> symbols;
    if (universe_cfg.contains("symbols") && !universe_cfg["symbols"].is_none()) {
        symbols = py::cast<std::vector<std::string>>(universe_cfg["symbols"]);
    }
    const std::string selection_mode = universe_cfg.contains("selection_mode")
        ? py::cast<std::string>(universe_cfg["selection_mode"])
        : "";
    if (!symbols.empty()) {
        return symbols;
    }
    for (const auto& [raw_symbol, raw_snapshot] : market) {
        const std::string symbol = py::cast<std::string>(raw_symbol);
        const py::dict snapshot = py::cast<py::dict>(raw_snapshot);
        if (selection_mode == "all_common_stock") {
            const std::string asset_type = snapshot.contains("asset_type") && !snapshot["asset_type"].is_none()
                ? py::cast<std::string>(snapshot["asset_type"])
                : "";
            std::string upper = asset_type;
            std::transform(upper.begin(), upper.end(), upper.begin(), ::toupper);
            if (upper != "CS") {
                continue;
            }
        }
        symbols.push_back(symbol);
    }
    std::sort(symbols.begin(), symbols.end());
    return symbols;
}

std::string percent_reason(const std::string& prefix, double value, const std::string& suffix) {
    std::ostringstream output;
    output << prefix << std::fixed << std::setprecision(1) << value * 100.0 << "%" << suffix;
    return output.str();
}

py::dict signal(
    const py::dict& runtime,
    const py::dict& snapshot,
    const std::string& symbol,
    const std::string& action,
    const std::string& reason,
    double score,
    py::dict metadata,
    py::object timestamp = py::none()
) {
    py::dict result;
    result["strategy_id"] = runtime["strategy_id"];
    result["ts"] = timestamp.is_none() ? raw_event_timestamp(snapshot) : timestamp;
    result["symbol"] = symbol;
    result["action"] = action;
    result["reason"] = reason;
    result["score"] = score;
    result["metadata"] = std::move(metadata);
    result["instrument_id"] = py::none();
    return result;
}

py::list evaluate_trend(const py::dict& runtime, const py::dict& market) {
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::dict signal_cfg = py::cast<py::dict>(params["signal"]);
    const py::dict risk_cfg = py::cast<py::dict>(params["risk"]);
    const py::dict fast = py::cast<py::dict>(signal_cfg["fast_indicator"]);
    const py::dict slow = py::cast<py::dict>(signal_cfg["slow_indicator"]);
    const std::string fast_key = py::cast<std::string>(fast["kind"]) + "_" + std::to_string(py::cast<int>(fast["window"]));
    const std::string slow_key = py::cast<std::string>(slow["kind"]) + "_" + std::to_string(py::cast<int>(slow["window"]));
    const double volume_multiplier = py::cast<double>(signal_cfg["volume_multiplier"]);
    const double atr_multiplier = py::cast<double>(signal_cfg["atr_multiplier"]);
    const double stop_loss_pct = py::cast<double>(risk_cfg["stop_loss_pct"]);
    const double stop_loss_atr = py::cast<double>(risk_cfg["stop_loss_atr"]);
    const double take_profit_atr = py::cast<double>(risk_cfg["take_profit_atr"]);
    py::list results;

    for (const std::string& symbol_name : universe(params, market)) {
        if (!market.contains(py::str(symbol_name))) {
            continue;
        }
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol_name)]);
        const double position = number_or(snapshot, "position");
        const auto average_entry = number(snapshot, "avg_entry_price");
        const auto close = number(snapshot, "close");
        const auto atr = number(snapshot, "atr_14");

        std::optional<std::string> action;
        std::optional<std::string> reason;
        double score = 0.0;
        if (position > 0.0 && close && average_entry && *average_entry > 0.0 && *close <= *average_entry * (1.0 - stop_loss_pct)) {
            action = "SELL";
            reason = "price fell below the fixed stop-loss threshold";
            score = std::abs((*average_entry - *close) / *average_entry);
        } else if (position > 0.0 && close && average_entry && *average_entry > 0.0 && atr && *atr > 0.0 && *close <= *average_entry - stop_loss_atr * *atr) {
            action = "SELL";
            reason = "price hit the ATR stop-loss threshold";
            score = std::abs((*average_entry - *close) / *average_entry);
        } else if (position > 0.0 && close && average_entry && *average_entry > 0.0 && atr && *atr > 0.0 && *close >= *average_entry + take_profit_atr * *atr) {
            action = "SELL";
            reason = "price reached the ATR take-profit threshold";
            score = std::abs((*close - *average_entry) / *average_entry);
        }

        py::dict config;
        config["volume_multiplier"] = volume_multiplier;
        config["atr_multiplier"] = atr_multiplier;
        config["stop_loss_pct"] = stop_loss_pct;
        config["stop_loss_atr"] = stop_loss_atr;
        config["take_profit_atr"] = take_profit_atr;
        if (action) {
            py::dict metadata;
            metadata["close"] = optional_number(close);
            metadata["atr_14"] = optional_number(atr);
            metadata["position"] = position;
            metadata["avg_entry_price"] = optional_number(average_entry);
            metadata["config"] = config;
            results.append(signal(runtime, snapshot, symbol_name, *action, *reason, score, metadata));
            continue;
        }

        const double current_volume = number_or(snapshot, "volume");
        const double average_volume = number_or(snapshot, "volume_sma_20");
        if (average_volume <= 0.0 || current_volume < volume_multiplier * average_volume) {
            continue;
        }
        const auto fast_now = number(snapshot, fast_key);
        const auto slow_now = number(snapshot, slow_key);
        auto previous_fast = number(snapshot, "prev_" + fast_key);
        auto previous_slow = number(snapshot, "prev_" + slow_key);
        if (!previous_fast) previous_fast = number(snapshot, "prev_fast");
        if (!previous_slow) previous_slow = number(snapshot, "prev_slow");
        if (!fast_now || !slow_now || !previous_fast || !previous_slow) {
            continue;
        }
        if (*previous_fast <= *previous_slow && *fast_now > *slow_now) {
            action = "BUY";
            reason = fast_key + " crossed above " + slow_key;
        } else if (*previous_fast >= *previous_slow && *fast_now < *slow_now) {
            action = "SELL";
            reason = fast_key + " crossed below " + slow_key;
        } else {
            continue;
        }
        py::dict strength;
        strength["separation_atr"] = atr && *atr > 0.0
            ? py::object(py::cast((*fast_now - *slow_now) / *atr))
            : py::object(py::none());
        strength["crossover_impulse_atr"] = atr && *atr > 0.0
            ? py::object(py::cast(((*fast_now - *slow_now) - (*previous_fast - *previous_slow)) / *atr))
            : py::object(py::none());
        strength["volume_ratio"] = current_volume / average_volume;
        py::dict metadata;
        metadata["close"] = item_or_none(snapshot, "close");
        metadata["atr_14"] = item_or_none(snapshot, "atr_14");
        metadata["position"] = position;
        metadata["avg_entry_price"] = optional_number(average_entry);
        metadata["config"] = config;
        metadata["strength_inputs"] = strength;
        results.append(signal(runtime, snapshot, symbol_name, *action, *reason, std::abs(*fast_now - *slow_now), metadata));
    }
    return results;
}

py::list evaluate_mean_reversion(const py::dict& runtime, const py::dict& market) {
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::dict signal_cfg = py::cast<py::dict>(params["signal"]);
    const py::dict risk_cfg = py::cast<py::dict>(params["risk"]);
    const int lookback = py::cast<int>(signal_cfg["lookback_window"]);
    const std::string zscore_key = "zscore_" + std::to_string(lookback);
    const double entry = py::cast<double>(signal_cfg["zscore_entry"]);
    const double exit = py::cast<double>(signal_cfg["zscore_exit"]);
    const double stop_loss_pct = py::cast<double>(risk_cfg["stop_loss_pct"]);
    const double take_profit_pct = py::cast<double>(risk_cfg["take_profit_pct"]);
    const int max_holding_days = integer_or(risk_cfg, "max_holding_days");
    py::list results;

    for (const std::string& symbol_name : universe(params, market)) {
        if (!market.contains(py::str(symbol_name))) continue;
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol_name)]);
        const auto zscore = number(snapshot, zscore_key);
        const double position = number_or(snapshot, "position");
        const auto average_entry = number(snapshot, "avg_entry_price");
        const auto close = number(snapshot, "close");
        const auto holding_days = position_holding_days(snapshot);
        std::optional<std::string> action;
        std::optional<std::string> reason;
        if (position > 0.0 && close && average_entry && *average_entry > 0.0 && *close <= *average_entry * (1.0 - stop_loss_pct)) {
            action = "SELL"; reason = percent_reason("price fell below the ", stop_loss_pct, " stop-loss threshold");
        } else if (position > 0.0 && close && average_entry && *average_entry > 0.0 && *close >= *average_entry * (1.0 + take_profit_pct)) {
            action = "SELL"; reason = percent_reason("price reached the ", take_profit_pct, " take-profit threshold");
        } else if (position > 0.0 && max_holding_days > 0 && holding_days && *holding_days >= max_holding_days) {
            action = "SELL"; reason = "position reached the " + std::to_string(max_holding_days) + "-day max holding period";
        } else if (position > 0.0 && zscore && *zscore >= -exit) {
            action = "SELL"; reason = zscore_key + " reverted above exit threshold";
        } else if (position < 0.0 && close && average_entry && *average_entry > 0.0 && *close >= *average_entry * (1.0 + stop_loss_pct)) {
            action = "BUY"; reason = percent_reason("price rose above the ", stop_loss_pct, " short stop-loss threshold");
        } else if (position < 0.0 && close && average_entry && *average_entry > 0.0 && *close <= *average_entry * (1.0 - take_profit_pct)) {
            action = "BUY"; reason = percent_reason("price reached the ", take_profit_pct, " short take-profit threshold");
        } else if (position < 0.0 && max_holding_days > 0 && holding_days && *holding_days >= max_holding_days) {
            action = "BUY"; reason = "short position reached the " + std::to_string(max_holding_days) + "-day max holding period";
        } else if (position < 0.0 && zscore && *zscore <= exit) {
            action = "BUY"; reason = zscore_key + " reverted below exit threshold";
        } else if (!zscore) {
            continue;
        } else if (*zscore <= -entry) {
            action = "BUY"; reason = zscore_key + " below negative entry threshold";
        } else if (*zscore >= entry) {
            action = "SELL"; reason = zscore_key + " above positive entry threshold";
        }
        if (!action) continue;
        py::dict config;
        config["lookback_window"] = lookback;
        config["zscore_entry"] = entry;
        config["zscore_exit"] = exit;
        config["stop_loss_pct"] = stop_loss_pct;
        config["take_profit_pct"] = take_profit_pct;
        config["max_holding_days"] = max_holding_days;
        py::dict strength;
        strength["absolute_zscore"] = zscore
            ? py::object(py::cast(std::abs(*zscore)))
            : py::object(py::none());
        py::dict metadata;
        metadata["close"] = item_or_none(snapshot, "close");
        metadata["atr_14"] = item_or_none(snapshot, "atr_14");
        metadata["rsi_14"] = item_or_none(snapshot, "rsi_14");
        metadata[py::str(zscore_key)] = optional_number(zscore);
        metadata["position"] = position;
        metadata["avg_entry_price"] = optional_number(average_entry);
        metadata["position_holding_days"] = holding_days
            ? py::object(py::cast(*holding_days))
            : py::object(py::none());
        metadata["config"] = config;
        metadata["strength_inputs"] = strength;
        results.append(signal(runtime, snapshot, symbol_name, *action, *reason, zscore ? std::abs(*zscore) : 0.0, metadata));
    }
    return results;
}

py::list evaluate_momentum(const py::dict& runtime, const py::dict& market) {
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::dict signal_cfg = py::cast<py::dict>(params["signal"]);
    const py::dict risk_cfg = py::cast<py::dict>(params["risk"]);
    const double minimum_return = py::cast<double>(signal_cfg["minimum_return_20d"]);
    const double buffer = py::cast<double>(signal_cfg["breakout_buffer_pct"]);
    const double volume_multiplier = py::cast<double>(signal_cfg["volume_multiplier"]);
    const double exit_return = py::cast<double>(signal_cfg["exit_return_20d"]);
    const double stop_loss_pct = py::cast<double>(risk_cfg["stop_loss_pct"]);
    const double take_profit_pct = py::cast<double>(risk_cfg["take_profit_pct"]);
    py::list results;
    auto symbols = universe(params, market);
    std::sort(symbols.begin(), symbols.end());
    symbols.erase(std::unique(symbols.begin(), symbols.end()), symbols.end());
    for (const std::string& symbol_name : symbols) {
        if (!market.contains(py::str(symbol_name))) continue;
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol_name)]);
        const auto close = number(snapshot, "close");
        const auto sma = number(snapshot, "sma_20");
        const auto return_20d = number(snapshot, "ret_20d");
        const auto volume = number(snapshot, "volume");
        const auto average_volume = number(snapshot, "volume_sma_20");
        if (!close || !sma || *sma <= 0.0 || !return_20d || !volume || !average_volume || *average_volume <= 0.0) continue;
        const double position = number_or(snapshot, "position");
        const auto average_entry = number(snapshot, "avg_entry_price");
        const double threshold = *sma * (1.0 + buffer);
        const double volume_ratio = *volume / *average_volume;
        std::optional<std::string> action;
        std::optional<std::string> reason;
        if (position > 0.0 && average_entry && *average_entry > 0.0 && *close <= *average_entry * (1.0 - stop_loss_pct)) {
            action = "SELL"; reason = percent_reason("price fell below the ", stop_loss_pct, " stop-loss threshold");
        } else if (position > 0.0 && average_entry && *average_entry > 0.0 && *close >= *average_entry * (1.0 + take_profit_pct)) {
            action = "SELL"; reason = percent_reason("price reached the ", take_profit_pct, " take-profit threshold");
        } else if (position > 0.0 && (*close < *sma || *return_20d <= exit_return)) {
            action = "SELL"; reason = "20-day momentum or SMA20 support failed";
        } else if (position <= 0.0 && *close >= threshold && *return_20d >= minimum_return && volume_ratio >= volume_multiplier) {
            action = "BUY"; reason = "adjusted close confirmed a volume-backed 20-day momentum breakout";
        }
        if (!action) continue;
        const double extension = *close / *sma - 1.0;
        py::dict config;
        config["minimum_return_20d"] = minimum_return;
        config["breakout_buffer_pct"] = buffer;
        config["volume_multiplier"] = volume_multiplier;
        config["exit_return_20d"] = exit_return;
        config["stop_loss_pct"] = stop_loss_pct;
        config["take_profit_pct"] = take_profit_pct;
        py::dict strength;
        strength["return_20d"] = *return_20d;
        strength["price_extension"] = extension;
        strength["volume_ratio"] = volume_ratio;
        py::dict metadata;
        metadata["close"] = *close;
        metadata["sma_20"] = *sma;
        metadata["ret_20d"] = *return_20d;
        metadata["volume"] = *volume;
        metadata["volume_sma_20"] = *average_volume;
        metadata["volume_ratio"] = volume_ratio;
        metadata["breakout_threshold"] = threshold;
        metadata["position"] = position;
        metadata["avg_entry_price"] = optional_number(average_entry);
        metadata["price_semantics"] = "forward_adjusted_fallback_unadjusted";
        metadata["config"] = config;
        metadata["strength_inputs"] = strength;
        results.append(signal(
            runtime,
            snapshot,
            symbol_name,
            *action,
            *reason,
            *return_20d + extension + volume_ratio,
            metadata,
            normalized_event_timestamp(snapshot)
        ));
    }
    return results;
}

py::list evaluate_day(const py::dict& runtime, const py::dict& market) {
    const std::string type = py::cast<std::string>(runtime["strategy_type"]);
    if (type == "trend") return evaluate_trend(runtime, market);
    if (type == "mean_reversion") return evaluate_mean_reversion(runtime, market);
    if (type == "momentum_breakout") return evaluate_momentum(runtime, market);
    if (type == "island_reversal" || type == "double_bottom" || type == "head_shoulders_bottom"
        || type == "rounded_bottom" || type == "v_reversal") {
        return quant_kernel::evaluate_pattern_day(runtime, market);
    }
    throw std::invalid_argument("native strategy is not implemented: " + type);
}

py::list catalog() {
    py::list result;
    const std::vector<std::tuple<std::string, int, int, std::vector<std::string>>> descriptors = {
        {"trend", 1, 0, {"close", "volume", "volume_sma_20", "atr_14", "ema_15", "sma_200"}},
        {"mean_reversion", 1, 0, {"close", "atr_14", "zscore_5", "zscore_10", "zscore_20"}},
        {"momentum_breakout", 1, 0, {"close", "sma_20", "ret_20d", "volume", "volume_sma_20"}},
        {"island_reversal", 1, 100, {"open", "high", "low", "close", "volume", "volume_sma_20", "sma_50"}},
        {"double_bottom", 1, 220, {"open", "high", "low", "close", "volume", "volume_sma_20"}},
        {"head_shoulders_bottom", 1, 160, {"open", "high", "low", "close", "volume", "volume_sma_20"}},
        {"rounded_bottom", 1, 200, {"open", "high", "low", "close", "volume", "volume_sma_20"}},
        {"v_reversal", 1, 180, {"open", "high", "low", "close", "volume", "volume_sma_20", "atr_14"}},
    };
    for (const auto& [type, revision, history_length, features] : descriptors) {
        py::dict item;
        item["strategy_type"] = type;
        item["algorithm_revision"] = revision;
        item["history_length"] = history_length;
        item["required_features"] = features;
        item["parameter_schema"] = py::dict();
        result.append(std::move(item));
    }
    return result;
}

py::dict normalize_strategy(const std::string& type, const py::dict& params) {
    static const std::set<std::string> implemented = {
        "trend", "mean_reversion", "momentum_breakout", "island_reversal", "double_bottom",
        "head_shoulders_bottom", "rounded_bottom", "v_reversal"
    };
    if (!implemented.contains(type)) {
        throw std::invalid_argument("native strategy is not implemented: " + type);
    }
    return py::module_::import("copy").attr("deepcopy")(params).cast<py::dict>();
}

}  // namespace

PYBIND11_MODULE(_native, module) {
    module.attr("KERNEL_VERSION") = kKernelVersion;
    module.attr("ABI_VERSION") = kAbiVersion;
    module.attr("BUILD_ID") = kBuildId;
    module.def("catalog", &catalog);
    module.def("normalize_strategy", &normalize_strategy, py::arg("strategy_type"), py::arg("params"));
    module.def("evaluate_day", &evaluate_day, py::arg("runtime"), py::arg("market_data"));
    quant_kernel::bind_support_resistance(module);
}
