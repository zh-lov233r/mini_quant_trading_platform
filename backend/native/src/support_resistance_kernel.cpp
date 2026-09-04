#include "support_resistance_kernel.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace py = pybind11;
namespace sr = quant_kernel::support_resistance;

namespace quant_kernel {
namespace {

constexpr int kDetectorImplementationRevision = 12;
constexpr int kRegimeLogicRevision = 3;
constexpr const char* kEntryChannelSemantics = "support_upper_to_resistance_lower_v1";

py::object value_for(const py::handle& value, const char* key) {
    if (py::isinstance<py::dict>(value)) {
        const py::dict object = py::reinterpret_borrow<py::dict>(value);
        return object.contains(key)
            ? py::reinterpret_borrow<py::object>(object[key])
            : py::object(py::none());
    }
    return py::hasattr(value, key)
        ? py::reinterpret_borrow<py::object>(value.attr(key))
        : py::object(py::none());
}

double required_number(const py::handle& value, const char* key) {
    const py::object item = value_for(value, key);
    if (item.is_none()) throw std::invalid_argument(std::string("missing numeric field: ") + key);
    const double result = py::cast<double>(item);
    if (!std::isfinite(result)) throw std::invalid_argument(std::string("non-finite numeric field: ") + key);
    return result;
}

double number_or(const py::handle& value, const char* key, double fallback) {
    const py::object item = value_for(value, key);
    if (item.is_none()) return fallback;
    const double result = py::cast<double>(item);
    return std::isfinite(result) ? result : fallback;
}

int integer_or(const py::handle& value, const char* key, int fallback) {
    const py::object item = value_for(value, key);
    return item.is_none() ? fallback : py::cast<int>(item);
}

bool bool_or(const py::handle& value, const char* key, bool fallback) {
    const py::object item = value_for(value, key);
    return item.is_none() ? fallback : py::cast<bool>(item);
}

std::string text_or(const py::handle& value, const char* key, std::string fallback = {}) {
    const py::object item = value_for(value, key);
    return item.is_none() ? std::move(fallback) : py::cast<std::string>(py::str(item));
}

std::int32_t ordinal(const py::handle& value) {
    if (py::isinstance<py::int_>(value)) return py::cast<std::int32_t>(value);
    if (py::isinstance<py::str>(value)) {
        return sr::date_ordinal(py::cast<std::string>(value));
    }
    if (py::hasattr(value, "toordinal")) {
        return py::cast<std::int32_t>(value.attr("toordinal")());
    }
    throw std::invalid_argument("expected an ISO date value");
}

std::optional<std::int32_t> optional_ordinal(const py::handle& value) {
    return value.is_none() ? std::nullopt : std::optional(ordinal(value));
}

std::int32_t snapshot_ordinal(const py::dict& snapshot) {
    py::object value = value_for(snapshot, "dt_ny");
    if (!value.is_none()) {
        if (py::hasattr(value, "date") && py::hasattr(value, "hour")) value = value.attr("date")();
        return ordinal(value);
    }
    const py::object timestamp = value_for(snapshot, "ts");
    if (!timestamp.is_none() && py::hasattr(timestamp, "date")) return ordinal(timestamp.attr("date")());
    throw std::invalid_argument("support/resistance bar is missing its trade date");
}

std::int64_t timestamp_us(const py::handle& value) {
    if (value.is_none()) return 0;
    if (py::isinstance<py::int_>(value)) return py::cast<std::int64_t>(value);
    if (py::hasattr(value, "timestamp")) {
        return static_cast<std::int64_t>(std::llround(py::cast<double>(value.attr("timestamp")()) * 1'000'000.0));
    }
    return 0;
}

sr::JsonValue json_from_python(const py::handle& value) {
    if (value.is_none()) return nullptr;
    if (py::isinstance<py::bool_>(value)) return py::cast<bool>(value);
    if (py::isinstance<py::int_>(value)) return py::cast<std::int64_t>(value);
    if (py::isinstance<py::float_>(value)) {
        const double result = py::cast<double>(value);
        return std::isfinite(result) ? sr::JsonValue(result) : sr::JsonValue(nullptr);
    }
    if (py::isinstance<py::str>(value)) return py::cast<std::string>(value);
    if (py::isinstance<py::dict>(value)) {
        sr::JsonObject result;
        for (const auto& [key, item] : py::reinterpret_borrow<py::dict>(value)) {
            result.emplace_back(py::cast<std::string>(py::str(key)), json_from_python(item));
        }
        return result;
    }
    if (py::isinstance<py::list>(value) || py::isinstance<py::tuple>(value)) {
        sr::JsonArray result;
        for (const py::handle item : py::reinterpret_borrow<py::iterable>(value)) {
            result.push_back(json_from_python(item));
        }
        return result;
    }
    if (py::hasattr(value, "isoformat")) return py::cast<std::string>(value.attr("isoformat")());
    return py::cast<std::string>(py::str(value));
}

sr::JsonObject object_from_python(const py::handle& value) {
    if (value.is_none()) return {};
    sr::JsonValue converted = json_from_python(value);
    if (auto* object = std::get_if<sr::JsonObject>(&converted.value)) return std::move(*object);
    throw std::invalid_argument("expected a JSON object");
}

py::object python_from_json(const sr::JsonValue& value) {
    if (std::holds_alternative<std::nullptr_t>(value.value)) return py::none();
    if (const auto* item = std::get_if<bool>(&value.value)) return py::bool_(*item);
    if (const auto* item = std::get_if<std::int64_t>(&value.value)) return py::int_(*item);
    if (const auto* item = std::get_if<double>(&value.value)) return py::float_(*item);
    if (const auto* item = std::get_if<std::string>(&value.value)) return py::str(*item);
    if (const auto* item = std::get_if<sr::JsonArray>(&value.value)) {
        py::list result;
        for (const sr::JsonValue& child : *item) result.append(python_from_json(child));
        return result;
    }
    py::dict result;
    for (const auto& [key, child] : std::get<sr::JsonObject>(value.value)) {
        result[py::str(key)] = python_from_json(child);
    }
    return result;
}

py::dict python_from_object(const sr::JsonObject& value) {
    return py::cast<py::dict>(python_from_json(sr::JsonValue(value)));
}

sr::PivotKind pivot_kind(const std::string& value) {
    if (value == "low") return sr::PivotKind::Low;
    if (value == "high") return sr::PivotKind::High;
    throw std::invalid_argument("invalid support/resistance pivot kind: " + value);
}

sr::ZoneRole zone_role(const std::string& value) {
    if (value == "support") return sr::ZoneRole::Support;
    if (value == "resistance") return sr::ZoneRole::Resistance;
    throw std::invalid_argument("invalid support/resistance zone role: " + value);
}

sr::ZoneStatus zone_status(const std::string& value) {
    if (value == "active") return sr::ZoneStatus::Active;
    if (value == "expired") return sr::ZoneStatus::Expired;
    throw std::invalid_argument("invalid support/resistance zone status: " + value);
}

sr::Regime regime(const std::string& value) {
    if (value == "uptrend") return sr::Regime::Uptrend;
    if (value == "downtrend") return sr::Regime::Downtrend;
    if (value == "range") return sr::Regime::Range;
    return sr::Regime::Transition;
}

sr::Setup setup(const std::string& value) {
    if (value == "support_bounce") return sr::Setup::SupportBounce;
    if (value == "resistance_breakout") return sr::Setup::ResistanceBreakout;
    if (value == "breakout_retest") return sr::Setup::BreakoutRetest;
    throw std::invalid_argument("invalid support/resistance setup: " + value);
}

std::vector<std::string> string_list(const py::handle& value) {
    std::vector<std::string> result;
    if (value.is_none()) return result;
    for (const py::handle item : py::reinterpret_borrow<py::iterable>(value)) {
        result.push_back(py::cast<std::string>(py::str(item)));
    }
    return result;
}

sr::Pivot pivot_from_python(const py::handle& value) {
    return {
        text_or(value, "pivot_key"),
        pivot_kind(text_or(value, "kind")),
        integer_or(value, "session_index", 0),
        ordinal(value_for(value, "trade_date")),
        ordinal(value_for(value, "confirmed_on")),
        required_number(value, "price"),
        required_number(value, "atr"),
    };
}

sr::Zone zone_from_python(const py::handle& value) {
    const double center = required_number(value, "center");
    const double lower = required_number(value, "lower");
    const double upper = required_number(value, "upper");
    const py::object timeline = value_for(value, "timeline_effective_from");
    return {
        text_or(value, "zone_key"),
        pivot_kind(text_or(value, "source_kind")),
        zone_role(text_or(value, "role")),
        zone_status(text_or(value, "status", "active")),
        center,
        lower,
        upper,
        required_number(value, "atr"),
        string_list(value_for(value, "pivot_keys")),
        integer_or(value, "pivot_count", 0),
        integer_or(value, "touch_count", 0),
        ordinal(value_for(value, "first_pivot_date")),
        ordinal(value_for(value, "last_pivot_date")),
        ordinal(value_for(value, "valid_from")),
        integer_or(value, "anchor_session_index", 0),
        number_or(value, "anchor_center", center),
        number_or(value, "anchor_lower", lower),
        number_or(value, "anchor_upper", upper),
        number_or(value, "slope_per_session", 0.0),
        number_or(value, "fit_residual_atr", 0.0),
        number_or(value, "recency_weight", 0.0),
        bool_or(value, "last_inside", false),
        timeline.is_none() ? 0 : ordinal(timeline),
    };
}

sr::EntryChannel entry_channel_from_python(const py::handle& value) {
    sr::EntryChannel result;
    if (value.is_none()) return result;
    result.valid = bool_or(value, "valid", false);
    result.reason_code = text_or(value, "reason_code");
    const py::object frozen = value_for(value, "signal_trade_date");
    result.frozen_on_ordinal = frozen.is_none() ? 0 : ordinal(frozen);
    result.support_zone_key = text_or(value, "support_zone_key");
    result.resistance_zone_key = text_or(value, "resistance_zone_key");
    result.lower = number_or(value, "lower", 0.0);
    result.upper = number_or(value, "upper", 0.0);
    result.lower_slope_per_session = number_or(value, "lower_slope_per_session", 0.0);
    result.upper_slope_per_session = number_or(value, "upper_slope_per_session", 0.0);
    result.signal_close = number_or(value, "signal_close", 0.0);
    result.projected_sessions = integer_or(value, "projected_sessions", 0);
    const py::object support = value_for(value, "support_zone");
    const py::object resistance = value_for(value, "resistance_zone");
    if (!support.is_none()) result.support_zone = zone_from_python(support);
    if (!resistance.is_none()) result.resistance_zone = zone_from_python(resistance);
    result.semantics = text_or(value, "semantics", kEntryChannelSemantics);
    return result;
}

std::optional<sr::Bar> bar_from_python(const py::dict& snapshot) {
    const py::object raw_open = value_for(snapshot, "open");
    const py::object raw_high = value_for(snapshot, "high");
    const py::object raw_low = value_for(snapshot, "low");
    const py::object raw_close = value_for(snapshot, "close");
    if (raw_open.is_none() || raw_high.is_none() || raw_low.is_none() || raw_close.is_none()) {
        return std::nullopt;
    }
    const double open = py::cast<double>(raw_open);
    const double high = py::cast<double>(raw_high);
    const double low = py::cast<double>(raw_low);
    const double close = py::cast<double>(raw_close);
    if (!std::isfinite(open) || !std::isfinite(high) || !std::isfinite(low) || !std::isfinite(close)) {
        return std::nullopt;
    }
    double atr = number_or(snapshot, "atr_14", 0.0);
    if (atr <= 0.0) atr = std::max(high - low, close * 0.005);
    return sr::Bar{
        snapshot_ordinal(snapshot),
        timestamp_us(value_for(snapshot, "ts")),
        open,
        high,
        low,
        close,
        number_or(snapshot, "volume", 0.0),
        number_or(snapshot, "volume_sma_20", 0.0),
        atr,
        value_for(snapshot, "market_close").is_none() ? std::nullopt
            : std::optional(required_number(snapshot, "market_close")),
        value_for(snapshot, "market_sma_200").is_none() ? std::nullopt
            : std::optional(required_number(snapshot, "market_sma_200")),
    };
}

sr::PositionView position_from_python(const py::dict& snapshot) {
    sr::PositionView result;
    result.quantity = number_or(snapshot, "position", 0.0);
    const py::object average = value_for(snapshot, "avg_entry_price");
    if (!average.is_none()) result.average_entry_price = py::cast<double>(average);
    const py::object holding_days = value_for(snapshot, "position_holding_days");
    if (!holding_days.is_none()) result.holding_days = py::cast<int>(holding_days);
    const py::object features = value_for(snapshot, "entry_signal_features");
    if (!features.is_none()) result.entry_signal_features = object_from_python(features);
    return result;
}

py::object date_object(std::int32_t value) {
    return py::module_::import("datetime").attr("date").attr("fromordinal")(value);
}

py::object datetime_object(std::int64_t value) {
    if (value == 0) return py::none();
    const py::module_ datetime = py::module_::import("datetime");
    return datetime.attr("datetime").attr("fromtimestamp")(
        static_cast<double>(value) / 1'000'000.0,
        datetime.attr("timezone").attr("utc")
    );
}

py::dict bar_to_python(const sr::Bar& bar) {
    py::dict result;
    result["dt_ny"] = date_object(bar.date_ordinal);
    result["ts"] = datetime_object(bar.timestamp_us);
    result["open"] = bar.open;
    result["high"] = bar.high;
    result["low"] = bar.low;
    result["close"] = bar.close;
    result["volume"] = bar.volume;
    result["volume_sma_20"] = bar.volume_sma_20;
    result["atr_14"] = bar.atr_14;
    result["market_close"] = bar.market_close ? py::object(py::float_(*bar.market_close)) : py::object(py::none());
    result["market_sma_200"] = bar.market_sma_200 ? py::object(py::float_(*bar.market_sma_200)) : py::object(py::none());
    return result;
}

py::object construct(const char* type, const py::kwargs& kwargs) {
    return py::module_::import("src.services.support_resistance_service").attr(type)(**kwargs);
}

py::object pivot_to_python(const sr::Pivot& pivot) {
    py::kwargs values;
    values["pivot_key"] = pivot.pivot_key;
    values["kind"] = std::string(sr::name(pivot.kind));
    values["session_index"] = pivot.session_index;
    values["trade_date"] = date_object(pivot.trade_date_ordinal);
    values["confirmed_on"] = date_object(pivot.confirmed_on_ordinal);
    values["price"] = pivot.price;
    values["atr"] = pivot.atr;
    return construct("Pivot", values);
}

py::object zone_to_python(const sr::Zone& zone) {
    py::kwargs values;
    values["zone_key"] = zone.zone_key;
    values["source_kind"] = std::string(sr::name(zone.source_kind));
    values["role"] = std::string(sr::name(zone.role));
    values["status"] = std::string(sr::name(zone.status));
    values["center"] = zone.center;
    values["lower"] = zone.lower;
    values["upper"] = zone.upper;
    values["atr"] = zone.atr;
    values["pivot_keys"] = py::cast(zone.pivot_keys);
    values["pivot_count"] = zone.pivot_count;
    values["touch_count"] = zone.touch_count;
    values["first_pivot_date"] = date_object(zone.first_pivot_date_ordinal);
    values["last_pivot_date"] = date_object(zone.last_pivot_date_ordinal);
    values["valid_from"] = date_object(zone.valid_from_ordinal);
    values["anchor_session_index"] = zone.anchor_session_index;
    values["anchor_center"] = zone.anchor_center;
    values["anchor_lower"] = zone.anchor_lower;
    values["anchor_upper"] = zone.anchor_upper;
    values["slope_per_session"] = zone.slope_per_session;
    values["fit_residual_atr"] = zone.fit_residual_atr;
    values["recency_weight"] = zone.recency_weight;
    values["last_inside"] = zone.last_inside;
    values["timeline_effective_from"] = zone.timeline_effective_from_ordinal == 0
        ? py::object(py::none()) : date_object(zone.timeline_effective_from_ordinal);
    return construct("Zone", values);
}

sr::SymbolState state_from_python(const py::handle& source) {
    sr::SymbolState state;
    state.stopped_zones = py::cast<std::map<std::string, int>>(value_for(source, "stopped_zones"));
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value_for(source, "history"))) {
        if (const auto bar = bar_from_python(py::cast<py::dict>(raw))) state.history.push_back(*bar);
    }
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value_for(source, "pivots"))) {
        state.pivots.push_back(pivot_from_python(raw));
    }
    for (const auto& [raw_key, raw_zone] : py::cast<py::dict>(value_for(source, "zones"))) {
        state.zones.emplace(py::cast<std::string>(py::str(raw_key)), zone_from_python(raw_zone));
    }
    for (const auto& [raw_key, raw] : py::cast<py::dict>(value_for(source, "breakouts"))) {
        const std::string key = py::cast<std::string>(py::str(raw_key));
        state.breakouts.emplace(key, sr::BreakoutRecord{
            key,
            ordinal(value_for(raw, "breakout_date")),
            integer_or(raw, "breakout_session_index", 0),
            required_number(raw, "breakout_volume"),
        });
    }
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value_for(source, "pending_outcomes"))) {
        state.pending_outcomes.push_back({
            setup(text_or(raw, "setup")),
            text_or(raw, "zone_key"),
            ordinal(value_for(raw, "origin_date")),
            integer_or(raw, "origin_session_index", 0),
            required_number(raw, "target"),
            required_number(raw, "stop"),
            object_from_python(value_for(raw, "frozen")),
            number_or(raw, "entry_price", 0.0),
            text_or(raw, "exit_reason"),
            number_or(raw, "channel_lower", 0.0),
            number_or(raw, "channel_upper", 0.0),
        });
    }
    const py::dict stats = py::cast<py::dict>(value_for(source, "stats"));
    for (const auto& [raw_key, raw] : stats) {
        state.stats.insert_or_assign(setup(py::cast<std::string>(py::str(raw_key))), sr::SetupStats{
            integer_or(raw, "wins", 0), integer_or(raw, "losses", 0), integer_or(raw, "censored", 0),
        });
    }
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value_for(source, "events"))) {
        state.events.push_back(object_from_python(raw));
    }
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value_for(source, "zone_versions"))) {
        state.zone_versions.push_back(object_from_python(raw));
    }
    const py::object signatures = value_for(source, "version_signatures");
    if (!signatures.is_none()) {
        for (const auto& [raw_key, raw] : py::cast<py::dict>(signatures)) {
            state.version_signatures.emplace(
                py::cast<std::string>(py::str(raw_key)),
                py::isinstance<py::str>(raw)
                    ? py::cast<std::string>(raw)
                    : py::cast<std::string>(py::repr(raw))
            );
        }
    }
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value_for(source, "regime_versions"))) {
        state.regime_versions.push_back(object_from_python(raw));
    }
    state.current_regime = regime(text_or(source, "current_regime", "transition"));
    state.current_regime_evidence = object_from_python(value_for(source, "current_regime_evidence"));
    const py::object current_channel = value_for(source, "current_entry_channel");
    if (!current_channel.is_none()) state.current_entry_channel = entry_channel_from_python(current_channel);
    hydrate_support_resistance_symbol_state(state, py::dict(
        py::arg("zone_timeline") = value_for(source, "cached_zone_timeline"),
        py::arg("regime_timeline") = value_for(source, "cached_regime_timeline"),
        py::arg("lifecycle_events") = value_for(source, "cached_lifecycle_events")
    ));
    return state;
}

void sync_state_to_python(const sr::SymbolState& state, const py::handle& target) {
    target.attr("stopped_zones") = py::cast(state.stopped_zones);
    py::list history;
    for (const sr::Bar& bar : state.history) history.append(bar_to_python(bar));
    target.attr("history") = history;
    py::list pivots;
    for (const sr::Pivot& pivot : state.pivots) pivots.append(pivot_to_python(pivot));
    target.attr("pivots") = pivots;
    py::dict zones;
    for (const auto& [key, zone] : state.zones) zones[py::str(key)] = zone_to_python(zone);
    target.attr("zones") = zones;
    py::dict breakouts;
    for (const auto& [key, breakout] : state.breakouts) {
        py::kwargs values;
        values["zone_key"] = breakout.zone_key;
        values["breakout_date"] = date_object(breakout.breakout_date_ordinal);
        values["breakout_session_index"] = breakout.breakout_session_index;
        values["breakout_volume"] = breakout.breakout_volume;
        breakouts[py::str(key)] = construct("BreakoutRecord", values);
    }
    target.attr("breakouts") = breakouts;
    py::list pending;
    for (const sr::PendingOutcome& outcome : state.pending_outcomes) {
        py::kwargs values;
        values["setup"] = std::string(sr::name(outcome.setup));
        values["zone_key"] = outcome.zone_key;
        values["origin_date"] = date_object(outcome.origin_date_ordinal);
        values["origin_session_index"] = outcome.origin_session_index;
        values["target"] = outcome.target;
        values["stop"] = outcome.stop;
        values["frozen"] = python_from_object(outcome.frozen);
        values["entry_price"] = outcome.entry_price;
        values["exit_reason"] = outcome.exit_reason;
        values["channel_lower"] = outcome.channel_lower;
        values["channel_upper"] = outcome.channel_upper;
        pending.append(construct("PendingOutcome", values));
    }
    target.attr("pending_outcomes") = pending;
    py::dict stats;
    for (const auto& [key, value] : state.stats) {
        py::object item = py::module_::import("src.services.support_resistance_service").attr("SetupStats")();
        item.attr("wins") = value.wins;
        item.attr("losses") = value.losses;
        item.attr("censored") = value.censored;
        stats[py::str(std::string(sr::name(key)))] = item;
    }
    target.attr("stats") = stats;
    py::list events;
    for (const sr::JsonObject& value : state.events) events.append(python_from_object(value));
    target.attr("events") = events;
    py::list zone_versions;
    for (const sr::JsonObject& value : state.zone_versions) zone_versions.append(python_from_object(value));
    target.attr("zone_versions") = zone_versions;
    py::dict signatures;
    for (const auto& [key, value] : state.version_signatures) signatures[py::str(key)] = value;
    target.attr("version_signatures") = signatures;
    py::list regime_versions;
    for (const sr::JsonObject& value : state.regime_versions) regime_versions.append(python_from_object(value));
    target.attr("regime_versions") = regime_versions;
    target.attr("current_regime") = std::string(sr::name(state.current_regime));
    target.attr("current_regime_evidence") = python_from_object(state.current_regime_evidence);
    target.attr("current_entry_channel") = state.current_entry_channel
        ? py::object(python_from_object(sr::entry_channel_json(*state.current_entry_channel)))
        : py::object(py::none());
}

py::dict decision_to_python(const sr::Decision& decision) {
    py::dict result;
    result["action"] = decision.action == sr::Action::Buy ? "BUY" : "SELL";
    result["reason"] = decision.reason;
    result["score"] = decision.score ? py::object(py::float_(*decision.score)) : py::object(py::none());
    result["support_resistance"] = python_from_object(decision.support_resistance);
    return result;
}

}  // namespace

sr::Config parse_support_resistance_config(const py::dict& signal, const py::dict& risk) {
    sr::Config result;
    result.max_zones_per_kind = integer_or(signal, "max_zones_per_kind", result.max_zones_per_kind);
    result.pivot_tolerance_atr = number_or(signal, "pivot_tolerance_atr", result.pivot_tolerance_atr);
    result.risk_per_trade_pct = number_or(risk, "risk_per_trade_pct", result.risk_per_trade_pct);
    result.stop_cooldown_sessions = integer_or(risk, "stop_cooldown_sessions", result.stop_cooldown_sessions);
    result.break_even_at_r = number_or(risk, "break_even_at_r", result.break_even_at_r);
    result.market_filter_enabled = bool_or(risk, "market_filter_enabled", false);
    result.pivot_left_bars = integer_or(signal, "pivot_left_bars", result.pivot_left_bars);
    result.pivot_right_bars = integer_or(signal, "pivot_right_bars", result.pivot_right_bars);
    result.detection_window = integer_or(signal, "detection_window", result.detection_window);
    result.min_line_pivots = integer_or(signal, "min_line_pivots", result.min_line_pivots);
    result.min_line_span_sessions = integer_or(signal, "min_line_span_sessions", result.min_line_span_sessions);
    result.line_inlier_tolerance_atr = number_or(signal, "line_inlier_tolerance_atr", result.line_inlier_tolerance_atr);
    result.max_abs_slope_atr_per_session = number_or(signal, "max_abs_slope_atr_per_session", result.max_abs_slope_atr_per_session);
    result.zone_half_width_atr = number_or(signal, "zone_half_width_atr", result.zone_half_width_atr);
    result.decay_half_life = number_or(signal, "decay_half_life", result.decay_half_life);
    result.bounce_confirmation_atr = number_or(signal, "bounce_confirmation_atr", result.bounce_confirmation_atr);
    result.breakout_confirmation_atr = number_or(signal, "breakout_confirmation_atr", result.breakout_confirmation_atr);
    result.breakout_volume_ratio_min = number_or(signal, "breakout_volume_ratio_min", result.breakout_volume_ratio_min);
    result.support_bounce_enabled = bool_or(signal, "support_bounce_enabled", result.support_bounce_enabled);
    result.resistance_breakout_enabled = bool_or(signal, "resistance_breakout_enabled", result.resistance_breakout_enabled);
    result.breakout_retest_enabled = bool_or(signal, "breakout_retest_enabled", result.breakout_retest_enabled);
    result.retest_window = integer_or(signal, "retest_window", result.retest_window);
    result.retest_volume_ratio_max = number_or(signal, "retest_volume_ratio_max", result.retest_volume_ratio_max);
    result.min_strength_score = number_or(signal, "min_strength_score", result.min_strength_score);
    result.max_holding_days = integer_or(risk, "max_holding_days", result.max_holding_days);
    result.min_reward_risk = number_or(risk, "min_reward_risk", result.min_reward_risk);
    result.stop_loss_atr = number_or(risk, "stop_loss_atr", result.stop_loss_atr);
    result.max_loss_pct = number_or(risk, "max_loss_pct", result.max_loss_pct);
    result.take_profit_atr = number_or(risk, "take_profit_atr", result.take_profit_atr);
    return result;
}

sr::RiskContext parse_support_risk_context(const py::dict& payload) {
    sr::RiskContext result;
    if (payload.contains("market")) {
        for (const auto& [key, raw] : py::cast<py::dict>(payload["market"])) {
            const auto pair = py::cast<std::pair<double, double>>(raw);
            if (!std::isfinite(pair.first) || !std::isfinite(pair.second)
                || pair.first <= 0.0 || pair.second <= 0.0) {
                throw std::invalid_argument("market filter inputs must be finite and positive");
            }
            result.market.emplace(std::stoi(py::cast<std::string>(key)), pair);
        }
    }
    return result;
}

void hydrate_support_resistance_symbol_state(sr::SymbolState& state, const py::dict& payload) {
    const py::object zone_timeline = value_for(payload, "zone_timeline");
    if (!zone_timeline.is_none()) {
        for (const py::handle raw : py::reinterpret_borrow<py::iterable>(zone_timeline)) {
            sr::Zone zone = zone_from_python(raw);
            const py::object effective_to = value_for(raw, "effective_to");
            state.cached_zone_timeline.push_back({
                integer_or(raw, "version", 0),
                ordinal(value_for(raw, "effective_from")),
                optional_ordinal(effective_to),
                std::move(zone),
            });
        }
    }
    const py::object regime_timeline = value_for(payload, "regime_timeline");
    if (!regime_timeline.is_none()) {
        for (const py::handle raw : py::reinterpret_borrow<py::iterable>(regime_timeline)) {
            const py::object lower = value_for(raw, "lower_zone_key");
            const py::object upper = value_for(raw, "upper_zone_key");
            state.cached_regime_timeline.push_back({
                integer_or(raw, "version", 0),
                ordinal(value_for(raw, "effective_from")),
                regime(text_or(raw, "regime", "transition")),
                lower.is_none() ? std::nullopt : std::optional(py::cast<std::string>(py::str(lower))),
                upper.is_none() ? std::nullopt : std::optional(py::cast<std::string>(py::str(upper))),
                text_or(raw, "reason_code", "unknown"),
                object_from_python(value_for(raw, "evidence")),
            });
        }
    }
    const py::object lifecycle = value_for(payload, "lifecycle_events");
    if (!lifecycle.is_none()) {
        for (const py::handle raw : py::reinterpret_borrow<py::iterable>(lifecycle)) {
            const py::sequence item = py::cast<py::sequence>(raw);
            if (py::len(item) != 3) {
                throw std::invalid_argument("support/resistance cached lifecycle event must have three fields");
            }
            state.cached_lifecycle_events.push_back({
                ordinal(item[0]),
                py::cast<std::string>(py::str(item[2])),
                py::cast<std::string>(py::str(item[1])),
            });
        }
    }
}

py::list evaluate_support_resistance_day(
    const py::dict& runtime,
    const py::dict& market,
    py::dict audit
) {
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const sr::Config config = parse_support_resistance_config(
        py::cast<py::dict>(params["signal"]), py::cast<py::dict>(params["risk"])
    );
    const py::dict universe_cfg = py::cast<py::dict>(params["universe"]);
    std::vector<std::string> universe;
    const py::object configured = value_for(universe_cfg, "symbols");
    const std::string selection_mode = text_or(universe_cfg, "selection_mode", "explicit");
    if (selection_mode == "all_common_stock" && (configured.is_none() || py::len(configured) == 0)) {
        for (const auto& [raw_symbol, raw_snapshot] : market) {
            const std::string asset_type = text_or(raw_snapshot, "asset_type");
            std::string normalized = asset_type;
            std::transform(normalized.begin(), normalized.end(), normalized.begin(), [](unsigned char value) {
                return static_cast<char>(std::toupper(value));
            });
            if (normalized == "CS") universe.push_back(py::cast<std::string>(raw_symbol));
        }
        std::sort(universe.begin(), universe.end());
    } else if (!configured.is_none() && py::len(configured) > 0) {
        universe = string_list(configured);
    } else {
        for (const auto& [raw_symbol, unused] : market) {
            static_cast<void>(unused);
            universe.push_back(py::cast<std::string>(raw_symbol));
        }
        std::sort(universe.begin(), universe.end());
    }

    struct WorkItem {
        std::string symbol;
        py::dict snapshot;
        std::vector<sr::Bar> bars;
        sr::PositionView position;
        sr::SymbolState state;
        std::optional<sr::Decision> decision;
    };
    std::vector<WorkItem> work;
    for (const std::string& symbol : universe) {
        if (!market.contains(py::str(symbol))) continue;
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol)]);
        WorkItem item{symbol, snapshot};
        const py::object hydration = value_for(snapshot, "support_resistance_hydration");
        if (!hydration.is_none()) hydrate_support_resistance_symbol_state(item.state, py::cast<py::dict>(hydration));
        const py::object recent = value_for(snapshot, "recent_bars");
        if (!recent.is_none()) {
            for (const py::handle raw : py::reinterpret_borrow<py::iterable>(recent)) {
                if (const auto bar = bar_from_python(py::cast<py::dict>(raw))) item.bars.push_back(*bar);
            }
        }
        const auto current = bar_from_python(snapshot);
        if (current && (item.bars.empty() || item.bars.back().date_ordinal != current->date_ordinal)) {
            item.bars.push_back(*current);
        }
        item.position = position_from_python(snapshot);
        const auto context_value = value_for(snapshot, "support_risk_context");
        if (!context_value.is_none()) {
            const auto context = parse_support_risk_context(py::cast<py::dict>(context_value));
            for (auto& bar : item.bars) {
                if (const auto found = context.market.find(bar.date_ordinal); found != context.market.end()) {
                    bar.market_close = found->second.first;
                    bar.market_sma_200 = found->second.second;
                }
            }
        }
        const auto stopped = value_for(snapshot, "support_stopped_zones");
        if (!stopped.is_none()) {
            for (const auto& [key, day] : py::cast<py::dict>(stopped)) {
                const auto stop_day = ordinal(day);
                const auto index = std::lower_bound(item.bars.begin(), item.bars.end(), stop_day,
                    [](const sr::Bar& bar, std::int32_t value) { return bar.date_ordinal < value; });
                item.state.stopped_zones[py::cast<std::string>(key)] = static_cast<int>(index - item.bars.begin());
            }
        }
        work.push_back(std::move(item));
    }
    {
        py::gil_scoped_release release;
        for (WorkItem& item : work) {
            for (std::size_t index = 0; index < item.bars.size(); ++index) {
                const bool last = index + 1U == item.bars.size();
                item.decision = sr::advance_symbol(
                    item.state,
                    item.bars[index],
                    last ? item.position : sr::PositionView{},
                    config,
                    last
                );
            }
        }
    }
    py::list signals;
    for (WorkItem& item : work) {
        py::dict symbol_audit;
        py::list events;
        for (const sr::JsonObject& value : item.state.events) events.append(python_from_object(value));
        py::list zones;
        for (const sr::JsonObject& value : item.state.zone_versions) zones.append(python_from_object(value));
        py::list regimes;
        for (const sr::JsonObject& value : item.state.regime_versions) regimes.append(python_from_object(value));
        symbol_audit["events"] = events;
        symbol_audit["zone_versions"] = zones;
        symbol_audit["regime_versions"] = regimes;
        audit[py::str(item.symbol)] = symbol_audit;
        if (!item.decision) continue;
        py::dict raw = decision_to_python(*item.decision);
        py::dict metadata;
        for (const char* key : {"close", "open", "high", "low", "atr_14"}) {
            metadata[py::str(key)] = value_for(item.snapshot, key);
        }
        metadata["position"] = item.position.quantity;
        metadata["avg_entry_price"] = item.position.average_entry_price
            ? py::object(py::float_(*item.position.average_entry_price)) : py::object(py::none());
        metadata["support_resistance"] = raw["support_resistance"];
        py::dict event;
        event["strategy_id"] = runtime["strategy_id"];
        py::object timestamp = value_for(item.snapshot, "ts");
        if (timestamp.is_none()) {
            const py::module_ datetime = py::module_::import("datetime");
            timestamp = datetime.attr("datetime").attr("now")(datetime.attr("timezone").attr("utc"));
        }
        event["ts"] = timestamp;
        event["symbol"] = item.symbol;
        event["action"] = raw["action"];
        event["reason"] = raw["reason"];
        event["score"] = raw["score"];
        event["metadata"] = metadata;
        event["instrument_id"] = value_for(item.snapshot, "instrument_id");
        signals.append(event);
    }
    return signals;
}

void bind_support_resistance(py::module_& module) {
    py::module_ native = module.def_submodule(
        "support_resistance", "Native causal support/resistance detector primitives."
    );
    native.attr("DETECTOR_IMPLEMENTATION_REVISION") = kDetectorImplementationRevision;
    native.attr("REGIME_LOGIC_REVISION") = kRegimeLogicRevision;
    native.attr("ENTRY_CHANNEL_SEMANTICS") = kEntryChannelSemantics;
    native.def("size_entry", [](const py::dict& frozen, double price, double equity, double cash,
        const py::dict& risk, double commission_bps, double commission_min, double slippage_bps) {
        const auto result = sr::size_entry(object_from_python(frozen), price, equity, cash,
            required_number(risk, "position_size_pct"), parse_support_resistance_config(py::dict(), risk),
            commission_bps, commission_min, slippage_bps);
        return py::dict(py::arg("quantity") = result.quantity, py::arg("stop") = result.stop,
            py::arg("planned_loss") = result.planned_loss, py::arg("reward_risk") = result.reward_risk,
            py::arg("reason_code") = result.reason_code, py::arg("maximum_entry_price") = result.maximum_entry_price);
    }, py::arg("frozen"), py::arg("price"), py::arg("equity"), py::arg("cash"), py::arg("risk"),
        py::arg("commission_bps") = 0.0, py::arg("commission_min") = 0.0, py::arg("slippage_bps") = 0.0);
    native.def("stored_zone_price", &sr::stored_zone_price, py::arg("value"));
    native.def("valid_zone_values", &sr::valid_zone_values, py::arg("center"), py::arg("lower"), py::arg("upper"), py::arg("atr"), py::arg("slope"));
    native.def("normalized_detector_params", [](const py::dict& params) {
        py::dict signal;
        if (params.contains("signal") && py::isinstance<py::dict>(params["signal"])) signal = py::cast<py::dict>(params["signal"]);
        py::dict result;
        result["implementation_revision"] = kDetectorImplementationRevision;
        result["regime_logic_revision"] = kRegimeLogicRevision;
        result["max_zones_per_kind"] = integer_or(signal, "max_zones_per_kind", 3);
        result["pivot_tolerance_atr"] = number_or(signal, "pivot_tolerance_atr", 0.05);
        for (const char* key : {"pivot_left_bars", "pivot_right_bars", "detection_window", "min_line_pivots", "min_line_span_sessions", "line_inlier_tolerance_atr", "max_abs_slope_atr_per_session", "zone_half_width_atr", "decay_half_life", "breakout_confirmation_atr", "breakout_volume_ratio_min", "retest_window", "retest_volume_ratio_max"}) {
            if (signal.contains(key)) result[py::str(key)] = signal[py::str(key)];
        }
        return result;
    });
    native.def("build_entry_channel", [](const py::iterable& values, double close, const py::handle& date) {
        std::vector<sr::Zone> zones;
        for (const py::handle value : values) zones.push_back(zone_from_python(value));
        return python_from_object(sr::entry_channel_json(sr::build_entry_channel(zones, close, ordinal(date))));
    }, py::arg("zones"), py::arg("close"), py::arg("trade_date"));
    native.def("project_entry_channel", [](const py::object& value, int sessions) {
        py::dict payload;
        if (!value.is_none()) {
            for (const auto& [key, item] : py::cast<py::dict>(value)) payload[key] = item;
        }
        if (!payload.contains("valid") || !py::cast<bool>(payload["valid"])) {
            payload["valid"] = false;
            payload["reason_code"] = payload.contains("reason_code") && !payload["reason_code"].is_none()
                && !text_or(payload, "reason_code").empty()
                    ? py::object(py::str(payload["reason_code"]))
                    : py::object(py::str("missing_valid_entry_channel"));
            return payload;
        }
        if (!payload.contains("lower") || payload["lower"].is_none()
            || !payload.contains("upper") || payload["upper"].is_none()
            || !payload.contains("lower_slope_per_session") || payload["lower_slope_per_session"].is_none()
            || !payload.contains("upper_slope_per_session") || payload["upper_slope_per_session"].is_none()) {
            payload["valid"] = false;
            payload["reason_code"] = "missing_channel_projection_values";
            return payload;
        }
        const double lower = py::cast<double>(payload["lower"])
            + py::cast<double>(payload["lower_slope_per_session"]) * sessions;
        const double upper = py::cast<double>(payload["upper"])
            + py::cast<double>(payload["upper_slope_per_session"]) * sessions;
        if (!std::isfinite(lower) || !std::isfinite(upper) || lower <= 0.0 || upper <= 0.0) {
            payload["valid"] = false;
            payload["reason_code"] = "invalid_channel_projection_values";
            return payload;
        }
        payload["lower"] = lower;
        payload["upper"] = upper;
        if (lower >= upper) {
            payload["valid"] = false;
            payload["reason_code"] = "projected_inner_edges_crossed";
            return payload;
        }
        payload["projected_sessions"] = sessions;
        payload["valid"] = true;
        payload["reason_code"] = "valid_projected_inner_edge_channel";
        return payload;
    }, py::arg("channel"), py::arg("sessions") = 1);
    native.def("entry_price_is_inside_channel", [](const py::object& value, double price) {
        if (value.is_none()) return std::pair(false, std::string("missing_valid_entry_channel"));
        const py::dict payload = py::cast<py::dict>(value);
        if (!payload.contains("valid") || !py::cast<bool>(payload["valid"])) {
            return std::pair(false, text_or(payload, "reason_code", "missing_valid_entry_channel"));
        }
        if (!payload.contains("lower") || payload["lower"].is_none()
            || !payload.contains("upper") || payload["upper"].is_none()) {
            return std::pair(false, std::string("invalid_entry_channel_values"));
        }
        const double lower = py::cast<double>(payload["lower"]);
        const double upper = py::cast<double>(payload["upper"]);
        if (!std::isfinite(lower) || !std::isfinite(upper) || !std::isfinite(price)
            || lower <= 0.0 || upper <= 0.0 || price <= 0.0) {
            return std::pair(false, std::string("non_finite_entry_channel_values"));
        }
        if (lower >= upper) return std::pair(false, std::string("unordered_entry_channel"));
        if (price < lower || price > upper) {
            return std::pair(false, std::string("entry_price_outside_valid_channel"));
        }
        return std::pair(true, std::string("entry_price_inside_valid_channel"));
    }, py::arg("channel"), py::arg("price"));
    native.def("project_zone", [](const py::handle& value, int session_index) {
        return python_from_object(sr::zone_json(sr::project_zone(zone_from_python(value), session_index)));
    }, py::arg("zone"), py::arg("session_index"));
    native.def("freeze_zones_for_session", [](const py::iterable& values, int session_index, const py::handle& date) {
        py::list active;
        py::list expired;
        py::list events;
        std::vector<sr::Zone> retained;
        const std::int32_t effective = ordinal(date);
        for (const py::handle value : values) {
            sr::Zone zone = zone_from_python(value);
            if (zone.status != sr::ZoneStatus::Active) continue;
            sr::Zone projected = sr::project_zone(zone, session_index);
            if (sr::valid_zone_values(projected.center, projected.lower, projected.upper, projected.atr, projected.slope_per_session)) {
                retained.push_back(std::move(projected));
                continue;
            }
            zone.status = sr::ZoneStatus::Expired;
            zone.anchor_session_index = session_index;
            zone.anchor_center = zone.center;
            zone.anchor_lower = zone.lower;
            zone.anchor_upper = zone.upper;
            zone.slope_per_session = 0.0;
            py::dict version = python_from_object(sr::zone_json(zone));
            version["effective_from"] = sr::iso_date(effective);
            expired.append(version);
            py::dict event;
            event["event_date"] = sr::iso_date(effective);
            event["event_type"] = "invalidation";
            event["zone_key"] = zone.zone_key;
            event["role"] = std::string(sr::name(zone.role));
            event["reason"] = "projected_zone_geometry_became_invalid";
            events.append(event);
        }
        std::sort(retained.begin(), retained.end(), [](const sr::Zone& left, const sr::Zone& right) {
            return std::tuple(std::string(sr::name(left.role)), left.center, left.zone_key)
                < std::tuple(std::string(sr::name(right.role)), right.center, right.zone_key);
        });
        for (const sr::Zone& zone : retained) active.append(python_from_object(sr::zone_json(zone)));
        py::dict result;
        result["active_zones"] = active;
        result["expired_zone_versions"] = expired;
        result["events"] = events;
        return result;
    }, py::arg("zones"), py::arg("session_index"), py::arg("trade_date"));
    native.def("new_zone_key", [](const std::string& kind, const py::iterable& values) {
        std::vector<sr::Pivot> pivots;
        for (const py::handle value : values) {
            if (value_for(value, "kind").is_none()) {
                sr::Pivot pivot;
                pivot.pivot_key = text_or(value, "pivot_key");
                pivots.push_back(std::move(pivot));
            } else {
                pivots.push_back(pivot_from_python(value));
            }
        }
        return sr::new_zone_key(pivot_kind(kind), pivots);
    }, py::arg("source_kind"), py::arg("pivots"));
    native.def("revived_zone_key", [](const std::string& key, const py::handle& date) {
        return sr::revived_zone_key(key, ordinal(date));
    }, py::arg("zone_key"), py::arg("effective_date"));
    native.def("fit_pivot_line", [](const py::iterable& values, int current_index, const py::dict& signal) -> py::object {
        std::vector<sr::Pivot> pivots;
        for (const py::handle value : values) pivots.push_back(pivot_from_python(value));
        const auto fit = sr::fit_pivots(pivots, current_index, parse_support_resistance_config(signal, py::dict()));
        if (!fit) return py::none();
        py::list keys;
        for (const sr::Pivot& pivot : fit->inliers) keys.append(pivot.pivot_key);
        py::dict result;
        result["inlier_pivot_keys"] = keys;
        result["center"] = fit->center;
        result["slope"] = fit->slope;
        result["residual_atr"] = fit->residual_atr;
        result["total_weight"] = fit->total_weight;
        return result;
    }, py::arg("pivots"), py::arg("current_index"), py::arg("signal_cfg"));
    native.def("classify_market_regime", [](const py::object& state_object, const py::iterable& zone_values, const py::dict& raw_bar, const py::dict& signal) {
        const sr::SymbolState state = state_from_python(state_object);
        std::vector<sr::Zone> zones;
        for (const py::handle value : zone_values) zones.push_back(zone_from_python(value));
        const auto bar = bar_from_python(raw_bar);
        if (!bar) throw std::invalid_argument("support/resistance regime bar is invalid");
        const sr::RegimeEvidence result = sr::classify_regime(state, zones, *bar, parse_support_resistance_config(signal, py::dict()));
        return py::make_tuple(std::string(sr::name(result.regime)), python_from_object(result.payload));
    }, py::arg("state"), py::arg("zones"), py::arg("bar"), py::arg("signal_cfg"));
    native.def("record_regime_version", [](const py::object& state_object, const py::handle& effective, const std::string& regime_name, const py::dict& evidence) {
        sr::SymbolState state = state_from_python(state_object);
        sr::RegimeEvidence value;
        value.regime = regime(regime_name);
        value.payload = object_from_python(evidence);
        value.reason_code = text_or(evidence, "reason_code", "unknown");
        const py::object lower = value_for(evidence, "lower_zone_key");
        const py::object upper = value_for(evidence, "upper_zone_key");
        if (!lower.is_none()) value.lower_zone_key = py::cast<std::string>(py::str(lower));
        if (!upper.is_none()) value.upper_zone_key = py::cast<std::string>(py::str(upper));
        sr::record_regime(state, ordinal(effective), value);
        sync_state_to_python(state, state_object);
    }, py::arg("state"), py::arg("effective_from"), py::arg("regime"), py::arg("evidence"));
    native.def("rebuild_zones", [](const py::object& state_object, const py::dict& raw_bar, const py::dict& signal) {
        sr::SymbolState state = state_from_python(state_object);
        const auto bar = bar_from_python(raw_bar);
        if (!bar) throw std::invalid_argument("support/resistance rebuild bar is invalid");
        sr::rebuild(state, *bar, parse_support_resistance_config(signal, py::dict()));
        sync_state_to_python(state, state_object);
    }, py::arg("state"), py::arg("bar"), py::arg("signal_cfg"));
    native.def("match_zone", [](const py::iterable& values, const std::string& kind, double center, double half_width, const py::iterable& raw_keys) -> py::object {
        std::vector<sr::Zone> zones;
        for (const py::handle value : values) zones.push_back(zone_from_python(value));
        std::vector<std::string> keys = string_list(raw_keys);
        const auto matched = sr::match_existing_zone(zones, pivot_kind(kind), center, half_width, keys);
        return matched ? zone_to_python(*matched) : py::object(py::none());
    }, py::arg("old_zones"), py::arg("source_kind"), py::arg("center"), py::arg("half_width"), py::arg("pivot_keys"));
    native.def("record_zone_version", [](const py::object& state_object, const py::handle& raw_zone, const py::handle& effective, const std::string& status) {
        sr::SymbolState state = state_from_python(state_object);
        sr::record_zone(state, zone_from_python(raw_zone), ordinal(effective), zone_status(status));
        sync_state_to_python(state, state_object);
    }, py::arg("state"), py::arg("zone"), py::arg("effective_date"), py::arg("status"));
    native.def("advance_symbol", [](const py::object& state_object, const py::dict& snapshot, const py::dict& signal, const py::dict& risk, bool emit_signals) -> py::object {
        sr::SymbolState state = state_from_python(state_object);
        const auto bar = bar_from_python(snapshot);
        if (!bar) return py::none();
        const sr::PositionView position = position_from_python(snapshot);
        const sr::Config config = parse_support_resistance_config(signal, risk);
        std::optional<sr::Decision> decision;
        {
            py::gil_scoped_release release;
            decision = sr::advance_symbol(state, *bar, position, config, emit_signals);
        }
        sync_state_to_python(state, state_object);
        return decision ? py::object(decision_to_python(*decision)) : py::object(py::none());
    }, py::arg("state"), py::arg("snapshot"), py::arg("signal_cfg"), py::arg("risk_cfg"), py::arg("emit_signals") = true);
}

}  // namespace quant_kernel
