#include "pattern_core.hpp"

#include "native_utils.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <type_traits>

namespace quant_kernel {
namespace {

constexpr std::int64_t kUnixEpochOrdinal = 719163;

const PatternObject& payload_fields(const PatternSetup& setup) {
    return std::visit([](const auto& payload) -> const PatternObject& { return payload.fields; }, setup.payload);
}

PatternObject& payload_fields(PatternSetup& setup) {
    return std::visit([](auto& payload) -> PatternObject& { return payload.fields; }, setup.payload);
}

std::string iso_date(std::int64_t ordinal) {
    const std::chrono::sys_days day{std::chrono::days{ordinal - kUnixEpochOrdinal}};
    const std::chrono::year_month_day value{day};
    std::ostringstream output;
    output << std::setfill('0') << std::setw(4) << static_cast<int>(value.year()) << '-'
           << std::setw(2) << static_cast<unsigned>(value.month()) << '-'
           << std::setw(2) << static_cast<unsigned>(value.day());
    return output.str();
}

std::string upper(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
        return static_cast<char>(std::toupper(character));
    });
    return value;
}

double unit(double value) {
    return std::round(std::clamp(value, 0.0, 1.0) * 1'000'000.0) / 1'000'000.0;
}

std::optional<double> volume_ratio(const PatternBar& bar) {
    if (!bar.volume || !bar.volume_sma_20 || *bar.volume_sma_20 <= 0.0) return std::nullopt;
    return *bar.volume / *bar.volume_sma_20;
}

PatternValue pattern_value(std::optional<double> value) {
    return value ? PatternValue(*value) : PatternValue{};
}

std::string setup_identity(
    const std::string& type,
    const std::string& symbol,
    const std::vector<std::string>& anchors
) {
    const std::string normalized_symbol = upper(symbol);
    std::string canonical = "[" + json_string(type) + "," + json_string(normalized_symbol);
    for (const std::string& anchor : anchors) canonical += "," + json_string(anchor);
    canonical += "]";
    return type + ":" + normalized_symbol + ":" + sha256_hex(canonical).substr(0U, 16U);
}

PatternSetupPayload empty_payload(PatternKind kind) {
    switch (kind) {
        case PatternKind::IslandReversal: return IslandSetupPayload{};
        case PatternKind::DoubleBottom: return DoubleBottomSetupPayload{};
        case PatternKind::HeadShouldersBottom: return HeadShouldersSetupPayload{};
        case PatternKind::RoundedBottom: return RoundedBottomSetupPayload{};
        case PatternKind::VReversal: return VReversalSetupPayload{};
    }
    throw std::invalid_argument("unsupported pattern kind");
}

PatternSetup build_setup(
    const PatternConfig& config,
    const std::string& symbol,
    int stage_index,
    std::string stage_key,
    PatternObject anchors,
    std::optional<double> invalidation_price,
    const std::vector<std::string>& id_anchors,
    PatternObject fields
) {
    PatternSetup setup{
        config.strategy_type,
        setup_identity(config.strategy_type, symbol, id_anchors),
        stage_index,
        std::move(stage_key),
        config.risk.stage_targets[stage_index - 1],
        std::move(anchors),
        invalidation_price,
        std::nullopt,
        empty_payload(config.kind),
    };
    payload_fields(setup) = std::move(fields);
    return setup;
}

PatternDecision buy_decision(
    std::string reason,
    PatternSetup setup,
    double structure,
    double price,
    double volume,
    double stage,
    std::optional<double> score = std::nullopt
) {
    PatternObject strength{
        {"price_confirmation", unit(price)},
        {"stage_confirmation", unit(stage)},
        {"structure_quality", unit(structure)},
        {"volume_quality", unit(volume)},
    };
    return {true, std::move(reason), std::move(setup), score, std::move(strength)};
}

std::optional<double> recent_atr(const std::vector<PatternBar>& bars, int end, int window = 20) {
    std::vector<double> ranges;
    for (int index = 0; index < end; ++index) {
        const PatternBar& bar = bars[static_cast<std::size_t>(index)];
        if (!bar.high || !bar.low) continue;
        double range = *bar.high - *bar.low;
        if (index > 0) {
            const auto previous = bars[static_cast<std::size_t>(index - 1)].close;
            if (previous) range = std::max({range, std::abs(*bar.high - *previous), std::abs(*bar.low - *previous)});
        }
        ranges.push_back(range);
    }
    if (static_cast<int>(ranges.size()) < window) return std::nullopt;
    double total = 0.0;
    for (int index = static_cast<int>(ranges.size()) - window; index < static_cast<int>(ranges.size()); ++index) {
        total += ranges[static_cast<std::size_t>(index)];
    }
    return total / window;
}

std::vector<int> confirmed_pivot_lows(const std::vector<PatternBar>& bars, int left, int right) {
    std::vector<int> pivots;
    for (int index = left; index < static_cast<int>(bars.size()) - right; ++index) {
        const auto low = bars[static_cast<std::size_t>(index)].low;
        if (!low) continue;
        bool all = true;
        bool any = false;
        for (int position = index - left; position <= index + right; ++position) {
            if (position == index) continue;
            const auto neighbor = bars[static_cast<std::size_t>(position)].low;
            if (!neighbor || *low > *neighbor) all = false;
            if (neighbor && *low < *neighbor) any = true;
        }
        if (all && any) pivots.push_back(index);
    }
    return pivots;
}

bool downtrend_context(const std::vector<PatternBar>& bars, int index, int lookback, double minimum_drop) {
    const auto current = bars[static_cast<std::size_t>(index)].close;
    if (!current) return false;
    std::optional<double> maximum;
    for (int position = std::max(0, index - lookback); position < index; ++position) {
        const auto close = bars[static_cast<std::size_t>(position)].close;
        if (close) maximum = maximum ? std::max(*maximum, *close) : *close;
    }
    return maximum && (*maximum - *current) / *maximum >= minimum_drop;
}

std::optional<int> highest_index(const std::vector<PatternBar>& bars, int start, int end) {
    std::optional<int> selected;
    std::optional<double> maximum;
    for (int index = start; index <= end; ++index) {
        const auto high = bars[static_cast<std::size_t>(index)].high;
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
    xs.reserve(values.size());
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

std::optional<PatternDecision> common_position_exit(
    const PatternConfig& config,
    const PatternState& state,
    const PatternPositionView& position
) {
    if (position.quantity <= 0.0 || state.bars.empty()) return std::nullopt;
    PatternSetup setup = position.setup ? *position.setup : PatternSetup{
        config.strategy_type,
        config.strategy_type + ":position:position",
        3,
        "position",
        1.0,
        {},
        std::nullopt,
        std::nullopt,
        empty_payload(config.kind),
    };
    const PatternBar& current = state.bars.back();
    std::optional<std::string> reason;
    std::optional<std::string> stage;
    if (setup.invalidation_price && current.low && *current.low < *setup.invalidation_price) {
        reason = "pattern invalidation price was breached";
        stage = "pattern_invalidation";
    } else if (current.close && position.average_entry_price && *position.average_entry_price > 0.0
        && *current.close <= *position.average_entry_price * (1.0 - config.risk.max_loss_pct)) {
        reason = "price fell through the configured maximum loss";
        stage = "max_loss_stop";
    } else if (current.close && current.atr_14 && position.average_entry_price
        && *current.close <= *position.average_entry_price - config.risk.stop_loss_atr * *current.atr_14) {
        reason = "price hit the ATR stop";
        stage = "atr_stop";
    } else if (current.close && current.atr_14 && position.average_entry_price
        && *current.close >= *position.average_entry_price + config.risk.take_profit_atr * *current.atr_14) {
        reason = "price reached the ATR take-profit target";
        stage = "take_profit";
    } else if (config.kind == PatternKind::VReversal) {
        const auto& signal = std::get<VReversalConfig>(config.signal);
        const auto ratio = volume_ratio(current);
        if (setup.stage_index < 3 && current.close && current.open && *current.close < *current.open
            && ratio && *ratio >= signal.bearish_reversal_volume_ratio_min) {
            reason = "high-volume bearish reversal invalidated the V setup";
            stage = "bearish_volume_failure";
        }
    }
    if (!reason) return std::nullopt;
    setup.exit_stage = stage;
    return PatternDecision{
        false,
        *reason,
        std::move(setup),
        std::nullopt,
        {
            {"price_confirmation", 1.0},
            {"stage_confirmation", 1.0},
            {"structure_quality", 1.0},
            {"volume_quality", 1.0},
        },
    };
}

bool local_minimum(const std::vector<PatternBar>& bars, int index, int before, int after) {
    if (index - before < 0 || index + after >= static_cast<int>(bars.size())) return false;
    const auto low = bars[static_cast<std::size_t>(index)].low;
    if (!low) return false;
    for (int neighbor = index - before; neighbor <= index + after; ++neighbor) {
        if (neighbor == index) continue;
        const auto value = bars[static_cast<std::size_t>(neighbor)].low;
        if (value && *value < *low) return false;
    }
    return true;
}

std::optional<double> up_day_ratio(const std::vector<PatternBar>& bars, int start, int end) {
    if (end <= start || !bars[static_cast<std::size_t>(start)].close) return std::nullopt;
    double previous = *bars[static_cast<std::size_t>(start)].close;
    int up_days = 0;
    int directional = 0;
    for (int index = start + 1; index <= end; ++index) {
        const auto close = bars[static_cast<std::size_t>(index)].close;
        if (!close) continue;
        if (*close > previous) { ++up_days; ++directional; }
        else if (*close < previous) { ++directional; }
        previous = *close;
    }
    return directional == 0 ? std::nullopt : std::optional<double>(static_cast<double>(up_days) / directional);
}

std::optional<std::pair<double, double>> linear_fit(const std::vector<double>& values) {
    if (values.size() < 2U) return std::nullopt;
    const double count = static_cast<double>(values.size());
    const double x_mean = (count - 1.0) / 2.0;
    double y_mean = 0.0;
    for (double value : values) y_mean += value;
    y_mean /= count;
    double squared_x = 0.0, xy = 0.0, total = 0.0;
    for (std::size_t index = 0; index < values.size(); ++index) {
        const double x_delta = static_cast<double>(index) - x_mean;
        const double y_delta = values[index] - y_mean;
        squared_x += x_delta * x_delta;
        xy += x_delta * y_delta;
        total += y_delta * y_delta;
    }
    if (squared_x <= 0.0) return std::nullopt;
    const double slope = xy / squared_x;
    const double intercept = y_mean - slope * x_mean;
    if (total <= 0.0) return std::pair{slope, 1.0};
    double residual = 0.0;
    for (std::size_t index = 0; index < values.size(); ++index) {
        residual += std::pow(values[index] - (intercept + slope * index), 2.0);
    }
    return std::pair{slope, std::clamp(1.0 - residual / total, 0.0, 1.0)};
}

bool smooth_downtrend(const std::vector<PatternBar>& bars, int bottom, const DoubleBottomConfig& config) {
    const int anchor = bottom - config.downtrend_lookback;
    if (anchor < 0) return false;
    const auto ratio = up_day_ratio(bars, anchor, bottom);
    if (!ratio || *ratio > config.downtrend_max_up_day_ratio) return false;
    std::vector<double> closes;
    for (int index = anchor; index <= bottom; ++index) {
        const auto close = bars[static_cast<std::size_t>(index)].close;
        if (!close || *close <= 0.0) return false;
        closes.push_back(*close);
    }
    const auto fit = linear_fit(closes);
    return fit && fit->first < 0.0 && fit->second >= config.downtrend_min_r_squared;
}

bool double_bottom_downtrend(const std::vector<PatternBar>& bars, int bottom, const DoubleBottomConfig& config) {
    const int anchor = bottom - config.downtrend_lookback;
    if (anchor < 0 || !bars[static_cast<std::size_t>(bottom)].close
        || !bars[static_cast<std::size_t>(anchor)].close || *bars[static_cast<std::size_t>(anchor)].close <= 0.0) return false;
    return *bars[static_cast<std::size_t>(bottom)].close / *bars[static_cast<std::size_t>(anchor)].close - 1.0
        <= -config.downtrend_min_drop_pct;
}

std::optional<DoubleBottomLeftCandidate> build_double_bottom_left(
    const std::vector<PatternBar>& bars,
    int current,
    const DoubleBottomConfig& config
) {
    const int index = current - config.left_bottom_after_bars;
    if (index < config.left_bottom_before_bars
        || !local_minimum(bars, index, config.left_bottom_before_bars, config.left_bottom_after_bars)) return std::nullopt;
    const PatternBar& bar = bars[static_cast<std::size_t>(index)];
    const auto ratio = volume_ratio(bar);
    if (!bar.low || *bar.low <= 0.0 || !ratio || *ratio > config.second_bottom_volume_ratio_max
        || !double_bottom_downtrend(bars, index, config) || !smooth_downtrend(bars, index, config)) return std::nullopt;
    return DoubleBottomLeftCandidate{index, *bar.low};
}

std::optional<DoubleBottomRightCandidate> build_double_bottom_right(
    const std::vector<PatternBar>& bars,
    const DoubleBottomLeftCandidate& left,
    int right_index,
    const DoubleBottomConfig& config
) {
    const PatternBar& right = bars[static_cast<std::size_t>(right_index)];
    const auto right_ratio = volume_ratio(right);
    if (!right.low || !right_ratio || *right_ratio > config.second_bottom_volume_ratio_max) return std::nullopt;
    const double distance = std::abs(*right.low - left.left_low) / std::max(*right.low, left.left_low);
    if (distance > config.bottom_tolerance_pct
        || *right.low < left.left_low * (1.0 - config.bottom_tolerance_pct)) return std::nullopt;
    const double floor = std::min(left.left_low, *right.low);
    if (right_index - left.left_index <= 1) return std::nullopt;
    for (int index = left.left_index + 1; index < right_index; ++index) {
        const auto low = bars[static_cast<std::size_t>(index)].low;
        if (!low || *low <= floor) return std::nullopt;
    }
    const auto neckline_index = highest_index(bars, left.left_index + 1, right_index - 1);
    if (!neckline_index || !bars[static_cast<std::size_t>(*neckline_index)].high) return std::nullopt;
    const double neckline = *bars[static_cast<std::size_t>(*neckline_index)].high;
    if (neckline < std::max(left.left_low, *right.low) * (1.0 + config.neckline_min_rebound_pct)) return std::nullopt;
    const auto rebound = up_day_ratio(bars, left.left_index, right_index);
    if (!rebound || *rebound < config.rebound_up_day_ratio_min) return std::nullopt;
    return DoubleBottomRightCandidate{
        left.left_index, *neckline_index, right_index, left.left_low, *right.low,
        neckline, distance, *rebound
    };
}

std::optional<DoubleBottomPattern> build_double_bottom_pattern(
    const std::vector<PatternBar>& bars,
    const DoubleBottomRightCandidate& candidate,
    int breakout_index,
    const DoubleBottomConfig& config
) {
    if (breakout_index <= candidate.right_index) return std::nullopt;
    const PatternBar& bar = bars[static_cast<std::size_t>(breakout_index)];
    const auto ratio = volume_ratio(bar);
    const double threshold = candidate.neckline * (1.0 + config.breakout_buffer_pct);
    if (!bar.high || !bar.close || !bar.volume || !ratio || *bar.high <= threshold
        || *ratio < config.breakout_volume_ratio_min) return std::nullopt;
    return DoubleBottomPattern{
        candidate.left_index, candidate.neckline_index, candidate.right_index, breakout_index,
        candidate.left_low, candidate.right_low, candidate.neckline, *bar.close, *bar.volume,
        *ratio, candidate.distance, candidate.rebound_ratio
    };
}

void advance_double_bottom(PatternState& state, const DoubleBottomConfig& config) {
    const int count = static_cast<int>(state.bars.size());
    if (count < 2) return;
    const int current = count - 1;
    const auto left = build_double_bottom_left(state.bars, current, config);
    if (left && std::none_of(state.double_bottom_left.begin(), state.double_bottom_left.end(), [&](const auto& value) {
            return value.left_index == left->left_index;
        })) state.double_bottom_left.push_back(*left);
    std::erase_if(state.double_bottom_left, [&](const auto& value) {
        return current > value.left_index + config.max_bottom_spacing + 1;
    });
    const int right_index = current - config.left_bottom_after_bars;
    if (right_index >= 0 && local_minimum(
            state.bars, right_index, config.left_bottom_before_bars, config.left_bottom_after_bars)) {
        for (const auto& candidate : state.double_bottom_left) {
            const int spacing = right_index - candidate.left_index;
            if (spacing < config.min_bottom_spacing || spacing > config.max_bottom_spacing) continue;
            const auto right = build_double_bottom_right(state.bars, candidate, right_index, config);
            if (!right) continue;
            const bool exists = std::any_of(state.double_bottom_right.begin(), state.double_bottom_right.end(), [&](const auto& value) {
                return value.left_index == right->left_index && value.right_index == right->right_index;
            });
            if (!exists) state.double_bottom_right.push_back(*right);
        }
    }
    std::vector<DoubleBottomRightCandidate> active;
    for (const auto& candidate : state.double_bottom_right) {
        if (current > candidate.right_index + config.max_breakout_bars_after_right_bottom) continue;
        const auto pattern = build_double_bottom_pattern(state.bars, candidate, current, config);
        if (!pattern) {
            active.push_back(candidate);
        } else if (!state.double_bottom_best
            || pattern->breakout_index > state.double_bottom_best->breakout_index
            || (pattern->breakout_index == state.double_bottom_best->breakout_index
                && pattern->right_index > state.double_bottom_best->right_index)) {
            state.double_bottom_best = pattern;
        }
    }
    state.double_bottom_right = std::move(active);
}

void prune_double_bottom(PatternState& state, int history_limit) {
    if (static_cast<int>(state.bars.size()) <= history_limit) return;
    state.bars.erase(state.bars.begin());
    for (auto& candidate : state.double_bottom_left) --candidate.left_index;
    std::erase_if(state.double_bottom_left, [](const auto& candidate) {
        return candidate.left_index < 0;
    });
    for (auto& candidate : state.double_bottom_right) {
        --candidate.left_index;
        --candidate.neckline_index;
        --candidate.right_index;
    }
    std::erase_if(state.double_bottom_right, [](const auto& candidate) {
        return candidate.left_index < 0 || candidate.neckline_index < 0
            || candidate.right_index < 0;
    });
    if (state.double_bottom_best) {
        --state.double_bottom_best->left_index;
        --state.double_bottom_best->neckline_index;
        --state.double_bottom_best->right_index;
        --state.double_bottom_best->breakout_index;
        if (state.double_bottom_best->left_index < 0) state.double_bottom_best.reset();
    }
}

bool double_bottom_right_pullback(
    const std::vector<PatternBar>& bars,
    const DoubleBottomRightCandidate& candidate,
    const DoubleBottomConfig& config
) {
    const int current = static_cast<int>(bars.size()) - 1;
    const int start = candidate.right_index + config.left_bottom_after_bars + 1;
    if (current < start + 1) return false;
    const double halfway = candidate.right_low + (candidate.neckline - candidate.right_low) * 0.5;
    double maximum_close = 0.0;
    for (int index = start; index < current; ++index) {
        maximum_close = std::max(maximum_close, bars[static_cast<std::size_t>(index)].close.value_or(0.0));
    }
    if (maximum_close < halfway) return false;
    const auto qualifies = [&](int index) {
        const PatternBar& bar = bars[static_cast<std::size_t>(index)];
        const auto previous_close = bars[static_cast<std::size_t>(index - 1)].close;
        const auto ratio = volume_ratio(bar);
        return bar.close && bar.low && previous_close && ratio
            && *bar.close < *previous_close && *bar.low > candidate.right_low
            && *bar.close > candidate.right_low && *ratio <= config.second_bottom_volume_ratio_max;
    };
    if (!qualifies(current)) return false;
    for (int index = start; index < current; ++index) if (qualifies(index)) return false;
    return true;
}

PatternSetup double_bottom_setup(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const DoubleBottomRightCandidate& candidate,
    int stage_index,
    const std::string& stage_key,
    const std::optional<DoubleBottomPattern>& pattern = std::nullopt
) {
    const auto& signal = std::get<DoubleBottomConfig>(config.signal);
    const std::string left_date = iso_date(state.bars[static_cast<std::size_t>(candidate.left_index)].date_ordinal);
    const std::string neckline_date = iso_date(state.bars[static_cast<std::size_t>(candidate.neckline_index)].date_ordinal);
    const std::string right_date = iso_date(state.bars[static_cast<std::size_t>(candidate.right_index)].date_ordinal);
    PatternObject anchors{
        {"left_bottom_price", candidate.left_low},
        {"left_bottom_trade_date", left_date},
        {"neckline_price", candidate.neckline},
        {"right_bottom_price", candidate.right_low},
        {"right_bottom_trade_date", right_date},
    };
    PatternObject fields{
        {"bottom_distance_pct", candidate.distance},
        {"left_bottom_low", candidate.left_low},
        {"left_bottom_trade_date", left_date},
        {"neckline_price", candidate.neckline},
        {"neckline_trade_date", neckline_date},
        {"rebound_up_day_ratio", candidate.rebound_ratio},
        {"right_bottom_low", candidate.right_low},
        {"right_bottom_trade_date", right_date},
    };
    if (pattern) {
        fields["breakout_atr"] = pattern_value(recent_atr(state.bars, pattern->breakout_index + 1));
        fields["breakout_close"] = pattern->breakout_close;
        fields["breakout_trade_date"] = iso_date(state.bars[static_cast<std::size_t>(pattern->breakout_index)].date_ordinal);
        fields["breakout_volume"] = pattern->breakout_volume;
        fields["breakout_volume_ratio"] = pattern->breakout_volume_ratio;
        fields["breakout_wait_bars"] = static_cast<std::int64_t>(pattern->breakout_index - pattern->right_index);
    }
    return build_setup(
        config, symbol, stage_index, stage_key, std::move(anchors),
        std::min(candidate.left_low, candidate.right_low) * (1.0 - signal.support_tolerance_pct),
        {left_date, right_date}, std::move(fields)
    );
}

PatternObject double_bottom_strength(const PatternBar& current, const PatternSetup& setup) {
    const PatternObject& fields = payload_fields(setup);
    const auto neckline = pattern_number(fields, "neckline_price");
    const auto breakout_close = pattern_number(fields, "breakout_close");
    const auto breakout_volume = pattern_number(fields, "breakout_volume");
    const auto right_low = pattern_number(fields, "right_bottom_low");
    PatternObject result{
        {"bottom_distance_pct", fields.contains("bottom_distance_pct") ? fields.at("bottom_distance_pct") : PatternValue{}},
        {"breakout_volume_ratio", fields.contains("breakout_volume_ratio") ? fields.at("breakout_volume_ratio") : PatternValue{}},
        {"rebound_up_day_ratio", fields.contains("rebound_up_day_ratio") ? fields.at("rebound_up_day_ratio") : PatternValue{}},
        {"stage", setup.stage_key},
    };
    result["current_volume_ratio"] = pattern_value(volume_ratio(current));
    result["pullback_hold_pct"] = current.close && right_low && neckline
        ? PatternValue((*current.close - *right_low) / std::max(*neckline - *right_low, 1e-12))
        : PatternValue{};
    result["breakout_extension_pct"] = breakout_close && neckline && *neckline > 0.0
        ? PatternValue(*breakout_close / *neckline - 1.0) : PatternValue{};
    result["retest_volume_ratio"] = current.volume && breakout_volume && *breakout_volume > 0.0
        ? PatternValue(*current.volume / *breakout_volume) : PatternValue{};
    return result;
}

double double_bottom_score(const PatternSetup& setup) {
    const PatternObject& fields = payload_fields(setup);
    const double distance = pattern_number(fields, "bottom_distance_pct").value_or(1.0);
    const double volume = pattern_number(fields, "breakout_volume_ratio").value_or(0.0);
    const double rebound = pattern_number(fields, "rebound_up_day_ratio").value_or(0.0);
    return (1.0 - distance) * 100.0 + volume + rebound * 10.0;
}

std::optional<PatternDecision> evaluate_double_bottom(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
) {
    if (state.bars.empty()) return std::nullopt;
    const auto& signal = std::get<DoubleBottomConfig>(config.signal);
    PatternSetup setup = position.setup ? *position.setup : PatternSetup{};
    bool has_setup = position.setup != nullptr;
    if (position.quantity > 0.0 && !has_setup && state.double_bottom_best) {
        const auto& pattern = *state.double_bottom_best;
        const DoubleBottomRightCandidate candidate{
            pattern.left_index, pattern.neckline_index, pattern.right_index,
            pattern.left_low, pattern.right_low, pattern.neckline, pattern.distance, pattern.rebound_ratio,
        };
        setup = double_bottom_setup(config, symbol, state, candidate, 3, "neckline_breakout", pattern);
        has_setup = true;
    }
    if (position.quantity > 0.0 && has_setup) {
        const PatternBar& current = state.bars.back();
        const PatternObject& fields = payload_fields(setup);
        const auto left_low = pattern_number(fields, "left_bottom_low");
        const auto right_low = pattern_number(fields, "right_bottom_low");
        const auto breakout_close = pattern_number(fields, "breakout_close");
        const auto breakout_atr = pattern_number(fields, "breakout_atr");
        std::optional<std::string> reason;
        std::optional<std::string> stage;
        if (right_low && current.close && *current.close < *right_low) {
            reason = "price closed below the right bottom after confirmation"; stage = "right_bottom_break";
        } else if (current.close && position.average_entry_price && *position.average_entry_price > 0.0
            && *current.close <= *position.average_entry_price * (1.0 - config.risk.max_loss_pct)) {
            reason = "price fell more than the configured max-loss threshold from entry"; stage = "max_loss_stop";
        } else if (left_low && right_low && current.low
            && *current.low < std::min(*left_low, *right_low) * (1.0 - signal.support_tolerance_pct)) {
            reason = "price broke below the double-bottom base"; stage = "base_break";
        } else if (current.close && breakout_close && breakout_atr
            && *current.close >= *breakout_close + config.risk.take_profit_atr * *breakout_atr) {
            reason = "price reached the ATR take-profit target from the breakout confirmation"; stage = "take_profit";
        } else {
            const auto current_atr = recent_atr(state.bars, static_cast<int>(state.bars.size()));
            const auto stop_anchor = breakout_close ? breakout_close : position.average_entry_price;
            if (current.close && current_atr && stop_anchor
                && *current.close < *stop_anchor - config.risk.stop_loss_atr * *current_atr) {
                reason = "price hit the ATR stop from the breakout confirmation"; stage = "atr_stop";
            }
        }
        if (reason) {
            setup.exit_stage = stage;
            return PatternDecision{
                false, *reason, setup, double_bottom_score(setup),
                double_bottom_strength(state.bars.back(), setup),
            };
        }
    }

    const int current = static_cast<int>(state.bars.size()) - 1;
    if (state.double_bottom_best && current == state.double_bottom_best->breakout_index) {
        const auto& pattern = *state.double_bottom_best;
        const DoubleBottomRightCandidate candidate{
            pattern.left_index, pattern.neckline_index, pattern.right_index,
            pattern.left_low, pattern.right_low, pattern.neckline, pattern.distance, pattern.rebound_ratio,
        };
        setup = double_bottom_setup(config, symbol, state, candidate, 3, "neckline_breakout", pattern);
        return PatternDecision{
            true, "broke above the double-bottom neckline on confirming volume", setup,
            double_bottom_score(setup), double_bottom_strength(state.bars.back(), setup),
        };
    }
    if (state.double_bottom_right.empty()) return std::nullopt;
    const auto& candidate = state.double_bottom_right.back();
    if (current == candidate.right_index + signal.left_bottom_after_bars) {
        setup = double_bottom_setup(config, symbol, state, candidate, 1, "second_bottom");
        return PatternDecision{
            true, "confirmed a low-volume second bottom", setup,
            double_bottom_score(setup), double_bottom_strength(state.bars.back(), setup),
        };
    }
    if (!double_bottom_right_pullback(state.bars, candidate, signal)) return std::nullopt;
    setup = double_bottom_setup(config, symbol, state, candidate, 2, "right_side_pullback");
    return PatternDecision{
        true, "confirmed the first low-volume right-side pullback above the second bottom", setup,
        double_bottom_score(setup), double_bottom_strength(state.bars.back(), setup),
    };
}

std::optional<PatternDecision> evaluate_head_shoulders(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
) {
    if (auto exit = common_position_exit(config, state, position)) return exit;
    const auto& signal = std::get<HeadShouldersConfig>(config.signal);
    const auto& bars = state.bars;
    const auto pivots = confirmed_pivot_lows(bars, signal.pivot_left_bars, signal.pivot_right_bars);
    if (pivots.size() < 2U) return std::nullopt;
    const int current = static_cast<int>(bars.size()) - 1;
    for (int offset = static_cast<int>(pivots.size()) - 2; offset >= 0; --offset) {
        const int left = pivots[static_cast<std::size_t>(offset)];
        const int head = pivots[static_cast<std::size_t>(offset + 1)];
        const int gap = head - left;
        const auto left_low = bars[static_cast<std::size_t>(left)].low;
        const auto head_low = bars[static_cast<std::size_t>(head)].low;
        if (gap < signal.min_segment_bars || gap > signal.max_segment_bars || !left_low || !head_low
            || *head_low > *left_low * (1.0 - signal.head_depth_min_pct)
            || !downtrend_context(bars, left, signal.downtrend_lookback, signal.downtrend_min_drop_pct)) continue;
        const auto head_volume = volume_ratio(bars[static_cast<std::size_t>(head)]);
        if (!head_volume || *head_volume > signal.head_volume_ratio_max) continue;
        if (current != head + signal.pivot_right_bars) continue;
        const double quality = std::min(
            (*left_low - *head_low) / std::max(*left_low * signal.head_depth_min_pct, 1e-12), 1.0
        );
        const std::string left_date = iso_date(bars[static_cast<std::size_t>(left)].date_ordinal);
        const std::string head_date = iso_date(bars[static_cast<std::size_t>(head)].date_ordinal);
        PatternSetup setup = build_setup(
            config, symbol, 1, "head_candidate",
            {{"head", head_date}, {"left_shoulder", left_date}},
            *head_low, {left_date, head_date},
            {{"head_low", *head_low}, {"left_shoulder_low", *left_low}}
        );
        return buy_decision(
            "confirmed a low-volume head candidate", std::move(setup), quality, quality,
            1.0 - *head_volume / signal.head_volume_ratio_max, 1.0 / 3.0
        );
    }
    if (pivots.size() < 3U) return std::nullopt;
    for (int offset = static_cast<int>(pivots.size()) - 3; offset >= 0; --offset) {
        const int left = pivots[static_cast<std::size_t>(offset)];
        const int head = pivots[static_cast<std::size_t>(offset + 1)];
        const int shoulder = pivots[static_cast<std::size_t>(offset + 2)];
        if (head - left < signal.min_segment_bars || head - left > signal.max_segment_bars
            || shoulder - head < signal.min_segment_bars || shoulder - head > signal.max_segment_bars) continue;
        const auto left_low = bars[static_cast<std::size_t>(left)].low;
        const auto head_low = bars[static_cast<std::size_t>(head)].low;
        const auto shoulder_low = bars[static_cast<std::size_t>(shoulder)].low;
        if (!left_low || !head_low || !shoulder_low) continue;
        const double shoulder_distance = std::abs(*shoulder_low - *left_low) / *left_low;
        if (shoulder_distance > signal.shoulder_tolerance_pct
            || *head_low > std::min(*left_low, *shoulder_low) * (1.0 - signal.head_depth_min_pct)) continue;
        const auto shoulder_volume = volume_ratio(bars[static_cast<std::size_t>(shoulder)]);
        if (!shoulder_volume || *shoulder_volume > signal.right_shoulder_volume_ratio_max) continue;
        const auto first_high_index = highest_index(bars, left, head);
        const auto second_high_index = highest_index(bars, head, shoulder);
        if (!first_high_index || !second_high_index || *first_high_index == *second_high_index) continue;
        const auto first_high = bars[static_cast<std::size_t>(*first_high_index)].high;
        const auto second_high = bars[static_cast<std::size_t>(*second_high_index)].high;
        if (!first_high || !second_high) continue;
        const double neckline = project_line(*first_high_index, *first_high, *second_high_index, *second_high, current);
        const std::string left_date = iso_date(bars[static_cast<std::size_t>(left)].date_ordinal);
        const std::string head_date = iso_date(bars[static_cast<std::size_t>(head)].date_ordinal);
        PatternObject anchors{
            {"head", head_date},
            {"left_shoulder", left_date},
            {"neckline_1", iso_date(bars[static_cast<std::size_t>(*first_high_index)].date_ordinal)},
            {"neckline_2", iso_date(bars[static_cast<std::size_t>(*second_high_index)].date_ordinal)},
            {"right_shoulder", iso_date(bars[static_cast<std::size_t>(shoulder)].date_ordinal)},
        };
        PatternObject fields{{"head_low", *head_low}, {"neckline_price", neckline}};
        const double structure = std::max(0.0, 1.0 - shoulder_distance / signal.shoulder_tolerance_pct);
        if (current == shoulder + signal.pivot_right_bars) {
            PatternSetup setup = build_setup(
                config, symbol, 2, "right_shoulder", std::move(anchors), *head_low,
                {left_date, head_date}, std::move(fields)
            );
            return buy_decision(
                "confirmed a low-volume right shoulder", std::move(setup), structure, structure,
                std::max(0.0, 1.0 - *shoulder_volume / signal.right_shoulder_volume_ratio_max),
                2.0 / 3.0
            );
        }
        const auto close = bars.back().close;
        const auto current_volume = volume_ratio(bars.back());
        if (!close || !current_volume || *close < neckline * (1.0 + signal.breakout_buffer_pct)
            || *current_volume < signal.breakout_volume_ratio_min) continue;
        PatternSetup setup = build_setup(
            config, symbol, 3, "neckline_breakout", std::move(anchors), *head_low,
            {left_date, head_date}, std::move(fields)
        );
        return buy_decision(
            "broke above the projected neckline on confirming volume", std::move(setup), structure,
            std::min(std::max((*close / neckline - 1.0) / std::max(signal.breakout_buffer_pct * 2.0, 1e-12), 0.0), 1.0),
            std::min(*current_volume / (signal.breakout_volume_ratio_min * 2.0), 1.0), 1.0
        );
    }
    return std::nullopt;
}

std::optional<int> v_anchor(const std::vector<PatternBar>& bars, const VReversalConfig& config) {
    for (int index = static_cast<int>(bars.size()) - 1;
         index >= std::max(config.downtrend_lookback - 1, 0); --index) {
        const PatternBar& bar = bars[static_cast<std::size_t>(index)];
        const auto ratio = volume_ratio(bar);
        if (!bar.open || !bar.close || !bar.low || !bar.atr_14 || !ratio
            || *bar.atr_14 == 0.0 || *bar.low == 0.0 || *bar.close <= *bar.open
            || (*bar.close - *bar.low) / *bar.low < config.reversal_min_return_pct
            || (*bar.close - *bar.low) / *bar.atr_14 < config.reversal_min_atr
            || *ratio < config.pivot_volume_ratio_min) continue;
        std::optional<double> minimum_low;
        for (int item = std::max(0, index - config.pivot_max_bars + 1); item <= index; ++item) {
            const auto low = bars[static_cast<std::size_t>(item)].low;
            if (low) minimum_low = minimum_low ? std::min(*minimum_low, *low) : *low;
        }
        if (!minimum_low || *bar.low > *minimum_low) continue;
        std::optional<double> maximum_close;
        for (int item = std::max(0, index - config.downtrend_lookback); item < index; ++item) {
            const auto close = bars[static_cast<std::size_t>(item)].close;
            if (close) maximum_close = maximum_close ? std::max(*maximum_close, *close) : *close;
        }
        if (!maximum_close || (*maximum_close - *bar.low) / *maximum_close < config.downtrend_min_drop_pct) continue;
        return index;
    }
    return std::nullopt;
}

std::optional<std::pair<int, double>> v_retest(
    const std::vector<PatternBar>& bars,
    int anchor,
    const VReversalConfig& config
) {
    const int current = static_cast<int>(bars.size()) - 1;
    const PatternBar& current_bar = bars.back();
    if (!current_bar.low || !current_bar.close || !current_bar.volume) return std::nullopt;
    const int first_breakout = std::max(anchor + config.consolidation_min_bars + 1, current - config.retest_window);
    for (int breakout = first_breakout; breakout < current; ++breakout) {
        const int start = std::max(anchor + 1, breakout - config.consolidation_max_bars);
        const int size = breakout - start;
        if (size < config.consolidation_min_bars || size > config.consolidation_max_bars) continue;
        double top = -std::numeric_limits<double>::infinity();
        bool missing = false;
        for (int item = start; item < breakout; ++item) {
            const auto high = bars[static_cast<std::size_t>(item)].high;
            if (!high) { missing = true; break; }
            top = std::max(top, *high);
        }
        if (missing) continue;
        const PatternBar& breakout_bar = bars[static_cast<std::size_t>(breakout)];
        const auto breakout_ratio = volume_ratio(breakout_bar);
        if (!breakout_bar.close || !breakout_ratio || !breakout_bar.volume
            || *breakout_bar.close <= top || *breakout_ratio < config.breakout_volume_ratio_min) continue;
        if (*current_bar.low >= top * (1.0 - config.support_tolerance_pct)
            && *current_bar.close >= top
            && *current_bar.volume <= *breakout_bar.volume * config.retest_volume_ratio_max) {
            return std::pair{breakout, top};
        }
    }
    return std::nullopt;
}

std::optional<PatternDecision> evaluate_v_reversal(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
) {
    if (auto exit = common_position_exit(config, state, position)) return exit;
    const auto& signal = std::get<VReversalConfig>(config.signal);
    const auto& bars = state.bars;
    const auto anchor_index = v_anchor(bars, signal);
    if (!anchor_index) return std::nullopt;
    const int current = static_cast<int>(bars.size()) - 1;
    const PatternBar& anchor = bars[static_cast<std::size_t>(*anchor_index)];
    const double anchor_low = *anchor.low;
    const double anchor_close = *anchor.close;
    const std::string anchor_date = iso_date(anchor.date_ordinal);
    PatternObject anchors{{"pivot", anchor_date}};
    PatternObject fields{{"pivot_low", anchor_low}};
    const double reversal_return = (anchor_close - anchor_low) / anchor_low;
    const double anchor_volume = volume_ratio(anchor).value_or(signal.pivot_volume_ratio_min);
    if (current == *anchor_index) {
        PatternSetup setup = build_setup(
            config, symbol, 1, "volume_pivot", std::move(anchors), anchor_low,
            {anchor_date}, std::move(fields)
        );
        const double quality = std::min(reversal_return / (signal.reversal_min_return_pct * 2.0), 1.0);
        return buy_decision(
            "confirmed a high-volume V reversal pivot", std::move(setup), quality, quality,
            std::min(anchor_volume / (signal.pivot_volume_ratio_min * 2.0), 1.0), 1.0 / 3.0
        );
    }
    const int distance = current - *anchor_index;
    const PatternBar& current_bar = bars.back();
    bool continuous = distance >= 2;
    std::optional<double> previous_close;
    for (int item = *anchor_index + 1; continuous && item <= current; ++item) {
        const PatternBar& bar = bars[static_cast<std::size_t>(item)];
        const auto ratio = volume_ratio(bar);
        if (!bar.close || !bar.open || *bar.close <= *bar.open || !ratio
            || *ratio < signal.continuation_volume_ratio_min
            || (previous_close && *bar.close <= *previous_close)) continuous = false;
        previous_close = bar.close;
    }
    const auto current_ratio = volume_ratio(current_bar);
    if (distance >= 2 && distance <= signal.continuation_window && continuous
        && current_bar.close && current_bar.open && *current_bar.close > *current_bar.open
        && *current_bar.close > anchor_close && current_ratio
        && *current_ratio >= signal.continuation_volume_ratio_min) {
        PatternSetup setup = build_setup(
            config, symbol, 2, "continuation", std::move(anchors), anchor_low,
            {anchor_date}, std::move(fields)
        );
        const double quality = std::min(
            (*current_bar.close / anchor_close - 1.0) / std::max(signal.reversal_min_return_pct, 1e-12), 1.0
        );
        return buy_decision(
            "continued higher with confirming volume after the V pivot", std::move(setup), quality, quality,
            std::min(*current_ratio / (signal.continuation_volume_ratio_min * 2.0), 1.0), 2.0 / 3.0
        );
    }
    const auto retest = v_retest(bars, *anchor_index, signal);
    if (!retest || !current_bar.close) return std::nullopt;
    const int breakout_index = retest->first;
    const double top = retest->second;
    const PatternBar& breakout = bars[static_cast<std::size_t>(breakout_index)];
    anchors["breakout"] = iso_date(breakout.date_ordinal);
    fields["consolidation_top"] = top;
    PatternSetup setup = build_setup(
        config, symbol, 3, "top_breakout_retest", std::move(anchors), anchor_low,
        {anchor_date}, std::move(fields)
    );
    const double breakout_ratio = volume_ratio(breakout).value_or(signal.breakout_volume_ratio_min);
    const double volume_quality = std::max(
        0.0, 1.0 - current_bar.volume.value_or(0.0)
            / std::max(breakout.volume.value_or(1.0) * signal.retest_volume_ratio_max, 1e-12)
    );
    const double price_quality = std::min(
        std::max((*current_bar.close - top) / std::max(top * signal.support_tolerance_pct, 1e-12), 0.0), 1.0
    );
    return buy_decision(
        "low-volume retest held the V consolidation top", std::move(setup),
        std::min(breakout_ratio / (signal.breakout_volume_ratio_min * 2.0), 1.0),
        price_quality, volume_quality, 1.0
    );
}

std::optional<PatternDecision> evaluate_rounded_bottom(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
) {
    if (auto exit = common_position_exit(config, state, position)) return exit;
    const auto& signal = std::get<RoundedBottomConfig>(config.signal);
    const auto& bars = state.bars;
    const int count = static_cast<int>(bars.size());
    if (count < signal.min_lookback) return std::nullopt;
    const int window_size = std::min(count, signal.max_lookback);
    const int window_start = count - window_size;
    std::vector<double> closes;
    closes.reserve(static_cast<std::size_t>(window_size));
    for (int index = window_start; index < count; ++index) {
        const auto close = bars[static_cast<std::size_t>(index)].close;
        if (!close || *close <= 0.0) return std::nullopt;
        closes.push_back(*close);
    }
    std::vector<double> logs;
    logs.reserve(closes.size());
    for (double close : closes) logs.push_back(std::log(close));
    const auto fit = quadratic_fit(logs);
    if (!fit) return std::nullopt;
    const auto [curvature, vertex, r_squared] = *fit;
    if (curvature <= 0.0 || r_squared < signal.min_r_squared
        || vertex < signal.vertex_position_min || vertex > signal.vertex_position_max) return std::nullopt;
    const int bottom_local = static_cast<int>(std::min_element(closes.begin(), closes.end()) - closes.begin());
    const int bottom_index = window_start + bottom_local;
    const double bottom_close = closes[static_cast<std::size_t>(bottom_local)];
    double left_rim = -std::numeric_limits<double>::infinity();
    const int rim_count = std::max(3, window_size / 10);
    for (int index = window_start; index < window_start + rim_count; ++index) {
        const auto high = bars[static_cast<std::size_t>(index)].high;
        if (!high) return std::nullopt;
        left_rim = std::max(left_rim, *high);
    }
    const double depth = left_rim > 0.0 ? (left_rim - bottom_close) / left_rim : 0.0;
    if (depth < signal.min_depth_pct) return std::nullopt;
    const auto pivots = confirmed_pivot_lows(bars, signal.pivot_left_bars, signal.pivot_right_bars);
    std::vector<int> qualified;
    for (int pivot : pivots) {
        if (pivot <= bottom_index) continue;
        const auto pivot_volume = volume_ratio(bars[static_cast<std::size_t>(pivot)]);
        double surge = 0.0;
        for (int item = std::max(bottom_index + 1, pivot - 5); item < pivot; ++item) {
            surge = std::max(surge, volume_ratio(bars[static_cast<std::size_t>(item)]).value_or(0.0));
        }
        if (!pivot_volume || *pivot_volume > signal.pullback_volume_ratio_max
            || surge < signal.right_volume_ratio_min) continue;
        if (!qualified.empty()) {
            if (pivot - qualified.back() < signal.min_pullback_spacing) continue;
            const auto low = bars[static_cast<std::size_t>(pivot)].low;
            const auto previous_low = bars[static_cast<std::size_t>(qualified.back())].low;
            if (!low || !previous_low || *low <= *previous_low) continue;
        }
        qualified.push_back(pivot);
    }
    const std::string bottom_date = iso_date(bars[static_cast<std::size_t>(bottom_index)].date_ordinal);
    std::vector<std::string> pullbacks;
    for (int index = 0; index < std::min<int>(2, qualified.size()); ++index) {
        pullbacks.push_back(iso_date(bars[static_cast<std::size_t>(qualified[static_cast<std::size_t>(index)])].date_ordinal));
    }
    PatternObject anchors{{"bottom", bottom_date}, {"pullbacks", pullbacks}};
    PatternObject fields{
        {"depth_pct", depth},
        {"r_squared", r_squared},
        {"rim_price", left_rim},
        {"vertex_position", vertex},
    };
    const double structure = std::min(
        std::max((r_squared - signal.min_r_squared) / std::max(1.0 - signal.min_r_squared, 1e-12), 0.0), 1.0
    );
    const int current = count - 1;
    for (int stage = 1; stage <= std::min<int>(2, qualified.size()); ++stage) {
        const int pivot = qualified[static_cast<std::size_t>(stage - 1)];
        if (current != pivot + signal.pivot_right_bars) continue;
        const std::string key = stage == 1 ? "first_right_pullback" : "second_right_pullback";
        PatternSetup setup = build_setup(
            config, symbol, stage, key, anchors, bottom_close, {bottom_date}, fields
        );
        const double ratio = volume_ratio(bars[static_cast<std::size_t>(pivot)]).value_or(signal.pullback_volume_ratio_max);
        return buy_decision(
            "confirmed a higher low-volume pullback on the bowl's right side", std::move(setup), structure,
            std::min(depth / (signal.min_depth_pct * 2.0), 1.0),
            std::max(0.0, 1.0 - ratio / signal.pullback_volume_ratio_max),
            static_cast<double>(stage) / 3.0
        );
    }
    const auto close = bars.back().close;
    const auto current_ratio = volume_ratio(bars.back());
    if (qualified.size() < 2U || !close || !current_ratio
        || *close < left_rim * (1.0 + signal.breakout_buffer_pct)
        || *current_ratio < signal.breakout_volume_ratio_min) return std::nullopt;
    PatternSetup setup = build_setup(
        config, symbol, 3, "rim_breakout", std::move(anchors), bottom_close,
        {bottom_date}, std::move(fields)
    );
    return buy_decision(
        "broke above the rounded-bottom rim on confirming volume", std::move(setup), structure,
        std::min(std::max((*close / left_rim - 1.0) / std::max(signal.breakout_buffer_pct * 2.0, 1e-12), 0.0), 1.0),
        std::min(*current_ratio / (signal.breakout_volume_ratio_min * 2.0), 1.0), 1.0
    );
}

struct IslandPatternMatch {
    int left_gap_index;
    int breakout_index;
    double island_low;
    double island_high;
    double breakout_gap_low;
    double breakout_close;
    double breakout_volume;
    double breakout_volume_ratio;
    double left_gap_pct;
    double breakout_gap_pct;
};

struct IslandExhaustion {
    int index;
    double high;
    double low;
    double left_gap_pct;
    double volume_ratio;
};

bool island_downtrend(const std::vector<PatternBar>& bars, int index, const IslandConfig& config) {
    const PatternBar& bar = bars[static_cast<std::size_t>(index)];
    std::optional<double> lookback_return;
    const int anchor = index - config.downtrend_lookback;
    if (bar.close && anchor >= 0) {
        const auto anchor_close = bars[static_cast<std::size_t>(anchor)].close;
        if (anchor_close && *anchor_close > 0.0) lookback_return = *bar.close / *anchor_close - 1.0;
    }
    return (lookback_return && *lookback_return <= -config.downtrend_min_drop_pct)
        || (bar.close && bar.sma_50 && *bar.close < *bar.sma_50);
}

std::optional<IslandPatternMatch> latest_island(
    const std::vector<PatternBar>& bars,
    const IslandConfig& config
) {
    const int count = static_cast<int>(bars.size());
    if (count < 4) return std::nullopt;
    for (int breakout_index = count - 1; breakout_index >= config.min_island_bars + 1; --breakout_index) {
        const PatternBar& breakout = bars[static_cast<std::size_t>(breakout_index)];
        const auto breakout_ratio = volume_ratio(breakout);
        if (!breakout.open || !breakout.close || !breakout.low || !breakout.volume || !breakout_ratio
            || *breakout.close <= *breakout.open || *breakout_ratio < config.right_volume_ratio_min) continue;
        const int latest_left = breakout_index - config.min_island_bars;
        const int earliest_left = std::max(1, breakout_index - config.max_island_bars);
        for (int left_index = latest_left; left_index >= earliest_left; --left_index) {
            const PatternBar& left = bars[static_cast<std::size_t>(left_index)];
            const auto left_ratio = volume_ratio(left);
            const auto previous_low = bars[static_cast<std::size_t>(left_index - 1)].low;
            if (!left.high || !left.open || !left.close || !left_ratio || !previous_low
                || *left.close >= *left.open || *previous_low <= 0.0) continue;
            const double left_gap = (*previous_low - *left.high) / *previous_low;
            if (left_gap < config.left_gap_min_pct || *left_ratio > config.left_volume_ratio_max
                || !island_downtrend(bars, left_index, config)
                || breakout_index - left_index < config.min_island_bars) continue;
            double island_high = -std::numeric_limits<double>::infinity();
            double island_low = std::numeric_limits<double>::infinity();
            bool invalid = false;
            for (int item = left_index; item < breakout_index; ++item) {
                const PatternBar& island_bar = bars[static_cast<std::size_t>(item)];
                island_high = std::max(island_high, island_bar.high.value_or(-std::numeric_limits<double>::infinity()));
                island_low = std::min(island_low, island_bar.low.value_or(std::numeric_limits<double>::infinity()));
                if (island_bar.high.value_or(std::numeric_limits<double>::infinity()) >= *previous_low) invalid = true;
            }
            if (!std::isfinite(island_high) || !std::isfinite(island_low) || invalid) continue;
            const double breakout_gap = island_high > 0.0 ? (*breakout.low - island_high) / island_high : 0.0;
            if (breakout_gap < config.right_gap_min_pct) continue;
            return IslandPatternMatch{
                left_index, breakout_index, island_low, island_high, *breakout.low,
                *breakout.close, *breakout.volume, *breakout_ratio, left_gap, breakout_gap,
            };
        }
    }
    return std::nullopt;
}

std::optional<IslandExhaustion> island_exhaustion(
    const std::vector<PatternBar>& bars,
    const IslandConfig& config
) {
    if (bars.size() < 2U) return std::nullopt;
    const int index = static_cast<int>(bars.size()) - 1;
    const PatternBar& bar = bars.back();
    const auto previous_low = bars[bars.size() - 2U].low;
    const auto ratio = volume_ratio(bar);
    if (!bar.high || !bar.low || !bar.open || !bar.close || !previous_low || !ratio
        || *previous_low <= 0.0 || *bar.close >= *bar.open) return std::nullopt;
    const double gap = (*previous_low - *bar.high) / *previous_low;
    if (gap < config.left_gap_min_pct || *ratio > config.left_volume_ratio_max
        || !island_downtrend(bars, index, config)) return std::nullopt;
    return IslandExhaustion{index, *bar.high, *bar.low, gap, *ratio};
}

std::optional<PatternDecision> evaluate_island(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
) {
    if (state.bars.empty()) return std::nullopt;
    const auto& signal = std::get<IslandConfig>(config.signal);
    const PatternBar& current = state.bars.back();
    if (position.quantity > 0.0 && position.setup) {
        PatternSetup setup = *position.setup;
        auto atr = current.atr_14;
        if (!atr || *atr == 0.0) atr = recent_atr(state.bars, static_cast<int>(state.bars.size()));
        std::optional<std::string> reason;
        std::optional<std::string> stage;
        if (setup.invalidation_price && current.low && *current.low < *setup.invalidation_price) {
            reason = "price broke the staged pattern invalidation level"; stage = "pattern_invalidation";
        } else if (current.close && position.average_entry_price
            && *current.close <= *position.average_entry_price * (1.0 - config.risk.max_loss_pct)) {
            reason = "price fell more than the configured max-loss threshold from entry"; stage = "max_loss_stop";
        } else if (current.close && atr && position.average_entry_price
            && *current.close <= *position.average_entry_price - config.risk.stop_loss_atr * *atr) {
            reason = "price hit the ATR stop"; stage = "atr_stop";
        } else if (current.close && atr && position.average_entry_price
            && *current.close >= *position.average_entry_price + config.risk.take_profit_atr * *atr) {
            reason = "price reached the ATR take-profit target"; stage = "take_profit";
        }
        if (reason) {
            setup.exit_stage = stage;
            const PatternObject& fields = payload_fields(setup);
            std::optional<double> score;
            const auto left_gap_pct = pattern_number(fields, "left_gap_pct");
            const auto breakout_gap_pct = pattern_number(fields, "breakout_gap_pct");
            const auto breakout_volume_ratio = pattern_number(fields, "breakout_volume_ratio");
            if (left_gap_pct && breakout_gap_pct && breakout_volume_ratio) {
                score = *left_gap_pct * 100.0 + *breakout_gap_pct * 100.0
                    + *breakout_volume_ratio;
            }
            return PatternDecision{
                false, *reason, setup, score,
                {
                    {"breakout_volume_ratio", fields.contains("breakout_volume_ratio") ? fields.at("breakout_volume_ratio") : PatternValue{}},
                    {"hold_margin_atr", PatternValue{}},
                    {"left_gap_pct", fields.contains("left_gap_pct") ? fields.at("left_gap_pct") : PatternValue{}},
                    {"left_volume_ratio", fields.contains("left_volume_ratio") ? fields.at("left_volume_ratio") : PatternValue{}},
                    {"retest_volume_ratio", PatternValue{}},
                    {"right_gap_pct", fields.contains("breakout_gap_pct") ? fields.at("breakout_gap_pct") : PatternValue{}},
                    {"stage", *stage},
                },
            };
        }
    }

    const auto pattern = latest_island(state.bars, signal);
    const auto exhaustion = pattern ? std::optional<IslandExhaustion>{} : island_exhaustion(state.bars, signal);
    std::string stage;
    std::string reason;
    if (pattern) {
        const int current_index = static_cast<int>(state.bars.size()) - 1;
        if (current_index == pattern->breakout_index) {
            stage = "breakout";
            reason = "confirmed the island reversal with a volume-backed upside gap";
        } else if (current_index > pattern->breakout_index
            && current_index <= pattern->breakout_index + signal.retest_window) {
            const double floor = pattern->island_high * (1.0 - signal.support_tolerance_pct);
            bool prior_held = true;
            for (int index = pattern->breakout_index + 1; index < current_index; ++index) {
                const auto close = state.bars[static_cast<std::size_t>(index)].close;
                if (!close || *close < floor) prior_held = false;
            }
            if (current.low && current.close && current.volume && prior_held
                && *current.low <= pattern->breakout_gap_low * (1.0 + signal.support_tolerance_pct)
                && *current.low >= floor && *current.close >= pattern->island_high
                && *current.volume <= pattern->breakout_volume * signal.retest_volume_ratio_max) {
                stage = "retest";
                reason = "low-volume retest held the upside gap after the island reversal";
            }
        }
    } else if (exhaustion) {
        stage = "exhaustion_gap";
        reason = "confirmed a low-volume downside exhaustion gap";
    }
    if (stage.empty()) return std::nullopt;

    PatternSetup setup;
    std::optional<double> score;
    if (pattern) {
        const std::string left_date = iso_date(state.bars[static_cast<std::size_t>(pattern->left_gap_index)].date_ordinal);
        const std::string breakout_date = iso_date(state.bars[static_cast<std::size_t>(pattern->breakout_index)].date_ordinal);
        const int stage_index = stage == "breakout" ? 2 : 3;
        setup = build_setup(
            config, symbol, stage_index, stage_index == 2 ? "upside_gap" : "gap_retest",
            {
                {"breakout_price", pattern->breakout_close},
                {"breakout_trade_date", breakout_date},
                {"left_gap_price", pattern->island_high},
                {"left_gap_trade_date", left_date},
            },
            pattern->island_low * (1.0 - signal.support_tolerance_pct), {left_date},
            {
                {"breakout_gap_low", pattern->breakout_gap_low},
                {"breakout_gap_pct", pattern->breakout_gap_pct},
                {"breakout_volume", pattern->breakout_volume},
                {"breakout_volume_ratio", pattern->breakout_volume_ratio},
                {"island_high", pattern->island_high},
                {"island_low", pattern->island_low},
                {"left_gap_pct", pattern->left_gap_pct},
            }
        );
        score = pattern->left_gap_pct * 100.0 + pattern->breakout_gap_pct * 100.0
            + pattern->breakout_volume_ratio;
    } else {
        const std::string left_date = iso_date(state.bars[static_cast<std::size_t>(exhaustion->index)].date_ordinal);
        setup = build_setup(
            config, symbol, 1, "exhaustion_gap",
            {{"left_gap_price", exhaustion->high}, {"left_gap_trade_date", left_date}},
            exhaustion->low * (1.0 - signal.support_tolerance_pct), {left_date},
            {
                {"island_high", exhaustion->high},
                {"island_low", exhaustion->low},
                {"left_gap_pct", exhaustion->left_gap_pct},
                {"left_volume_ratio", exhaustion->volume_ratio},
            }
        );
        score = exhaustion->left_gap_pct * 100.0;
    }
    const PatternObject& fields = payload_fields(setup);
    const auto breakout_volume = pattern_number(fields, "breakout_volume");
    const auto island_high = pattern_number(fields, "island_high");
    PatternObject strength{
        {"breakout_volume_ratio", fields.contains("breakout_volume_ratio") ? fields.at("breakout_volume_ratio") : PatternValue{}},
        {"left_gap_pct", fields.contains("left_gap_pct") ? fields.at("left_gap_pct") : PatternValue{}},
        {"left_volume_ratio", fields.contains("left_volume_ratio") ? fields.at("left_volume_ratio") : PatternValue{}},
        {"right_gap_pct", fields.contains("breakout_gap_pct") ? fields.at("breakout_gap_pct") : PatternValue{}},
        {"stage", stage},
    };
    strength["retest_volume_ratio"] = current.volume && breakout_volume && *breakout_volume > 0.0
        ? PatternValue(*current.volume / *breakout_volume) : PatternValue{};
    strength["hold_margin_atr"] = current.close && current.atr_14 && *current.atr_14 > 0.0 && island_high
        ? PatternValue((*current.close - *island_high) / *current.atr_14) : PatternValue{};
    return PatternDecision{true, reason, std::move(setup), score, std::move(strength)};
}

void append_json_number(std::string& output, double value) {
    if (!std::isfinite(value)) {
        output += "null";
        return;
    }
    std::ostringstream formatted;
    formatted.imbue(std::locale::classic());
    formatted << std::setprecision(17) << value;
    output += formatted.str();
}

void append_json_value(std::string& output, const PatternValue& value) {
    std::visit([&](const auto& item) {
        using T = std::decay_t<decltype(item)>;
        if constexpr (std::is_same_v<T, std::monostate>) output += "null";
        else if constexpr (std::is_same_v<T, bool>) output += item ? "true" : "false";
        else if constexpr (std::is_same_v<T, std::int64_t>) output += std::to_string(item);
        else if constexpr (std::is_same_v<T, double>) append_json_number(output, item);
        else if constexpr (std::is_same_v<T, std::string>) output += json_string(item);
        else {
            output.push_back('[');
            bool first = true;
            for (const std::string& entry : item) {
                if (!first) output.push_back(',');
                first = false;
                output += json_string(entry);
            }
            output.push_back(']');
        }
    }, value);
}

std::string pattern_object_json(const PatternObject& object) {
    std::string output = "{";
    bool first = true;
    for (const auto& [key, value] : object) {
        if (!first) output.push_back(',');
        first = false;
        output += json_string(key);
        output.push_back(':');
        append_json_value(output, value);
    }
    output.push_back('}');
    return output;
}

void append_field(std::string& output, bool& first, const std::string& key, const std::string& json) {
    if (!first) output.push_back(',');
    first = false;
    output += json_string(key);
    output.push_back(':');
    output += json;
}

void append_number_field(std::string& output, bool& first, const std::string& key, std::optional<double> value) {
    std::string json;
    if (value) append_json_number(json, *value); else json = "null";
    append_field(output, first, key, json);
}

std::string config_json(const PatternConfig& config) {
    PatternObject value;
    if (config.kind == PatternKind::IslandReversal) {
        const auto& signal = std::get<IslandConfig>(config.signal);
        value = {
            {"downtrend_min_drop_pct", signal.downtrend_min_drop_pct},
            {"left_gap_min_pct", signal.left_gap_min_pct},
            {"max_loss_pct", config.risk.max_loss_pct},
            {"retest_window", static_cast<std::int64_t>(signal.retest_window)},
            {"right_gap_min_pct", signal.right_gap_min_pct},
            {"support_tolerance_pct", signal.support_tolerance_pct},
            {"take_profit_atr", config.risk.take_profit_atr},
        };
    } else if (config.kind == PatternKind::DoubleBottom) {
        const auto& signal = std::get<DoubleBottomConfig>(config.signal);
        value = {
            {"bottom_tolerance_pct", signal.bottom_tolerance_pct},
            {"breakout_buffer_pct", signal.breakout_buffer_pct},
            {"breakout_volume_ratio_min", signal.breakout_volume_ratio_min},
            {"downtrend_max_up_day_ratio", signal.downtrend_max_up_day_ratio},
            {"downtrend_min_drop_pct", signal.downtrend_min_drop_pct},
            {"downtrend_min_r_squared", signal.downtrend_min_r_squared},
            {"left_bottom_after_bars", static_cast<std::int64_t>(signal.left_bottom_after_bars)},
            {"left_bottom_before_bars", static_cast<std::int64_t>(signal.left_bottom_before_bars)},
            {"max_breakout_bars_after_right_bottom", static_cast<std::int64_t>(signal.max_breakout_bars_after_right_bottom)},
            {"max_loss_pct", config.risk.max_loss_pct},
            {"neckline_min_rebound_pct", signal.neckline_min_rebound_pct},
            {"retest_window", static_cast<std::int64_t>(signal.retest_window)},
            {"support_tolerance_pct", signal.support_tolerance_pct},
            {"take_profit_atr", config.risk.take_profit_atr},
        };
    }
    return pattern_object_json(value);
}

}  // namespace

std::optional<double> pattern_number(const PatternObject& object, const std::string& key) {
    const auto found = object.find(key);
    if (found == object.end()) return std::nullopt;
    if (const auto* number = std::get_if<double>(&found->second)) return *number;
    if (const auto* integer = std::get_if<std::int64_t>(&found->second)) return static_cast<double>(*integer);
    return std::nullopt;
}

std::optional<std::string> pattern_text(const PatternObject& object, const std::string& key) {
    const auto found = object.find(key);
    if (found == object.end()) return std::nullopt;
    if (const auto* text = std::get_if<std::string>(&found->second)) return *text;
    return std::nullopt;
}

void append_pattern_bar(PatternState& state, const PatternConfig& config, PatternBar bar) {
    if (!state.bars.empty() && state.bars.back().date_ordinal == bar.date_ordinal) {
        state.bars.back() = std::move(bar);
        return;
    }
    state.bars.push_back(std::move(bar));
    if (config.kind == PatternKind::DoubleBottom) {
        advance_double_bottom(state, std::get<DoubleBottomConfig>(config.signal));
        prune_double_bottom(state, config.history_limit);
    } else if (static_cast<int>(state.bars.size()) > config.history_limit) {
        state.bars.erase(state.bars.begin());
    }
}

std::optional<PatternDecision> evaluate_pattern(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
) {
    switch (config.kind) {
        case PatternKind::IslandReversal: return evaluate_island(config, symbol, state, position);
        case PatternKind::DoubleBottom: return evaluate_double_bottom(config, symbol, state, position);
        case PatternKind::HeadShouldersBottom: return evaluate_head_shoulders(config, symbol, state, position);
        case PatternKind::RoundedBottom: return evaluate_rounded_bottom(config, symbol, state, position);
        case PatternKind::VReversal: return evaluate_v_reversal(config, symbol, state, position);
    }
    return std::nullopt;
}

std::string pattern_setup_json(const PatternSetup& setup) {
    std::string output = "{";
    bool first = true;
    append_field(output, first, "pattern_type", json_string(setup.pattern_type));
    append_field(output, first, "setup_id", json_string(setup.setup_id));
    append_field(output, first, "stage_index", std::to_string(setup.stage_index));
    append_field(output, first, "stage_key", json_string(setup.stage_key));
    std::string stage_target;
    append_json_number(stage_target, setup.stage_target_pct);
    append_field(output, first, "stage_target_pct", stage_target);
    append_field(output, first, "anchors", pattern_object_json(setup.anchors));
    append_number_field(output, first, "invalidation_price", setup.invalidation_price);
    for (const auto& [key, value] : payload_fields(setup)) {
        std::string json;
        append_json_value(json, value);
        append_field(output, first, key, json);
    }
    append_field(output, first, "stage", json_string(setup.stage_key));
    if (setup.exit_stage) append_field(output, first, "exit_stage", json_string(*setup.exit_stage));
    output.push_back('}');
    return output;
}

std::string pattern_strength_inputs_json(const PatternObject& inputs) {
    return pattern_object_json(inputs);
}

std::string pattern_metadata_json(
    const PatternConfig& config,
    const PatternBar& current,
    const PatternPositionView& position,
    const PatternDecision& decision,
    const std::string& strength_json
) {
    std::string output = "{";
    bool first = true;
    append_number_field(output, first, "close", current.close);
    append_number_field(output, first, "open", current.open);
    append_number_field(output, first, "high", current.high);
    append_number_field(output, first, "low", current.low);
    append_number_field(output, first, "volume", current.volume);
    append_number_field(output, first, "atr_14", current.atr_14);
    append_number_field(output, first, "position", position.quantity);
    append_number_field(output, first, "avg_entry_price", position.average_entry_price);
    append_field(output, first, "setup", pattern_setup_json(decision.setup));
    append_field(output, first, "strength_inputs", pattern_strength_inputs_json(decision.strength_inputs));
    if (config.kind == PatternKind::IslandReversal || config.kind == PatternKind::DoubleBottom) {
        append_field(output, first, "config", config_json(config));
    } else {
        append_field(output, first, "price_semantics", json_string("forward_adjusted_fallback_unadjusted"));
    }
    if (!strength_json.empty()) append_field(output, first, "strength", strength_json);
    output.push_back('}');
    return output;
}

}  // namespace quant_kernel
