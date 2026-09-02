#include "support_resistance_kernel.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

constexpr int kDetectorImplementationRevision = 10;
constexpr int kRegimeLogicRevision = 2;
constexpr int kZonePriceScale = 10;
constexpr const char* kEntryChannelSemantics = "support_upper_to_resistance_lower_v1";

constexpr std::array<std::uint32_t, 64> kSha256RoundConstants = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U,
    0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U,
    0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
    0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
    0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU,
    0x5b9cca4fU, 0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
};

std::uint32_t rotate_right(std::uint32_t value, std::uint32_t count) {
    return (value >> count) | (value << (32U - count));
}

std::string sha256_hex(std::string_view input) {
    std::vector<std::uint8_t> bytes(input.begin(), input.end());
    const std::uint64_t bit_length = static_cast<std::uint64_t>(bytes.size()) * 8U;
    bytes.push_back(0x80U);
    while (bytes.size() % 64U != 56U) bytes.push_back(0U);
    for (int shift = 56; shift >= 0; shift -= 8) {
        bytes.push_back(static_cast<std::uint8_t>((bit_length >> shift) & 0xffU));
    }

    std::array<std::uint32_t, 8> hash = {
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
    };
    for (std::size_t offset = 0; offset < bytes.size(); offset += 64U) {
        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16U; ++index) {
            const std::size_t start = offset + index * 4U;
            words[index] = (static_cast<std::uint32_t>(bytes[start]) << 24U)
                | (static_cast<std::uint32_t>(bytes[start + 1U]) << 16U)
                | (static_cast<std::uint32_t>(bytes[start + 2U]) << 8U)
                | static_cast<std::uint32_t>(bytes[start + 3U]);
        }
        for (std::size_t index = 16U; index < words.size(); ++index) {
            const std::uint32_t s0 = rotate_right(words[index - 15U], 7U)
                ^ rotate_right(words[index - 15U], 18U) ^ (words[index - 15U] >> 3U);
            const std::uint32_t s1 = rotate_right(words[index - 2U], 17U)
                ^ rotate_right(words[index - 2U], 19U) ^ (words[index - 2U] >> 10U);
            words[index] = words[index - 16U] + s0 + words[index - 7U] + s1;
        }

        std::uint32_t a = hash[0];
        std::uint32_t b = hash[1];
        std::uint32_t c = hash[2];
        std::uint32_t d = hash[3];
        std::uint32_t e = hash[4];
        std::uint32_t f = hash[5];
        std::uint32_t g = hash[6];
        std::uint32_t h = hash[7];
        for (std::size_t index = 0; index < words.size(); ++index) {
            const std::uint32_t sum1 = rotate_right(e, 6U) ^ rotate_right(e, 11U) ^ rotate_right(e, 25U);
            const std::uint32_t choice = (e & f) ^ ((~e) & g);
            const std::uint32_t temporary1 = h + sum1 + choice + kSha256RoundConstants[index] + words[index];
            const std::uint32_t sum0 = rotate_right(a, 2U) ^ rotate_right(a, 13U) ^ rotate_right(a, 22U);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temporary2 = sum0 + majority;
            h = g;
            g = f;
            f = e;
            e = d + temporary1;
            d = c;
            c = b;
            b = a;
            a = temporary1 + temporary2;
        }
        hash[0] += a;
        hash[1] += b;
        hash[2] += c;
        hash[3] += d;
        hash[4] += e;
        hash[5] += f;
        hash[6] += g;
        hash[7] += h;
    }

    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const std::uint32_t value : hash) output << std::setw(8) << value;
    return output.str();
}

std::string iso_date(const py::handle& value) {
    if (py::isinstance<py::str>(value)) {
        const std::string text = py::cast<std::string>(value);
        return text.size() >= 10U ? text.substr(0U, 10U) : text;
    }
    if (!py::hasattr(value, "isoformat")) {
        throw std::invalid_argument("expected an ISO date value");
    }
    const std::string text = py::cast<std::string>(value.attr("isoformat")());
    return text.size() >= 10U ? text.substr(0U, 10U) : text;
}

double required_number(const py::dict& value, const char* key) {
    if (!value.contains(key) || value[key].is_none()) {
        throw std::invalid_argument(std::string("missing numeric field: ") + key);
    }
    return py::cast<double>(value[key]);
}

double optional_number(const py::dict& value, const char* key, double fallback) {
    return value.contains(key) && !value[key].is_none() ? py::cast<double>(value[key]) : fallback;
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
        if (value < '0' || value > '9') throw std::invalid_argument("zone price must be finite");
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
        scaled_integer.insert(0U, static_cast<std::size_t>(kZonePriceScale) + 1U - scaled_integer.size(), '0');
    }
    const std::size_t point = scaled_integer.size() - static_cast<std::size_t>(kZonePriceScale);
    scaled_integer.insert(point, 1U, '.');
    if (negative) scaled_integer.insert(0U, 1U, '-');
    return scaled_integer;
}

double stored_zone_price(double value) {
    if (!std::isfinite(value)) throw std::invalid_argument("zone price must be finite");
    const std::string python_float = py::cast<std::string>(py::str(py::float_(value)));
    return std::stod(quantized_decimal_string(python_float));
}

bool valid_zone_values(double center, double lower, double upper, double atr, double slope) {
    return std::isfinite(center) && std::isfinite(lower) && std::isfinite(upper)
        && std::isfinite(atr) && std::isfinite(slope) && atr > 0.0
        && lower > 0.0 && lower <= center && center <= upper;
}

bool valid_zone_geometry(const py::dict& zone) {
    return valid_zone_values(
        required_number(zone, "center"),
        required_number(zone, "lower"),
        required_number(zone, "upper"),
        required_number(zone, "atr"),
        optional_number(zone, "slope_per_session", 0.0)
    );
}

py::dict copy_dict(const py::dict& source) {
    py::dict result;
    for (const auto& [key, value] : source) result[key] = value;
    return result;
}

py::dict zone_snapshot(const py::dict& zone) {
    py::dict result = copy_dict(zone);
    for (const char* key : {"first_pivot_date", "last_pivot_date", "valid_from"}) {
        if (result.contains(key) && !result[key].is_none()) result[key] = iso_date(result[key]);
    }
    if (result.contains("timeline_effective_from")) result.attr("pop")("timeline_effective_from");
    if (result.contains("pivot_keys") && !result["pivot_keys"].is_none()) {
        result["pivot_keys"] = py::list(result["pivot_keys"]);
    }
    return result;
}

py::dict project_zone(const py::dict& zone, int session_index) {
    py::dict projected = copy_dict(zone);
    const int anchor_index = zone.contains("anchor_session_index")
        ? py::cast<int>(zone["anchor_session_index"])
        : 0;
    const double center = optional_number(zone, "anchor_center", required_number(zone, "center"));
    const double lower = optional_number(zone, "anchor_lower", required_number(zone, "lower"));
    const double upper = optional_number(zone, "anchor_upper", required_number(zone, "upper"));
    const double slope = optional_number(zone, "slope_per_session", 0.0);
    const double delta = slope * static_cast<double>(session_index - anchor_index);
    projected["center"] = stored_zone_price(center + delta);
    projected["lower"] = stored_zone_price(lower + delta);
    projected["upper"] = stored_zone_price(upper + delta);
    return projected;
}

py::dict freeze_zones_for_session(const py::iterable& zones, int session_index, const py::handle& trade_date) {
    py::list active;
    py::list expired;
    py::list events;
    const std::string effective_date = iso_date(trade_date);
    std::vector<py::dict> retained;
    for (const py::handle raw_zone : zones) {
        const py::dict zone = py::cast<py::dict>(raw_zone);
        if (!zone.contains("status") || py::cast<std::string>(zone["status"]) != "active") continue;
        py::dict projected = project_zone(zone, session_index);
        if (valid_zone_geometry(projected)) {
            retained.push_back(std::move(projected));
            continue;
        }
        py::dict tombstone = copy_dict(zone);
        tombstone["status"] = "expired";
        tombstone["anchor_session_index"] = session_index;
        tombstone["anchor_center"] = required_number(zone, "center");
        tombstone["anchor_lower"] = required_number(zone, "lower");
        tombstone["anchor_upper"] = required_number(zone, "upper");
        tombstone["slope_per_session"] = 0.0;
        tombstone = zone_snapshot(tombstone);
        tombstone["effective_from"] = effective_date;
        expired.append(std::move(tombstone));

        py::dict event;
        event["event_date"] = effective_date;
        event["event_type"] = "invalidation";
        event["zone_key"] = zone["zone_key"];
        event["role"] = zone["role"];
        event["reason"] = "projected_zone_geometry_became_invalid";
        events.append(std::move(event));
    }
    std::sort(retained.begin(), retained.end(), [](const py::dict& left, const py::dict& right) {
        return std::tuple(
            py::cast<std::string>(left["role"]),
            py::cast<double>(left["center"]),
            py::cast<std::string>(left["zone_key"])
        ) < std::tuple(
            py::cast<std::string>(right["role"]),
            py::cast<double>(right["center"]),
            py::cast<std::string>(right["zone_key"])
        );
    });
    for (py::dict& zone : retained) active.append(std::move(zone));

    py::dict result;
    result["active_zones"] = std::move(active);
    result["expired_zone_versions"] = std::move(expired);
    result["events"] = std::move(events);
    return result;
}

std::string new_zone_key(const std::string& source_kind, const py::iterable& pivots) {
    std::vector<std::string> keys;
    for (const py::handle raw_pivot : pivots) {
        const py::dict pivot = py::cast<py::dict>(raw_pivot);
        keys.push_back(py::cast<std::string>(pivot["pivot_key"]));
    }
    std::sort(keys.begin(), keys.end());
    std::ostringstream membership;
    for (std::size_t index = 0; index < keys.size(); ++index) {
        if (index > 0U) membership << '|';
        membership << keys[index];
    }
    return "srz_" + sha256_hex(source_kind + "|" + membership.str()).substr(0U, 20U);
}

std::string revived_zone_key(const std::string& zone_key, const py::handle& effective_date) {
    return "srz_" + sha256_hex(zone_key + "|revived|" + iso_date(effective_date)).substr(0U, 20U);
}

double weighted_median(std::vector<std::pair<double, double>> values) {
    std::stable_sort(values.begin(), values.end(), [](const auto& left, const auto& right) {
        return left.first < right.first;
    });
    double total = 0.0;
    for (const auto& [value, weight] : values) {
        static_cast<void>(value);
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

struct PivotValue {
    py::dict raw;
    std::string key;
    int session_index;
    double price;
    double atr;
    double weight;
};

std::optional<std::pair<double, double>> fit_line(
    const std::vector<PivotValue>& pivots,
    int minimum_span
) {
    std::vector<std::pair<double, double>> slopes;
    for (std::size_t left_index = 0; left_index < pivots.size(); ++left_index) {
        const PivotValue& left = pivots[left_index];
        for (std::size_t right_index = left_index + 1U; right_index < pivots.size(); ++right_index) {
            const PivotValue& right = pivots[right_index];
            const int span = right.session_index - left.session_index;
            if (span < minimum_span) continue;
            slopes.emplace_back(
                (right.price - left.price) / static_cast<double>(span),
                std::sqrt(left.weight * right.weight)
            );
        }
    }
    if (slopes.empty()) return std::nullopt;
    const double slope = weighted_median(std::move(slopes));
    std::vector<std::pair<double, double>> intercepts;
    intercepts.reserve(pivots.size());
    for (const PivotValue& pivot : pivots) {
        intercepts.emplace_back(pivot.price - slope * pivot.session_index, pivot.weight);
    }
    return std::pair(slope, weighted_median(std::move(intercepts)));
}

py::object fit_pivot_line(
    const py::iterable& raw_pivots,
    int current_index,
    const py::dict& signal_cfg
) {
    std::vector<PivotValue> pivots;
    for (const py::handle raw_pivot : raw_pivots) {
        py::dict pivot = py::cast<py::dict>(raw_pivot);
        pivots.push_back(PivotValue{
            .raw = pivot,
            .key = py::cast<std::string>(pivot["pivot_key"]),
            .session_index = py::cast<int>(pivot["session_index"]),
            .price = py::cast<double>(pivot["price"]),
            .atr = py::cast<double>(pivot["atr"]),
            .weight = 0.0,
        });
    }
    const int minimum = py::cast<int>(signal_cfg["min_line_pivots"]);
    const int minimum_span = py::cast<int>(signal_cfg["min_line_span_sessions"]);
    if (pivots.size() < static_cast<std::size_t>(minimum)
        || pivots.back().session_index - pivots.front().session_index < minimum_span) {
        return py::none();
    }
    const int half_life = py::cast<int>(signal_cfg["decay_half_life"]);
    for (PivotValue& pivot : pivots) {
        pivot.weight = std::pow(
            0.5,
            static_cast<double>(std::max(current_index - pivot.session_index, 0))
                / static_cast<double>(half_life)
        );
    }
    const auto initial = fit_line(pivots, minimum_span);
    if (!initial) return py::none();
    const auto [initial_slope, initial_intercept] = *initial;
    const double tolerance = py::cast<double>(signal_cfg["line_inlier_tolerance_atr"]);
    std::vector<PivotValue> inliers;
    for (const PivotValue& pivot : pivots) {
        if (std::abs(pivot.price - (initial_intercept + initial_slope * pivot.session_index))
            <= tolerance * pivot.atr) {
            inliers.push_back(pivot);
        }
    }
    if (inliers.size() < static_cast<std::size_t>(minimum)
        || inliers.back().session_index - inliers.front().session_index < minimum_span) {
        return py::none();
    }
    const auto refined = fit_line(inliers, minimum_span);
    if (!refined) return py::none();
    const auto [slope, intercept] = *refined;
    std::vector<std::pair<double, double>> atrs;
    for (const PivotValue& pivot : inliers) atrs.emplace_back(pivot.atr, pivot.weight);
    const double representative_atr = weighted_median(std::move(atrs));
    if (representative_atr <= 0.0
        || std::abs(slope) / representative_atr
            > py::cast<double>(signal_cfg["max_abs_slope_atr_per_session"])) {
        return py::none();
    }
    double total_weight = 0.0;
    double weighted_residual = 0.0;
    py::list inlier_keys;
    for (const PivotValue& pivot : inliers) {
        total_weight += pivot.weight;
        weighted_residual += pivot.weight
            * std::abs(pivot.price - (intercept + slope * pivot.session_index))
            / std::max(pivot.atr, 1e-12);
        inlier_keys.append(pivot.key);
    }
    py::dict result;
    result["inlier_pivot_keys"] = std::move(inlier_keys);
    result["center"] = intercept + slope * current_index;
    result["slope"] = slope;
    result["residual_atr"] = weighted_residual / total_weight;
    result["total_weight"] = total_weight;
    return std::move(result);
}

struct ChannelCandidate {
    py::dict zone;
    double distance;
    int pivot_count;
    std::string last_pivot_date;
    double fit_residual_atr;
    std::string zone_key;
};

bool channel_candidate_less(const ChannelCandidate& left, const ChannelCandidate& right) {
    if (left.distance != right.distance) return left.distance < right.distance;
    if (left.pivot_count != right.pivot_count) return left.pivot_count > right.pivot_count;
    if (left.last_pivot_date != right.last_pivot_date) return left.last_pivot_date > right.last_pivot_date;
    if (left.fit_residual_atr != right.fit_residual_atr) {
        return left.fit_residual_atr < right.fit_residual_atr;
    }
    return left.zone_key < right.zone_key;
}

py::dict build_entry_channel(const py::iterable& zones, double close, const py::handle& trade_date) {
    std::vector<ChannelCandidate> supports;
    std::vector<ChannelCandidate> resistances;
    for (const py::handle raw_zone : zones) {
        py::dict zone = py::cast<py::dict>(raw_zone);
        const std::string role = py::cast<std::string>(zone["role"]);
        const std::string status = py::cast<std::string>(zone["status"]);
        if (status != "active" || !valid_zone_geometry(zone)) continue;
        const double inner_edge = role == "support"
            ? required_number(zone, "upper")
            : required_number(zone, "lower");
        const bool eligible = role == "support" ? inner_edge <= close : inner_edge >= close;
        if (!eligible || (role != "support" && role != "resistance")) continue;
        ChannelCandidate candidate{
            .zone = zone,
            .distance = role == "support" ? close - inner_edge : inner_edge - close,
            .pivot_count = py::cast<int>(zone["pivot_count"]),
            .last_pivot_date = iso_date(zone["last_pivot_date"]),
            .fit_residual_atr = optional_number(zone, "fit_residual_atr", 0.0),
            .zone_key = py::cast<std::string>(zone["zone_key"]),
        };
        (role == "support" ? supports : resistances).push_back(std::move(candidate));
    }
    std::sort(supports.begin(), supports.end(), channel_candidate_less);
    std::sort(resistances.begin(), resistances.end(), channel_candidate_less);
    const ChannelCandidate* support = supports.empty() ? nullptr : &supports.front();
    const ChannelCandidate* resistance = resistances.empty() ? nullptr : &resistances.front();

    py::dict payload;
    payload["semantics"] = kEntryChannelSemantics;
    payload["signal_trade_date"] = iso_date(trade_date);
    payload["signal_close"] = close;
    payload["valid"] = false;
    payload["reason_code"] = py::none();
    payload["support_zone_key"] = support ? py::cast(support->zone_key) : py::none();
    payload["resistance_zone_key"] = resistance ? py::cast(resistance->zone_key) : py::none();
    payload["lower"] = support ? py::cast(required_number(support->zone, "upper")) : py::none();
    payload["upper"] = resistance ? py::cast(required_number(resistance->zone, "lower")) : py::none();
    payload["lower_slope_per_session"] = support
        ? py::cast(optional_number(support->zone, "slope_per_session", 0.0))
        : py::none();
    payload["upper_slope_per_session"] = resistance
        ? py::cast(optional_number(resistance->zone, "slope_per_session", 0.0))
        : py::none();
    payload["support_zone"] = support
        ? py::object(zone_snapshot(support->zone))
        : py::object(py::none());
    payload["resistance_zone"] = resistance
        ? py::object(zone_snapshot(resistance->zone))
        : py::object(py::none());
    if (!support || !resistance) {
        payload["reason_code"] = "missing_support_or_resistance";
        return payload;
    }
    const double lower = required_number(support->zone, "upper");
    const double upper = required_number(resistance->zone, "lower");
    if (!(lower < upper)) {
        payload["reason_code"] = "unordered_or_overlapping_inner_edges";
        return payload;
    }
    if (!(lower <= close && close <= upper)) {
        payload["reason_code"] = "signal_close_outside_inner_edges";
        return payload;
    }
    payload["valid"] = true;
    payload["reason_code"] = "valid_inner_edge_channel";
    return payload;
}

py::dict project_entry_channel(const py::object& channel, int sessions) {
    py::dict payload;
    if (!channel.is_none()) payload = copy_dict(py::cast<py::dict>(channel));
    const bool valid = payload.contains("valid") && py::cast<bool>(payload["valid"]);
    if (!valid) {
        payload["valid"] = false;
        payload["reason_code"] = payload.contains("reason_code") && !payload["reason_code"].is_none()
            ? py::str(payload["reason_code"])
            : py::str("missing_valid_entry_channel");
        return payload;
    }
    double lower = 0.0;
    double upper = 0.0;
    try {
        lower = required_number(payload, "lower")
            + required_number(payload, "lower_slope_per_session") * sessions;
        upper = required_number(payload, "upper")
            + required_number(payload, "upper_slope_per_session") * sessions;
    } catch (const std::exception&) {
        payload["valid"] = false;
        payload["reason_code"] = "missing_channel_projection_values";
        return payload;
    }
    if (!std::isfinite(lower) || !std::isfinite(upper) || lower <= 0.0 || upper <= 0.0) {
        payload["valid"] = false;
        payload["reason_code"] = "invalid_channel_projection_values";
        return payload;
    }
    if (lower >= upper) {
        payload["lower"] = lower;
        payload["upper"] = upper;
        payload["valid"] = false;
        payload["reason_code"] = "projected_inner_edges_crossed";
        return payload;
    }
    payload["lower"] = lower;
    payload["upper"] = upper;
    payload["projected_sessions"] = sessions;
    payload["valid"] = valid;
    payload["reason_code"] = "valid_projected_inner_edge_channel";
    return payload;
}

py::tuple entry_price_is_inside_channel(const py::object& channel, double price) {
    if (channel.is_none()) return py::make_tuple(false, "missing_valid_entry_channel");
    const py::dict payload = py::cast<py::dict>(channel);
    if (!payload.contains("valid") || !py::cast<bool>(payload["valid"])) {
        const std::string reason = payload.contains("reason_code") && !payload["reason_code"].is_none()
            ? py::cast<std::string>(py::str(payload["reason_code"]))
            : "missing_valid_entry_channel";
        return py::make_tuple(false, reason);
    }
    double lower = 0.0;
    double upper = 0.0;
    try {
        lower = required_number(payload, "lower");
        upper = required_number(payload, "upper");
    } catch (const std::exception&) {
        return py::make_tuple(false, "invalid_entry_channel_values");
    }
    if (!std::isfinite(lower) || !std::isfinite(upper) || !std::isfinite(price)
        || lower <= 0.0 || upper <= 0.0 || price <= 0.0) {
        return py::make_tuple(false, "non_finite_entry_channel_values");
    }
    if (lower >= upper) return py::make_tuple(false, "unordered_entry_channel");
    if (price < lower || price > upper) return py::make_tuple(false, "entry_price_outside_valid_channel");
    return py::make_tuple(true, "entry_price_inside_valid_channel");
}

py::dict normalized_detector_params(const py::dict& params) {
    py::dict signal;
    if (params.contains("signal") && !params["signal"].is_none()) {
        signal = py::cast<py::dict>(params["signal"]);
    }
    py::dict result;
    result["implementation_revision"] = kDetectorImplementationRevision;
    result["regime_logic_revision"] = kRegimeLogicRevision;
    for (const char* key : {
        "pivot_left_bars",
        "pivot_right_bars",
        "detection_window",
        "min_line_pivots",
        "min_line_span_sessions",
        "line_inlier_tolerance_atr",
        "max_abs_slope_atr_per_session",
        "zone_half_width_atr",
        "decay_half_life",
        "breakout_confirmation_atr",
        "breakout_volume_ratio_min",
        "retest_window",
        "retest_volume_ratio_max",
    }) {
        if (!signal.contains(key)) throw py::key_error(key);
        result[key] = signal[key];
    }
    return result;
}

}  // namespace

void bind_support_resistance(py::module_& module) {
    py::module_ support_resistance = module.def_submodule(
        "support_resistance",
        "Native causal support/resistance detector primitives."
    );
    support_resistance.attr("DETECTOR_IMPLEMENTATION_REVISION") = kDetectorImplementationRevision;
    support_resistance.attr("REGIME_LOGIC_REVISION") = kRegimeLogicRevision;
    support_resistance.attr("ENTRY_CHANNEL_SEMANTICS") = kEntryChannelSemantics;
    support_resistance.def("normalized_detector_params", &normalized_detector_params, py::arg("params"));
    support_resistance.def("stored_zone_price", &stored_zone_price, py::arg("value"));
    support_resistance.def(
        "valid_zone_values",
        &valid_zone_values,
        py::arg("center"),
        py::arg("lower"),
        py::arg("upper"),
        py::arg("atr"),
        py::arg("slope")
    );
    support_resistance.def("project_zone", &project_zone, py::arg("zone"), py::arg("session_index"));
    support_resistance.def(
        "freeze_zones_for_session",
        &freeze_zones_for_session,
        py::arg("zones"),
        py::arg("session_index"),
        py::arg("trade_date")
    );
    support_resistance.def("new_zone_key", &new_zone_key, py::arg("source_kind"), py::arg("pivots"));
    support_resistance.def(
        "revived_zone_key",
        &revived_zone_key,
        py::arg("zone_key"),
        py::arg("effective_date")
    );
    support_resistance.def(
        "fit_pivot_line",
        &fit_pivot_line,
        py::arg("pivots"),
        py::arg("current_index"),
        py::arg("signal_cfg")
    );
    support_resistance.def(
        "build_entry_channel",
        &build_entry_channel,
        py::arg("zones"),
        py::arg("close"),
        py::arg("trade_date")
    );
    support_resistance.def(
        "project_entry_channel",
        &project_entry_channel,
        py::arg("channel"),
        py::arg("sessions") = 1
    );
    support_resistance.def(
        "entry_price_is_inside_channel",
        &entry_price_is_inside_channel,
        py::arg("channel"),
        py::arg("price")
    );
}

}  // namespace quant_kernel
