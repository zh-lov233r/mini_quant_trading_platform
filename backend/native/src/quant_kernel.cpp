#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "backtest_kernel.hpp"
#include "native_utils.hpp"
#include "pattern_kernel.hpp"
#include "signal_strength.hpp"
#include "support_resistance_kernel.hpp"
#include "strategy_descriptor.hpp"

namespace py = pybind11;

namespace {

constexpr const char* kKernelVersion = "cpp-v1";
constexpr int kAbiVersion = 2;
#ifndef QUANT_KERNEL_BUILD_ID
#define QUANT_KERNEL_BUILD_ID "local"
#endif
constexpr const char* kBuildId = QUANT_KERNEL_BUILD_ID;

struct DayAuditCollection {
    std::vector<std::int32_t> symbol_ids;
    std::vector<std::string> payload_json;
};

struct DayResult {
    std::vector<std::string> symbols;
    std::vector<std::string> strategy_ids;
    std::vector<std::int64_t> timestamp_us;
    std::vector<std::int64_t> instrument_ids;
    std::vector<std::int32_t> symbol_ids;
    std::vector<std::int8_t> actions;
    std::vector<double> scores;
    std::vector<std::string> reasons;
    std::vector<std::string> metadata_json;
    std::vector<std::string> support_symbols;
    DayAuditCollection support_events;
    DayAuditCollection support_zone_versions;
    DayAuditCollection support_regime_versions;
};

template <typename T>
py::array_t<T> day_vector_view(const py::object& owner, std::vector<T>& values) {
    py::array_t<T> result(
        {static_cast<py::ssize_t>(values.size())},
        {static_cast<py::ssize_t>(sizeof(T))},
        values.data(),
        owner
    );
    result.attr("setflags")(py::arg("write") = false);
    return result;
}

std::int64_t timestamp_us(const py::handle& value) {
    const double seconds = py::cast<double>(value.attr("timestamp")());
    return static_cast<std::int64_t>(std::llround(seconds * 1'000'000.0));
}

void append_native_json(std::string& output, const py::handle& value) {
    if (value.is_none()) {
        output += "null";
    } else if (py::isinstance<py::bool_>(value)) {
        output += py::cast<bool>(value) ? "true" : "false";
    } else if (py::isinstance<py::int_>(value)) {
        output += py::cast<std::string>(py::str(value));
    } else if (py::isinstance<py::float_>(value)) {
        const double number = py::cast<double>(value);
        if (!std::isfinite(number)) {
            throw std::invalid_argument("native day metadata contains a non-finite number");
        }
        std::ostringstream encoded;
        encoded.imbue(std::locale::classic());
        encoded << std::setprecision(std::numeric_limits<double>::max_digits10) << number;
        output += encoded.str();
    } else if (py::isinstance<py::str>(value)) {
        output += quant_kernel::json_string(py::cast<std::string>(value));
    } else if (py::isinstance<py::dict>(value)) {
        output.push_back('{');
        bool first = true;
        for (const auto& [raw_key, raw_value] : py::cast<py::dict>(value)) {
            if (!first) output.push_back(',');
            first = false;
            output += quant_kernel::json_string(py::cast<std::string>(py::str(raw_key)));
            output.push_back(':');
            append_native_json(output, raw_value);
        }
        output.push_back('}');
    } else if (py::isinstance<py::list>(value) || py::isinstance<py::tuple>(value)) {
        output.push_back('[');
        bool first = true;
        for (const py::handle item : py::reinterpret_borrow<py::iterable>(value)) {
            if (!first) output.push_back(',');
            first = false;
            append_native_json(output, item);
        }
        output.push_back(']');
    } else {
        output += quant_kernel::json_string(py::cast<std::string>(py::str(value)));
    }
}

std::string canonical_json(const py::handle& value) {
    std::string output;
    append_native_json(output, value);
    return output;
}

DayResult typed_day_result(const py::list& raw_signals, py::dict support_resistance) {
    DayResult result;
    const std::size_t count = static_cast<std::size_t>(py::len(raw_signals));
    result.strategy_ids.reserve(count);
    result.timestamp_us.reserve(count);
    result.instrument_ids.reserve(count);
    result.symbol_ids.reserve(count);
    result.actions.reserve(count);
    result.scores.reserve(count);
    result.reasons.reserve(count);
    result.metadata_json.reserve(count);
    std::unordered_map<std::string, std::int32_t> symbol_ids;
    for (const py::handle raw : raw_signals) {
        const py::dict signal = py::cast<py::dict>(raw);
        const std::string symbol = py::cast<std::string>(signal["symbol"]);
        auto found = symbol_ids.find(symbol);
        if (found == symbol_ids.end()) {
            const auto symbol_id = static_cast<std::int32_t>(result.symbols.size());
            result.symbols.push_back(symbol);
            found = symbol_ids.emplace(symbol, symbol_id).first;
        }
        const std::string action = py::cast<std::string>(signal["action"]);
        if (action != "BUY" && action != "SELL") {
            throw std::invalid_argument("native day signal action must be BUY or SELL");
        }
        result.strategy_ids.push_back(py::cast<std::string>(py::str(signal["strategy_id"])));
        result.timestamp_us.push_back(::timestamp_us(signal["ts"]));
        result.instrument_ids.push_back(
            signal.contains("instrument_id") && !signal["instrument_id"].is_none()
                ? py::cast<std::int64_t>(signal["instrument_id"])
                : -1
        );
        result.symbol_ids.push_back(found->second);
        result.actions.push_back(action == "BUY" ? 1 : -1);
        result.scores.push_back(
            signal.contains("score") && !signal["score"].is_none()
                ? py::cast<double>(signal["score"])
                : std::numeric_limits<double>::quiet_NaN()
        );
        result.reasons.push_back(py::cast<std::string>(py::str(signal["reason"])));
        result.metadata_json.push_back(canonical_json(signal["metadata"]));
    }
    std::vector<std::string> audit_symbols;
    audit_symbols.reserve(static_cast<std::size_t>(py::len(support_resistance)));
    for (const auto& [raw_symbol, raw_payload] : support_resistance) {
        static_cast<void>(raw_payload);
        audit_symbols.push_back(py::cast<std::string>(raw_symbol));
    }
    std::sort(audit_symbols.begin(), audit_symbols.end());
    const auto append_collection = [](
        DayAuditCollection& target,
        std::int32_t symbol_id,
        const py::dict& payload,
        const char* key
    ) {
        if (!payload.contains(key) || payload[key].is_none()) return;
        for (const py::handle item : py::reinterpret_borrow<py::iterable>(payload[key])) {
            target.symbol_ids.push_back(symbol_id);
            target.payload_json.push_back(canonical_json(item));
        }
    };
    for (const std::string& symbol : audit_symbols) {
        const auto symbol_id = static_cast<std::int32_t>(result.support_symbols.size());
        result.support_symbols.push_back(symbol);
        const py::dict payload = py::cast<py::dict>(support_resistance[py::str(symbol)]);
        append_collection(result.support_events, symbol_id, payload, "events");
        append_collection(result.support_zone_versions, symbol_id, payload, "zone_versions");
        append_collection(result.support_regime_versions, symbol_id, payload, "regime_versions");
    }
    return result;
}

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
    result["instrument_id"] = snapshot.contains("instrument_id")
        ? py::reinterpret_borrow<py::object>(snapshot["instrument_id"])
        : py::object(py::none());
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

template <typename T>
T prepared_value(
    const py::buffer_info& info,
    py::ssize_t row,
    py::ssize_t column
) {
    const auto* bytes = static_cast<const char*>(info.ptr);
    return *reinterpret_cast<const T*>(
        bytes + row * info.strides[0] + column * info.strides[1]
    );
}

py::dict prepared_day_market(
    const py::object& dataset,
    const py::dict& portfolio_state
) {
    const py::array integers = py::cast<py::array>(dataset.attr("integers"));
    const py::array floats = py::cast<py::array>(dataset.attr("floats"));
    if (!integers.dtype().is(py::dtype::of<std::int64_t>())
        || !floats.dtype().is(py::dtype::of<double>())) {
        throw std::invalid_argument("prepared day arrays must use int64 and float64");
    }
    const py::buffer_info integer_info = integers.request();
    const py::buffer_info float_info = floats.request();
    constexpr py::ssize_t integer_columns = 9;
    constexpr py::ssize_t float_columns = 35;
    if (integer_info.ndim != 2 || float_info.ndim != 2
        || integer_info.shape[1] != integer_columns
        || float_info.shape[1] != float_columns
        || integer_info.shape[0] != float_info.shape[0]) {
        throw std::invalid_argument("prepared day arrays do not match schema v3");
    }
    const py::dict sidecar = py::cast<py::dict>(dataset.attr("sidecar"));
    const std::vector<std::string> symbols = py::cast<std::vector<std::string>>(
        sidecar["symbols"]
    );
    const std::vector<std::string> asset_types = py::cast<std::vector<std::string>>(
        sidecar["asset_types"]
    );
    const std::vector<std::string> exchanges = py::cast<std::vector<std::string>>(
        sidecar["exchanges"]
    );
    static constexpr const char* float_fields[float_columns] = {
        "open", "high", "low", "close", "close_unadjusted", "volume", "atr_14",
        "volume_sma_20", "dollar_volume_20", "ret_20d", "ret_60d", "sma_10",
        "sma_20", "sma_50", "sma_100", "sma_200", "ema_12", "ema_15", "ema_20",
        "ema_50", "rsi_2", "rsi_5", "rsi_14", "zscore_5", "zscore_10", "zscore_20",
        "prev_sma_10", "prev_sma_20", "prev_sma_50", "prev_sma_100", "prev_sma_200",
        "prev_ema_12", "prev_ema_15", "prev_ema_20", "prev_ema_50"
    };
    const py::module_ datetime_module = py::module_::import("datetime");
    const py::object date_type = datetime_module.attr("date");
    const py::object datetime_type = datetime_module.attr("datetime");
    const py::object utc = datetime_module.attr("timezone").attr("utc");
    const auto date_value = [&](std::int64_t ordinal) -> py::object {
        if (ordinal == std::numeric_limits<std::int64_t>::min()) return py::none();
        return date_type.attr("fromordinal")(ordinal);
    };
    const auto dictionary_value = [](
        const std::vector<std::string>& values,
        std::int64_t index,
        const char* label
    ) -> const std::string& {
        if (index < 0 || static_cast<std::size_t>(index) >= values.size()) {
            throw std::invalid_argument(std::string("prepared day ") + label + " id is invalid");
        }
        return values[static_cast<std::size_t>(index)];
    };

    std::map<std::int64_t, py::list> history_by_instrument;
    std::map<std::int64_t, py::dict> latest_by_instrument;
    for (py::ssize_t row = 0; row < integer_info.shape[0]; ++row) {
        const std::int64_t instrument_id = prepared_value<std::int64_t>(integer_info, row, 1);
        const std::int64_t timestamp = prepared_value<std::int64_t>(integer_info, row, 2);
        const std::int64_t ordinal = prepared_value<std::int64_t>(integer_info, row, 3);
        const std::string& symbol = dictionary_value(
            symbols, prepared_value<std::int64_t>(integer_info, row, 4), "symbol"
        );
        py::dict snapshot;
        snapshot["instrument_id"] = instrument_id;
        snapshot["symbol"] = symbol;
        snapshot["dt_ny"] = date_value(ordinal);
        snapshot["ts"] = timestamp == 0
            ? py::object(py::none())
            : datetime_type.attr("fromtimestamp")(
                static_cast<double>(timestamp) / 1'000'000.0,
                py::arg("tz") = utc
            );
        snapshot["asset_type"] = dictionary_value(
            asset_types, prepared_value<std::int64_t>(integer_info, row, 5), "asset type"
        );
        snapshot["exchange"] = dictionary_value(
            exchanges, prepared_value<std::int64_t>(integer_info, row, 6), "exchange"
        );
        snapshot["listed_at"] = date_value(
            prepared_value<std::int64_t>(integer_info, row, 7)
        );
        snapshot["delisted_at"] = date_value(
            prepared_value<std::int64_t>(integer_info, row, 8)
        );
        for (py::ssize_t column = 0; column < float_columns; ++column) {
            const double value = prepared_value<double>(float_info, row, column);
            snapshot[py::str(float_fields[column])] = std::isfinite(value)
                ? py::object(py::float_(value))
                : py::object(py::none());
        }
        history_by_instrument[instrument_id].append(snapshot);
        latest_by_instrument[instrument_id] = std::move(snapshot);
    }

    const py::dict positions = portfolio_state.contains("positions")
        && py::isinstance<py::dict>(portfolio_state["positions"])
        ? py::cast<py::dict>(portfolio_state["positions"])
        : py::dict();
    const py::dict hydration = portfolio_state.contains("support_resistance_hydration")
        && py::isinstance<py::dict>(portfolio_state["support_resistance_hydration"])
        ? py::cast<py::dict>(portfolio_state["support_resistance_hydration"])
        : py::dict();
    py::dict market;
    for (auto& [instrument_id, snapshot] : latest_by_instrument) {
        snapshot["recent_bars"] = history_by_instrument.at(instrument_id);
        const py::str instrument_key(std::to_string(instrument_id));
        py::dict position;
        if (positions.contains(instrument_key) && py::isinstance<py::dict>(positions[instrument_key])) {
            position = py::cast<py::dict>(positions[instrument_key]);
        }
        for (const char* key : {
            "position", "avg_entry_price", "entry_trade_date",
            "position_holding_days", "entry_signal_features", "support_risk_context", "support_stopped_zones"
        }) {
            snapshot[py::str(key)] = position.contains(key)
                ? py::reinterpret_borrow<py::object>(position[key])
                : py::object(py::none());
        }
        if (snapshot["position"].is_none()) snapshot["position"] = 0.0;
        if (hydration.contains(instrument_key)
            && py::isinstance<py::dict>(hydration[instrument_key])) {
            snapshot["support_resistance_hydration"] = hydration[instrument_key];
        }
        market[snapshot["symbol"]] = std::move(snapshot);
    }
    return market;
}

DayResult evaluate_day(
    const py::object& dataset_day,
    const py::dict& runtime,
    const py::dict& portfolio_state
) {
    const py::dict market = prepared_day_market(dataset_day, portfolio_state);
    const std::string type = py::cast<std::string>(runtime["strategy_type"]);
    py::list results;
    py::dict support_resistance;
    if (type == "trend") results = evaluate_trend(runtime, market);
    else if (type == "mean_reversion") results = evaluate_mean_reversion(runtime, market);
    else if (type == "momentum_breakout") results = evaluate_momentum(runtime, market);
    else if (type == "island_reversal" || type == "double_bottom" || type == "head_shoulders_bottom"
        || type == "rounded_bottom" || type == "v_reversal") {
        results = quant_kernel::evaluate_pattern_day(runtime, market);
    }
    else if (type == "support_resistance") {
        results = quant_kernel::evaluate_support_resistance_day(
            runtime, market, support_resistance
        );
    } else {
        throw std::invalid_argument("native strategy is not implemented: " + type);
    }
    quant_kernel::annotate_signal_strength(type, runtime, results);
    return typed_day_result(results, std::move(support_resistance));
}

py::list catalog() {
    return quant_kernel::strategy_catalog();
}

py::dict normalize_strategy(const std::string& type, const py::dict& params) {
    return quant_kernel::normalize_strategy_params(type, params);
}

}  // namespace

PYBIND11_MODULE(_native, module) {
    module.attr("KERNEL_VERSION") = kKernelVersion;
    module.attr("ABI_VERSION") = kAbiVersion;
    module.attr("BUILD_ID") = kBuildId;
    py::class_<DayResult>(module, "DayResult")
        .def_property_readonly("symbols", [](const DayResult& value) { return value.symbols; })
        .def_property_readonly("signals", [](py::object owner) {
            DayResult& value = owner.cast<DayResult&>();
            py::dict result;
            result["strategy_id"] = value.strategy_ids;
            result["timestamp_us"] = day_vector_view(owner, value.timestamp_us);
            result["instrument_id"] = day_vector_view(owner, value.instrument_ids);
            result["symbol_id"] = day_vector_view(owner, value.symbol_ids);
            result["action"] = day_vector_view(owner, value.actions);
            result["score"] = day_vector_view(owner, value.scores);
            result["reason"] = value.reasons;
            result["metadata_json"] = value.metadata_json;
            return result;
        })
        .def_property_readonly(
            "support_resistance",
            [](py::object owner) {
                DayResult& value = owner.cast<DayResult&>();
                const auto collection = [&owner](DayAuditCollection& items) {
                    py::dict result;
                    result["symbol_id"] = day_vector_view(owner, items.symbol_ids);
                    result["payload_json"] = items.payload_json;
                    return result;
                };
                py::dict result;
                result["symbols"] = value.support_symbols;
                result["events"] = collection(value.support_events);
                result["zone_versions"] = collection(value.support_zone_versions);
                result["regime_versions"] = collection(value.support_regime_versions);
                return result;
            }
        )
        .def("__len__", [](const DayResult& value) { return value.actions.size(); });
    module.def("catalog", &catalog);
    module.def("normalize_strategy", &normalize_strategy, py::arg("strategy_type"), py::arg("params"));
    module.def(
        "evaluate_day",
        &evaluate_day,
        py::arg("dataset_day"),
        py::arg("strategy"),
        py::arg("portfolio_state")
    );
    quant_kernel::bind_backtest(module);
    quant_kernel::bind_support_resistance(module);
}
