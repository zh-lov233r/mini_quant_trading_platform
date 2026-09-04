#include "support_resistance_core.hpp"

#include "native_utils.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <locale>
#include <set>
#include <sstream>
#include <stdexcept>
#include <tuple>

namespace quant_kernel::support_resistance {
namespace {

constexpr int kZonePriceScale = 10;

template <typename T>
const T* get(const JsonObject& value, const std::string& key) {
    const JsonValue* item = find(value, key);
    return item == nullptr ? nullptr : std::get_if<T>(&item->value);
}

std::optional<double> number(const JsonObject& value, const std::string& key) {
    const JsonValue* item = find(value, key);
    if (item == nullptr) return std::nullopt;
    if (const auto* result = std::get_if<double>(&item->value)) return *result;
    if (const auto* result = std::get_if<std::int64_t>(&item->value)) {
        return static_cast<double>(*result);
    }
    return std::nullopt;
}

std::string increment_decimal_integer(std::string digits) {
    if (digits.empty()) return "1";
    for (auto iterator = digits.rbegin(); iterator != digits.rend(); ++iterator) {
        if (*iterator != '9') {
            ++(*iterator);
            return digits;
        }
        *iterator = '0';
    }
    return "1" + digits;
}

std::string quantized_decimal_string(const std::string& input) {
    std::size_t cursor = 0;
    bool negative = false;
    if (cursor < input.size() && (input[cursor] == '+' || input[cursor] == '-')) {
        negative = input[cursor] == '-';
        ++cursor;
    }
    const std::size_t exponent_marker = input.find_first_of("eE", cursor);
    const std::string mantissa = input.substr(cursor, exponent_marker - cursor);
    int exponent = 0;
    if (exponent_marker != std::string::npos) {
        exponent = std::stoi(input.substr(exponent_marker + 1U));
    }
    const std::size_t decimal_point = mantissa.find('.');
    const int fractional_digits = decimal_point == std::string::npos
        ? 0
        : static_cast<int>(mantissa.size() - decimal_point - 1U);
    std::string digits;
    digits.reserve(mantissa.size());
    for (const char value : mantissa) {
        if (value == '.') continue;
        if (value < '0' || value > '9') {
            throw std::invalid_argument("zone price must be finite");
        }
        digits.push_back(value);
    }
    const std::size_t first_nonzero = digits.find_first_not_of('0');
    digits = first_nonzero == std::string::npos ? "0" : digits.substr(first_nonzero);

    const int scaled_exponent = exponent - fractional_digits + kZonePriceScale;
    std::string scaled_integer;
    if (scaled_exponent >= 0) {
        scaled_integer = digits + std::string(static_cast<std::size_t>(scaled_exponent), '0');
    } else {
        const std::size_t removed = static_cast<std::size_t>(-scaled_exponent);
        if (removed >= digits.size()) {
            const std::size_t leading_zeros = removed - digits.size();
            const bool round_up = leading_zeros == 0U && !digits.empty() && digits.front() >= '5';
            scaled_integer = round_up ? "1" : "0";
        } else {
            const std::size_t split = digits.size() - removed;
            scaled_integer = digits.substr(0U, split);
            if (digits[split] >= '5') scaled_integer = increment_decimal_integer(scaled_integer);
        }
    }
    const std::size_t nonzero = scaled_integer.find_first_not_of('0');
    scaled_integer = nonzero == std::string::npos ? "0" : scaled_integer.substr(nonzero);
    if (scaled_integer.size() <= static_cast<std::size_t>(kZonePriceScale)) {
        scaled_integer.insert(
            0U,
            static_cast<std::size_t>(kZonePriceScale) + 1U - scaled_integer.size(),
            '0'
        );
    }
    const std::size_t point = scaled_integer.size() - static_cast<std::size_t>(kZonePriceScale);
    scaled_integer.insert(point, 1U, '.');
    if (negative) scaled_integer.insert(0U, 1U, '-');
    return scaled_integer;
}

void append_json(std::string& output, const JsonValue& value);

void append_object(std::string& output, const JsonObject& value) {
    output.push_back('{');
    for (std::size_t index = 0; index < value.size(); ++index) {
        if (index > 0U) output.push_back(',');
        output += json_string(value[index].first);
        output.push_back(':');
        append_json(output, value[index].second);
    }
    output.push_back('}');
}

void append_json(std::string& output, const JsonValue& value) {
    if (std::holds_alternative<std::nullptr_t>(value.value)) {
        output += "null";
    } else if (const auto* item = std::get_if<bool>(&value.value)) {
        output += *item ? "true" : "false";
    } else if (const auto* item = std::get_if<std::int64_t>(&value.value)) {
        output += std::to_string(*item);
    } else if (const auto* item = std::get_if<double>(&value.value)) {
        if (!std::isfinite(*item)) throw std::invalid_argument("JSON number must be finite");
        std::ostringstream encoded;
        encoded.imbue(std::locale::classic());
        encoded << std::setprecision(std::numeric_limits<double>::max_digits10) << *item;
        output += encoded.str();
    } else if (const auto* item = std::get_if<std::string>(&value.value)) {
        output += json_string(*item);
    } else if (const auto* item = std::get_if<JsonArray>(&value.value)) {
        output.push_back('[');
        for (std::size_t index = 0; index < item->size(); ++index) {
            if (index > 0U) output.push_back(',');
            append_json(output, item->at(index));
        }
        output.push_back(']');
    } else {
        append_object(output, std::get<JsonObject>(value.value));
    }
}

JsonValue nullable(const std::optional<std::string>& value) {
    return value ? JsonValue(*value) : JsonValue(nullptr);
}

JsonValue nullable(const std::optional<double>& value) {
    return value ? JsonValue(*value) : JsonValue(nullptr);
}

JsonArray strings(const std::vector<std::string>& values) {
    JsonArray result;
    result.reserve(values.size());
    for (const std::string& value : values) result.emplace_back(value);
    return result;
}

JsonObject zone_snapshot(const Zone& zone) {
    JsonObject result = zone_json(zone);
    return result;
}

int setup_priority(Setup setup) {
    if (setup == Setup::BreakoutRetest) return 0;
    if (setup == Setup::SupportBounce) return 1;
    return 2;
}

double weighted_median(std::vector<std::pair<double, double>> values) {
    std::stable_sort(values.begin(), values.end(), [](const auto& left, const auto& right) {
        return left.first < right.first;
    });
    double total = 0.0;
    for (const auto& [unused, weight] : values) {
        static_cast<void>(unused);
        total += weight;
    }
    const double threshold = total / 2.0;
    double cumulative = 0.0;
    for (const auto& [value, weight] : values) {
        cumulative += weight;
        if (cumulative >= threshold) return value;
    }
    return values.back().first;
}

struct WeightedPivot {
    const Pivot* pivot = nullptr;
    double weight = 0.0;
};

std::optional<std::pair<double, double>> fit_line(
    const std::vector<WeightedPivot>& pivots,
    int minimum_span
) {
    std::vector<std::pair<double, double>> slopes;
    for (std::size_t left_index = 0; left_index < pivots.size(); ++left_index) {
        const WeightedPivot& left = pivots[left_index];
        for (std::size_t right_index = left_index + 1U; right_index < pivots.size(); ++right_index) {
            const WeightedPivot& right = pivots[right_index];
            const int span = right.pivot->session_index - left.pivot->session_index;
            if (span < minimum_span) continue;
            slopes.emplace_back(
                (right.pivot->price - left.pivot->price) / static_cast<double>(span),
                std::sqrt(left.weight * right.weight)
            );
        }
    }
    if (slopes.empty()) return std::nullopt;
    const double slope = weighted_median(std::move(slopes));
    std::vector<std::pair<double, double>> intercepts;
    intercepts.reserve(pivots.size());
    for (const WeightedPivot& pivot : pivots) {
        intercepts.emplace_back(
            pivot.pivot->price - slope * pivot.pivot->session_index,
            pivot.weight
        );
    }
    return std::pair(slope, weighted_median(std::move(intercepts)));
}

struct FitResult {
    std::vector<const Pivot*> inliers;
    double center = 0.0;
    double slope = 0.0;
    double residual_atr = 0.0;
    double total_weight = 0.0;
};

std::optional<FitResult> fit_pivot_line(
    const std::vector<const Pivot*>& raw_pivots,
    int current_index,
    const Config& config
) {
    if (raw_pivots.size() < static_cast<std::size_t>(config.min_line_pivots)
        || raw_pivots.back()->session_index - raw_pivots.front()->session_index
            < config.min_line_span_sessions) {
        return std::nullopt;
    }
    std::vector<WeightedPivot> pivots;
    pivots.reserve(raw_pivots.size());
    for (const Pivot* pivot : raw_pivots) {
        pivots.push_back({
            pivot,
            std::pow(
                0.5,
                static_cast<double>(std::max(current_index - pivot->session_index, 0))
                    / config.decay_half_life
            ),
        });
    }
    const auto initial = fit_line(pivots, config.min_line_span_sessions);
    if (!initial) return std::nullopt;
    const auto [initial_slope, initial_intercept] = *initial;
    std::vector<WeightedPivot> inliers;
    for (const WeightedPivot& pivot : pivots) {
        if (std::abs(
                pivot.pivot->price
                    - (initial_intercept + initial_slope * pivot.pivot->session_index)
            ) <= config.line_inlier_tolerance_atr * pivot.pivot->atr) {
            inliers.push_back(pivot);
        }
    }
    if (inliers.size() < static_cast<std::size_t>(config.min_line_pivots)
        || inliers.back().pivot->session_index - inliers.front().pivot->session_index
            < config.min_line_span_sessions) {
        return std::nullopt;
    }
    const auto refined = fit_line(inliers, config.min_line_span_sessions);
    if (!refined) return std::nullopt;
    const auto [slope, intercept] = *refined;
    std::vector<std::pair<double, double>> atrs;
    for (const WeightedPivot& pivot : inliers) {
        atrs.emplace_back(pivot.pivot->atr, pivot.weight);
    }
    const double representative_atr = weighted_median(std::move(atrs));
    if (representative_atr <= 0.0
        || std::abs(slope) / representative_atr > config.max_abs_slope_atr_per_session) {
        return std::nullopt;
    }
    FitResult result;
    result.center = intercept + slope * current_index;
    result.slope = slope;
    for (const WeightedPivot& pivot : inliers) {
        result.inliers.push_back(pivot.pivot);
        result.total_weight += pivot.weight;
        result.residual_atr += pivot.weight
            * std::abs(pivot.pivot->price - (intercept + slope * pivot.pivot->session_index))
            / std::max(pivot.pivot->atr, 1e-12);
    }
    result.residual_atr /= result.total_weight;
    return result;
}

std::vector<const Pivot*> zone_member_pivots(const SymbolState& state, const Zone& zone) {
    std::vector<const Pivot*> result;
    for (const Pivot& pivot : state.pivots) {
        if (pivot.kind == zone.source_kind) result.push_back(&pivot);
    }
    std::sort(result.begin(), result.end(), [](const Pivot* left, const Pivot* right) {
        return std::tuple(left->session_index, left->trade_date_ordinal, left->pivot_key)
            < std::tuple(right->session_index, right->trade_date_ordinal, right->pivot_key);
    });
    if (result.size() > 4U) result.erase(result.begin(), result.end() - 4);
    return result;
}

std::string direction(double delta, double tolerance) {
    if (delta > tolerance) return "up";
    if (delta < -tolerance) return "down";
    return "flat";
}

std::string pivot_direction(const std::vector<const Pivot*>& pivots, double half_width_ratio) {
    std::vector<std::pair<double, double>> changes;
    for (std::size_t i = 0; i < pivots.size(); ++i) {
        for (std::size_t j = i + 1; j < pivots.size(); ++j) {
            const double atr = (pivots[i]->atr + pivots[j]->atr) / 2.0;
            changes.emplace_back((pivots[j]->price - pivots[i]->price) / atr, 1.0);
        }
    }
    return direction(weighted_median(std::move(changes)), half_width_ratio);
}

const Zone* best_boundary(
    const std::vector<Zone>& zones,
    PivotKind source_kind,
    double close
) {
    std::vector<const Zone*> matches;
    for (const Zone& zone : zones) {
        if (zone.source_kind == source_kind && zone.status == ZoneStatus::Active
            && valid_zone_values(
                zone.center, zone.lower, zone.upper, zone.atr, zone.slope_per_session
            )) {
            matches.push_back(&zone);
        }
    }
    if (matches.empty()) return nullptr;
    std::sort(matches.begin(), matches.end(), [close](const Zone* left, const Zone* right) {
        return std::tuple(
            -left->pivot_count,
            -left->recency_weight,
            left->fit_residual_atr,
            std::abs(left->center - close),
            left->zone_key
        ) < std::tuple(
            -right->pivot_count,
            -right->recency_weight,
            right->fit_residual_atr,
            std::abs(right->center - close),
            right->zone_key
        );
    });
    return matches.front();
}

RegimeEvidence classify_market_regime(
    const SymbolState& state,
    const std::vector<Zone>& zones,
    const Bar& bar,
    const Config& config
) {
    const Zone* lower = best_boundary(zones, PivotKind::Low, bar.close);
    const Zone* upper = best_boundary(zones, PivotKind::High, bar.close);
    RegimeEvidence result;
    result.lower_zone_key = lower ? std::optional(lower->zone_key) : std::nullopt;
    result.upper_zone_key = upper ? std::optional(upper->zone_key) : std::nullopt;
    result.payload = object({
        {"lower_zone_key", nullable(result.lower_zone_key)},
        {"upper_zone_key", nullable(result.upper_zone_key)},
        {"evidence_trade_date", iso_date(bar.date_ordinal)},
        {"close", bar.close},
    });
    auto finish = [&](Regime regime, std::string reason) {
        result.regime = regime;
        result.reason_code = std::move(reason);
        set(result.payload, "reason_code", result.reason_code);
        return result;
    };
    if (lower == nullptr || upper == nullptr) {
        return finish(Regime::Transition, "missing_boundary");
    }
    if (lower->center >= upper->center) {
        set(result.payload, "lower_center", lower->center);
        set(result.payload, "upper_center", upper->center);
        return finish(Regime::Transition, "unordered_boundaries");
    }
    const std::vector<const Pivot*> lower_pivots = zone_member_pivots(state, *lower);
    const std::vector<const Pivot*> upper_pivots = zone_member_pivots(state, *upper);
    if (lower_pivots.size() < 2U || upper_pivots.size() < 2U) {
        set(result.payload, "lower_pivot_count", lower_pivots.size());
        set(result.payload, "upper_pivot_count", upper_pivots.size());
        return finish(Regime::Transition, "insufficient_pivot_structure");
    }
    const int span = std::max(config.min_line_span_sessions, 1);
    const double half_width_ratio = config.zone_half_width_atr;
    const std::string lower_boundary_direction = direction(
        lower->slope_per_session * span,
        std::max(lower->atr * half_width_ratio, 1e-12)
    );
    const std::string upper_boundary_direction = direction(
        upper->slope_per_session * span,
        std::max(upper->atr * half_width_ratio, 1e-12)
    );
    const std::string lower_pivot_direction = pivot_direction(lower_pivots, half_width_ratio);
    const std::string upper_pivot_direction = pivot_direction(upper_pivots, half_width_ratio);
    set(result.payload, "lower_center", lower->center);
    set(result.payload, "upper_center", upper->center);
    set(result.payload, "lower_boundary_direction", lower_boundary_direction);
    set(result.payload, "upper_boundary_direction", upper_boundary_direction);
    set(result.payload, "lower_pivot_direction", lower_pivot_direction);
    set(result.payload, "upper_pivot_direction", upper_pivot_direction);
    JsonArray lower_keys, upper_keys;
    for (const Pivot* pivot : lower_pivots) lower_keys.emplace_back(pivot->pivot_key);
    for (const Pivot* pivot : upper_pivots) upper_keys.emplace_back(pivot->pivot_key);
    set(result.payload, "lower_pivot_keys", std::move(lower_keys));
    set(result.payload, "upper_pivot_keys", std::move(upper_keys));
    set(result.payload, "pivot_structure_semantics", "latest_four_confirmed_swings");
    const std::array<std::string, 4> directions = {
        lower_boundary_direction,
        upper_boundary_direction,
        lower_pivot_direction,
        upper_pivot_direction,
    };
    if (std::all_of(directions.begin(), directions.end(), [](const auto& value) {
        return value == "up";
    })) {
        return bar.close < lower->lower
            ? finish(Regime::Transition, "uptrend_lower_boundary_broken")
            : finish(Regime::Uptrend, "rising_channel_higher_highs_higher_lows");
    }
    if (std::all_of(directions.begin(), directions.end(), [](const auto& value) {
        return value == "down";
    })) {
        return bar.close > upper->upper
            ? finish(Regime::Transition, "downtrend_upper_boundary_broken")
            : finish(Regime::Downtrend, "falling_channel_lower_highs_lower_lows");
    }
    const bool inside = lower->lower <= bar.close && bar.close <= upper->upper;
    if (inside && std::all_of(directions.begin(), directions.end(), [](const auto& value) {
        return value == "flat";
    })) {
        return finish(Regime::Range, "flat_range");
    }
    const bool contracting = (lower_boundary_direction == "up" || lower_boundary_direction == "flat")
        && (lower_pivot_direction == "up" || lower_pivot_direction == "flat")
        && (upper_boundary_direction == "down" || upper_boundary_direction == "flat")
        && (upper_pivot_direction == "down" || upper_pivot_direction == "flat")
        && (lower_boundary_direction == "up" || lower_pivot_direction == "up")
        && (upper_boundary_direction == "down" || upper_pivot_direction == "down");
    if (inside && contracting) return finish(Regime::Range, "contracting_range");
    const bool expanding = (lower_boundary_direction == "down" || lower_boundary_direction == "flat")
        && (lower_pivot_direction == "down" || lower_pivot_direction == "flat")
        && (upper_boundary_direction == "up" || upper_boundary_direction == "flat")
        && (upper_pivot_direction == "up" || upper_pivot_direction == "flat")
        && (lower_boundary_direction == "down" || lower_pivot_direction == "down")
        && (upper_boundary_direction == "up" || upper_pivot_direction == "up");
    if (inside && expanding) return finish(Regime::Range, "expanding_range");
    return finish(Regime::Transition, inside ? "structure_conflict" : "price_outside_range");
}

double rounded_two(double value) {
    return std::nearbyint(value * 100.0) / 100.0;
}

StrengthComponent strength_component(
    std::string key,
    double raw_value,
    double weight,
    double gate,
    double cap_or_ideal,
    bool rise = true
) {
    const double normalized = rise
        ? 100.0 * std::clamp((raw_value - gate) / (cap_or_ideal - gate), 0.0, 1.0)
        : 100.0 * std::clamp((gate - raw_value) / (gate - cap_or_ideal), 0.0, 1.0);
    return {std::move(key), raw_value, rounded_two(normalized), weight};
}

Strength build_strength(
    Setup setup,
    const Config& config,
    double confirmation_atr,
    double hold_margin_atr,
    std::optional<double> volume_ratio,
    std::optional<double> retest_volume_ratio,
    double reward_risk,
    const Zone& zone
) {
    std::vector<StrengthComponent> components;
    components = {
        strength_component("reward_risk", reward_risk, 0.25,
            config.min_reward_risk, config.min_reward_risk * 2.0),
        strength_component("pivot_count", zone.pivot_count, 0.15,
            config.min_line_pivots - 1.0, config.min_line_pivots + 3.0),
        strength_component("touch_count", zone.touch_count, 0.10, 0.0, 3.0),
        strength_component("fit_residual_atr", zone.fit_residual_atr, 0.15,
            config.line_inlier_tolerance_atr, 0.0, false),
    };
    if (setup == Setup::ResistanceBreakout) {
        components.push_back(strength_component("confirmation_atr", confirmation_atr,
            0.20, config.breakout_confirmation_atr, config.breakout_confirmation_atr * 2.0));
        components.push_back(strength_component("volume_ratio", volume_ratio.value_or(0.0),
            0.15, config.breakout_volume_ratio_min, config.breakout_volume_ratio_min * 2.0));
    } else {
        const double ideal = setup == Setup::SupportBounce ? config.bounce_confirmation_atr : 0.0;
        components.push_back(strength_component("support_proximity_atr", confirmation_atr,
            0.20, ideal + 2.0, ideal, false));
        components.push_back(setup == Setup::SupportBounce
            ? strength_component("volume_ratio", volume_ratio.value_or(0.0), 0.15, 0.5, 1.5)
            : strength_component("retest_volume_ratio", retest_volume_ratio.value_or(0.0),
                0.15, config.retest_volume_ratio_max, 0.0, false));
    }
    double weighted = 0.0;
    double total = 0.0;
    for (const StrengthComponent& component : components) {
        weighted += component.normalized_score * component.weight;
        total += component.weight;
    }
    const double score = rounded_two(weighted / total);
    return {
        score,
        config.min_strength_score,
        score >= config.min_strength_score,
        "support_resistance:" + std::string(name(setup)) + ":v2",
        std::move(components),
    };
}

}  // namespace

JsonObject object(std::initializer_list<std::pair<std::string, JsonValue>> values) {
    return JsonObject(values);
}

void set(JsonObject& value, std::string key, JsonValue item) {
    for (auto& [existing, current] : value) {
        if (existing == key) {
            current = std::move(item);
            return;
        }
    }
    value.emplace_back(std::move(key), std::move(item));
}

const JsonValue* find(const JsonObject& value, const std::string& key) {
    for (const auto& [existing, item] : value) {
        if (existing == key) return &item;
    }
    return nullptr;
}

std::string json(const JsonValue& value) {
    std::string output;
    append_json(output, value);
    return output;
}

std::string json(const JsonObject& value) {
    std::string output;
    append_object(output, value);
    return output;
}

std::string iso_date(std::int32_t ordinal) {
    using namespace std::chrono;
    const sys_days epoch = year{1970}/January/1;
    const year_month_day date{epoch + days{static_cast<std::int64_t>(ordinal) - 719163}};
    std::ostringstream output;
    output << std::setfill('0') << std::setw(4) << static_cast<int>(date.year()) << '-'
           << std::setw(2) << static_cast<unsigned>(date.month()) << '-'
           << std::setw(2) << static_cast<unsigned>(date.day());
    return output.str();
}

std::int32_t date_ordinal(const std::string& value) {
    if (value.size() < 10U) throw std::invalid_argument("expected an ISO date value");
    const int year_value = std::stoi(value.substr(0U, 4U));
    const unsigned month_value = static_cast<unsigned>(std::stoi(value.substr(5U, 2U)));
    const unsigned day_value = static_cast<unsigned>(std::stoi(value.substr(8U, 2U)));
    using namespace std::chrono;
    const sys_days epoch = year{1970}/January/1;
    const sys_days date = year{year_value}/month{month_value}/day{day_value};
    return static_cast<std::int32_t>((date - epoch).count() + 719163);
}

std::string_view name(PivotKind value) {
    return value == PivotKind::Low ? "low" : "high";
}

std::string_view name(ZoneRole value) {
    return value == ZoneRole::Support ? "support" : "resistance";
}

std::string_view name(ZoneStatus value) {
    return value == ZoneStatus::Active ? "active" : "expired";
}

std::string_view name(Regime value) {
    switch (value) {
        case Regime::Uptrend: return "uptrend";
        case Regime::Downtrend: return "downtrend";
        case Regime::Range: return "range";
        case Regime::Transition: return "transition";
    }
    return "transition";
}

std::string_view name(Setup value) {
    switch (value) {
        case Setup::SupportBounce: return "support_bounce";
        case Setup::ResistanceBreakout: return "resistance_breakout";
        case Setup::BreakoutRetest: return "breakout_retest";
    }
    return "support_bounce";
}

double stored_zone_price(double value) {
    if (!std::isfinite(value)) throw std::invalid_argument("zone price must be finite");
    std::array<char, 64> buffer{};
    std::string shortest;
    for (int precision = 1; precision <= std::numeric_limits<double>::max_digits10; ++precision) {
        const int size = std::snprintf(
            buffer.data(), buffer.size(), "%.*g", precision, value
        );
        if (size <= 0 || static_cast<std::size_t>(size) >= buffer.size()) {
            throw std::invalid_argument("zone price could not be encoded");
        }
        char* end = nullptr;
        const double roundtrip = std::strtod(buffer.data(), &end);
        if (end != nullptr && *end == '\0'
            && std::bit_cast<std::uint64_t>(roundtrip) == std::bit_cast<std::uint64_t>(value)) {
            shortest.assign(buffer.data(), static_cast<std::size_t>(size));
            break;
        }
    }
    if (shortest.empty()) throw std::invalid_argument("zone price could not be encoded");
    return std::stod(quantized_decimal_string(shortest));
}

bool valid_zone_values(double center, double lower, double upper, double atr, double slope) {
    return std::isfinite(center) && std::isfinite(lower) && std::isfinite(upper)
        && std::isfinite(atr) && std::isfinite(slope) && atr > 0.0
        && lower > 0.0 && lower <= center && center <= upper;
}

Zone project_zone(const Zone& zone, int session_index) {
    Zone result = zone;
    const double delta = zone.slope_per_session
        * static_cast<double>(session_index - zone.anchor_session_index);
    result.center = stored_zone_price(zone.anchor_center + delta);
    result.lower = stored_zone_price(zone.anchor_lower + delta);
    result.upper = stored_zone_price(zone.anchor_upper + delta);
    return result;
}

std::string new_zone_key(PivotKind source_kind, const std::vector<Pivot>& pivots) {
    std::vector<std::string> keys;
    for (const Pivot& pivot : pivots) keys.push_back(pivot.pivot_key);
    std::sort(keys.begin(), keys.end());
    std::string membership;
    for (std::size_t index = 0; index < keys.size(); ++index) {
        if (index > 0U) membership.push_back('|');
        membership += keys[index];
    }
    return "srz_" + sha256_hex(std::string(name(source_kind)) + "|" + membership).substr(0U, 20U);
}

std::string revived_zone_key(const std::string& zone_key, std::int32_t effective_date_ordinal) {
    return "srz_" + sha256_hex(
        zone_key + "|revived|" + iso_date(effective_date_ordinal)
    ).substr(0U, 20U);
}

EntryChannel build_entry_channel(
    const std::vector<Zone>& zones,
    double close,
    std::int32_t trade_date_ordinal
) {
    struct ChannelCandidate {
        const Zone* zone = nullptr;
        double distance = 0.0;
    };
    auto less = [](const ChannelCandidate& left, const ChannelCandidate& right) {
        if (left.distance != right.distance) return left.distance < right.distance;
        if (left.zone->pivot_count != right.zone->pivot_count) {
            return left.zone->pivot_count > right.zone->pivot_count;
        }
        if (left.zone->last_pivot_date_ordinal != right.zone->last_pivot_date_ordinal) {
            return left.zone->last_pivot_date_ordinal > right.zone->last_pivot_date_ordinal;
        }
        if (left.zone->fit_residual_atr != right.zone->fit_residual_atr) {
            return left.zone->fit_residual_atr < right.zone->fit_residual_atr;
        }
        return left.zone->zone_key < right.zone->zone_key;
    };
    std::vector<ChannelCandidate> supports;
    std::vector<ChannelCandidate> resistances;
    for (const Zone& zone : zones) {
        if (zone.status != ZoneStatus::Active || !valid_zone_values(
                zone.center, zone.lower, zone.upper, zone.atr, zone.slope_per_session
            )) {
            continue;
        }
        if (zone.upper <= close) supports.push_back({&zone, close - zone.upper});
        if (zone.lower >= close) resistances.push_back({&zone, zone.lower - close});
    }
    std::sort(supports.begin(), supports.end(), less);
    std::sort(resistances.begin(), resistances.end(), less);
    const Zone* support = supports.empty() ? nullptr : supports.front().zone;
    const Zone* resistance = resistances.empty() ? nullptr : resistances.front().zone;
    EntryChannel result;
    result.frozen_on_ordinal = trade_date_ordinal;
    result.signal_close = close;
    if (support != nullptr) {
        result.support_zone_key = support->zone_key;
        result.lower = support->upper;
        result.lower_slope_per_session = support->slope_per_session;
        result.support_zone = *support;
    }
    if (resistance != nullptr) {
        result.resistance_zone_key = resistance->zone_key;
        result.upper = resistance->lower;
        result.upper_slope_per_session = resistance->slope_per_session;
        result.resistance_zone = *resistance;
    }
    if (support == nullptr || resistance == nullptr) {
        result.reason_code = "missing_support_or_resistance";
    } else if (!(result.lower < result.upper)) {
        result.reason_code = "unordered_or_overlapping_inner_edges";
    } else if (!(result.lower <= close && close <= result.upper)) {
        result.reason_code = "signal_close_outside_inner_edges";
    } else {
        result.valid = true;
        result.reason_code = "valid_inner_edge_channel";
    }
    return result;
}

EntryChannel project_entry_channel(const EntryChannel& channel, int sessions) {
    EntryChannel result = channel;
    if (!result.valid) {
        result.reason_code = result.reason_code.empty()
            ? "missing_valid_entry_channel"
            : result.reason_code;
        return result;
    }
    const double lower = result.lower + result.lower_slope_per_session * sessions;
    const double upper = result.upper + result.upper_slope_per_session * sessions;
    if (!std::isfinite(lower) || !std::isfinite(upper) || lower <= 0.0 || upper <= 0.0) {
        result.valid = false;
        result.reason_code = "invalid_channel_projection_values";
        return result;
    }
    result.lower = lower;
    result.upper = upper;
    if (lower >= upper) {
        result.valid = false;
        result.reason_code = "projected_inner_edges_crossed";
        return result;
    }
    result.projected_sessions = sessions;
    result.reason_code = "valid_projected_inner_edge_channel";
    return result;
}

std::pair<bool, std::string> entry_price_is_inside_channel(
    const std::optional<EntryChannel>& channel,
    double price
) {
    if (!channel || !channel->valid) {
        return {false, channel && !channel->reason_code.empty()
            ? channel->reason_code
            : "missing_valid_entry_channel"};
    }
    if (!std::isfinite(channel->lower) || !std::isfinite(channel->upper)
        || !std::isfinite(price) || channel->lower <= 0.0 || channel->upper <= 0.0
        || price <= 0.0) {
        return {false, "non_finite_entry_channel_values"};
    }
    if (channel->lower >= channel->upper) return {false, "unordered_entry_channel"};
    if (price < channel->lower || price > channel->upper) {
        return {false, "entry_price_outside_valid_channel"};
    }
    return {true, "entry_price_inside_valid_channel"};
}

JsonObject zone_json(const Zone& zone) {
    return object({
        {"zone_key", zone.zone_key},
        {"source_kind", std::string(name(zone.source_kind))},
        {"role", std::string(name(zone.role))},
        {"status", std::string(name(zone.status))},
        {"center", zone.center},
        {"lower", zone.lower},
        {"upper", zone.upper},
        {"atr", zone.atr},
        {"pivot_keys", strings(zone.pivot_keys)},
        {"pivot_count", zone.pivot_count},
        {"touch_count", zone.touch_count},
        {"first_pivot_date", iso_date(zone.first_pivot_date_ordinal)},
        {"last_pivot_date", iso_date(zone.last_pivot_date_ordinal)},
        {"valid_from", iso_date(zone.valid_from_ordinal)},
        {"anchor_session_index", zone.anchor_session_index},
        {"anchor_center", zone.anchor_center},
        {"anchor_lower", zone.anchor_lower},
        {"anchor_upper", zone.anchor_upper},
        {"slope_per_session", zone.slope_per_session},
        {"fit_residual_atr", zone.fit_residual_atr},
        {"recency_weight", zone.recency_weight},
        {"last_inside", zone.last_inside},
    });
}

JsonObject entry_channel_json(const EntryChannel& channel) {
    JsonObject result = object({
        {"semantics", channel.semantics},
        {"signal_trade_date", iso_date(channel.frozen_on_ordinal)},
        {"signal_close", channel.signal_close},
        {"valid", channel.valid},
        {"reason_code", channel.reason_code.empty() ? JsonValue(nullptr) : JsonValue(channel.reason_code)},
        {"support_zone_key", channel.support_zone_key.empty()
            ? JsonValue(nullptr) : JsonValue(channel.support_zone_key)},
        {"resistance_zone_key", channel.resistance_zone_key.empty()
            ? JsonValue(nullptr) : JsonValue(channel.resistance_zone_key)},
        {"lower", channel.support_zone ? JsonValue(channel.lower) : JsonValue(nullptr)},
        {"upper", channel.resistance_zone ? JsonValue(channel.upper) : JsonValue(nullptr)},
        {"lower_slope_per_session", channel.support_zone
            ? JsonValue(channel.lower_slope_per_session) : JsonValue(nullptr)},
        {"upper_slope_per_session", channel.resistance_zone
            ? JsonValue(channel.upper_slope_per_session) : JsonValue(nullptr)},
        {"support_zone", channel.support_zone
            ? JsonValue(zone_snapshot(*channel.support_zone)) : JsonValue(nullptr)},
        {"resistance_zone", channel.resistance_zone
            ? JsonValue(zone_snapshot(*channel.resistance_zone)) : JsonValue(nullptr)},
    });
    if (channel.projected_sessions != 0) {
        set(result, "projected_sessions", channel.projected_sessions);
    }
    return result;
}

JsonObject strength_json(const Strength& strength) {
    std::string level = "very_strong";
    if (strength.score < 50.0) level = "weak";
    else if (strength.score < 70.0) level = "medium";
    else if (strength.score < 85.0) level = "strong";
    JsonArray components;
    for (const StrengthComponent& component : strength.components) {
        components.emplace_back(object({
            {"key", component.key},
            {"raw_value", component.raw_value},
            {"normalized_score", component.normalized_score},
            {"weight", component.weight},
        }));
    }
    return object({
        {"score", strength.score},
        {"level", level},
        {"threshold", strength.threshold},
        {"passes_threshold", strength.passes_threshold},
        {"rank", nullptr},
        {"model_version", strength.model_version},
        {"components", std::move(components)},
    });
}

JsonObject candidate_json(const Candidate& candidate) {
    return object({
        {"setup", std::string(name(candidate.setup))},
        {"zone_key", candidate.zone_key},
        {"zone", zone_snapshot(candidate.zone)},
        {"score", candidate.score},
        {"score_evidence", candidate.score_evidence},
        {"entry_eligible", candidate.entry_eligible},
        {"rejection_reason", nullable(candidate.rejection_reason)},
        {"stop_price", candidate.stop_price},
        {"target_price", candidate.target_price},
        {"reward_risk", candidate.reward_risk},
        {"overhead_count", candidate.overhead_count},
        {"target_source", candidate.overhead_count == 0 ? "atr_fallback" : "overhead_zone"},
        {"reason", candidate.reason},
        {"strength_inputs", candidate.strength_inputs},
        {"strength", strength_json(candidate.strength)},
        {"risk_eligible", candidate.risk_eligible},
        {"regime", std::string(name(candidate.regime))},
        {"regime_evidence", candidate.regime_evidence},
        {"regime_eligible", candidate.regime_eligible},
        {"entry_channel", entry_channel_json(candidate.entry_channel)},
        {"channel_eligible", candidate.channel_eligible},
    });
}

namespace {

void record_entry_channel_transition(
    SymbolState& state,
    const EntryChannel& channel,
    std::int32_t trade_date_ordinal
) {
    const auto pair_for = [](const std::optional<EntryChannel>& item)
        -> std::optional<std::pair<std::string, std::string>> {
        if (!item || !item->valid) return std::nullopt;
        return std::pair(item->support_zone_key, item->resistance_zone_key);
    };
    const std::optional<std::pair<std::string, std::string>> previous_pair = pair_for(
        state.current_entry_channel
    );
    const std::optional<EntryChannel> current_value = channel.valid
        ? std::optional(channel)
        : std::nullopt;
    const std::optional<std::pair<std::string, std::string>> current_pair = pair_for(current_value);
    if (previous_pair == current_pair) {
        state.current_entry_channel = current_value;
        return;
    }
    if (previous_pair && state.current_entry_channel) {
        const EntryChannel& previous = *state.current_entry_channel;
        state.events.push_back(object({
            {"event_date", iso_date(trade_date_ordinal)},
            {"event_type", "entry_channel_ended"},
            {"zone_key", previous.support_zone_key},
            {"lower", previous.lower},
            {"upper", previous.upper},
            {"entry_channel", entry_channel_json(previous)},
            {"reason_code", channel.reason_code.empty()
                ? JsonValue("entry_channel_pair_changed") : JsonValue(channel.reason_code)},
        }));
    }
    if (current_pair) {
        state.events.push_back(object({
            {"event_date", iso_date(trade_date_ordinal)},
            {"event_type", "entry_channel_started"},
            {"zone_key", channel.support_zone_key},
            {"lower", channel.lower},
            {"upper", channel.upper},
            {"entry_channel", entry_channel_json(channel)},
            {"reason_code", channel.reason_code},
        }));
    }
    state.current_entry_channel = current_value;
}

void activate_cached_zones(SymbolState& state, std::int32_t trade_date_ordinal) {
    std::map<std::string, const CachedZoneVersion*> latest;
    for (const CachedZoneVersion& payload : state.cached_zone_timeline) {
        if (payload.effective_from_ordinal >= trade_date_ordinal) continue;
        const auto previous = latest.find(payload.zone.zone_key);
        if (previous == latest.end()
            || previous->second->effective_from_ordinal < payload.effective_from_ordinal) {
            latest.insert_or_assign(payload.zone.zone_key, &payload);
        }
    }
    std::map<std::string, Zone> activated;
    for (const auto& [zone_key, payload] : latest) {
        if (payload->zone.status != ZoneStatus::Active) continue;
        Zone zone = payload->zone;
        const auto old = state.zones.find(zone_key);
        if (old != state.zones.end()
            && old->second.timeline_effective_from_ordinal == payload->effective_from_ordinal) {
            zone.touch_count = old->second.touch_count;
            zone.last_inside = old->second.last_inside;
        }
        zone.timeline_effective_from_ordinal = payload->effective_from_ordinal;
        activated.emplace(zone_key, std::move(zone));
    }
    state.zones = std::move(activated);
}

RegimeEvidence activate_cached_regime(SymbolState& state, std::int32_t trade_date_ordinal) {
    const CachedRegimeVersion* selected = nullptr;
    for (const CachedRegimeVersion& payload : state.cached_regime_timeline) {
        if (payload.effective_from_ordinal > trade_date_ordinal) continue;
        if (selected == nullptr
            || selected->effective_from_ordinal < payload.effective_from_ordinal
            || (selected->effective_from_ordinal == payload.effective_from_ordinal
                && selected->version < payload.version)) {
            selected = &payload;
        }
    }
    RegimeEvidence result;
    if (selected == nullptr) {
        result.regime = Regime::Transition;
        result.reason_code = "missing_cached_regime";
        result.payload = object({
            {"reason_code", result.reason_code},
            {"trade_date", iso_date(trade_date_ordinal)},
        });
    } else {
        result.regime = selected->regime;
        result.lower_zone_key = selected->lower_zone_key;
        result.upper_zone_key = selected->upper_zone_key;
        result.reason_code = selected->reason_code;
        result.payload = selected->evidence;
    }
    state.current_regime = result.regime;
    state.current_regime_evidence = result.payload;
    return result;
}

void record_regime_version(
    SymbolState& state,
    std::int32_t effective_from,
    const RegimeEvidence& evidence
) {
    std::optional<Regime> previous;
    if (!state.regime_versions.empty()) {
        if (const std::string* prior = get<std::string>(state.regime_versions.back(), "regime")) {
            if (*prior == "uptrend") previous = Regime::Uptrend;
            else if (*prior == "downtrend") previous = Regime::Downtrend;
            else if (*prior == "range") previous = Regime::Range;
            else previous = Regime::Transition;
        }
    }
    state.current_regime = evidence.regime;
    if (previous && *previous == evidence.regime) {
        state.current_regime_evidence = *get<JsonObject>(state.regime_versions.back(), "evidence");
        return;
    }
    state.current_regime_evidence = evidence.payload;
    JsonObject payload = object({
        {"version", state.regime_versions.size() + 1U},
        {"effective_from", iso_date(effective_from)},
        {"regime", std::string(name(evidence.regime))},
        {"lower_zone_key", nullable(evidence.lower_zone_key)},
        {"upper_zone_key", nullable(evidence.upper_zone_key)},
        {"reason_code", evidence.reason_code.empty() ? "unknown" : evidence.reason_code},
        {"evidence", evidence.payload},
    });
    state.regime_versions.push_back(payload);
    JsonObject event = object({
        {"event_date", iso_date(effective_from)},
        {"event_type", "regime_transition"},
        {"from_regime", previous
            ? JsonValue(std::string(name(*previous))) : JsonValue(nullptr)},
        {"to_regime", std::string(name(evidence.regime))},
    });
    for (const auto& item : payload) set(event, item.first, item.second);
    state.events.push_back(std::move(event));
}

void record_cached_lifecycle_events(SymbolState& state, std::int32_t trade_date_ordinal) {
    for (const CachedZoneVersion& payload : state.cached_zone_timeline) {
        if (payload.effective_from_ordinal != trade_date_ordinal
            || payload.zone.status != ZoneStatus::Expired) {
            continue;
        }
        const LifecycleEventKey signature{
            trade_date_ordinal,
            "invalidation",
            payload.zone.zone_key,
        };
        if (std::find(
                state.cached_lifecycle_events.begin(),
                state.cached_lifecycle_events.end(),
                signature
            ) != state.cached_lifecycle_events.end()) {
            continue;
        }
        state.cached_lifecycle_events.push_back(signature);
        state.events.push_back(object({
            {"event_date", iso_date(trade_date_ordinal)},
            {"event_type", "invalidation"},
            {"zone_key", payload.zone.zone_key},
            {"role", std::string(name(payload.zone.role))},
        }));
    }
}

std::optional<Decision> resolve_exit(const PositionView&, const Bar&, const Config&,
    const RegimeEvidence&, const SymbolState&);

double entry_stop(const JsonObject& frozen) {
    double stop = *number(frozen, "stop_price");
    if (const auto* zone = get<JsonObject>(frozen, "zone")) {
        stop = std::max(stop, number(*zone, "lower").value_or(stop)
            + std::max(number(*zone, "slope_per_session").value_or(0.0), 0.0));
    }
    return stop;
}

void resolve_prior_outcomes(SymbolState& state, const Bar& bar, int session_index,
    const Config& config, const RegimeEvidence& regime) {
    std::vector<PendingOutcome> remaining;
    for (PendingOutcome outcome : state.pending_outcomes) {
        SetupStats& stats = state.stats.at(outcome.setup);
        std::string result;
        if (!outcome.exit_reason.empty()) {
            if (outcome.exit_reason == "max_holding") { ++stats.censored; result = "censored"; }
            else if (bar.open > outcome.entry_price) { ++stats.wins; result = "win"; }
            else { ++stats.losses; result = "loss"; }
        } else {
            if (outcome.entry_price == 0.0) {
                outcome.stop = entry_stop(outcome.frozen);
                if (bar.open < outcome.channel_lower || bar.open > outcome.channel_upper
                    || bar.open <= outcome.stop
                    || (outcome.target - bar.open) / (bar.open - outcome.stop) < config.min_reward_risk) {
                    result = "entry_rejected";
                } else outcome.entry_price = bar.open;
            }
            if (result.empty()) {
                PositionView position{1.0, outcome.entry_price,
                    session_index - outcome.origin_session_index - 1,
                    object({{"support_resistance", outcome.frozen}})};
                if (const auto decision = resolve_exit(position, bar, config, regime, state)) {
                    outcome.exit_reason = *get<std::string>(decision->support_resistance, "exit_reason_code");
                }
                remaining.push_back(std::move(outcome));
                continue;
            }
        }
        state.events.push_back(object({
            {"event_date", iso_date(bar.date_ordinal)}, {"event_type", "score_outcome"},
            {"zone_key", outcome.zone_key}, {"setup", std::string(name(outcome.setup))},
            {"origin_date", iso_date(outcome.origin_date_ordinal)}, {"result", result},
            {"entry_price", outcome.entry_price}, {"exit_price", bar.open},
            {"exit_reason_code", outcome.exit_reason},
            {"return_pct", outcome.entry_price > 0.0 ? bar.open / outcome.entry_price - 1.0 : 0.0},
            {"posterior", stats.posterior()}, {"resolved_samples", stats.resolved()},
            {"censored", stats.censored},
            {"sampling", "one_active_episode_per_instrument_setup"},
            {"semantics", "gross_next_open_setup_outcome_not_portfolio_performance"},
        }));
    }
    state.pending_outcomes = std::move(remaining);
}

Candidate candidate_payload(
    const SymbolState& state,
    Setup setup,
    const Zone& zone,
    const std::vector<Zone>& zones,
    const Bar& bar,
    const Config& config
) {
    const double entry = bar.close;
    const double stop = std::max({
        zone.lower,
        entry - config.stop_loss_atr * bar.atr_14,
        entry * (1.0 - config.max_loss_pct),
    });
    std::vector<double> overhead;
    for (const Zone& candidate : zones) {
        if (candidate.status == ZoneStatus::Active && candidate.zone_key != zone.zone_key
            && candidate.lower > entry) {
            overhead.push_back(candidate.lower);
        }
    }
    std::sort(overhead.begin(), overhead.end());
    const double target = overhead.empty()
        ? entry + config.take_profit_atr * bar.atr_14
        : overhead.front();
    const double risk = entry - stop;
    const double reward_risk = risk > 0.0 ? (target - entry) / risk : 0.0;
    const bool eligible = reward_risk >= config.min_reward_risk;
    const SetupStats& stats = state.stats.at(setup);
    const auto breakout = state.breakouts.find(zone.zone_key);
    const std::optional<double> volume_ratio = bar.volume_sma_20 > 0.0
        ? std::optional(bar.volume / bar.volume_sma_20)
        : std::nullopt;
    const std::optional<double> retest_volume_ratio = breakout != state.breakouts.end()
        && breakout->second.breakout_volume > 0.0
            ? std::optional(bar.volume / breakout->second.breakout_volume)
            : std::nullopt;
    const double confirmation_atr = (entry - zone.upper) / bar.atr_14;
    const double hold_margin_atr = confirmation_atr;
    std::string reason;
    if (setup == Setup::SupportBounce) {
        reason = "confirmed bounce above a frozen support zone";
    } else if (setup == Setup::ResistanceBreakout) {
        reason = "volume-confirmed close above a frozen resistance zone";
    } else {
        reason = "low-volume retest held the former resistance zone";
    }
    Candidate result;
    result.setup = setup;
    result.zone_key = zone.zone_key;
    result.zone = zone;
    result.score = stats.posterior();
    result.score_evidence = object({
        {"wins", stats.wins},
        {"losses", stats.losses},
        {"censored", stats.censored},
        {"resolved_samples", stats.resolved()},
        {"alpha", stats.wins + 1},
        {"beta", stats.losses + stats.censored + 1},
        {"semantics", "positive_resolved_return_by_horizon_censored_as_non_success"},
    });
    result.entry_eligible = eligible;
    result.risk_eligible = eligible;
    result.rejection_reason = eligible
        ? std::nullopt
        : std::optional<std::string>("nearest resistance yields reward/risk below minimum");
    result.stop_price = stop;
    result.target_price = target;
    result.reward_risk = reward_risk;
    result.overhead_count = static_cast<int>(overhead.size());
    result.reason = std::move(reason);
    result.strength_inputs = object({
        {"confirmation_atr", confirmation_atr},
        {"hold_margin_atr", hold_margin_atr},
        {"volume_ratio", nullable(volume_ratio)},
        {"retest_volume_ratio", nullable(retest_volume_ratio)},
        {"reward_risk", reward_risk},
    });
    result.strength = build_strength(
        setup,
        config,
        confirmation_atr,
        hold_margin_atr,
        volume_ratio,
        retest_volume_ratio,
        reward_risk,
        zone
    );
    return result;
}

std::vector<Candidate> detect_candidates(
    SymbolState& state,
    const Bar& bar,
    const std::vector<Zone>& zones,
    int session_index,
    const Config& config
) {
    const std::optional<double> previous_close = state.history.empty()
        ? std::nullopt
        : std::optional(state.history.back().close);
    std::vector<Candidate> candidates;
    for (const Zone& zone : zones) {
        std::optional<Setup> setup;
        const bool breakout = previous_close
            && *previous_close <= zone.upper + config.breakout_confirmation_atr * bar.atr_14
            && bar.close > zone.upper + config.breakout_confirmation_atr * bar.atr_14
            && bar.volume_sma_20 > 0.0
            && bar.volume >= config.breakout_volume_ratio_min * bar.volume_sma_20;
        if (config.support_bounce_enabled && previous_close
            && *previous_close > zone.upper && bar.low <= zone.upper
            && bar.close >= zone.upper + config.bounce_confirmation_atr * bar.atr_14) {
            setup = Setup::SupportBounce;
        }
        if (breakout) {
            state.breakouts.insert_or_assign(zone.zone_key, BreakoutRecord{
                zone.zone_key,
                bar.date_ordinal,
                session_index,
                bar.volume,
            });
            state.events.push_back(object({
                {"event_date", iso_date(bar.date_ordinal)},
                {"event_type", "breakout"},
                {"zone_key", zone.zone_key},
                {"setup", "resistance_breakout"},
                {"role", std::string(name(zone.role))},
                {"lower", zone.lower},
                {"upper", zone.upper},
                {"breakout_volume", bar.volume},
            }));
            if (config.resistance_breakout_enabled) setup = Setup::ResistanceBreakout;
        }
        if (setup) candidates.push_back(candidate_payload(
            state, *setup, zone, zones, bar, config
        ));
    }
    for (const auto& [key, breakout] : state.breakouts) {
        const int elapsed = session_index - breakout.breakout_session_index;
        if (elapsed <= 0 || elapsed > config.retest_window) continue;
        const auto zone = std::find_if(zones.begin(), zones.end(), [&](const Zone& candidate) {
            return candidate.zone_key == key;
        });
        if (zone == zones.end()) continue;
        if (bar.low <= zone->upper && bar.close >= zone->upper
            && bar.volume <= breakout.breakout_volume * config.retest_volume_ratio_max) {
            state.events.push_back(object({
                {"event_date", iso_date(bar.date_ordinal)},
                {"event_type", "retest"},
                {"zone_key", key},
                {"setup", "breakout_retest"},
                {"role", std::string(name(zone->role))},
                {"lower", zone->lower},
                {"upper", zone->upper},
                {"breakout_date", iso_date(breakout.breakout_date_ordinal)},
                {"breakout_volume", breakout.breakout_volume},
                {"retest_volume", bar.volume},
            }));
            if (config.breakout_retest_enabled) candidates.push_back(candidate_payload(
                state, Setup::BreakoutRetest, *zone, zones, bar, config
            ));
        }
    }
    std::sort(candidates.begin(), candidates.end(), [](const Candidate& left, const Candidate& right) {
        return std::pair(setup_priority(left.setup), left.zone_key)
            < std::pair(setup_priority(right.setup), right.zone_key);
    });
    return candidates;
}

void apply_regime_entry_policy(
    SymbolState& state,
    std::vector<Candidate>& candidates,
    const RegimeEvidence& regime,
    const EntryChannel& entry_channel,
    std::int32_t trade_date_ordinal
) {
    for (Candidate& candidate : candidates) {
        candidate.risk_eligible = candidate.entry_eligible;
        candidate.regime = regime.regime;
        candidate.regime_evidence = regime.payload;
        candidate.regime_eligible = (regime.regime == Regime::Uptrend
                && (candidate.setup == Setup::SupportBounce
                    || candidate.setup == Setup::BreakoutRetest))
            || (regime.regime == Regime::Range && candidate.setup == Setup::SupportBounce);
        candidate.entry_channel = entry_channel;
        candidate.channel_eligible = entry_channel.valid;
        const bool direct_breakout = candidate.setup == Setup::ResistanceBreakout;
        candidate.entry_eligible = candidate.risk_eligible && candidate.regime_eligible
            && candidate.channel_eligible && !direct_breakout;
        if (direct_breakout) {
            candidate.rejection_reason = "direct_breakout_audit_only";
            state.events.push_back(object({
                {"event_date", iso_date(trade_date_ordinal)},
                {"event_type", "direct_breakout_audit"},
                {"zone_key", candidate.zone_key},
                {"setup", std::string(name(candidate.setup))},
                {"regime", std::string(name(regime.regime))},
                {"reason_code", "direct_breakout_audit_only"},
                {"entry_channel", entry_channel_json(entry_channel)},
            }));
            continue;
        }
        if (!candidate.channel_eligible) {
            candidate.rejection_reason = entry_channel.reason_code.empty()
                ? "missing_valid_entry_channel"
                : entry_channel.reason_code;
            state.events.push_back(object({
                {"event_date", iso_date(trade_date_ordinal)},
                {"event_type", "entry_channel_rejection"},
                {"zone_key", candidate.zone_key},
                {"setup", std::string(name(candidate.setup))},
                {"regime", std::string(name(regime.regime))},
                {"reason_code", *candidate.rejection_reason},
                {"entry_channel", entry_channel_json(entry_channel)},
            }));
        }
        if (!candidate.regime_eligible) {
            candidate.rejection_reason = "setup " + std::string(name(candidate.setup))
                + " is not allowed in " + std::string(name(regime.regime)) + " regime";
            state.events.push_back(object({
                {"event_date", iso_date(trade_date_ordinal)},
                {"event_type", "regime_rejection"},
                {"zone_key", candidate.zone_key},
                {"setup", std::string(name(candidate.setup))},
                {"regime", std::string(name(regime.regime))},
                {"reason_code", "setup_not_allowed_in_regime"},
                {"regime_evidence", regime.payload},
            }));
        }
    }
}

const Candidate* select_candidate(const std::vector<Candidate>& candidates) {
    std::vector<const Candidate*> eligible;
    for (const Candidate& candidate : candidates) {
        if (candidate.entry_eligible) eligible.push_back(&candidate);
    }
    if (eligible.empty()) return nullptr;
    std::sort(eligible.begin(), eligible.end(), [](const Candidate* left, const Candidate* right) {
        return std::tuple(-left->strength.score, setup_priority(left->setup), left->zone_key)
            < std::tuple(-right->strength.score, setup_priority(right->setup), right->zone_key);
    });
    return eligible.front();
}

std::optional<Decision> resolve_exit(
    const PositionView& position,
    const Bar& bar,
    const Config& config,
    const RegimeEvidence& regime,
    const SymbolState& state
) {
    if (!position.entry_signal_features) return std::nullopt;
    const JsonObject* frozen = get<JsonObject>(*position.entry_signal_features, "support_resistance");
    if (frozen == nullptr) return std::nullopt;
    std::optional<double> entry = position.average_entry_price;
    if (!entry || *entry == 0.0) entry = number(*frozen, "entry_close");
    if (!entry) return std::nullopt;
    std::optional<double> atr = number(*frozen, "entry_atr");
    if (!atr || *atr == 0.0) atr = bar.atr_14;
    std::optional<double> zone_line;
    if (const JsonObject* zone = get<JsonObject>(*frozen, "zone")) {
        zone_line = number(*zone, "lower");
        if (zone_line) {
            const double slope = number(*zone, "slope_per_session").value_or(0.0);
            int elapsed = position.holding_days.value_or(0) + 1;
            if (const auto* signal_date = get<std::string>(*frozen, "signal_date")) {
                const auto ordinal = date_ordinal(*signal_date);
                elapsed = 1 + static_cast<int>(std::count_if(state.history.begin(), state.history.end(),
                    [&](const Bar& past) { return past.date_ordinal > ordinal; }));
            }
            zone_line = *zone_line + std::max(slope, 0.0) * elapsed;
        }
    }
    double stop = std::max(
        *entry - config.stop_loss_atr * *atr,
        *entry * (1.0 - config.max_loss_pct)
    );
    if (zone_line) stop = std::max(stop, *zone_line);
    const double initial_stop = number(*frozen, "stop_price").value_or(stop);
    stop = std::max(stop, initial_stop);
    const double initial_risk = *entry - initial_stop;
    if (const auto* signal_date = get<std::string>(*frozen, "signal_date")) {
        const auto ordinal = date_ordinal(*signal_date);
        if (initial_risk > 0.0 && std::any_of(state.history.begin(), state.history.end(),
            [&](const Bar& past) {
                return past.date_ordinal > ordinal
                    && past.close >= *entry + config.break_even_at_r * initial_risk;
            })) stop = std::max(stop, *entry);
    }
    const std::optional<double> target_value = number(*frozen, "target_price");
    const double target = target_value && *target_value != 0.0
        ? *target_value
        : *entry + config.take_profit_atr * *atr;
    std::optional<std::string> reason;
    if (bar.close < stop) reason = "closed below the projected zone-aware stop";
    else if (bar.close >= target) reason = "reached the frozen support/resistance target";
    else if (regime.regime == Regime::Downtrend) reason = "confirmed downtrend regime";
    else if (position.holding_days.value_or(0) >= config.max_holding_days) {
        reason = "reached the maximum support/resistance holding period";
    }
    if (!reason) return std::nullopt;
    JsonObject metadata = *frozen;
    set(metadata, "exit_reason_code", bar.close < stop ? "stop" : (bar.close >= target ? "target" : (regime.regime == Regime::Downtrend ? "downtrend" : "max_holding")));
    set(metadata, "exit_stop_price", stop);
    set(metadata, "exit_target_price", target);
    set(metadata, "exit_regime", std::string(name(regime.regime)));
    set(metadata, "exit_regime_evidence", regime.payload);
    return Decision{Action::Sell, *reason, std::nullopt, std::move(metadata)};
}

void apply_current_bar_zone_state(
    SymbolState& state,
    const Bar& bar,
    int session_index,
    const Config& config
) {
    for (auto& [unused, zone] : state.zones) {
        static_cast<void>(unused);
        zone = project_zone(zone, session_index);
    }
    for (auto& [key, zone] : state.zones) {
        if (zone.status != ZoneStatus::Active) continue;
        const bool inside = bar.high >= zone.lower && bar.low <= zone.upper;
        if (inside && !zone.last_inside) {
            ++zone.touch_count;
            state.events.push_back(object({
                {"event_date", iso_date(bar.date_ordinal)},
                {"event_type", "touch"},
                {"zone_key", zone.zone_key},
                {"role", std::string(name(zone.role))},
                {"lower", zone.lower},
                {"upper", zone.upper},
            }));
        }
        zone.last_inside = inside;
        const auto breakout = state.breakouts.find(key);
        const ZoneRole role = zone.role;
        if (role == ZoneRole::Resistance && bar.close >= zone.center) {
            zone.role = ZoneRole::Support;
            state.events.push_back(object({
                {"event_date", iso_date(bar.date_ordinal)},
                {"event_type", "role_transition"},
                {"zone_key", key},
                {"from_role", "resistance"},
                {"to_role", "support"},
                {"lower", zone.lower},
                {"upper", zone.upper},
                {"reason", breakout != state.breakouts.end()
                    && session_index == breakout->second.breakout_session_index
                        ? "confirmed_breakout" : "close_above_resistance"},
            }));
        } else if (role == ZoneRole::Support && bar.close < zone.center) {
            zone.role = ZoneRole::Resistance;
            state.events.push_back(object({
                {"event_date", iso_date(bar.date_ordinal)},
                {"event_type", "role_transition"},
                {"zone_key", key},
                {"from_role", "support"},
                {"to_role", "resistance"},
                {"lower", zone.lower},
                {"upper", zone.upper},
                {"reason", "support_breakdown"},
            }));
            state.breakouts.erase(key);
        } else if (role == ZoneRole::Support && breakout != state.breakouts.end()
            && session_index > breakout->second.breakout_session_index
            && session_index <= breakout->second.breakout_session_index + config.retest_window
            && bar.low <= zone.upper && bar.close >= zone.upper
            && bar.volume <= breakout->second.breakout_volume * config.retest_volume_ratio_max) {
            state.breakouts.erase(key);
        }
    }
    std::vector<std::string> expired;
    for (const auto& [key, breakout] : state.breakouts) {
        if (session_index - breakout.breakout_session_index > config.retest_window) {
            expired.push_back(key);
        }
    }
    for (const std::string& key : expired) state.breakouts.erase(key);
}

void confirm_pivots(SymbolState& state, const Config& config) {
    const int pivot_index = static_cast<int>(state.history.size()) - 1 - config.pivot_right_bars;
    if (pivot_index < config.pivot_left_bars) return;
    const Bar& candidate = state.history[static_cast<std::size_t>(pivot_index)];
    const std::int32_t confirmed_on = state.history.back().date_ordinal;
    for (const PivotKind kind : {PivotKind::High, PivotKind::Low}) {
        std::vector<double> values;
        for (int index = pivot_index - config.pivot_left_bars;
             index <= pivot_index + config.pivot_right_bars;
             ++index) {
            const Bar& bar = state.history[static_cast<std::size_t>(index)];
            values.push_back(kind == PivotKind::High ? bar.high : bar.low);
        }
        const double price = kind == PivotKind::High ? candidate.high : candidate.low;
        const double extreme = kind == PivotKind::High
            ? *std::max_element(values.begin(), values.end())
            : *std::min_element(values.begin(), values.end());
        const double tolerance = config.pivot_tolerance_atr * candidate.atr_14;
        const auto first = std::find_if(values.begin(), values.end(), [&](double value) {
            return std::abs(value - extreme) <= tolerance;
        });
        if (std::abs(price - extreme) > tolerance
            || std::distance(values.begin(), first) != config.pivot_left_bars) continue;
        const std::string key = std::string(name(kind)) + ":" + iso_date(candidate.date_ordinal);
        if (std::any_of(state.pivots.begin(), state.pivots.end(), [&](const Pivot& pivot) {
            return pivot.pivot_key == key;
        })) {
            continue;
        }
        state.pivots.push_back(Pivot{
            key,
            kind,
            pivot_index,
            candidate.date_ordinal,
            confirmed_on,
            price,
            candidate.atr_14,
        });
    }
}

const Zone* match_zone(
    const std::map<std::string, Zone>& old_zones,
    PivotKind source_kind,
    double center,
    double half_width,
    const std::vector<std::string>& pivot_keys
) {
    std::vector<const Zone*> candidates;
    std::vector<const Zone*> exact;
    for (const auto& [unused, zone] : old_zones) {
        static_cast<void>(unused);
        if (zone.source_kind == source_kind && zone.lower <= center + half_width
            && zone.upper >= center - half_width) {
            candidates.push_back(&zone);
            if (zone.pivot_keys == pivot_keys) exact.push_back(&zone);
        }
    }
    std::vector<const Zone*>& selected = exact.empty() ? candidates : exact;
    if (selected.empty()) return nullptr;
    std::sort(selected.begin(), selected.end(), [center](const Zone* left, const Zone* right) {
        return std::pair(std::abs(left->center - center), left->zone_key)
            < std::pair(std::abs(right->center - center), right->zone_key);
    });
    return selected.front();
}

std::string zone_signature(const Zone& zone, ZoneStatus status) {
    return std::string(name(zone.role)) + "|" + std::string(name(status)) + "|"
        + json(strings(zone.pivot_keys)) + "|" + std::to_string(zone.anchor_session_index)
        + "|" + json(zone.anchor_center) + "|" + json(zone.slope_per_session)
        + "|" + json(zone.anchor_lower) + "|" + json(zone.anchor_upper);
}

void record_zone_version(
    SymbolState& state,
    const Zone& zone,
    std::int32_t effective_date,
    ZoneStatus status
) {
    const std::string signature = zone_signature(zone, status);
    const auto previous = state.version_signatures.find(zone.zone_key);
    if (previous != state.version_signatures.end() && previous->second == signature) return;
    state.version_signatures.insert_or_assign(zone.zone_key, signature);
    JsonObject version = zone_snapshot(zone);
    set(version, "status", std::string(name(status)));
    set(version, "effective_from", iso_date(effective_date));
    state.zone_versions.push_back(std::move(version));
}

void rebuild_zones(SymbolState& state, const Bar& bar, const Config& config) {
    const int current_index = static_cast<int>(state.history.size()) - 1;
    state.pivots.erase(
        std::remove_if(state.pivots.begin(), state.pivots.end(), [&](const Pivot& pivot) {
            return current_index - pivot.session_index >= config.detection_window;
        }),
        state.pivots.end()
    );
    std::map<std::string, Zone> old_zones;
    for (const auto& [key, zone] : state.zones) old_zones.emplace(key, project_zone(zone, current_index));
    std::map<std::string, Zone> selected;
    for (const PivotKind source_kind : {PivotKind::Low, PivotKind::High}) {
        std::vector<const Pivot*> pivots;
        for (const Pivot& pivot : state.pivots) {
            if (pivot.kind == source_kind) pivots.push_back(&pivot);
        }
        std::sort(pivots.begin(), pivots.end(), [](const Pivot* a, const Pivot* b) {
            return std::tie(a->session_index, a->pivot_key) < std::tie(b->session_index, b->pivot_key);
        });
        std::map<std::vector<std::string>, FitResult> fits;
        std::set<std::vector<std::string>> tried;
        auto add_fit = [&](const std::vector<const Pivot*>& members) {
            std::vector<std::string> keys;
            for (const Pivot* pivot : members) keys.push_back(pivot->pivot_key);
            if (!tried.insert(keys).second) return;
            auto fit = fit_pivot_line(members, current_index, config);
            if (!fit) return;
            keys.clear();
            for (const Pivot* pivot : fit->inliers) keys.push_back(pivot->pivot_key);
            fits.emplace(std::move(keys), std::move(*fit));
        };
        add_fit(pivots);
        for (std::size_t i = 0; i < pivots.size(); ++i) {
            for (std::size_t j = i + 1; j < pivots.size(); ++j) {
                const int span = pivots[j]->session_index - pivots[i]->session_index;
                if (span < config.min_line_span_sessions) continue;
                const double slope = (pivots[j]->price - pivots[i]->price) / span;
                std::vector<const Pivot*> members;
                for (const Pivot* pivot : pivots) {
                    const double residual = std::abs(pivot->price - pivots[i]->price
                        - slope * (pivot->session_index - pivots[i]->session_index));
                    if (residual <= config.line_inlier_tolerance_atr * pivot->atr) members.push_back(pivot);
                }
                add_fit(members);
            }
        }
        std::vector<std::pair<std::vector<std::string>, FitResult>> ordered(fits.begin(), fits.end());
        std::sort(ordered.begin(), ordered.end(), [&](const auto& a, const auto& b) {
            const auto quality = [&](const auto& item) {
                const auto& fit = item.second;
                return std::tuple(-static_cast<int>(fit.inliers.size()),
                    -fit.total_weight / fit.inliers.size(), fit.residual_atr,
                    std::abs(fit.center - bar.close), item.first);
            };
            return quality(a) < quality(b);
        });
        int count = 0;
        std::set<std::string> used_old;
        for (const auto& [member_keys, fit] : ordered) {
            if (count >= config.max_zones_per_kind) break;
            double half_width = config.zone_half_width_atr * bar.atr_14;
            for (const Pivot* pivot : fit.inliers) {
                half_width = std::max(half_width, std::abs(pivot->price - fit.center
                    - fit.slope * (pivot->session_index - current_index)));
            }
            if (!valid_zone_values(fit.center, fit.center - half_width, fit.center + half_width,
                    bar.atr_14, fit.slope)) continue;
            // Overlapping fits of the same boundary do not consume additional slots.
            if (std::any_of(selected.begin(), selected.end(), [&](const auto& item) {
                const Zone& zone = item.second;
                return zone.source_kind == source_kind && zone.lower <= fit.center + half_width
                    && zone.upper >= fit.center - half_width;
            })) continue;
            std::vector<std::string> keys = member_keys;
            std::sort(keys.begin(), keys.end());
            std::map<std::string, Zone> available;
            for (const auto& [key, old] : old_zones) {
                if (!used_old.contains(key)) available.emplace(key, old);
            }
            const Zone* matched = match_zone(available, source_kind, fit.center, half_width, keys);
            Zone zone;
            const bool unchanged = matched && matched->pivot_keys == keys
                && bar.atr_14 >= matched->atr * 0.5 && bar.atr_14 <= matched->atr * 2.0;
            if (unchanged) {
                zone = *matched;
            } else {
                std::vector<Pivot> cluster;
                for (const Pivot* pivot : fit.inliers) cluster.push_back(*pivot);
                zone.zone_key = matched ? matched->zone_key : new_zone_key(source_kind, cluster);
                if (!matched && std::any_of(state.zone_versions.begin(), state.zone_versions.end(),
                    [&](const JsonObject& version) {
                        const auto* key = get<std::string>(version, "zone_key");
                        const auto* day = get<std::string>(version, "effective_from");
                        return key && day && *key == zone.zone_key && *day == iso_date(bar.date_ordinal);
                    })) zone.zone_key = revived_zone_key(zone.zone_key, bar.date_ordinal);
                zone.source_kind = source_kind;
                zone.center = zone.anchor_center = stored_zone_price(fit.center);
                zone.lower = zone.anchor_lower = stored_zone_price(fit.center - half_width);
                zone.upper = zone.anchor_upper = stored_zone_price(fit.center + half_width);
                zone.atr = stored_zone_price(bar.atr_14);
                zone.anchor_session_index = current_index;
                zone.slope_per_session = stored_zone_price(fit.slope);
                zone.pivot_keys = keys;
                zone.pivot_count = static_cast<int>(fit.inliers.size());
                zone.touch_count = matched ? matched->touch_count : 0;
                zone.last_inside = matched ? matched->last_inside : false;
                zone.first_pivot_date_ordinal = fit.inliers.front()->trade_date_ordinal;
                zone.last_pivot_date_ordinal = fit.inliers.back()->trade_date_ordinal;
                zone.valid_from_ordinal = matched ? matched->valid_from_ordinal : bar.date_ordinal;
                // Residuals use the same persisted NUMERIC scale as zone prices.
                zone.fit_residual_atr = stored_zone_price(fit.residual_atr);
            }
            // A positive fit can round to zero at the persisted NUMERIC(24,10) scale.
            if (!valid_zone_values(zone.center, zone.lower, zone.upper, zone.atr,
                    zone.slope_per_session)) continue;
            if (!unchanged) zone.recency_weight = fit.total_weight / fit.inliers.size();
            zone.role = bar.close >= zone.center ? ZoneRole::Support : ZoneRole::Resistance;
            zone.status = ZoneStatus::Active;
            if (matched) used_old.insert(matched->zone_key);
            selected.emplace(zone.zone_key, std::move(zone));
            ++count;
        }
    }
    for (const auto& [key, old] : old_zones) {
        if (selected.contains(key)) continue;
        record_zone_version(state, old, bar.date_ordinal, ZoneStatus::Expired);
        state.events.push_back(object({
            {"event_date", iso_date(bar.date_ordinal)},
            {"event_type", "invalidation"},
            {"zone_key", key},
            {"role", std::string(name(old.role))},
        }));
    }
    state.zones = std::move(selected);
    for (const auto& [unused, zone] : state.zones) {
        static_cast<void>(unused);
        record_zone_version(state, zone, bar.date_ordinal, ZoneStatus::Active);
    }
}

}  // namespace

std::optional<LineFit> fit_pivots(
    const std::vector<Pivot>& pivots,
    int current_index,
    const Config& config
) {
    std::vector<const Pivot*> values;
    values.reserve(pivots.size());
    for (const Pivot& pivot : pivots) values.push_back(&pivot);
    const std::optional<FitResult> result = fit_pivot_line(values, current_index, config);
    if (!result) return std::nullopt;
    LineFit output;
    for (const Pivot* pivot : result->inliers) output.inliers.push_back(*pivot);
    output.center = result->center;
    output.slope = result->slope;
    output.residual_atr = result->residual_atr;
    output.total_weight = result->total_weight;
    return output;
}

RegimeEvidence classify_regime(
    const SymbolState& state,
    const std::vector<Zone>& zones,
    const Bar& bar,
    const Config& config
) {
    return classify_market_regime(state, zones, bar, config);
}

void record_regime(
    SymbolState& state,
    std::int32_t effective_from,
    const RegimeEvidence& evidence
) {
    record_regime_version(state, effective_from, evidence);
}

void rebuild(SymbolState& state, const Bar& bar, const Config& config) {
    rebuild_zones(state, bar, config);
}

std::optional<Zone> match_existing_zone(
    const std::vector<Zone>& old_zones,
    PivotKind source_kind,
    double center,
    double half_width,
    const std::vector<std::string>& pivot_keys
) {
    std::map<std::string, Zone> values;
    for (const Zone& zone : old_zones) values.emplace(zone.zone_key, zone);
    const Zone* result = match_zone(values, source_kind, center, half_width, pivot_keys);
    return result == nullptr ? std::nullopt : std::optional(*result);
}

void record_zone(
    SymbolState& state,
    const Zone& zone,
    std::int32_t effective_date,
    ZoneStatus status
) {
    record_zone_version(state, zone, effective_date, status);
}

std::optional<Decision> advance_symbol(
    SymbolState& state,
    const Bar& bar,
    const PositionView& position,
    const Config& config,
    bool emit_signals
) {
    if (!std::isfinite(bar.open) || !std::isfinite(bar.high) || !std::isfinite(bar.low)
        || !std::isfinite(bar.close) || !std::isfinite(bar.volume)
        || !std::isfinite(bar.volume_sma_20) || !std::isfinite(bar.atr_14)
        || bar.open <= 0.0 || bar.high <= 0.0 || bar.low <= 0.0 || bar.close <= 0.0
        || bar.atr_14 <= 0.0) {
        return std::nullopt;
    }
    const int session_index = static_cast<int>(state.history.size());
    const bool has_cached_zones = !state.cached_zone_timeline.empty();
    if (has_cached_zones) activate_cached_zones(state, bar.date_ordinal);

    std::vector<Zone> frozen_zones;
    std::map<std::string, Zone> retained;
    for (const auto& [key, zone] : state.zones) {
        if (zone.status != ZoneStatus::Active) continue;
        Zone projected = project_zone(zone, session_index);
        if (valid_zone_values(
                projected.center,
                projected.lower,
                projected.upper,
                projected.atr,
                projected.slope_per_session
            )) {
            frozen_zones.push_back(projected);
            retained.emplace(key, std::move(projected));
            continue;
        }
        state.breakouts.erase(key);
        if (!has_cached_zones) {
            Zone tombstone = zone;
            tombstone.status = ZoneStatus::Expired;
            tombstone.anchor_session_index = session_index;
            tombstone.anchor_center = zone.center;
            tombstone.anchor_lower = zone.lower;
            tombstone.anchor_upper = zone.upper;
            tombstone.slope_per_session = 0.0;
            record_zone_version(state, tombstone, bar.date_ordinal, ZoneStatus::Expired);
            state.events.push_back(object({
                {"event_date", iso_date(bar.date_ordinal)},
                {"event_type", "invalidation"},
                {"zone_key", key},
                {"role", std::string(name(zone.role))},
                {"reason", "projected_zone_geometry_became_invalid"},
            }));
        }
    }
    state.zones = std::move(retained);
    std::sort(frozen_zones.begin(), frozen_zones.end(), [](const Zone& left, const Zone& right) {
        return std::tuple(std::string(name(left.role)), left.center, left.zone_key)
            < std::tuple(std::string(name(right.role)), right.center, right.zone_key);
    });
    const EntryChannel entry_channel = build_entry_channel(
        frozen_zones, bar.close, bar.date_ordinal
    );
    record_entry_channel_transition(state, entry_channel, bar.date_ordinal);

    RegimeEvidence regime;
    const bool has_cached_regimes = !state.cached_regime_timeline.empty();
    if (has_cached_regimes) {
        regime = activate_cached_regime(state, bar.date_ordinal);
    } else {
        regime = classify_market_regime(state, frozen_zones, bar, config);
        record_regime_version(state, bar.date_ordinal, regime);
        // Both fresh and cached runs cite the same sparse interval-start evidence.
        // Classification itself still uses today's close and prior confirmed pivots.
        regime.payload = state.current_regime_evidence;
    }

    const std::optional<Decision> exit_decision = position.quantity > 0.0
        ? resolve_exit(position, bar, config, regime, state)
        : std::nullopt;
    std::vector<Candidate> candidates = detect_candidates(
        state, bar, frozen_zones, session_index, config
    );
    apply_regime_entry_policy(
        state, candidates, regime, entry_channel, bar.date_ordinal
    );
    if (exit_decision) {
        const auto* reason = get<std::string>(exit_decision->support_resistance, "exit_reason_code");
        const auto* key = get<std::string>(exit_decision->support_resistance, "zone_key");
        if (reason && key && *reason == "stop") state.stopped_zones[*key] = session_index;
    }
    for (Candidate& candidate : candidates) {
        const auto stopped = state.stopped_zones.find(candidate.zone_key);
        if (stopped != state.stopped_zones.end()
            && session_index >= stopped->second
            && session_index - stopped->second <= config.stop_cooldown_sessions) {
            candidate.entry_eligible = false;
            candidate.rejection_reason = "zone_stop_cooldown";
        }
        if (config.market_filter_enabled && (!bar.market_close || !bar.market_sma_200
                || *bar.market_close < *bar.market_sma_200)) {
            candidate.entry_eligible = false;
            candidate.rejection_reason = bar.market_close && bar.market_sma_200
                ? "market_below_sma_200" : "missing_market_filter_data";
        }
        set(candidate.regime_evidence, "market_close", nullable(bar.market_close));
        set(candidate.regime_evidence, "market_sma_200", nullable(bar.market_sma_200));
    }
    const Candidate* selected = select_candidate(candidates);

    resolve_prior_outcomes(state, bar, session_index, config, regime);
    for (const Candidate& candidate : candidates) {
        JsonObject event = object({
            {"event_date", iso_date(bar.date_ordinal)},
            {"event_type", "candidate"},
        });
        for (const auto& item : candidate_json(candidate)) {
            set(event, item.first, item.second);
        }
        state.events.push_back(std::move(event));
        if (candidate.entry_eligible && candidate.strength.passes_threshold
            && std::none_of(state.pending_outcomes.begin(), state.pending_outcomes.end(),
                [&](const PendingOutcome& outcome) { return outcome.setup == candidate.setup; })) {
            const auto channel = project_entry_channel(candidate.entry_channel);
            PendingOutcome outcome{candidate.setup, candidate.zone_key, bar.date_ordinal,
                session_index, candidate.target_price, candidate.stop_price};
            outcome.channel_lower = channel.lower;
            outcome.channel_upper = channel.upper;
            outcome.frozen = object({
                {"zone_key", candidate.zone_key}, {"zone", zone_snapshot(candidate.zone)},
                {"signal_date", iso_date(bar.date_ordinal)}, {"entry_atr", bar.atr_14},
                {"entry_close", bar.close}, {"stop_price", candidate.stop_price},
                {"target_price", candidate.target_price},
            });
            state.pending_outcomes.push_back(std::move(outcome));
        }
    }
    JsonArray candidate_setups;
    for (const Candidate& candidate : candidates) {
        candidate_setups.emplace_back(std::string(name(candidate.setup)));
    }
    if (selected != nullptr) {
        state.events.push_back(object({
            {"event_date", iso_date(bar.date_ordinal)},
            {"event_type", "selection"},
            {"zone_key", selected->zone_key},
            {"setup", std::string(name(selected->setup))},
            {"score", selected->score},
            {"score_evidence", selected->score_evidence},
            {"zone", zone_snapshot(selected->zone)},
            {"candidate_setups", candidate_setups},
            {"regime", std::string(name(regime.regime))},
            {"regime_evidence", regime.payload},
            {"entry_channel", entry_channel_json(entry_channel)},
        }));
    }

    apply_current_bar_zone_state(state, bar, session_index, config);
    if (has_cached_zones) record_cached_lifecycle_events(state, bar.date_ordinal);
    state.history.push_back(bar);
    if (!has_cached_zones) {
        confirm_pivots(state, config);
        rebuild_zones(state, bar, config);
    }

    if (!emit_signals) return std::nullopt;
    if (exit_decision) return exit_decision;
    if (position.quantity > 0.0 || selected == nullptr || !selected->entry_eligible) {
        return std::nullopt;
    }
    JsonArray raw_candidates;
    for (const Candidate& candidate : candidates) raw_candidates.emplace_back(candidate_json(candidate));
    JsonObject metadata = object({
        {"zone_key", selected->zone_key},
        {"selected_setup", std::string(name(selected->setup))},
        {"candidate_setups", candidate_setups},
        {"zone", zone_snapshot(selected->zone)},
        {"signal_date", iso_date(bar.date_ordinal)},
        {"entry_atr", bar.atr_14},
        {"entry_close", bar.close},
        {"stop_price", selected->stop_price},
        {"target_price", selected->target_price},
        {"reward_risk", selected->reward_risk},
        {"strength", strength_json(selected->strength)},
        {"score_evidence", selected->score_evidence},
        {"candidates", std::move(raw_candidates)},
        {"regime", std::string(name(regime.regime))},
        {"regime_evidence", regime.payload},
        {"entry_channel", entry_channel_json(entry_channel)},
        {"price_semantics", "forward_adjusted_preferred_unadjusted_fallback"},
    });
    Decision decision{
        Action::Buy,
        selected->reason,
        selected->score,
        std::move(metadata),
        selected->strength,
        entry_channel,
        selected->setup,
    };
    return decision;
}

EntrySizing size_entry(const JsonObject& frozen, double price, double equity, double cash,
    double position_cap, const Config& config, double commission_bps,
    double commission_min, double slippage_bps) {
    EntrySizing result;
    const auto stop = number(frozen, "stop_price");
    const auto target = number(frozen, "target_price");
    if (!stop || !target) throw std::invalid_argument("support entry requires stop_price and target_price");
    result.stop = entry_stop(frozen);
    result.maximum_entry_price = (*target + config.min_reward_risk * result.stop)
        / (1.0 + config.min_reward_risk);
    if (!std::isfinite(price) || price <= result.stop || equity <= 0.0 || cash <= 0.0) {
        result.reason_code = "invalid_execution_risk";
        return result;
    }
    const double bps = commission_bps / 10'000.0;
    const double exit_stop = result.stop * (1.0 - slippage_bps / 10'000.0);
    const double exit_target = *target * (1.0 - slippage_bps / 10'000.0);
    const double loss = price - exit_stop;
    const double budget = equity * config.risk_per_trade_pct;
    // Each min-commission combination is a linear upper bound on quantity.
    const double quantity = std::max(0.0, std::min({
        equity * position_cap / price, cash / (price * (1.0 + bps)),
        (cash - commission_min) / price,
        budget / (loss + bps * (price + exit_stop)),
        (budget - commission_min) / (loss + bps * price),
        (budget - commission_min) / (loss + bps * exit_stop),
        (budget - 2.0 * commission_min) / loss,
    }));
    auto fee = [&](double value) { return std::max(quantity * value * bps, commission_min); };
    result.planned_loss = quantity * loss + fee(price) + fee(exit_stop);
    result.reward_risk = result.planned_loss > 0.0
        ? (quantity * (exit_target - price) - fee(price) - fee(exit_target)) / result.planned_loss : 0.0;
    result.reason_code = quantity <= 0.0 ? "insufficient_risk_budget"
        : (result.reward_risk < config.min_reward_risk ? "net_reward_risk_below_minimum" : "risk_sized_entry");
    if (quantity > 0.0 && result.reward_risk >= config.min_reward_risk) result.quantity = quantity;
    return result;
}

void record_execution_rejection(
    SymbolState& state,
    const EntryChannel& entry_channel,
    Setup setup,
    std::int32_t signal_date_ordinal,
    std::int32_t execution_date_ordinal,
    double reference_open,
    double simulated_execution_price,
    const std::string& reason_code
) {
    const EntryChannel projected = project_entry_channel(entry_channel, 1);
    state.events.push_back(object({
        {"event_date", iso_date(execution_date_ordinal)},
        {"event_type", "execution_rejection"},
        {"zone_key", projected.support_zone_key.empty()
            ? JsonValue(nullptr) : JsonValue(projected.support_zone_key)},
        {"setup", std::string(name(setup))},
        {"reason_code", reason_code},
        {"signal_date", iso_date(signal_date_ordinal)},
        {"execution_date", iso_date(execution_date_ordinal)},
        {"reference_open", reference_open},
        {"simulated_execution_price", simulated_execution_price},
        {"lower", projected.support_zone ? JsonValue(projected.lower) : JsonValue(nullptr)},
        {"upper", projected.resistance_zone ? JsonValue(projected.upper) : JsonValue(nullptr)},
        {"entry_channel", entry_channel_json(projected)},
    }));
}

}  // namespace quant_kernel::support_resistance
