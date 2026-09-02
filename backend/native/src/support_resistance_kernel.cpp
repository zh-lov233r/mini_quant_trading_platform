#include "support_resistance_kernel.hpp"

#include <pybind11/stl.h>

#include <algorithm>
#include <array>
#include <cctype>
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

py::object service_type(const char* name) {
    return py::module_::import("src.services.support_resistance_service").attr(name);
}

py::object copy_object(const py::handle& value) {
    return py::module_::import("copy").attr("copy")(value);
}

py::object dict_value(const py::dict& value, const char* key, py::object fallback = py::none()) {
    if (!value.contains(key)) return fallback;
    return py::reinterpret_borrow<py::object>(value[key]);
}

bool truthy(const py::handle& value) {
    return PyObject_IsTrue(value.ptr()) == 1;
}

std::optional<double> finite_number(const py::handle& value) {
    if (value.is_none()) return std::nullopt;
    try {
        const double result = py::cast<double>(py::module_::import("builtins").attr("float")(value));
        return std::isfinite(result) ? std::optional(result) : std::nullopt;
    } catch (const py::error_already_set&) {
        return std::nullopt;
    }
}

py::object resolved_bar_date(const py::dict& snapshot) {
    const py::module_ datetime = py::module_::import("datetime");
    const py::object date_type = datetime.attr("date");
    const py::object datetime_type = datetime.attr("datetime");
    py::object value = dict_value(snapshot, "dt_ny");
    if (py::isinstance(value, datetime_type)) return value.attr("date")();
    if (py::isinstance(value, date_type)) return value;
    if (py::isinstance<py::str>(value)) {
        try {
            const std::string text = py::cast<std::string>(value);
            return date_type.attr("fromisoformat")(text.substr(0U, 10U));
        } catch (const py::error_already_set&) {
            return py::none();
        }
    }
    py::object timestamp = dict_value(snapshot, "ts");
    return py::isinstance(timestamp, datetime_type) ? timestamp.attr("date")() : py::none();
}

py::object normalize_bar(const py::dict& snapshot) {
    py::object trade_date = resolved_bar_date(snapshot);
    if (trade_date.is_none()) return py::none();
    std::array<std::optional<double>, 4> values = {
        finite_number(dict_value(snapshot, "open")),
        finite_number(dict_value(snapshot, "high")),
        finite_number(dict_value(snapshot, "low")),
        finite_number(dict_value(snapshot, "close")),
    };
    if (std::any_of(values.begin(), values.end(), [](const auto& value) { return !value; })) {
        return py::none();
    }
    double atr = finite_number(dict_value(snapshot, "atr_14")).value_or(0.0);
    if (atr <= 0.0) atr = std::max(*values[1] - *values[2], *values[3] * 0.005);
    py::dict bar;
    bar["dt_ny"] = trade_date;
    bar["ts"] = dict_value(snapshot, "ts");
    bar["open"] = *values[0];
    bar["high"] = *values[1];
    bar["low"] = *values[2];
    bar["close"] = *values[3];
    bar["volume"] = finite_number(dict_value(snapshot, "volume")).value_or(0.0);
    bar["volume_sma_20"] = finite_number(dict_value(snapshot, "volume_sma_20")).value_or(0.0);
    bar["atr_14"] = atr;
    return std::move(bar);
}

py::dict zone_object_snapshot(const py::handle& zone) {
    return py::cast<py::dict>(zone.attr("snapshot")());
}

py::object project_zone_object(const py::handle& zone, int session_index) {
    py::object projected = copy_object(zone);
    const int anchor_index = py::cast<int>(zone.attr("anchor_session_index"));
    const double center = zone.attr("anchor_center").is_none()
        ? py::cast<double>(zone.attr("center"))
        : py::cast<double>(zone.attr("anchor_center"));
    const double lower = zone.attr("anchor_lower").is_none()
        ? py::cast<double>(zone.attr("lower"))
        : py::cast<double>(zone.attr("anchor_lower"));
    const double upper = zone.attr("anchor_upper").is_none()
        ? py::cast<double>(zone.attr("upper"))
        : py::cast<double>(zone.attr("anchor_upper"));
    const double slope = py::cast<double>(zone.attr("slope_per_session"));
    const double delta = slope * static_cast<double>(session_index - anchor_index);
    projected.attr("center") = stored_zone_price(center + delta);
    projected.attr("lower") = stored_zone_price(lower + delta);
    projected.attr("upper") = stored_zone_price(upper + delta);
    return projected;
}

bool valid_zone_object(const py::handle& zone) {
    return valid_zone_values(
        py::cast<double>(zone.attr("center")),
        py::cast<double>(zone.attr("lower")),
        py::cast<double>(zone.attr("upper")),
        py::cast<double>(zone.attr("atr")),
        py::cast<double>(zone.attr("slope_per_session"))
    );
}

int setup_priority(const std::string& setup) {
    if (setup == "breakout_retest") return 0;
    if (setup == "support_bounce") return 1;
    return 2;
}

std::vector<py::object> object_list(const py::handle& value) {
    std::vector<py::object> result;
    for (const py::handle item : py::reinterpret_borrow<py::iterable>(value)) {
        result.push_back(py::reinterpret_borrow<py::object>(item));
    }
    return result;
}

py::object best_boundary(
    const std::vector<py::object>& zones,
    const std::string& source_kind,
    double close
) {
    std::vector<py::object> matches;
    for (const py::object& zone : zones) {
        if (py::cast<std::string>(zone.attr("source_kind")) == source_kind
            && py::cast<std::string>(zone.attr("status")) == "active"
            && valid_zone_object(zone)) {
            matches.push_back(zone);
        }
    }
    if (matches.empty()) return py::none();
    std::sort(matches.begin(), matches.end(), [close](const py::object& left, const py::object& right) {
        return std::tuple(
            -py::cast<int>(left.attr("pivot_count")),
            -py::cast<double>(left.attr("recency_weight")),
            py::cast<double>(left.attr("fit_residual_atr")),
            std::abs(py::cast<double>(left.attr("center")) - close),
            py::cast<std::string>(left.attr("zone_key"))
        ) < std::tuple(
            -py::cast<int>(right.attr("pivot_count")),
            -py::cast<double>(right.attr("recency_weight")),
            py::cast<double>(right.attr("fit_residual_atr")),
            std::abs(py::cast<double>(right.attr("center")) - close),
            py::cast<std::string>(right.attr("zone_key"))
        );
    });
    return matches.front();
}

std::vector<py::object> zone_member_pivots(const py::object& state, const py::object& zone) {
    std::set<std::string> member_keys;
    for (const py::handle raw_key : py::reinterpret_borrow<py::iterable>(zone.attr("pivot_keys"))) {
        member_keys.insert(py::cast<std::string>(raw_key));
    }
    std::vector<py::object> result;
    for (const py::handle raw_pivot : py::reinterpret_borrow<py::iterable>(state.attr("pivots"))) {
        py::object pivot = py::reinterpret_borrow<py::object>(raw_pivot);
        if (member_keys.contains(py::cast<std::string>(pivot.attr("pivot_key")))) result.push_back(pivot);
    }
    std::sort(result.begin(), result.end(), [](const py::object& left, const py::object& right) {
        return std::tuple(
            py::cast<int>(left.attr("session_index")),
            iso_date(left.attr("trade_date")),
            py::cast<std::string>(left.attr("pivot_key"))
        ) < std::tuple(
            py::cast<int>(right.attr("session_index")),
            iso_date(right.attr("trade_date")),
            py::cast<std::string>(right.attr("pivot_key"))
        );
    });
    return result;
}

std::string direction(double delta, double tolerance) {
    if (delta > tolerance) return "up";
    if (delta < -tolerance) return "down";
    return "flat";
}

std::string pivot_direction(const std::vector<py::object>& pivots, double half_width_ratio) {
    const py::object& previous = pivots[pivots.size() - 2U];
    const py::object& latest = pivots.back();
    const double tolerance = std::max(
        ((py::cast<double>(previous.attr("atr")) + py::cast<double>(latest.attr("atr"))) / 2.0)
            * half_width_ratio,
        1e-12
    );
    return direction(
        py::cast<double>(latest.attr("price")) - py::cast<double>(previous.attr("price")),
        tolerance
    );
}

py::tuple classify_market_regime_native(
    const py::object& state,
    const py::iterable& raw_zones,
    const py::dict& bar,
    const py::dict& signal_cfg
) {
    const std::vector<py::object> zones = object_list(raw_zones);
    const double close = required_number(bar, "close");
    py::object lower = best_boundary(zones, "low", close);
    py::object upper = best_boundary(zones, "high", close);
    py::dict evidence;
    evidence["lower_zone_key"] = lower.is_none() ? py::object(py::none()) : lower.attr("zone_key");
    evidence["upper_zone_key"] = upper.is_none() ? py::object(py::none()) : upper.attr("zone_key");
    evidence["close"] = close;
    if (lower.is_none() || upper.is_none()) {
        evidence["reason_code"] = "missing_boundary";
        return py::make_tuple("transition", evidence);
    }
    const double lower_center = py::cast<double>(lower.attr("center"));
    const double upper_center = py::cast<double>(upper.attr("center"));
    if (lower_center >= upper_center) {
        evidence["reason_code"] = "unordered_boundaries";
        evidence["lower_center"] = lower_center;
        evidence["upper_center"] = upper_center;
        return py::make_tuple("transition", evidence);
    }

    const std::vector<py::object> lower_pivots = zone_member_pivots(state, lower);
    const std::vector<py::object> upper_pivots = zone_member_pivots(state, upper);
    if (lower_pivots.size() < 2U || upper_pivots.size() < 2U) {
        evidence["reason_code"] = "insufficient_pivot_structure";
        evidence["lower_pivot_count"] = lower_pivots.size();
        evidence["upper_pivot_count"] = upper_pivots.size();
        return py::make_tuple("transition", evidence);
    }

    const int span = std::max(py::cast<int>(signal_cfg["min_line_span_sessions"]), 1);
    const double half_width_ratio = py::cast<double>(signal_cfg["zone_half_width_atr"]);
    const std::string lower_boundary_direction = direction(
        py::cast<double>(lower.attr("slope_per_session")) * span,
        std::max(py::cast<double>(lower.attr("atr")) * half_width_ratio, 1e-12)
    );
    const std::string upper_boundary_direction = direction(
        py::cast<double>(upper.attr("slope_per_session")) * span,
        std::max(py::cast<double>(upper.attr("atr")) * half_width_ratio, 1e-12)
    );
    const std::string lower_pivot_direction = pivot_direction(lower_pivots, half_width_ratio);
    const std::string upper_pivot_direction = pivot_direction(upper_pivots, half_width_ratio);
    evidence["lower_center"] = lower_center;
    evidence["upper_center"] = upper_center;
    evidence["lower_boundary_direction"] = lower_boundary_direction;
    evidence["upper_boundary_direction"] = upper_boundary_direction;
    evidence["lower_pivot_direction"] = lower_pivot_direction;
    evidence["upper_pivot_direction"] = upper_pivot_direction;
    py::list lower_keys;
    lower_keys.append(lower_pivots[lower_pivots.size() - 2U].attr("pivot_key"));
    lower_keys.append(lower_pivots.back().attr("pivot_key"));
    evidence["lower_pivot_keys"] = lower_keys;
    py::list upper_keys;
    upper_keys.append(upper_pivots[upper_pivots.size() - 2U].attr("pivot_key"));
    upper_keys.append(upper_pivots.back().attr("pivot_key"));
    evidence["upper_pivot_keys"] = upper_keys;

    const std::array<std::string, 4> directions = {
        lower_boundary_direction,
        upper_boundary_direction,
        lower_pivot_direction,
        upper_pivot_direction,
    };
    if (std::all_of(directions.begin(), directions.end(), [](const auto& value) { return value == "up"; })) {
        if (close < py::cast<double>(lower.attr("lower"))) {
            evidence["reason_code"] = "uptrend_lower_boundary_broken";
            return py::make_tuple("transition", evidence);
        }
        evidence["reason_code"] = "rising_channel_higher_highs_higher_lows";
        return py::make_tuple("uptrend", evidence);
    }
    if (std::all_of(directions.begin(), directions.end(), [](const auto& value) { return value == "down"; })) {
        if (close > py::cast<double>(upper.attr("upper"))) {
            evidence["reason_code"] = "downtrend_upper_boundary_broken";
            return py::make_tuple("transition", evidence);
        }
        evidence["reason_code"] = "falling_channel_lower_highs_lower_lows";
        return py::make_tuple("downtrend", evidence);
    }

    const bool inside = py::cast<double>(lower.attr("lower")) <= close
        && close <= py::cast<double>(upper.attr("upper"));
    if (inside && std::all_of(directions.begin(), directions.end(), [](const auto& value) {
        return value == "flat";
    })) {
        evidence["reason_code"] = "flat_range";
        return py::make_tuple("range", evidence);
    }
    const bool contracting = (lower_boundary_direction == "up" || lower_boundary_direction == "flat")
        && (lower_pivot_direction == "up" || lower_pivot_direction == "flat")
        && (upper_boundary_direction == "down" || upper_boundary_direction == "flat")
        && (upper_pivot_direction == "down" || upper_pivot_direction == "flat")
        && (lower_boundary_direction == "up" || lower_pivot_direction == "up")
        && (upper_boundary_direction == "down" || upper_pivot_direction == "down");
    if (inside && contracting) {
        evidence["reason_code"] = "contracting_range";
        return py::make_tuple("range", evidence);
    }
    const bool expanding = (lower_boundary_direction == "down" || lower_boundary_direction == "flat")
        && (lower_pivot_direction == "down" || lower_pivot_direction == "flat")
        && (upper_boundary_direction == "up" || upper_boundary_direction == "flat")
        && (upper_pivot_direction == "up" || upper_pivot_direction == "flat")
        && (lower_boundary_direction == "down" || lower_pivot_direction == "down")
        && (upper_boundary_direction == "up" || upper_pivot_direction == "up");
    if (inside && expanding) {
        evidence["reason_code"] = "expanding_range";
        return py::make_tuple("range", evidence);
    }
    evidence["reason_code"] = inside ? "structure_conflict" : "price_outside_range";
    return py::make_tuple("transition", evidence);
}

void record_regime_version_native(
    const py::object& state,
    const py::handle& effective_from,
    const std::string& regime,
    const py::dict& evidence
) {
    py::list versions = py::cast<py::list>(state.attr("regime_versions"));
    py::object previous = versions.empty() ? py::object(py::none()) : versions[versions.size() - 1U];
    state.attr("current_regime") = regime;
    state.attr("current_regime_evidence") = copy_dict(evidence);
    if (!previous.is_none() && py::cast<std::string>(py::cast<py::dict>(previous)["regime"]) == regime) {
        return;
    }
    py::dict payload;
    payload["version"] = versions.size() + 1U;
    payload["effective_from"] = iso_date(effective_from);
    payload["regime"] = regime;
    payload["lower_zone_key"] = dict_value(evidence, "lower_zone_key");
    payload["upper_zone_key"] = dict_value(evidence, "upper_zone_key");
    py::object reason = dict_value(evidence, "reason_code");
    payload["reason_code"] = truthy(reason) ? reason : py::str("unknown");
    payload["evidence"] = copy_dict(evidence);
    versions.append(payload);
    py::dict event;
    event["event_date"] = iso_date(effective_from);
    event["event_type"] = "regime_transition";
    event["from_regime"] = previous.is_none()
        ? py::object(py::none())
        : py::object(py::cast<py::dict>(previous)["regime"]);
    event["to_regime"] = regime;
    for (const auto& [key, value] : payload) event[key] = value;
    py::cast<py::list>(state.attr("events")).append(event);
}

py::tuple activate_cached_regime(const py::object& state, const py::handle& trade_date) {
    py::object selected = py::none();
    for (const py::handle raw_payload : py::reinterpret_borrow<py::iterable>(state.attr("cached_regime_timeline"))) {
        py::dict payload = py::cast<py::dict>(raw_payload);
        if (PyObject_RichCompareBool(payload["effective_from"].ptr(), trade_date.ptr(), Py_LE) != 1) continue;
        if (selected.is_none()) {
            selected = payload;
            continue;
        }
        py::dict current = py::cast<py::dict>(selected);
        const int date_comparison = PyObject_RichCompareBool(
            current["effective_from"].ptr(), payload["effective_from"].ptr(), Py_LT
        );
        const int current_version = current.contains("version") && !current["version"].is_none()
            ? py::cast<int>(current["version"])
            : 0;
        const int candidate_version = payload.contains("version") && !payload["version"].is_none()
            ? py::cast<int>(payload["version"])
            : 0;
        const bool same_date = PyObject_RichCompareBool(
            current["effective_from"].ptr(), payload["effective_from"].ptr(), Py_EQ
        ) == 1;
        if (date_comparison == 1 || (same_date && candidate_version > current_version)) selected = payload;
    }
    if (selected.is_none()) {
        py::dict evidence;
        evidence["reason_code"] = "missing_cached_regime";
        evidence["trade_date"] = iso_date(trade_date);
        state.attr("current_regime") = "transition";
        state.attr("current_regime_evidence") = evidence;
        return py::make_tuple("transition", evidence);
    }
    py::dict payload = py::cast<py::dict>(selected);
    std::string regime = py::cast<std::string>(py::str(payload["regime"]));
    if (regime != "uptrend" && regime != "downtrend" && regime != "range" && regime != "transition") {
        regime = "transition";
    }
    py::dict evidence;
    if (payload.contains("evidence") && !payload["evidence"].is_none()) {
        evidence = copy_dict(py::cast<py::dict>(payload["evidence"]));
    }
    state.attr("current_regime") = regime;
    state.attr("current_regime_evidence") = evidence;
    return py::make_tuple(regime, evidence);
}

py::object make_zone_from_cached_payload(
    const std::string& zone_key,
    const py::dict& payload,
    const py::object& old
) {
    const double center = py::cast<double>(payload["center"]);
    const double lower = py::cast<double>(payload["lower"]);
    const double upper = py::cast<double>(payload["upper"]);
    const py::object effective_from = py::reinterpret_borrow<py::object>(payload["effective_from"]);
    const bool same_version = !old.is_none()
        && PyObject_RichCompareBool(old.attr("timeline_effective_from").ptr(), effective_from.ptr(), Py_EQ) == 1;
    const py::object pivot_keys = py::tuple(payload["pivot_keys"]);
    const py::object touch_count = same_version
        ? py::object(old.attr("touch_count"))
        : py::object(payload["touch_count"]);
    return service_type("Zone")(
        py::arg("zone_key") = zone_key,
        py::arg("source_kind") = payload["source_kind"],
        py::arg("role") = payload["role"],
        py::arg("status") = "active",
        py::arg("center") = center,
        py::arg("lower") = lower,
        py::arg("upper") = upper,
        py::arg("atr") = payload["atr"],
        py::arg("pivot_keys") = pivot_keys,
        py::arg("pivot_count") = payload["pivot_count"],
        py::arg("touch_count") = touch_count,
        py::arg("first_pivot_date") = payload["first_pivot_date"],
        py::arg("last_pivot_date") = payload["last_pivot_date"],
        py::arg("valid_from") = payload["valid_from"],
        py::arg("anchor_session_index") = payload.contains("anchor_session_index")
            && truthy(payload["anchor_session_index"])
                ? payload["anchor_session_index"]
                : py::int_(0),
        py::arg("anchor_center") = payload.contains("anchor_center")
            ? payload["anchor_center"]
            : py::float_(center),
        py::arg("anchor_lower") = payload.contains("anchor_lower")
            ? payload["anchor_lower"]
            : py::float_(lower),
        py::arg("anchor_upper") = payload.contains("anchor_upper")
            ? payload["anchor_upper"]
            : py::float_(upper),
        py::arg("slope_per_session") = payload.contains("slope_per_session")
            && truthy(payload["slope_per_session"])
                ? payload["slope_per_session"]
                : py::float_(0.0),
        py::arg("fit_residual_atr") = payload.contains("fit_residual_atr")
            && truthy(payload["fit_residual_atr"])
                ? payload["fit_residual_atr"]
                : py::float_(0.0),
        py::arg("recency_weight") = payload.contains("recency_weight")
            && truthy(payload["recency_weight"])
                ? payload["recency_weight"]
                : py::float_(0.0),
        py::arg("last_inside") = same_version
            ? old.attr("last_inside")
            : py::bool_(payload.contains("last_inside") && truthy(payload["last_inside"])),
        py::arg("timeline_effective_from") = effective_from
    );
}

void activate_cached_zones(const py::object& state, const py::handle& trade_date) {
    std::map<std::string, py::dict> latest;
    for (const py::handle raw_payload : py::reinterpret_borrow<py::iterable>(state.attr("cached_zone_timeline"))) {
        py::dict payload = py::cast<py::dict>(raw_payload);
        if (PyObject_RichCompareBool(payload["effective_from"].ptr(), trade_date.ptr(), Py_GE) == 1) continue;
        const std::string key = py::cast<std::string>(payload["zone_key"]);
        const auto previous = latest.find(key);
        if (previous == latest.end() || PyObject_RichCompareBool(
            previous->second["effective_from"].ptr(), payload["effective_from"].ptr(), Py_LT
        ) == 1) {
            latest.insert_or_assign(key, payload);
        }
    }
    py::dict old_zones = py::cast<py::dict>(state.attr("zones"));
    py::dict activated;
    for (const auto& [zone_key, payload] : latest) {
        if (py::cast<std::string>(payload["status"]) != "active") continue;
        py::object old = old_zones.contains(py::str(zone_key))
            ? py::object(old_zones[py::str(zone_key)])
            : py::object(py::none());
        activated[py::str(zone_key)] = make_zone_from_cached_payload(zone_key, payload, old);
    }
    state.attr("zones") = activated;
}

void record_entry_channel_transition(
    const py::object& state,
    const py::dict& channel,
    const py::handle& trade_date
) {
    py::object previous = state.attr("current_entry_channel");
    std::optional<std::pair<std::string, std::string>> previous_pair;
    if (!previous.is_none()) {
        py::dict previous_dict = py::cast<py::dict>(previous);
        if (previous_dict.contains("valid") && truthy(previous_dict["valid"])) {
            previous_pair = std::pair(
                py::cast<std::string>(previous_dict["support_zone_key"]),
                py::cast<std::string>(previous_dict["resistance_zone_key"])
            );
        }
    }
    std::optional<std::pair<std::string, std::string>> current_pair;
    if (channel.contains("valid") && truthy(channel["valid"])) {
        current_pair = std::pair(
            py::cast<std::string>(channel["support_zone_key"]),
            py::cast<std::string>(channel["resistance_zone_key"])
        );
    }
    if (previous_pair == current_pair) {
        state.attr("current_entry_channel") = current_pair
            ? py::object(copy_dict(channel))
            : py::object(py::none());
        return;
    }
    py::list events = py::cast<py::list>(state.attr("events"));
    if (previous_pair) {
        py::dict previous_dict = py::cast<py::dict>(previous);
        py::dict event;
        event["event_date"] = iso_date(trade_date);
        event["event_type"] = "entry_channel_ended";
        event["zone_key"] = dict_value(previous_dict, "support_zone_key");
        event["lower"] = dict_value(previous_dict, "lower");
        event["upper"] = dict_value(previous_dict, "upper");
        event["entry_channel"] = previous;
        py::object reason = dict_value(channel, "reason_code");
        event["reason_code"] = truthy(reason) ? reason : py::str("entry_channel_pair_changed");
        events.append(event);
    }
    if (current_pair) {
        py::dict event;
        event["event_date"] = iso_date(trade_date);
        event["event_type"] = "entry_channel_started";
        event["zone_key"] = channel["support_zone_key"];
        event["lower"] = channel["lower"];
        event["upper"] = channel["upper"];
        event["entry_channel"] = channel;
        event["reason_code"] = channel["reason_code"];
        events.append(event);
    }
    state.attr("current_entry_channel") = current_pair
        ? py::object(copy_dict(channel))
        : py::object(py::none());
}

void record_cached_lifecycle_events(const py::object& state, const py::handle& trade_date) {
    py::set signatures = py::cast<py::set>(state.attr("cached_lifecycle_events"));
    py::list events = py::cast<py::list>(state.attr("events"));
    for (const py::handle raw_payload : py::reinterpret_borrow<py::iterable>(state.attr("cached_zone_timeline"))) {
        py::dict payload = py::cast<py::dict>(raw_payload);
        if (PyObject_RichCompareBool(payload["effective_from"].ptr(), trade_date.ptr(), Py_EQ) != 1
            || py::cast<std::string>(payload["status"]) != "expired") {
            continue;
        }
        py::tuple signature = py::make_tuple(
            py::reinterpret_borrow<py::object>(trade_date),
            py::str(payload["zone_key"]),
            "invalidation"
        );
        if (signatures.contains(signature)) continue;
        signatures.add(signature);
        py::dict event;
        event["event_date"] = iso_date(trade_date);
        event["event_type"] = "invalidation";
        event["zone_key"] = payload["zone_key"];
        event["role"] = payload["role"];
        events.append(event);
    }
}

double setup_posterior(const py::object& stats) {
    const int wins = py::cast<int>(stats.attr("wins"));
    const int losses = py::cast<int>(stats.attr("losses"));
    return static_cast<double>(wins + 1) / static_cast<double>(wins + losses + 2);
}

int setup_resolved(const py::object& stats) {
    return py::cast<int>(stats.attr("wins")) + py::cast<int>(stats.attr("losses"));
}

void resolve_prior_outcomes(
    const py::object& state,
    const py::dict& bar,
    int session_index,
    const py::dict& signal_cfg
) {
    py::list remaining;
    py::list events = py::cast<py::list>(state.attr("events"));
    py::dict stats_by_setup = py::cast<py::dict>(state.attr("stats"));
    const int horizon = py::cast<int>(signal_cfg["score_outcome_window"]);
    for (const py::handle raw_outcome : py::reinterpret_borrow<py::iterable>(state.attr("pending_outcomes"))) {
        py::object outcome = py::reinterpret_borrow<py::object>(raw_outcome);
        const int elapsed = session_index - py::cast<int>(outcome.attr("origin_session_index"));
        const bool hit_target = required_number(bar, "high") >= py::cast<double>(outcome.attr("target"));
        const bool hit_stop = required_number(bar, "low") <= py::cast<double>(outcome.attr("stop"));
        const std::string setup = py::cast<std::string>(outcome.attr("setup"));
        py::object stats = py::reinterpret_borrow<py::object>(stats_by_setup[py::str(setup)]);
        std::optional<std::string> result;
        if (hit_target && hit_stop) {
            stats.attr("losses") = py::cast<int>(stats.attr("losses")) + 1;
            result = "loss_same_day_both";
        } else if (hit_stop) {
            stats.attr("losses") = py::cast<int>(stats.attr("losses")) + 1;
            result = "loss";
        } else if (hit_target) {
            stats.attr("wins") = py::cast<int>(stats.attr("wins")) + 1;
            result = "win";
        } else if (elapsed >= horizon) {
            stats.attr("censored") = py::cast<int>(stats.attr("censored")) + 1;
            result = "censored";
        } else {
            remaining.append(outcome);
        }
        if (result) {
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "score_outcome";
            event["zone_key"] = outcome.attr("zone_key");
            event["setup"] = setup;
            event["origin_date"] = iso_date(outcome.attr("origin_date"));
            event["result"] = *result;
            event["posterior"] = setup_posterior(stats);
            event["resolved_samples"] = setup_resolved(stats);
            events.append(event);
        }
    }
    state.attr("pending_outcomes") = remaining;
}

double rounded_two(double value) {
    return py::cast<double>(py::module_::import("builtins").attr("round")(value, 2));
}

double normalized_strength_score(double value, double gate, double cap_or_ideal, bool rise) {
    if (rise) {
        return rounded_two(100.0 * std::clamp((value - gate) / (cap_or_ideal - gate), 0.0, 1.0));
    }
    return rounded_two(100.0 * std::clamp((gate - value) / (gate - cap_or_ideal), 0.0, 1.0));
}

py::dict strength_component(
    const std::string& key,
    double raw_value,
    double weight,
    double gate,
    double cap_or_ideal,
    bool rise = true
) {
    py::dict component;
    component["key"] = key;
    component["raw_value"] = raw_value;
    component["normalized_score"] = normalized_strength_score(
        raw_value, gate, cap_or_ideal, rise
    );
    component["weight"] = weight;
    return component;
}

py::dict build_strength_record(
    const std::string& model_version,
    double threshold,
    const std::vector<py::dict>& components
) {
    double weighted_score = 0.0;
    double total_weight = 0.0;
    py::list raw_components;
    for (const py::dict& component : components) {
        const double weight = py::cast<double>(component["weight"]);
        total_weight += weight;
        weighted_score += py::cast<double>(component["normalized_score"]) * weight;
        raw_components.append(component);
    }
    const double score = rounded_two(weighted_score / total_weight);
    std::string level = "very_strong";
    if (score < 50.0) level = "weak";
    else if (score < 70.0) level = "medium";
    else if (score < 85.0) level = "strong";
    py::dict result;
    result["score"] = score;
    result["level"] = level;
    result["threshold"] = threshold;
    result["passes_threshold"] = score >= threshold;
    result["rank"] = py::none();
    result["model_version"] = model_version;
    result["components"] = raw_components;
    return result;
}

py::dict support_resistance_strength(
    const std::string& setup,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg,
    const py::dict& measurements,
    double reward_risk
) {
    const double threshold = signal_cfg.contains("min_strength_score")
        ? py::cast<double>(signal_cfg["min_strength_score"])
        : 50.0;
    const double minimum_reward_risk = py::cast<double>(risk_cfg["min_reward_risk"]);
    const py::dict reward_component = strength_component(
        "reward_risk",
        reward_risk,
        setup == "resistance_breakout" ? 0.20 : 0.30,
        minimum_reward_risk,
        minimum_reward_risk * 2.0
    );
    std::vector<py::dict> components;
    if (setup == "support_bounce") {
        const double confirmation = py::cast<double>(measurements["confirmation_atr"]);
        const double gate = py::cast<double>(signal_cfg["bounce_confirmation_atr"]);
        components = {
            strength_component("confirmation_atr", confirmation, 0.70, gate, gate * 2.0),
            reward_component,
        };
    } else if (setup == "resistance_breakout") {
        const double confirmation = py::cast<double>(measurements["confirmation_atr"]);
        const double confirmation_gate = py::cast<double>(signal_cfg["breakout_confirmation_atr"]);
        const double volume_ratio = py::cast<double>(measurements["volume_ratio"]);
        const double volume_gate = py::cast<double>(signal_cfg["breakout_volume_ratio_min"]);
        components = {
            strength_component(
                "confirmation_atr", confirmation, 0.45, confirmation_gate, confirmation_gate * 2.0
            ),
            strength_component("volume_ratio", volume_ratio, 0.35, volume_gate, volume_gate * 2.0),
            reward_component,
        };
    } else {
        const double hold_margin = py::cast<double>(measurements["hold_margin_atr"]);
        const double hold_cap = py::cast<double>(signal_cfg["bounce_confirmation_atr"]);
        const double retest_ratio = py::cast<double>(measurements["retest_volume_ratio"]);
        const double retest_gate = py::cast<double>(signal_cfg["retest_volume_ratio_max"]);
        components = {
            strength_component("hold_margin_atr", hold_margin, 0.35, 0.0, hold_cap),
            strength_component("retest_volume_ratio", retest_ratio, 0.35, retest_gate, 0.0, false),
            reward_component,
        };
    }
    return build_strength_record("support_resistance:" + setup + ":v1", threshold, components);
}

py::dict candidate_payload(
    const py::object& state,
    const std::string& setup,
    const py::object& zone,
    const std::vector<py::object>& zones,
    const py::dict& bar,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    const double entry = required_number(bar, "close");
    const double atr = required_number(bar, "atr_14");
    const double stop = std::max({
        py::cast<double>(zone.attr("lower")),
        entry - py::cast<double>(risk_cfg["stop_loss_atr"]) * atr,
        entry * (1.0 - py::cast<double>(risk_cfg["max_loss_pct"])),
    });
    std::vector<double> overhead;
    const std::string zone_key = py::cast<std::string>(zone.attr("zone_key"));
    for (const py::object& candidate : zones) {
        const double lower = py::cast<double>(candidate.attr("lower"));
        if (py::cast<std::string>(candidate.attr("role")) == "resistance"
            && py::cast<std::string>(candidate.attr("zone_key")) != zone_key
            && lower > entry) {
            overhead.push_back(lower);
        }
    }
    std::sort(overhead.begin(), overhead.end());
    const double target = overhead.empty()
        ? entry + py::cast<double>(risk_cfg["take_profit_atr"]) * atr
        : overhead.front();
    const double risk = entry - stop;
    const double reward_risk = risk > 0.0 ? (target - entry) / risk : 0.0;
    const bool eligible = reward_risk >= py::cast<double>(risk_cfg["min_reward_risk"]);
    py::dict stats_by_setup = py::cast<py::dict>(state.attr("stats"));
    py::object stats = py::reinterpret_borrow<py::object>(stats_by_setup[py::str(setup)]);
    py::dict breakouts = py::cast<py::dict>(state.attr("breakouts"));
    py::object breakout = breakouts.contains(py::str(zone_key))
        ? py::object(breakouts[py::str(zone_key)])
        : py::object(py::none());
    const double volume = required_number(bar, "volume");
    const double volume_average = required_number(bar, "volume_sma_20");
    py::dict strength_inputs;
    strength_inputs["confirmation_atr"] = (entry - py::cast<double>(zone.attr("upper"))) / atr;
    strength_inputs["hold_margin_atr"] = (entry - py::cast<double>(zone.attr("upper"))) / atr;
    strength_inputs["volume_ratio"] = volume_average > 0.0
        ? py::object(py::float_(volume / volume_average))
        : py::object(py::none());
    strength_inputs["retest_volume_ratio"] = !breakout.is_none()
        && py::cast<double>(breakout.attr("breakout_volume")) > 0.0
            ? py::object(py::float_(volume / py::cast<double>(breakout.attr("breakout_volume"))))
            : py::object(py::none());
    strength_inputs["reward_risk"] = reward_risk;

    const int wins = py::cast<int>(stats.attr("wins"));
    const int losses = py::cast<int>(stats.attr("losses"));
    const int censored = py::cast<int>(stats.attr("censored"));
    py::dict score_evidence;
    score_evidence["wins"] = wins;
    score_evidence["losses"] = losses;
    score_evidence["censored"] = censored;
    score_evidence["resolved_samples"] = wins + losses;
    score_evidence["alpha"] = wins + 1;
    score_evidence["beta"] = losses + 1;
    std::string reason;
    if (setup == "support_bounce") reason = "confirmed bounce above a frozen support zone";
    else if (setup == "resistance_breakout") reason = "volume-confirmed close above a frozen resistance zone";
    else reason = "low-volume retest held the former resistance zone";
    py::dict candidate;
    candidate["setup"] = setup;
    candidate["zone_key"] = zone_key;
    candidate["zone"] = zone_object_snapshot(zone);
    candidate["score"] = setup_posterior(stats);
    candidate["score_evidence"] = score_evidence;
    candidate["entry_eligible"] = eligible;
    candidate["rejection_reason"] = eligible
        ? py::object(py::none())
        : py::object(py::str("nearest resistance yields reward/risk below minimum"));
    candidate["stop_price"] = stop;
    candidate["target_price"] = target;
    candidate["reward_risk"] = reward_risk;
    candidate["reason"] = reason;
    candidate["strength_inputs"] = strength_inputs;
    candidate["strength"] = support_resistance_strength(
        setup, signal_cfg, risk_cfg, strength_inputs, reward_risk
    );
    return candidate;
}

std::vector<py::dict> detect_candidates(
    const py::object& state,
    const py::dict& bar,
    const std::vector<py::object>& zones,
    int session_index,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg
) {
    py::list history = py::cast<py::list>(state.attr("history"));
    const std::optional<double> previous_close = history.empty()
        ? std::nullopt
        : std::optional(required_number(py::cast<py::dict>(history[history.size() - 1U]), "close"));
    py::dict breakouts = py::cast<py::dict>(state.attr("breakouts"));
    py::list events = py::cast<py::list>(state.attr("events"));
    std::vector<py::dict> candidates;
    const double atr = required_number(bar, "atr_14");
    for (const py::object& zone : zones) {
        const std::string role = py::cast<std::string>(zone.attr("role"));
        const double upper = py::cast<double>(zone.attr("upper"));
        std::optional<std::string> setup;
        const bool breakout = role == "resistance" && previous_close
            && *previous_close <= upper + py::cast<double>(signal_cfg["breakout_confirmation_atr"]) * atr
            && required_number(bar, "close") > upper
                + py::cast<double>(signal_cfg["breakout_confirmation_atr"]) * atr
            && required_number(bar, "volume_sma_20") > 0.0
            && required_number(bar, "volume") >= py::cast<double>(signal_cfg["breakout_volume_ratio_min"])
                * required_number(bar, "volume_sma_20");
        if (role == "support" && truthy(signal_cfg["support_bounce_enabled"]) && previous_close
            && *previous_close > upper && required_number(bar, "low") <= upper
            && required_number(bar, "close") >= upper
                + py::cast<double>(signal_cfg["bounce_confirmation_atr"]) * atr) {
            setup = "support_bounce";
        }
        if (breakout) {
            const std::string key = py::cast<std::string>(zone.attr("zone_key"));
            py::object record = service_type("BreakoutRecord")(
                py::arg("zone_key") = key,
                py::arg("breakout_date") = bar["dt_ny"],
                py::arg("breakout_session_index") = session_index,
                py::arg("breakout_volume") = bar["volume"]
            );
            breakouts[py::str(key)] = record;
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "breakout";
            event["zone_key"] = key;
            event["setup"] = "resistance_breakout";
            event["role"] = role;
            event["lower"] = zone.attr("lower");
            event["upper"] = zone.attr("upper");
            event["breakout_volume"] = bar["volume"];
            events.append(event);
            if (truthy(signal_cfg["resistance_breakout_enabled"])) setup = "resistance_breakout";
        }
        if (setup) candidates.push_back(candidate_payload(
            state, *setup, zone, zones, bar, signal_cfg, risk_cfg
        ));
    }

    std::vector<std::string> breakout_keys;
    for (const auto& [raw_key, value] : breakouts) {
        static_cast<void>(value);
        breakout_keys.push_back(py::cast<std::string>(raw_key));
    }
    std::sort(breakout_keys.begin(), breakout_keys.end());
    for (const std::string& key : breakout_keys) {
        py::object breakout = py::reinterpret_borrow<py::object>(breakouts[py::str(key)]);
        const int elapsed = session_index - py::cast<int>(breakout.attr("breakout_session_index"));
        if (elapsed <= 0 || elapsed > py::cast<int>(signal_cfg["retest_window"])) continue;
        py::object zone = py::none();
        for (const py::object& candidate : zones) {
            if (py::cast<std::string>(candidate.attr("zone_key")) == key) {
                zone = candidate;
                break;
            }
        }
        if (zone.is_none()) continue;
        if (required_number(bar, "low") <= py::cast<double>(zone.attr("upper"))
            && required_number(bar, "close") >= py::cast<double>(zone.attr("upper"))
            && required_number(bar, "volume") <= py::cast<double>(breakout.attr("breakout_volume"))
                * py::cast<double>(signal_cfg["retest_volume_ratio_max"])) {
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "retest";
            event["zone_key"] = key;
            event["setup"] = "breakout_retest";
            event["role"] = zone.attr("role");
            event["lower"] = zone.attr("lower");
            event["upper"] = zone.attr("upper");
            event["breakout_date"] = iso_date(breakout.attr("breakout_date"));
            event["breakout_volume"] = breakout.attr("breakout_volume");
            event["retest_volume"] = bar["volume"];
            events.append(event);
            if (truthy(signal_cfg["breakout_retest_enabled"])) {
                candidates.push_back(candidate_payload(
                    state, "breakout_retest", zone, zones, bar, signal_cfg, risk_cfg
                ));
            }
        }
    }
    std::sort(candidates.begin(), candidates.end(), [](const py::dict& left, const py::dict& right) {
        return std::pair(
            setup_priority(py::cast<std::string>(left["setup"])),
            py::cast<std::string>(left["zone_key"])
        ) < std::pair(
            setup_priority(py::cast<std::string>(right["setup"])),
            py::cast<std::string>(right["zone_key"])
        );
    });
    return candidates;
}

void apply_regime_entry_policy(
    const py::object& state,
    std::vector<py::dict>& candidates,
    const std::string& regime,
    const py::dict& regime_evidence,
    const py::dict& entry_channel,
    const py::handle& trade_date
) {
    std::set<std::string> allowed;
    if (regime == "uptrend") allowed = {"support_bounce", "breakout_retest"};
    else if (regime == "range") allowed = {"support_bounce"};
    py::list events = py::cast<py::list>(state.attr("events"));
    for (py::dict& candidate : candidates) {
        const std::string setup = py::cast<std::string>(candidate["setup"]);
        const bool risk_eligible = truthy(candidate["entry_eligible"]);
        const bool regime_eligible = allowed.contains(setup);
        const bool channel_eligible = entry_channel.contains("valid") && truthy(entry_channel["valid"]);
        const bool direct_breakout = setup == "resistance_breakout";
        candidate["risk_eligible"] = risk_eligible;
        candidate["regime"] = regime;
        candidate["regime_evidence"] = copy_dict(regime_evidence);
        candidate["regime_eligible"] = regime_eligible;
        candidate["entry_channel"] = entry_channel;
        candidate["channel_eligible"] = channel_eligible;
        candidate["entry_eligible"] = risk_eligible && regime_eligible && channel_eligible && !direct_breakout;
        if (direct_breakout) {
            candidate["rejection_reason"] = "direct_breakout_audit_only";
            py::dict event;
            event["event_date"] = iso_date(trade_date);
            event["event_type"] = "direct_breakout_audit";
            event["zone_key"] = candidate["zone_key"];
            event["setup"] = setup;
            event["regime"] = regime;
            event["reason_code"] = "direct_breakout_audit_only";
            event["entry_channel"] = entry_channel;
            events.append(event);
            continue;
        }
        if (!channel_eligible) {
            py::object reason = dict_value(entry_channel, "reason_code");
            candidate["rejection_reason"] = truthy(reason)
                ? reason
                : py::str("missing_valid_entry_channel");
            py::dict event;
            event["event_date"] = iso_date(trade_date);
            event["event_type"] = "entry_channel_rejection";
            event["zone_key"] = candidate["zone_key"];
            event["setup"] = setup;
            event["regime"] = regime;
            event["reason_code"] = candidate["rejection_reason"];
            event["entry_channel"] = entry_channel;
            events.append(event);
        }
        if (!regime_eligible) {
            candidate["rejection_reason"] = "setup " + setup + " is not allowed in " + regime + " regime";
            py::dict event;
            event["event_date"] = iso_date(trade_date);
            event["event_type"] = "regime_rejection";
            event["zone_key"] = candidate["zone_key"];
            event["setup"] = setup;
            event["regime"] = regime;
            event["reason_code"] = "setup_not_allowed_in_regime";
            event["regime_evidence"] = copy_dict(regime_evidence);
            events.append(event);
        }
    }
}

py::object select_candidate(const std::vector<py::dict>& candidates) {
    std::vector<py::dict> eligible;
    for (const py::dict& candidate : candidates) {
        if (truthy(candidate["entry_eligible"])) eligible.push_back(candidate);
    }
    if (eligible.empty()) return py::none();
    std::sort(eligible.begin(), eligible.end(), [](const py::dict& left, const py::dict& right) {
        const py::dict left_strength = py::cast<py::dict>(left["strength"]);
        const py::dict right_strength = py::cast<py::dict>(right["strength"]);
        return std::tuple(
            -py::cast<double>(left_strength["score"]),
            setup_priority(py::cast<std::string>(left["setup"])),
            py::cast<std::string>(left["zone_key"])
        ) < std::tuple(
            -py::cast<double>(right_strength["score"]),
            setup_priority(py::cast<std::string>(right["setup"])),
            py::cast<std::string>(right["zone_key"])
        );
    });
    return eligible.front();
}

py::object resolve_exit(
    const py::dict& snapshot,
    const py::dict& bar,
    const py::dict& risk_cfg,
    const std::string& regime,
    const py::dict& regime_evidence
) {
    py::object features = dict_value(snapshot, "entry_signal_features");
    if (features.is_none() || !py::isinstance<py::dict>(features)) return py::none();
    py::dict features_dict = py::cast<py::dict>(features);
    py::object frozen_value = dict_value(features_dict, "support_resistance");
    if (frozen_value.is_none() || !py::isinstance<py::dict>(frozen_value)) return py::none();
    py::dict frozen = py::cast<py::dict>(frozen_value);
    std::optional<double> entry = finite_number(dict_value(snapshot, "avg_entry_price"));
    if (!entry || *entry == 0.0) entry = finite_number(dict_value(frozen, "entry_close"));
    if (!entry) return py::none();
    std::optional<double> atr = finite_number(dict_value(frozen, "entry_atr"));
    if (!atr || *atr == 0.0) atr = required_number(bar, "atr_14");
    py::dict frozen_zone;
    py::object zone_value = dict_value(frozen, "zone");
    if (!zone_value.is_none() && py::isinstance<py::dict>(zone_value)) frozen_zone = py::cast<py::dict>(zone_value);
    const std::optional<double> zone_line = finite_number(dict_value(frozen_zone, "lower"));
    double stop = std::max(
        *entry - py::cast<double>(risk_cfg["stop_loss_atr"]) * *atr,
        *entry * (1.0 - py::cast<double>(risk_cfg["max_loss_pct"]))
    );
    if (zone_line) stop = std::max(stop, *zone_line);
    std::optional<double> target_value = finite_number(dict_value(frozen, "target_price"));
    const double target = target_value && *target_value != 0.0
        ? *target_value
        : *entry + py::cast<double>(risk_cfg["take_profit_atr"]) * *atr;
    const int holding_days = snapshot.contains("position_holding_days")
        && truthy(snapshot["position_holding_days"])
            ? py::cast<int>(snapshot["position_holding_days"])
            : 0;
    std::optional<std::string> reason;
    if (required_number(bar, "close") < stop) {
        reason = "closed below the frozen zone-aware invalidation line";
    } else if (required_number(bar, "close") >= target) {
        reason = "reached the frozen support/resistance target";
    } else if (regime == "downtrend") {
        reason = "confirmed downtrend regime";
    } else if (holding_days >= py::cast<int>(risk_cfg["max_holding_days"])) {
        reason = "reached the maximum support/resistance holding period";
    }
    if (!reason) return py::none();
    py::dict metadata = copy_dict(frozen);
    metadata["exit_stop_price"] = stop;
    metadata["exit_target_price"] = target;
    metadata["exit_regime"] = regime;
    metadata["exit_regime_evidence"] = regime_evidence;
    py::dict decision;
    decision["action"] = "SELL";
    decision["reason"] = *reason;
    decision["score"] = py::none();
    decision["support_resistance"] = metadata;
    return std::move(decision);
}

void pop_if_present(py::dict& values, const std::string& key) {
    if (values.contains(py::str(key))) values.attr("pop")(py::str(key));
}

void apply_current_bar_zone_state(
    const py::object& state,
    const py::dict& bar,
    int session_index,
    const py::dict& signal_cfg
) {
    py::dict current = py::cast<py::dict>(state.attr("zones"));
    py::dict projected;
    for (const auto& [raw_key, raw_zone] : current) {
        projected[raw_key] = project_zone_object(raw_zone, session_index);
    }
    state.attr("zones") = projected;
    std::vector<py::object> zones;
    for (const auto& [raw_key, raw_zone] : projected) {
        static_cast<void>(raw_key);
        zones.push_back(py::reinterpret_borrow<py::object>(raw_zone));
    }
    std::sort(zones.begin(), zones.end(), [](const py::object& left, const py::object& right) {
        return py::cast<std::string>(left.attr("zone_key"))
            < py::cast<std::string>(right.attr("zone_key"));
    });
    py::dict breakouts = py::cast<py::dict>(state.attr("breakouts"));
    py::list events = py::cast<py::list>(state.attr("events"));
    for (py::object& zone : zones) {
        if (py::cast<std::string>(zone.attr("status")) != "active") continue;
        const double lower = py::cast<double>(zone.attr("lower"));
        const double upper = py::cast<double>(zone.attr("upper"));
        const bool inside = required_number(bar, "high") >= lower && required_number(bar, "low") <= upper;
        if (inside && !truthy(zone.attr("last_inside"))) {
            zone.attr("touch_count") = py::cast<int>(zone.attr("touch_count")) + 1;
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "touch";
            event["zone_key"] = zone.attr("zone_key");
            event["role"] = zone.attr("role");
            event["lower"] = lower;
            event["upper"] = upper;
            events.append(event);
        }
        zone.attr("last_inside") = inside;

        const std::string key = py::cast<std::string>(zone.attr("zone_key"));
        py::object breakout = breakouts.contains(py::str(key))
            ? py::object(breakouts[py::str(key)])
            : py::object(py::none());
        const std::string role = py::cast<std::string>(zone.attr("role"));
        if (role == "resistance" && required_number(bar, "close") > upper) {
            zone.attr("role") = "support";
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "role_transition";
            event["zone_key"] = key;
            event["from_role"] = role;
            event["to_role"] = "support";
            event["lower"] = lower;
            event["upper"] = upper;
            event["reason"] = !breakout.is_none()
                && session_index == py::cast<int>(breakout.attr("breakout_session_index"))
                    ? "confirmed_breakout"
                    : "close_above_resistance";
            events.append(event);
        } else if (role == "support" && required_number(bar, "close") < lower) {
            zone.attr("role") = "resistance";
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "role_transition";
            event["zone_key"] = key;
            event["from_role"] = role;
            event["to_role"] = "resistance";
            event["lower"] = lower;
            event["upper"] = upper;
            event["reason"] = "support_breakdown";
            events.append(event);
            pop_if_present(breakouts, key);
        } else if (role == "support" && !breakout.is_none()
            && session_index > py::cast<int>(breakout.attr("breakout_session_index"))
            && session_index <= py::cast<int>(breakout.attr("breakout_session_index"))
                + py::cast<int>(signal_cfg["retest_window"])
            && required_number(bar, "low") <= upper
            && required_number(bar, "close") >= upper
            && required_number(bar, "volume") <= py::cast<double>(breakout.attr("breakout_volume"))
                * py::cast<double>(signal_cfg["retest_volume_ratio_max"])) {
            pop_if_present(breakouts, key);
        }
    }
    std::vector<std::string> expired;
    for (const auto& [raw_key, raw_breakout] : breakouts) {
        py::object breakout = py::reinterpret_borrow<py::object>(raw_breakout);
        if (session_index - py::cast<int>(breakout.attr("breakout_session_index"))
            > py::cast<int>(signal_cfg["retest_window"])) {
            expired.push_back(py::cast<std::string>(raw_key));
        }
    }
    for (const std::string& key : expired) pop_if_present(breakouts, key);
}

void confirm_pivots(const py::object& state, const py::dict& signal_cfg) {
    const int left = py::cast<int>(signal_cfg["pivot_left_bars"]);
    const int right = py::cast<int>(signal_cfg["pivot_right_bars"]);
    py::list history = py::cast<py::list>(state.attr("history"));
    const int pivot_index = static_cast<int>(history.size()) - 1 - right;
    if (pivot_index < left) return;
    py::dict candidate = py::cast<py::dict>(history[static_cast<std::size_t>(pivot_index)]);
    const py::object confirmed_on = history[history.size() - 1U].cast<py::dict>()["dt_ny"];
    py::list pivots = py::cast<py::list>(state.attr("pivots"));
    for (const std::string& kind : {std::string("high"), std::string("low")}) {
        std::vector<double> values;
        for (int index = pivot_index - left; index <= pivot_index + right; ++index) {
            values.push_back(required_number(
                py::cast<py::dict>(history[static_cast<std::size_t>(index)]), kind.c_str()
            ));
        }
        const double price = required_number(candidate, kind.c_str());
        const double extreme = kind == "high"
            ? *std::max_element(values.begin(), values.end())
            : *std::min_element(values.begin(), values.end());
        if (price != extreme || std::count(values.begin(), values.end(), extreme) != 1) continue;
        const std::string key = kind + ":" + iso_date(candidate["dt_ny"]);
        bool exists = false;
        for (const py::handle raw_pivot : pivots) {
            if (py::cast<std::string>(raw_pivot.attr("pivot_key")) == key) {
                exists = true;
                break;
            }
        }
        if (exists) continue;
        pivots.append(service_type("Pivot")(
            py::arg("pivot_key") = key,
            py::arg("kind") = kind,
            py::arg("session_index") = pivot_index,
            py::arg("trade_date") = candidate["dt_ny"],
            py::arg("confirmed_on") = confirmed_on,
            py::arg("price") = price,
            py::arg("atr") = candidate["atr_14"]
        ));
    }
}

py::list raw_pivots(const std::vector<py::object>& pivots) {
    py::list result;
    for (const py::object& pivot : pivots) {
        py::dict raw;
        raw["pivot_key"] = pivot.attr("pivot_key");
        raw["session_index"] = pivot.attr("session_index");
        raw["price"] = pivot.attr("price");
        raw["atr"] = pivot.attr("atr");
        result.append(raw);
    }
    return result;
}

bool pivot_keys_equal(const py::handle& left, const py::handle& right) {
    return PyObject_RichCompareBool(left.ptr(), right.ptr(), Py_EQ) == 1;
}

py::object match_zone_native(
    const py::iterable& old_zones,
    const std::string& source_kind,
    double center,
    double half_width,
    const py::tuple& pivot_keys
) {
    std::vector<py::object> candidates;
    for (const py::handle raw_zone : old_zones) {
        py::object zone = py::reinterpret_borrow<py::object>(raw_zone);
        if (py::cast<std::string>(zone.attr("source_kind")) == source_kind
            && py::cast<double>(zone.attr("lower")) <= center + half_width
            && py::cast<double>(zone.attr("upper")) >= center - half_width) {
            candidates.push_back(zone);
        }
    }
    if (candidates.empty()) return py::none();
    std::vector<py::object> exact;
    for (const py::object& zone : candidates) {
        if (pivot_keys_equal(zone.attr("pivot_keys"), pivot_keys)) exact.push_back(zone);
    }
    std::vector<py::object>& selected = exact.empty() ? candidates : exact;
    std::sort(selected.begin(), selected.end(), [center](const py::object& left, const py::object& right) {
        return std::pair(
            std::abs(py::cast<double>(left.attr("center")) - center),
            py::cast<std::string>(left.attr("zone_key"))
        ) < std::pair(
            std::abs(py::cast<double>(right.attr("center")) - center),
            py::cast<std::string>(right.attr("zone_key"))
        );
    });
    return selected.front();
}

void record_zone_version_native(
    const py::object& state,
    const py::object& zone,
    const py::handle& effective_date,
    const std::string& status
) {
    py::tuple signature = py::make_tuple(
        zone.attr("role"),
        status,
        zone.attr("pivot_keys"),
        zone.attr("anchor_session_index"),
        zone.attr("anchor_center"),
        zone.attr("slope_per_session")
    );
    const std::string key = py::cast<std::string>(zone.attr("zone_key"));
    py::dict signatures = py::cast<py::dict>(state.attr("version_signatures"));
    if (signatures.contains(py::str(key))
        && PyObject_RichCompareBool(signatures[py::str(key)].ptr(), signature.ptr(), Py_EQ) == 1) {
        return;
    }
    signatures[py::str(key)] = signature;
    py::dict version = zone_object_snapshot(zone);
    version["status"] = status;
    version["effective_from"] = iso_date(effective_date);
    py::cast<py::list>(state.attr("zone_versions")).append(version);
}

std::string zone_key_from_pivots(const std::string& source_kind, const std::vector<py::object>& pivots) {
    std::vector<std::string> keys;
    for (const py::object& pivot : pivots) keys.push_back(py::cast<std::string>(pivot.attr("pivot_key")));
    std::sort(keys.begin(), keys.end());
    std::ostringstream membership;
    for (std::size_t index = 0; index < keys.size(); ++index) {
        if (index > 0U) membership << '|';
        membership << keys[index];
    }
    return "srz_" + sha256_hex(source_kind + "|" + membership.str()).substr(0U, 20U);
}

void rebuild_zones_native(
    const py::object& state,
    const py::dict& bar,
    const py::dict& signal_cfg
) {
    py::list history = py::cast<py::list>(state.attr("history"));
    const int current_index = static_cast<int>(history.size()) - 1;
    const int lookback = py::cast<int>(signal_cfg["detection_window"]);
    std::vector<py::object> retained_pivots;
    for (const py::handle raw_pivot : py::reinterpret_borrow<py::iterable>(state.attr("pivots"))) {
        py::object pivot = py::reinterpret_borrow<py::object>(raw_pivot);
        if (current_index - py::cast<int>(pivot.attr("session_index")) < lookback) retained_pivots.push_back(pivot);
    }
    py::list retained_list;
    for (const py::object& pivot : retained_pivots) retained_list.append(pivot);
    state.attr("pivots") = retained_list;
    const double half_width = py::cast<double>(signal_cfg["zone_half_width_atr"])
        * required_number(bar, "atr_14");
    py::dict current_zones = py::cast<py::dict>(state.attr("zones"));
    py::dict old_zones;
    for (const auto& [raw_key, raw_zone] : current_zones) {
        old_zones[raw_key] = project_zone_object(raw_zone, current_index);
    }
    py::dict rebuilt;
    for (const auto& [source_kind, default_role] : {
        std::pair(std::string("low"), std::string("support")),
        std::pair(std::string("high"), std::string("resistance")),
    }) {
        std::vector<py::object> pivots;
        for (const py::object& pivot : retained_pivots) {
            if (py::cast<std::string>(pivot.attr("kind")) == source_kind) pivots.push_back(pivot);
        }
        std::sort(pivots.begin(), pivots.end(), [](const py::object& left, const py::object& right) {
            return std::tuple(
                py::cast<int>(left.attr("session_index")),
                iso_date(left.attr("trade_date")),
                py::cast<std::string>(left.attr("pivot_key"))
            ) < std::tuple(
                py::cast<int>(right.attr("session_index")),
                iso_date(right.attr("trade_date")),
                py::cast<std::string>(right.attr("pivot_key"))
            );
        });
        py::object fit = fit_pivot_line(raw_pivots(pivots), current_index, signal_cfg);
        if (fit.is_none()) continue;
        py::dict fit_result = py::cast<py::dict>(fit);
        std::map<std::string, py::object> pivot_by_key;
        for (const py::object& pivot : pivots) {
            pivot_by_key.emplace(py::cast<std::string>(pivot.attr("pivot_key")), pivot);
        }
        std::vector<py::object> cluster;
        for (const py::handle raw_key : py::reinterpret_borrow<py::iterable>(fit_result["inlier_pivot_keys"])) {
            cluster.push_back(pivot_by_key.at(py::cast<std::string>(raw_key)));
        }
        const double center = py::cast<double>(fit_result["center"]);
        const double slope = py::cast<double>(fit_result["slope"]);
        if (!valid_zone_values(
            center,
            center - half_width,
            center + half_width,
            required_number(bar, "atr_14"),
            slope
        )) continue;
        std::vector<std::string> sorted_keys;
        for (const py::object& pivot : cluster) sorted_keys.push_back(py::cast<std::string>(pivot.attr("pivot_key")));
        std::sort(sorted_keys.begin(), sorted_keys.end());
        py::tuple pivot_keys(sorted_keys.size());
        for (std::size_t index = 0; index < sorted_keys.size(); ++index) pivot_keys[index] = sorted_keys[index];
        py::object matched = match_zone_native(old_zones.attr("values")(), source_kind, center, half_width, pivot_keys);
        std::string key = matched.is_none()
            ? zone_key_from_pivots(source_kind, cluster)
            : py::cast<std::string>(matched.attr("zone_key"));
        if (matched.is_none()) {
            const std::string effective = iso_date(bar["dt_ny"]);
            bool same_day = false;
            for (const py::handle raw_version : py::reinterpret_borrow<py::iterable>(state.attr("zone_versions"))) {
                py::dict version = py::cast<py::dict>(raw_version);
                if (py::cast<std::string>(version["zone_key"]) == key
                    && py::cast<std::string>(version["effective_from"]) == effective) {
                    same_day = true;
                    break;
                }
            }
            if (same_day) key = "srz_" + sha256_hex(key + "|revived|" + effective).substr(0U, 20U);
        }
        std::string role;
        if (!matched.is_none()) role = py::cast<std::string>(matched.attr("role"));
        else if (required_number(bar, "close") > center + half_width) role = "support";
        else if (required_number(bar, "close") < center - half_width) role = "resistance";
        else role = default_role;
        const bool unchanged = !matched.is_none() && pivot_keys_equal(matched.attr("pivot_keys"), pivot_keys);
        py::object zone;
        if (unchanged) {
            zone = copy_object(matched);
            zone.attr("role") = role;
            zone.attr("status") = "active";
            zone.attr("touch_count") = std::max(
                static_cast<int>(cluster.size()), py::cast<int>(matched.attr("touch_count"))
            );
        } else {
            const double stored_center = stored_zone_price(center);
            const double stored_half_width = stored_zone_price(half_width);
            py::object first_date = cluster.front().attr("trade_date");
            py::object last_date = cluster.front().attr("trade_date");
            for (const py::object& pivot : cluster) {
                if (PyObject_RichCompareBool(pivot.attr("trade_date").ptr(), first_date.ptr(), Py_LT) == 1) {
                    first_date = pivot.attr("trade_date");
                }
                if (PyObject_RichCompareBool(pivot.attr("trade_date").ptr(), last_date.ptr(), Py_GT) == 1) {
                    last_date = pivot.attr("trade_date");
                }
            }
            const int previous_touches = matched.is_none() ? 0 : py::cast<int>(matched.attr("touch_count"));
            const py::object valid_from = matched.is_none()
                ? py::object(bar["dt_ny"])
                : py::object(matched.attr("valid_from"));
            const py::object last_inside = matched.is_none()
                ? py::object(py::bool_(false))
                : py::object(matched.attr("last_inside"));
            zone = service_type("Zone")(
                py::arg("zone_key") = key,
                py::arg("source_kind") = source_kind,
                py::arg("role") = role,
                py::arg("status") = "active",
                py::arg("center") = stored_center,
                py::arg("lower") = stored_zone_price(stored_center - stored_half_width),
                py::arg("upper") = stored_zone_price(stored_center + stored_half_width),
                py::arg("atr") = stored_zone_price(required_number(bar, "atr_14")),
                py::arg("pivot_keys") = pivot_keys,
                py::arg("pivot_count") = cluster.size(),
                py::arg("touch_count") = std::max(static_cast<int>(cluster.size()), previous_touches),
                py::arg("first_pivot_date") = first_date,
                py::arg("last_pivot_date") = last_date,
                py::arg("valid_from") = valid_from,
                py::arg("anchor_session_index") = current_index,
                py::arg("anchor_center") = stored_center,
                py::arg("anchor_lower") = stored_zone_price(stored_center - stored_half_width),
                py::arg("anchor_upper") = stored_zone_price(stored_center + stored_half_width),
                py::arg("slope_per_session") = stored_zone_price(slope),
                py::arg("fit_residual_atr") = stored_zone_price(py::cast<double>(fit_result["residual_atr"])),
                py::arg("recency_weight") = fit_result["total_weight"],
                py::arg("last_inside") = last_inside
            );
        }
        rebuilt[py::str(key)] = zone;
    }

    py::dict selected;
    for (const std::string& source_kind : {std::string("low"), std::string("high")}) {
        std::vector<py::object> zones;
        for (const auto& [raw_key, raw_zone] : rebuilt) {
            static_cast<void>(raw_key);
            py::object zone = py::reinterpret_borrow<py::object>(raw_zone);
            if (py::cast<std::string>(zone.attr("source_kind")) == source_kind) zones.push_back(zone);
        }
        std::sort(zones.begin(), zones.end(), [&bar](const py::object& left, const py::object& right) {
            return std::tuple(
                -py::cast<int>(left.attr("pivot_count")),
                -py::cast<double>(left.attr("recency_weight")),
                py::cast<double>(left.attr("fit_residual_atr")),
                std::abs(py::cast<double>(left.attr("center")) - required_number(bar, "close")),
                py::cast<std::string>(left.attr("zone_key"))
            ) < std::tuple(
                -py::cast<int>(right.attr("pivot_count")),
                -py::cast<double>(right.attr("recency_weight")),
                py::cast<double>(right.attr("fit_residual_atr")),
                std::abs(py::cast<double>(right.attr("center")) - required_number(bar, "close")),
                py::cast<std::string>(right.attr("zone_key"))
            );
        });
        if (!zones.empty()) selected[zones.front().attr("zone_key")] = zones.front();
    }
    py::list events = py::cast<py::list>(state.attr("events"));
    for (const auto& [raw_key, raw_zone] : old_zones) {
        const std::string key = py::cast<std::string>(raw_key);
        if (selected.contains(py::str(key))) continue;
        py::object old = py::reinterpret_borrow<py::object>(raw_zone);
        record_zone_version_native(state, old, bar["dt_ny"], "expired");
        py::dict event;
        event["event_date"] = iso_date(bar["dt_ny"]);
        event["event_type"] = "invalidation";
        event["zone_key"] = key;
        event["role"] = old.attr("role");
        events.append(event);
    }
    state.attr("zones") = selected;
    std::vector<py::object> sorted_selected;
    for (const auto& [raw_key, raw_zone] : selected) {
        static_cast<void>(raw_key);
        sorted_selected.push_back(py::reinterpret_borrow<py::object>(raw_zone));
    }
    std::sort(sorted_selected.begin(), sorted_selected.end(), [](const py::object& left, const py::object& right) {
        return py::cast<std::string>(left.attr("zone_key")) < py::cast<std::string>(right.attr("zone_key"));
    });
    for (const py::object& zone : sorted_selected) {
        record_zone_version_native(state, zone, bar["dt_ny"], "active");
    }
}

py::object advance_symbol_native(
    const py::object& state,
    const py::dict& snapshot,
    const py::dict& signal_cfg,
    const py::dict& risk_cfg,
    bool emit_signals
) {
    py::object normalized = normalize_bar(snapshot);
    if (normalized.is_none()) return py::none();
    py::dict bar = py::cast<py::dict>(normalized);
    py::list history = py::cast<py::list>(state.attr("history"));
    const int session_index = static_cast<int>(history.size());
    const bool has_cached_zones = py::len(state.attr("cached_zone_timeline")) > 0;
    if (has_cached_zones) activate_cached_zones(state, bar["dt_ny"]);

    std::vector<py::object> frozen_zones;
    py::dict retained;
    py::dict zones = py::cast<py::dict>(state.attr("zones"));
    py::dict breakouts = py::cast<py::dict>(state.attr("breakouts"));
    py::list events = py::cast<py::list>(state.attr("events"));
    for (const auto& [raw_key, raw_zone] : zones) {
        static_cast<void>(raw_key);
        py::object zone = py::reinterpret_borrow<py::object>(raw_zone);
        if (py::cast<std::string>(zone.attr("status")) != "active") continue;
        py::object projected = project_zone_object(zone, session_index);
        const std::string key = py::cast<std::string>(zone.attr("zone_key"));
        if (valid_zone_object(projected)) {
            frozen_zones.push_back(projected);
            retained[py::str(key)] = projected;
            continue;
        }
        pop_if_present(breakouts, key);
        if (!has_cached_zones) {
            py::object tombstone = copy_object(zone);
            tombstone.attr("status") = "expired";
            tombstone.attr("anchor_session_index") = session_index;
            tombstone.attr("anchor_center") = zone.attr("center");
            tombstone.attr("anchor_lower") = zone.attr("lower");
            tombstone.attr("anchor_upper") = zone.attr("upper");
            tombstone.attr("slope_per_session") = 0.0;
            record_zone_version_native(state, tombstone, bar["dt_ny"], "expired");
            py::dict event;
            event["event_date"] = iso_date(bar["dt_ny"]);
            event["event_type"] = "invalidation";
            event["zone_key"] = key;
            event["role"] = zone.attr("role");
            event["reason"] = "projected_zone_geometry_became_invalid";
            events.append(event);
        }
    }
    state.attr("zones") = retained;
    std::sort(frozen_zones.begin(), frozen_zones.end(), [](const py::object& left, const py::object& right) {
        return std::tuple(
            py::cast<std::string>(left.attr("role")),
            py::cast<double>(left.attr("center")),
            py::cast<std::string>(left.attr("zone_key"))
        ) < std::tuple(
            py::cast<std::string>(right.attr("role")),
            py::cast<double>(right.attr("center")),
            py::cast<std::string>(right.attr("zone_key"))
        );
    });
    py::list frozen_snapshots;
    for (const py::object& zone : frozen_zones) frozen_snapshots.append(zone_object_snapshot(zone));
    py::dict entry_channel = build_entry_channel(
        frozen_snapshots, required_number(bar, "close"), bar["dt_ny"]
    );
    record_entry_channel_transition(state, entry_channel, bar["dt_ny"]);

    std::string regime;
    py::dict regime_evidence;
    const bool has_cached_regimes = py::len(state.attr("cached_regime_timeline")) > 0;
    if (has_cached_regimes) {
        py::tuple resolved = activate_cached_regime(state, bar["dt_ny"]);
        regime = py::cast<std::string>(resolved[0]);
        regime_evidence = py::cast<py::dict>(resolved[1]);
    } else {
        py::list raw_frozen;
        for (const py::object& zone : frozen_zones) raw_frozen.append(zone);
        py::tuple resolved = classify_market_regime_native(state, raw_frozen, bar, signal_cfg);
        regime = py::cast<std::string>(resolved[0]);
        regime_evidence = py::cast<py::dict>(resolved[1]);
        record_regime_version_native(state, bar["dt_ny"], regime, regime_evidence);
    }

    const double position = finite_number(dict_value(snapshot, "position")).value_or(0.0);
    py::object exit_decision = position > 0.0
        ? resolve_exit(snapshot, bar, risk_cfg, regime, regime_evidence)
        : py::object(py::none());
    std::vector<py::dict> candidates = detect_candidates(
        state, bar, frozen_zones, session_index, signal_cfg, risk_cfg
    );
    apply_regime_entry_policy(
        state, candidates, regime, regime_evidence, entry_channel, bar["dt_ny"]
    );
    py::object selected = select_candidate(candidates);

    resolve_prior_outcomes(state, bar, session_index, signal_cfg);
    py::list pending = py::cast<py::list>(state.attr("pending_outcomes"));
    for (const py::dict& candidate : candidates) {
        py::dict event;
        event["event_date"] = iso_date(bar["dt_ny"]);
        event["event_type"] = "candidate";
        for (const auto& [key, value] : candidate) event[key] = value;
        events.append(event);
        const double atr = required_number(bar, "atr_14");
        pending.append(service_type("PendingOutcome")(
            py::arg("setup") = candidate["setup"],
            py::arg("zone_key") = candidate["zone_key"],
            py::arg("origin_date") = bar["dt_ny"],
            py::arg("origin_session_index") = session_index,
            py::arg("target") = required_number(bar, "close")
                + py::cast<double>(signal_cfg["score_target_atr"]) * atr,
            py::arg("stop") = required_number(bar, "close")
                - py::cast<double>(signal_cfg["score_stop_atr"]) * atr
        ));
    }
    if (!selected.is_none()) {
        py::dict candidate = py::cast<py::dict>(selected);
        py::list setups;
        for (const py::dict& item : candidates) setups.append(item["setup"]);
        py::dict event;
        event["event_date"] = iso_date(bar["dt_ny"]);
        event["event_type"] = "selection";
        event["zone_key"] = candidate["zone_key"];
        event["setup"] = candidate["setup"];
        event["score"] = candidate["score"];
        event["score_evidence"] = candidate["score_evidence"];
        event["zone"] = candidate["zone"];
        event["candidate_setups"] = setups;
        event["regime"] = regime;
        event["regime_evidence"] = regime_evidence;
        event["entry_channel"] = entry_channel;
        events.append(event);
    }

    apply_current_bar_zone_state(state, bar, session_index, signal_cfg);
    if (has_cached_zones) record_cached_lifecycle_events(state, bar["dt_ny"]);
    history.append(bar);
    if (!has_cached_zones) {
        confirm_pivots(state, signal_cfg);
        rebuild_zones_native(state, bar, signal_cfg);
    }

    if (!emit_signals) return py::none();
    if (!exit_decision.is_none()) return exit_decision;
    if (position > 0.0 || selected.is_none()) return py::none();
    py::dict candidate = py::cast<py::dict>(selected);
    if (!truthy(candidate["entry_eligible"])) return py::none();
    py::list setups;
    for (const py::dict& item : candidates) setups.append(item["setup"]);
    py::dict metadata;
    metadata["zone_key"] = candidate["zone_key"];
    metadata["selected_setup"] = candidate["setup"];
    metadata["candidate_setups"] = setups;
    metadata["zone"] = candidate["zone"];
    metadata["entry_atr"] = bar["atr_14"];
    metadata["entry_close"] = bar["close"];
    metadata["stop_price"] = candidate["stop_price"];
    metadata["target_price"] = candidate["target_price"];
    metadata["reward_risk"] = candidate["reward_risk"];
    metadata["strength"] = candidate["strength"];
    metadata["score_evidence"] = candidate["score_evidence"];
    py::list raw_candidates;
    for (const py::dict& item : candidates) raw_candidates.append(item);
    metadata["candidates"] = raw_candidates;
    metadata["regime"] = regime;
    metadata["regime_evidence"] = regime_evidence;
    metadata["entry_channel"] = entry_channel;
    metadata["price_semantics"] = "forward_adjusted_preferred_unadjusted_fallback";
    py::dict decision;
    decision["action"] = "BUY";
    decision["reason"] = candidate["reason"];
    decision["score"] = candidate["score"];
    decision["support_resistance"] = metadata;
    return std::move(decision);
}

}  // namespace

py::list evaluate_support_resistance_day(const py::dict& runtime, const py::dict& market) {
    const py::dict params = py::cast<py::dict>(runtime["params"]);
    const py::dict signal_cfg = py::cast<py::dict>(params["signal"]);
    const py::dict risk_cfg = py::cast<py::dict>(params["risk"]);
    const py::dict universe_cfg = py::cast<py::dict>(params["universe"]);

    std::vector<std::string> universe;
    const py::object configured_symbols = dict_value(universe_cfg, "symbols", py::list());
    const std::string selection_mode = py::cast<std::string>(
        dict_value(universe_cfg, "selection_mode", py::str("explicit"))
    );
    if (selection_mode == "all_common_stock" && !truthy(configured_symbols)) {
        for (const auto& [raw_symbol, raw_snapshot] : market) {
            const py::dict snapshot = py::cast<py::dict>(raw_snapshot);
            std::string asset_type = py::cast<std::string>(
                py::module_::import("builtins").attr("str")(
                    dict_value(snapshot, "asset_type", py::str(""))
                )
            );
            std::transform(asset_type.begin(), asset_type.end(), asset_type.begin(), [](unsigned char value) {
                return static_cast<char>(std::toupper(value));
            });
            if (asset_type == "CS") universe.push_back(py::cast<std::string>(raw_symbol));
        }
        std::sort(universe.begin(), universe.end());
    } else if (truthy(configured_symbols)) {
        for (const py::handle symbol : py::reinterpret_borrow<py::iterable>(configured_symbols)) {
            universe.push_back(py::cast<std::string>(symbol));
        }
    } else {
        for (const auto& [raw_symbol, raw_snapshot] : market) {
            static_cast<void>(raw_snapshot);
            universe.push_back(py::cast<std::string>(raw_symbol));
        }
        std::sort(universe.begin(), universe.end());
    }

    py::list signals;
    const py::object state_type = service_type("SupportResistanceSymbolState");
    const py::module_ builtins = py::module_::import("builtins");
    for (const std::string& symbol : universe) {
        if (!market.contains(py::str(symbol))) continue;
        const py::dict snapshot = py::cast<py::dict>(market[py::str(symbol)]);
        if (!truthy(snapshot)) continue;

        const py::object state = state_type();
        py::list history;
        const py::object recent_bars = dict_value(snapshot, "recent_bars", py::list());
        if (truthy(recent_bars)) history = builtins.attr("list")(recent_bars).cast<py::list>();
        const py::object snapshot_date = dict_value(snapshot, "dt_ny");
        bool append_snapshot = history.empty();
        if (!append_snapshot) {
            const py::dict last_bar = py::cast<py::dict>(history[history.size() - 1U]);
            const py::object last_date = dict_value(last_bar, "dt_ny");
            const int differs = PyObject_RichCompareBool(last_date.ptr(), snapshot_date.ptr(), Py_NE);
            if (differs < 0) throw py::error_already_set();
            append_snapshot = differs == 1;
        }
        if (append_snapshot) history.append(snapshot);

        py::object decision = py::none();
        for (py::ssize_t index = 0; index < history.size(); ++index) {
            py::dict replay_snapshot = builtins.attr("dict")(history[index]).cast<py::dict>();
            const bool is_last = index == history.size() - 1;
            if (is_last) {
                for (const char* key : {
                    "position", "avg_entry_price", "position_holding_days", "entry_signal_features"
                }) {
                    replay_snapshot[py::str(key)] = dict_value(snapshot, key);
                }
            }
            decision = advance_symbol_native(
                state, replay_snapshot, signal_cfg, risk_cfg, is_last
            );
        }
        if (decision.is_none()) continue;

        const py::dict raw_decision = py::cast<py::dict>(decision);
        py::object timestamp = dict_value(snapshot, "ts");
        if (!truthy(timestamp)) {
            const py::module_ datetime_module = py::module_::import("datetime");
            timestamp = datetime_module.attr("datetime").attr("now")(
                datetime_module.attr("timezone").attr("utc")
            );
        }
        py::object average_entry = dict_value(snapshot, "avg_entry_price");
        if (!average_entry.is_none()) average_entry = builtins.attr("float")(average_entry);
        py::object position = builtins.attr("float")(
            truthy(dict_value(snapshot, "position"))
                ? dict_value(snapshot, "position")
                : py::object(py::float_(0.0))
        );
        py::dict metadata;
        for (const char* key : {"close", "open", "high", "low", "atr_14"}) {
            metadata[py::str(key)] = dict_value(snapshot, key);
        }
        metadata["position"] = position;
        metadata["avg_entry_price"] = average_entry;
        metadata["support_resistance"] = raw_decision["support_resistance"];

        py::dict event;
        event["strategy_id"] = runtime["strategy_id"];
        event["ts"] = timestamp;
        event["symbol"] = symbol;
        event["action"] = raw_decision["action"];
        event["reason"] = raw_decision["reason"];
        event["score"] = dict_value(raw_decision, "score");
        event["metadata"] = metadata;
        event["instrument_id"] = py::none();
        signals.append(std::move(event));
    }
    return signals;
}

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
    support_resistance.def(
        "classify_market_regime",
        &classify_market_regime_native,
        py::arg("state"),
        py::arg("zones"),
        py::arg("bar"),
        py::arg("signal_cfg")
    );
    support_resistance.def(
        "record_regime_version",
        &record_regime_version_native,
        py::arg("state"),
        py::arg("effective_from"),
        py::arg("regime"),
        py::arg("evidence")
    );
    support_resistance.def(
        "advance_symbol",
        &advance_symbol_native,
        py::arg("state"),
        py::arg("snapshot"),
        py::arg("signal_cfg"),
        py::arg("risk_cfg"),
        py::arg("emit_signals") = true
    );
    support_resistance.def(
        "rebuild_zones",
        &rebuild_zones_native,
        py::arg("state"),
        py::arg("bar"),
        py::arg("signal_cfg")
    );
    support_resistance.def(
        "match_zone",
        &match_zone_native,
        py::arg("old_zones"),
        py::arg("source_kind"),
        py::arg("center"),
        py::arg("half_width"),
        py::arg("pivot_keys")
    );
    support_resistance.def(
        "record_zone_version",
        &record_zone_version_native,
        py::arg("state"),
        py::arg("zone"),
        py::arg("effective_date"),
        py::arg("status")
    );
}

}  // namespace quant_kernel
