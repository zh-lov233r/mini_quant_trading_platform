#include "backtest_kernel.hpp"
#include "pattern_kernel.hpp"
#include "support_resistance_kernel.hpp"

#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <bit>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace quant_kernel {
namespace {

constexpr std::int64_t kDateSentinel = std::numeric_limits<std::int64_t>::min();
constexpr std::size_t kIntegerColumns = 9;
constexpr std::size_t kFloatColumns = 35;

enum IntegerColumn : std::size_t {
    kSessionIndex = 0,
    kInstrumentId = 1,
    kTimestampUs = 2,
    kDateOrdinal = 3,
    kSymbolId = 4,
    kAssetTypeId = 5,
    kExchangeId = 6,
    kListedOrdinal = 7,
    kDelistedOrdinal = 8,
};

enum FloatColumn : std::size_t {
    kOpen = 0,
    kHigh = 1,
    kLow = 2,
    kClose = 3,
    kCloseUnadjusted = 4,
    kVolume = 5,
    kAtr14 = 6,
    kVolumeSma20 = 7,
    kDollarVolume20 = 8,
    kReturn20d = 9,
    kReturn60d = 10,
    kSma10 = 11,
    kSma20 = 12,
    kSma50 = 13,
    kSma100 = 14,
    kSma200 = 15,
    kEma12 = 16,
    kEma15 = 17,
    kEma20 = 18,
    kEma50 = 19,
    kRsi2 = 20,
    kRsi5 = 21,
    kRsi14 = 22,
    kZscore5 = 23,
    kZscore10 = 24,
    kZscore20 = 25,
    kPrevSma10 = 26,
    kPrevSma20 = 27,
    kPrevSma50 = 28,
    kPrevSma100 = 29,
    kPrevSma200 = 30,
    kPrevEma12 = 31,
    kPrevEma15 = 32,
    kPrevEma20 = 33,
    kPrevEma50 = 34,
};

enum class StrategyKind {
    Trend,
    MeanReversion,
    MomentumBreakout,
    IslandReversal,
    DoubleBottom,
    HeadShouldersBottom,
    RoundedBottom,
    VReversal,
    SupportResistance,
};
enum class Action : std::int8_t { Sell = -1, Buy = 1 };

class BacktestCancelled final : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

template <typename T>
struct MatrixView {
    const char* data;
    py::ssize_t rows;
    py::ssize_t columns;
    py::ssize_t row_stride;
    py::ssize_t column_stride;

    T at(py::ssize_t row, std::size_t column) const {
        return *reinterpret_cast<const T*>(
            data + row * row_stride + static_cast<py::ssize_t>(column) * column_stride
        );
    }
};

struct DatasetView {
    py::array integers_owner;
    py::array floats_owner;
    MatrixView<std::int64_t> integers;
    MatrixView<double> floats;
    std::vector<std::string> symbols;
    std::vector<std::string> asset_types;
    std::vector<std::string> exchanges;
    std::vector<std::int32_t> history_sessions;
    std::map<std::int64_t, std::map<std::int64_t, double>> split_adjustments;
    py::dict support_resistance_hydration;
};

struct SessionRange {
    py::ssize_t begin;
    py::ssize_t end;
    std::int64_t session_index;
    std::int64_t date_ordinal;
    std::int64_t timestamp_us;
};

struct CostConfig {
    double commission_bps = 1.0;
    double commission_min = 1.0;
    double slippage_bps = 5.0;
};

struct UniversePolicy {
    std::set<std::string> asset_types;
    std::set<std::string> exchanges;
    double minimum_unadjusted_close;
    double minimum_dollar_volume_20;
    std::int32_t minimum_history_sessions;
};

enum class UniverseExclusion {
    None,
    AssetType,
    Exchange,
    BeforeListing,
    AfterDelisting,
    Price,
    Liquidity,
    History,
};

struct StrategyConfig {
    StrategyKind kind;
    double minimum_strength = 50.0;
    int max_positions = 10;
    double position_size_pct = 0.1;

    std::size_t fast_column = 0;
    std::size_t slow_column = 0;
    std::size_t previous_fast_column = 0;
    std::size_t previous_slow_column = 0;
    std::string fast_key;
    std::string slow_key;
    double volume_multiplier = 0.0;
    double atr_multiplier = 0.0;
    double stop_loss_pct = 0.0;
    double stop_loss_atr = 0.0;
    double take_profit_atr = 0.0;

    std::size_t zscore_column = 0;
    int zscore_lookback = 0;
    double zscore_entry = 0.0;
    double zscore_exit = 0.0;
    double take_profit_pct = 0.0;
    int max_holding_days = 0;

    double minimum_return_20d = 0.0;
    double breakout_buffer_pct = 0.0;
    double exit_return_20d = 0.0;

    std::optional<PatternConfig> pattern;
    support_resistance::Config support_resistance;
};

struct Position {
    double quantity;
    double average_entry_price;
    std::int64_t entry_session;
    std::int32_t symbol_id;
    std::int64_t entry_date_ordinal;
    std::optional<PatternSetup> pattern_setup;
    std::vector<std::string> entry_history_json;
    std::string entry_signal_features_json;
    std::optional<support_resistance::JsonObject> support_entry_signal_features;
};

struct StrengthComponentAudit {
    std::string key;
    double raw_value;
    double normalized_score;
    double weight;
};

struct SignalRecord {
    std::int64_t session_index;
    std::int64_t instrument_id;
    std::int64_t timestamp_us;
    std::int32_t symbol_id;
    Action action;
    double score;
    double strength_score = std::numeric_limits<double>::quiet_NaN();
    std::int32_t strength_rank = 0;
    bool passes_threshold = true;
    std::string reason;
    py::ssize_t dataset_row = 0;
    double position = 0.0;
    double average_entry_price = std::numeric_limits<double>::quiet_NaN();
    std::int64_t position_holding_days = -1;
    bool trend_crossover = false;
    std::string strength_model_version;
    std::vector<StrengthComponentAudit> strength_components;
    std::string metadata_json;
    std::string entry_signal_features_json;
    std::optional<PatternSetup> pattern_setup;
    std::string pattern_setup_json;
    std::string strength_json;
    bool has_entry_channel = false;
    double entry_channel_lower = std::numeric_limits<double>::quiet_NaN();
    double entry_channel_upper = std::numeric_limits<double>::quiet_NaN();
    double entry_channel_lower_slope = 0.0;
    double entry_channel_upper_slope = 0.0;
    std::string support_setup;
    std::string support_entry_channel_json;
    support_resistance::JsonObject support_metadata;
    std::optional<support_resistance::EntryChannel> support_entry_channel;
    std::optional<support_resistance::Setup> support_setup_kind;
};

struct KernelResult {
    double initial_cash = 0.0;
    double final_equity = 0.0;
    double total_return = 0.0;
    double max_drawdown = 0.0;
    double total_fees = 0.0;
    double total_slippage = 0.0;
    double delisting_zero_write_off = 0.0;
    std::int64_t trading_days = 0;
    std::vector<std::string> symbols;

    std::vector<std::int64_t> signal_session_index;
    std::vector<std::int64_t> signal_instrument_id;
    std::vector<std::int64_t> signal_timestamp_us;
    std::vector<std::int32_t> signal_symbol_id;
    std::vector<std::int8_t> signal_action;
    std::vector<double> signal_score;
    std::vector<double> signal_strength_score;
    std::vector<std::int32_t> signal_strength_rank;
    std::vector<std::uint8_t> signal_passes_threshold;
    std::vector<std::string> signal_reason;
    std::vector<std::string> signal_metadata_json;

    std::vector<std::int64_t> trade_session_index;
    std::vector<std::int64_t> trade_signal_session_index;
    std::vector<std::int64_t> trade_instrument_id;
    std::vector<std::int32_t> trade_symbol_id;
    std::vector<std::int8_t> trade_side;
    std::vector<double> trade_quantity;
    std::vector<double> trade_price;
    std::vector<double> trade_fee;
    std::vector<double> trade_reference_price;
    std::vector<double> trade_slippage_cost;
    std::vector<std::int64_t> trade_execution_timestamp_us;
    std::vector<std::int64_t> trade_signal_timestamp_us;
    std::vector<std::int64_t> trade_execution_date_ordinal;
    std::vector<std::string> trade_reason;
    std::vector<std::string> trade_entry_signal_features_json;
    std::vector<std::string> trade_setup_id;
    std::vector<std::int8_t> trade_stage_index;
    std::vector<std::string> trade_stage_key;
    std::vector<double> trade_position_quantity_before;
    std::vector<double> trade_position_quantity_after;
    std::vector<double> trade_position_average_entry_price_after;
    std::vector<double> trade_stage_target_pct;
    std::vector<double> trade_slippage_bps;
    std::vector<double> trade_gross_notional;
    std::vector<double> trade_net_cash_flow;

    std::vector<std::int64_t> equity_session_index;
    std::vector<std::int64_t> equity_timestamp_us;
    std::vector<double> equity_cash;
    std::vector<double> equity_value;
    std::vector<double> equity_drawdown;
    std::vector<double> equity_gross_exposure;
    std::vector<std::int32_t> equity_holdings_count;
    std::vector<std::string> equity_positions_json;
    std::vector<std::string> equity_metrics_json;

    std::vector<std::int64_t> position_session_index;
    std::vector<std::int64_t> position_instrument_id;
    std::vector<std::int32_t> position_symbol_id;
    std::vector<double> position_quantity;
    std::vector<double> position_average_entry_price;
    std::vector<double> position_close;
    std::vector<double> position_market_value;
    std::vector<std::int64_t> position_entry_date_ordinal;
    std::vector<std::string> position_entry_signal_features_json;

    bool has_universe_membership = false;
    std::vector<std::int64_t> universe_session_index;
    std::vector<std::int64_t> universe_date_ordinal;
    std::vector<std::int32_t> universe_eligible_count;
    std::vector<std::int32_t> universe_excluded_asset_type;
    std::vector<std::int32_t> universe_excluded_exchange;
    std::vector<std::int32_t> universe_excluded_before_listing;
    std::vector<std::int32_t> universe_excluded_after_delisting;
    std::vector<std::int32_t> universe_excluded_price;
    std::vector<std::int32_t> universe_excluded_liquidity;
    std::vector<std::int32_t> universe_excluded_history;

    std::vector<std::int64_t> support_event_instrument_id;
    std::vector<std::int32_t> support_event_symbol_id;
    std::vector<std::string> support_event_json;
    std::vector<std::int64_t> support_zone_instrument_id;
    std::vector<std::int32_t> support_zone_symbol_id;
    std::vector<std::string> support_zone_version_json;
    std::vector<std::int64_t> support_regime_instrument_id;
    std::vector<std::int32_t> support_regime_symbol_id;
    std::vector<std::string> support_regime_version_json;
};

py::dict required_dict(const py::dict& parent, const char* key) {
    if (!parent.contains(key) || !py::isinstance<py::dict>(parent[key])) {
        throw std::invalid_argument(std::string("missing object: ") + key);
    }
    return py::cast<py::dict>(parent[key]);
}

double number_or(const py::dict& value, const char* key, double fallback) {
    return value.contains(key) && !value[key].is_none() ? py::cast<double>(value[key]) : fallback;
}

int integer_or(const py::dict& value, const char* key, int fallback) {
    return value.contains(key) && !value[key].is_none() ? py::cast<int>(value[key]) : fallback;
}

std::string upper_trimmed(std::string value) {
    const auto first = std::find_if_not(value.begin(), value.end(), [](unsigned char character) {
        return std::isspace(character) != 0;
    });
    const auto last = std::find_if_not(value.rbegin(), value.rend(), [](unsigned char character) {
        return std::isspace(character) != 0;
    }).base();
    if (first >= last) return {};
    std::string result(first, last);
    std::transform(result.begin(), result.end(), result.begin(), [](unsigned char character) {
        return static_cast<char>(std::toupper(character));
    });
    return result;
}

std::set<std::string> normalized_string_set(
    const py::dict& value,
    const char* key,
    const std::vector<std::string>& fallback
) {
    std::set<std::string> result;
    if (!value.contains(key)) {
        result.insert(fallback.begin(), fallback.end());
        return result;
    }
    for (const py::handle raw : py::reinterpret_borrow<py::iterable>(value[key])) {
        const std::string normalized = upper_trimmed(py::cast<std::string>(py::str(raw)));
        if (!normalized.empty()) result.insert(normalized);
    }
    return result;
}

std::optional<UniversePolicy> parse_universe_policy(const py::dict& runtime) {
    const py::dict params = required_dict(runtime, "params");
    if (!params.contains("universe") || !py::isinstance<py::dict>(params["universe"])) {
        return std::nullopt;
    }
    const py::dict universe = py::cast<py::dict>(params["universe"]);
    if (!universe.contains("policy") || universe["policy"].is_none()) return std::nullopt;
    if (!py::isinstance<py::dict>(universe["policy"])) {
        throw std::invalid_argument("universe.policy must be an object");
    }
    const py::dict value = py::cast<py::dict>(universe["policy"]);
    const std::string type = value.contains("type") && !value["type"].is_none()
        ? py::cast<std::string>(py::str(value["type"])) : "point_in_time_liquid";
    const std::string membership_as_of = value.contains("membershipAsOf")
        && !value["membershipAsOf"].is_none()
        ? py::cast<std::string>(py::str(value["membershipAsOf"])) : "signal_close";
    const std::string existing_position_policy = value.contains("existingPositionPolicy")
        && !value["existingPositionPolicy"].is_none()
        ? py::cast<std::string>(py::str(value["existingPositionPolicy"])) : "exit_only";
    const std::string delisting_value_policy = value.contains("delistingValuePolicy")
        && !value["delistingValuePolicy"].is_none()
        ? py::cast<std::string>(py::str(value["delistingValuePolicy"]))
        : "zero_with_last_close_sensitivity";
    UniversePolicy policy{
        normalized_string_set(value, "assetTypes", {"CS"}),
        normalized_string_set(value, "exchanges", {"XNAS", "XNYS", "XASE"}),
        number_or(value, "minUnadjustedClose", 5.0),
        number_or(value, "minDollarVolume20", 10'000'000.0),
        static_cast<std::int32_t>(integer_or(value, "minHistorySessions", 200)),
    };
    if (type != "point_in_time_liquid") {
        throw std::invalid_argument("unsupported universePolicy type");
    }
    if (policy.asset_types.empty() || policy.exchanges.empty()) {
        throw std::invalid_argument("universePolicy assetTypes and exchanges must be non-empty");
    }
    if (!std::isfinite(policy.minimum_unadjusted_close)
        || !std::isfinite(policy.minimum_dollar_volume_20)
        || policy.minimum_unadjusted_close <= 0.0 || policy.minimum_dollar_volume_20 <= 0.0) {
        throw std::invalid_argument("universePolicy price and liquidity thresholds must be positive");
    }
    if (policy.minimum_history_sessions < 20 || policy.minimum_history_sessions > 252) {
        throw std::invalid_argument("universePolicy minHistorySessions must be between 20 and 252");
    }
    if (membership_as_of != "signal_close") {
        throw std::invalid_argument("universePolicy membershipAsOf must be signal_close");
    }
    if (existing_position_policy != "exit_only") {
        throw std::invalid_argument("universePolicy existingPositionPolicy must be exit_only");
    }
    if (delisting_value_policy != "zero_with_last_close_sensitivity") {
        throw std::invalid_argument(
            "universePolicy delistingValuePolicy must be zero_with_last_close_sensitivity"
        );
    }
    return policy;
}

std::size_t float_column(const std::string& name) {
    static const std::unordered_map<std::string, std::size_t> columns = {
        {"sma_10", kSma10}, {"sma_20", kSma20}, {"sma_50", kSma50},
        {"sma_100", kSma100}, {"sma_200", kSma200}, {"ema_12", kEma12},
        {"ema_15", kEma15}, {"ema_20", kEma20}, {"ema_50", kEma50},
        {"prev_sma_10", kPrevSma10}, {"prev_sma_20", kPrevSma20},
        {"prev_sma_50", kPrevSma50}, {"prev_sma_100", kPrevSma100},
        {"prev_sma_200", kPrevSma200}, {"prev_ema_12", kPrevEma12},
        {"prev_ema_15", kPrevEma15}, {"prev_ema_20", kPrevEma20},
        {"prev_ema_50", kPrevEma50}, {"zscore_5", kZscore5},
        {"zscore_10", kZscore10}, {"zscore_20", kZscore20},
    };
    const auto found = columns.find(name);
    if (found == columns.end()) throw std::invalid_argument("prepared dataset does not contain " + name);
    return found->second;
}

StrategyConfig parse_strategy(const py::dict& runtime) {
    const std::string type = py::cast<std::string>(runtime["strategy_type"]);
    const py::dict params = required_dict(runtime, "params");
    const py::dict signal = required_dict(params, "signal");
    const py::dict risk = required_dict(params, "risk");
    StrategyConfig config{};
    config.minimum_strength = number_or(signal, "min_strength_score", 50.0);
    config.max_positions = integer_or(risk, "max_positions", 10);
    config.position_size_pct = number_or(risk, "position_size_pct", 0.1);
    if (!std::isfinite(config.minimum_strength)
        || config.minimum_strength < 0.0 || config.minimum_strength > 100.0) {
        throw std::invalid_argument("signal.min_strength_score must be within [0, 100]");
    }
    if (config.max_positions <= 0 || !std::isfinite(config.position_size_pct)
        || config.position_size_pct <= 0.0 || config.position_size_pct > 1.0) {
        throw std::invalid_argument("invalid native portfolio risk configuration");
    }

    config.stop_loss_pct = number_or(risk, "stop_loss_pct", 0.0);
    if (type == "trend") {
        config.kind = StrategyKind::Trend;
        const py::dict fast = required_dict(signal, "fast_indicator");
        const py::dict slow = required_dict(signal, "slow_indicator");
        config.fast_key = py::cast<std::string>(fast["kind"]) + "_" + std::to_string(py::cast<int>(fast["window"]));
        config.slow_key = py::cast<std::string>(slow["kind"]) + "_" + std::to_string(py::cast<int>(slow["window"]));
        config.fast_column = float_column(config.fast_key);
        config.slow_column = float_column(config.slow_key);
        config.previous_fast_column = float_column("prev_" + config.fast_key);
        config.previous_slow_column = float_column("prev_" + config.slow_key);
        config.volume_multiplier = py::cast<double>(signal["volume_multiplier"]);
        config.atr_multiplier = py::cast<double>(signal["atr_multiplier"]);
        config.stop_loss_atr = py::cast<double>(risk["stop_loss_atr"]);
        config.take_profit_atr = py::cast<double>(risk["take_profit_atr"]);
    } else if (type == "mean_reversion") {
        config.kind = StrategyKind::MeanReversion;
        config.zscore_lookback = py::cast<int>(signal["lookback_window"]);
        config.zscore_column = float_column("zscore_" + std::to_string(config.zscore_lookback));
        config.zscore_entry = py::cast<double>(signal["zscore_entry"]);
        config.zscore_exit = py::cast<double>(signal["zscore_exit"]);
        config.take_profit_pct = py::cast<double>(risk["take_profit_pct"]);
        config.max_holding_days = integer_or(risk, "max_holding_days", 0);
    } else if (type == "momentum_breakout") {
        config.kind = StrategyKind::MomentumBreakout;
        config.minimum_return_20d = py::cast<double>(signal["minimum_return_20d"]);
        config.breakout_buffer_pct = py::cast<double>(signal["breakout_buffer_pct"]);
        config.volume_multiplier = py::cast<double>(signal["volume_multiplier"]);
        config.exit_return_20d = py::cast<double>(signal["exit_return_20d"]);
        config.take_profit_pct = py::cast<double>(risk["take_profit_pct"]);
    } else if (type == "island_reversal" || type == "double_bottom"
        || type == "head_shoulders_bottom" || type == "rounded_bottom"
        || type == "v_reversal") {
        config.pattern = parse_pattern_config(runtime);
        config.minimum_strength = config.pattern->minimum_strength;
        config.max_positions = config.pattern->risk.max_positions;
        config.position_size_pct = config.pattern->risk.position_size_pct;
        if (type == "island_reversal") config.kind = StrategyKind::IslandReversal;
        else if (type == "double_bottom") config.kind = StrategyKind::DoubleBottom;
        else if (type == "head_shoulders_bottom") config.kind = StrategyKind::HeadShouldersBottom;
        else if (type == "rounded_bottom") config.kind = StrategyKind::RoundedBottom;
        else config.kind = StrategyKind::VReversal;
    } else if (type == "support_resistance") {
        config.kind = StrategyKind::SupportResistance;
        config.support_resistance = parse_support_resistance_config(signal, risk);
    } else {
        throw std::invalid_argument("native full backtest is not implemented: " + type);
    }
    return config;
}

std::int64_t ordinal_value(const py::handle& value) {
    if (py::isinstance<py::int_>(value)) return py::cast<std::int64_t>(value);
    if (py::isinstance<py::str>(value)) {
        return py::cast<std::int64_t>(
            py::module_::import("datetime").attr("date").attr("fromisoformat")(value).attr("toordinal")()
        );
    }
    if (py::hasattr(value, "toordinal")) return py::cast<std::int64_t>(value.attr("toordinal")());
    throw std::invalid_argument("backtest date must be an ordinal, date, or ISO date");
}

std::int64_t option_ordinal(
    const py::dict& options,
    const char* ordinal_key,
    const char* date_key,
    std::int64_t fallback
) {
    if (options.contains(ordinal_key) && !options[ordinal_key].is_none()) {
        return ordinal_value(options[ordinal_key]);
    }
    if (options.contains(date_key) && !options[date_key].is_none()) {
        return ordinal_value(options[date_key]);
    }
    return fallback;
}

template <typename T>
MatrixView<T> matrix_view(const py::array& array, std::size_t expected_columns, const char* label) {
    if (!array.dtype().is(py::dtype::of<T>())) {
        throw std::invalid_argument(std::string(label) + " dtype is invalid");
    }
    const py::buffer_info info = array.request();
    if (info.ndim != 2 || info.shape[1] != static_cast<py::ssize_t>(expected_columns)) {
        throw std::invalid_argument(std::string(label) + " shape is invalid");
    }
    if (info.shape[0] > 1 && info.strides[0] != static_cast<py::ssize_t>(sizeof(T))) {
        throw std::invalid_argument(std::string(label) + " must use Fortran column-major storage");
    }
    if (info.shape[1] > 1
        && info.strides[1] != info.shape[0] * static_cast<py::ssize_t>(sizeof(T))) {
        throw std::invalid_argument(std::string(label) + " must use Fortran column-major storage");
    }
    return {
        static_cast<const char*>(info.ptr), info.shape[0], info.shape[1],
        info.strides[0], info.strides[1]
    };
}

DatasetView parse_dataset(const py::object& dataset) {
    DatasetView view;
    view.integers_owner = py::cast<py::array>(dataset.attr("integers"));
    view.floats_owner = py::cast<py::array>(dataset.attr("floats"));
    view.integers = matrix_view<std::int64_t>(view.integers_owner, kIntegerColumns, "dataset.integers");
    view.floats = matrix_view<double>(view.floats_owner, kFloatColumns, "dataset.floats");
    if (view.integers.rows != view.floats.rows) {
        throw std::invalid_argument("prepared dataset integer/float row count differs");
    }
    const py::dict sidecar = py::cast<py::dict>(dataset.attr("sidecar"));
    if (sidecar.contains("symbols")) view.symbols = py::cast<std::vector<std::string>>(sidecar["symbols"]);
    if (sidecar.contains("asset_types")) {
        view.asset_types = py::cast<std::vector<std::string>>(sidecar["asset_types"]);
        for (std::string& value : view.asset_types) value = upper_trimmed(std::move(value));
    }
    if (sidecar.contains("exchanges")) {
        view.exchanges = py::cast<std::vector<std::string>>(sidecar["exchanges"]);
        for (std::string& value : view.exchanges) value = upper_trimmed(std::move(value));
    }
    if (view.symbols.empty() && view.integers.rows > 0) {
        throw std::invalid_argument("prepared dataset symbol mapping is missing");
    }
    if (sidecar.contains("corporate_actions") && !sidecar["corporate_actions"].is_none()) {
        for (const py::handle raw : py::reinterpret_borrow<py::iterable>(sidecar["corporate_actions"])) {
            const py::sequence item = py::cast<py::sequence>(raw);
            if (py::len(item) != 3) throw std::invalid_argument("split adjustment must have three fields");
            const std::int64_t day = ordinal_value(item[0]);
            const std::int64_t instrument_id = py::cast<std::int64_t>(item[1]);
            const double factor = py::cast<double>(item[2]);
            if (!std::isfinite(factor) || factor <= 0.0) {
                throw std::invalid_argument("split adjustment factor must be positive and finite");
            }
            double& combined = view.split_adjustments[day][instrument_id];
            combined = combined == 0.0 ? factor : combined * factor;
        }
    }
    if (sidecar.contains("support_resistance_hydration")
        && py::isinstance<py::dict>(sidecar["support_resistance_hydration"])) {
        view.support_resistance_hydration = py::cast<py::dict>(
            sidecar["support_resistance_hydration"]
        );
    }
    return view;
}

std::vector<SessionRange> session_ranges(
    const DatasetView& dataset,
    std::int64_t start_ordinal,
    std::int64_t end_ordinal
) {
    std::vector<SessionRange> sessions;
    std::int64_t previous_session = std::numeric_limits<std::int64_t>::min();
    std::int64_t previous_instrument = std::numeric_limits<std::int64_t>::min();
    for (py::ssize_t row = 0; row < dataset.integers.rows; ++row) {
        const std::int64_t ordinal = dataset.integers.at(row, kDateOrdinal);
        if (ordinal == kDateSentinel) continue;
        const std::int64_t session = dataset.integers.at(row, kSessionIndex);
        const std::int64_t instrument = dataset.integers.at(row, kInstrumentId);
        if (session < previous_session || (session == previous_session && instrument <= previous_instrument)) {
            throw std::invalid_argument("prepared dataset must be sorted by session_index and instrument_id");
        }
        if (session != previous_session) previous_instrument = std::numeric_limits<std::int64_t>::min();
        previous_session = session;
        previous_instrument = instrument;
        if (ordinal < start_ordinal || ordinal > end_ordinal) continue;
        if (sessions.empty() || sessions.back().session_index != session) {
            sessions.push_back({
                row, row + 1, session, ordinal, dataset.integers.at(row, kTimestampUs)
            });
        } else {
            if (sessions.back().date_ordinal != ordinal) {
                throw std::invalid_argument("one prepared session contains multiple trade dates");
            }
            sessions.back().end = row + 1;
        }
    }
    if (sessions.empty()) throw std::invalid_argument("prepared dataset has no rows inside the backtest window");
    return sessions;
}

void attach_history_sessions(
    DatasetView& dataset,
    const std::vector<SessionRange>& sessions
) {
    dataset.history_sessions.assign(static_cast<std::size_t>(dataset.integers.rows), 0);
    std::unordered_map<std::int64_t, std::int32_t> history_by_instrument;
    for (const SessionRange& session : sessions) {
        for (py::ssize_t row = session.begin; row < session.end; ++row) {
            const std::int64_t instrument = dataset.integers.at(row, kInstrumentId);
            dataset.history_sessions[static_cast<std::size_t>(row)] =
                ++history_by_instrument[instrument];
        }
    }
}

std::optional<double> finite(double value) {
    return std::isfinite(value) ? std::optional<double>(value) : std::nullopt;
}

const std::string& dictionary_value(
    const std::vector<std::string>& values,
    std::int64_t index
) {
    static const std::string empty;
    if (index < 0 || static_cast<std::size_t>(index) >= values.size()) return empty;
    return values[static_cast<std::size_t>(index)];
}

UniverseExclusion universe_exclusion(
    const DatasetView& dataset,
    py::ssize_t row,
    const UniversePolicy& policy
) {
    const std::string& asset_type = dictionary_value(
        dataset.asset_types, dataset.integers.at(row, kAssetTypeId)
    );
    if (!policy.asset_types.contains(asset_type)) return UniverseExclusion::AssetType;
    const std::string& exchange = dictionary_value(
        dataset.exchanges, dataset.integers.at(row, kExchangeId)
    );
    if (!policy.exchanges.contains(exchange)) return UniverseExclusion::Exchange;
    const std::int64_t trade_date = dataset.integers.at(row, kDateOrdinal);
    const std::int64_t listed = dataset.integers.at(row, kListedOrdinal);
    if (listed != kDateSentinel && trade_date < listed) return UniverseExclusion::BeforeListing;
    const std::int64_t delisted = dataset.integers.at(row, kDelistedOrdinal);
    if (delisted != kDateSentinel && trade_date > delisted) {
        return UniverseExclusion::AfterDelisting;
    }
    const double unadjusted_close = dataset.floats.at(row, kCloseUnadjusted);
    if (!std::isfinite(unadjusted_close)
        || unadjusted_close < policy.minimum_unadjusted_close) {
        return UniverseExclusion::Price;
    }
    const double dollar_volume = dataset.floats.at(row, kDollarVolume20);
    if (!std::isfinite(dollar_volume)
        || dollar_volume < policy.minimum_dollar_volume_20) {
        return UniverseExclusion::Liquidity;
    }
    if (dataset.history_sessions[static_cast<std::size_t>(row)]
        < policy.minimum_history_sessions) {
        return UniverseExclusion::History;
    }
    return UniverseExclusion::None;
}

void append_universe_membership(
    KernelResult& result,
    const SessionRange& session,
    const std::vector<UniverseExclusion>& exclusions
) {
    std::int32_t eligible = 0;
    std::int32_t asset_type = 0;
    std::int32_t exchange = 0;
    std::int32_t before_listing = 0;
    std::int32_t after_delisting = 0;
    std::int32_t price = 0;
    std::int32_t liquidity = 0;
    std::int32_t history = 0;
    for (const UniverseExclusion exclusion : exclusions) {
        switch (exclusion) {
            case UniverseExclusion::None: ++eligible; break;
            case UniverseExclusion::AssetType: ++asset_type; break;
            case UniverseExclusion::Exchange: ++exchange; break;
            case UniverseExclusion::BeforeListing: ++before_listing; break;
            case UniverseExclusion::AfterDelisting: ++after_delisting; break;
            case UniverseExclusion::Price: ++price; break;
            case UniverseExclusion::Liquidity: ++liquidity; break;
            case UniverseExclusion::History: ++history; break;
        }
    }
    result.universe_session_index.push_back(session.session_index);
    result.universe_date_ordinal.push_back(session.date_ordinal);
    result.universe_eligible_count.push_back(eligible);
    result.universe_excluded_asset_type.push_back(asset_type);
    result.universe_excluded_exchange.push_back(exchange);
    result.universe_excluded_before_listing.push_back(before_listing);
    result.universe_excluded_after_delisting.push_back(after_delisting);
    result.universe_excluded_price.push_back(price);
    result.universe_excluded_liquidity.push_back(liquidity);
    result.universe_excluded_history.push_back(history);
}

double rounded_two(double value) {
    if (!std::isfinite(value) || value == 0.0) return value;

    const bool negative = value < 0.0;
    const std::uint64_t bits = std::bit_cast<std::uint64_t>(std::abs(value));
    const std::uint64_t exponent_bits = (bits >> 52U) & 0x7ffU;
    const std::uint64_t fraction = bits & ((std::uint64_t{1} << 52U) - 1U);
    const std::uint64_t significand = exponent_bits == 0
        ? fraction
        : fraction | (std::uint64_t{1} << 52U);
    const int exponent = exponent_bits == 0
        ? 1 - 1023 - 52
        : static_cast<int>(exponent_bits) - 1023 - 52;
    const std::uint64_t scaled_significand = significand * 100U;

    std::uint64_t cents = 0;
    if (exponent >= 0) {
        cents = scaled_significand << exponent;
    } else {
        const unsigned shift = static_cast<unsigned>(-exponent);
        if (shift < 64U) {
            const std::uint64_t quotient = scaled_significand >> shift;
            const std::uint64_t remainder = scaled_significand
                & ((std::uint64_t{1} << shift) - 1U);
            const std::uint64_t halfway = std::uint64_t{1} << (shift - 1U);
            cents = quotient
                + (remainder > halfway || (remainder == halfway && (quotient & 1U)) ? 1U : 0U);
        }
    }
    const double rounded = static_cast<double>(cents) / 100.0;
    return negative ? -rounded : rounded;
}

double rise_score(double value, double gate, double cap) {
    if (!std::isfinite(value) || cap <= gate) throw std::invalid_argument("invalid signal strength input");
    return rounded_two(100.0 * std::clamp((value - gate) / (cap - gate), 0.0, 1.0));
}

StrengthComponentAudit rise_component(
    std::string key,
    double raw_value,
    double weight,
    double gate,
    double cap
) {
    return {
        std::move(key), raw_value, rise_score(raw_value, gate, cap), weight
    };
}

double fall_score(double value, double gate, double ideal) {
    if (!std::isfinite(value) || gate <= ideal) throw std::invalid_argument("invalid signal strength input");
    return rounded_two(100.0 * std::clamp((gate - value) / (gate - ideal), 0.0, 1.0));
}

StrengthComponentAudit fall_component(
    std::string key,
    double raw_value,
    double weight,
    double gate,
    double ideal
) {
    return {std::move(key), raw_value, fall_score(raw_value, gate, ideal), weight};
}

double weighted_strength(const std::vector<StrengthComponentAudit>& components) {
    double score = 0.0;
    double weight = 0.0;
    for (const StrengthComponentAudit& component : components) {
        score += component.normalized_score * component.weight;
        weight += component.weight;
    }
    return rounded_two(score / weight);
}

bool is_pattern_strategy(const StrategyConfig& config) {
    return config.pattern.has_value();
}

PatternBar dataset_pattern_bar(const DatasetView& dataset, py::ssize_t row) {
    const auto value = [&](std::size_t column) -> std::optional<double> {
        const double raw = dataset.floats.at(row, column);
        return std::isfinite(raw) ? std::optional<double>(raw) : std::nullopt;
    };
    return PatternBar{
        dataset.integers.at(row, kDateOrdinal),
        dataset.integers.at(row, kTimestampUs),
        value(kOpen), value(kHigh), value(kLow), value(kClose), value(kVolume), value(kAtr14),
        value(kVolumeSma20), value(kReturn20d), value(kReturn60d), value(kSma20), value(kSma50),
    };
}

double required_pattern_number(const PatternObject& values, const std::string& key) {
    const auto value = pattern_number(values, key);
    if (!value || !std::isfinite(*value)) {
        throw std::invalid_argument("pattern BUY signal is missing finite strength input: " + key);
    }
    return *value;
}

void attach_pattern_strength(
    SignalRecord& result,
    const PatternConfig& config,
    const PatternDecision& decision
) {
    if (result.action != Action::Buy) return;
    const PatternObject& inputs = decision.strength_inputs;
    if (config.kind == PatternKind::IslandReversal) {
        const auto& signal = std::get<IslandConfig>(config.signal);
        const std::string stage = pattern_text(inputs, "stage").value_or("");
        result.strength_model_version = "island_reversal:" + stage + ":v1";
        if (stage == "exhaustion_gap") {
            result.strength_components = {
                rise_component(
                    "left_gap_pct", required_pattern_number(inputs, "left_gap_pct"), 0.60,
                    signal.left_gap_min_pct, signal.left_gap_min_pct * 2.0
                ),
                fall_component(
                    "left_volume_ratio", required_pattern_number(inputs, "left_volume_ratio"), 0.40,
                    signal.left_volume_ratio_max, 0.0
                ),
            };
        } else {
            const bool breakout = stage == "breakout" || stage == "upside_gap";
            result.strength_components = {
                rise_component(
                    "left_gap_pct", required_pattern_number(inputs, "left_gap_pct"), breakout ? 0.30 : 0.15,
                    signal.left_gap_min_pct, signal.left_gap_min_pct * 2.0
                ),
                rise_component(
                    "right_gap_pct", required_pattern_number(inputs, "right_gap_pct"), breakout ? 0.40 : 0.20,
                    signal.right_gap_min_pct, signal.right_gap_min_pct * 2.0
                ),
                rise_component(
                    "breakout_volume_ratio", required_pattern_number(inputs, "breakout_volume_ratio"), breakout ? 0.30 : 0.20,
                    signal.right_volume_ratio_min, signal.right_volume_ratio_min * 2.0
                ),
            };
            if (!breakout) {
                result.strength_components.push_back(fall_component(
                    "retest_volume_ratio", required_pattern_number(inputs, "retest_volume_ratio"), 0.25,
                    signal.retest_volume_ratio_max, 0.0
                ));
                result.strength_components.push_back(rise_component(
                    "hold_margin_atr", required_pattern_number(inputs, "hold_margin_atr"), 0.20, 0.0, 1.0
                ));
            }
        }
    } else if (config.kind == PatternKind::DoubleBottom) {
        const auto& signal = std::get<DoubleBottomConfig>(config.signal);
        const std::string stage = pattern_text(inputs, "stage").value_or("retest");
        result.strength_model_version = "double_bottom:" + stage + ":v1";
        if (stage == "second_bottom") {
            result.strength_components = {
                fall_component("bottom_distance_pct", required_pattern_number(inputs, "bottom_distance_pct"), 0.40, signal.bottom_tolerance_pct, 0.0),
                rise_component("rebound_up_day_ratio", required_pattern_number(inputs, "rebound_up_day_ratio"), 0.30, signal.rebound_up_day_ratio_min, 1.0),
                fall_component("current_volume_ratio", required_pattern_number(inputs, "current_volume_ratio"), 0.30, signal.second_bottom_volume_ratio_max, 0.0),
            };
        } else if (stage == "right_side_pullback") {
            result.strength_components = {
                fall_component("bottom_distance_pct", required_pattern_number(inputs, "bottom_distance_pct"), 0.25, signal.bottom_tolerance_pct, 0.0),
                rise_component("rebound_up_day_ratio", required_pattern_number(inputs, "rebound_up_day_ratio"), 0.25, signal.rebound_up_day_ratio_min, 1.0),
                fall_component("current_volume_ratio", required_pattern_number(inputs, "current_volume_ratio"), 0.25, signal.second_bottom_volume_ratio_max, 0.0),
                rise_component("pullback_hold_pct", required_pattern_number(inputs, "pullback_hold_pct"), 0.25, 0.0, 1.0),
            };
        } else if (stage == "neckline_breakout") {
            result.strength_components = {
                fall_component("bottom_distance_pct", required_pattern_number(inputs, "bottom_distance_pct"), 0.25, signal.bottom_tolerance_pct, 0.0),
                rise_component("rebound_up_day_ratio", required_pattern_number(inputs, "rebound_up_day_ratio"), 0.25, signal.rebound_up_day_ratio_min, 1.0),
                rise_component("breakout_volume_ratio", required_pattern_number(inputs, "breakout_volume_ratio"), 0.25, signal.breakout_volume_ratio_min, signal.breakout_volume_ratio_min * 2.0),
                rise_component("breakout_extension_pct", required_pattern_number(inputs, "breakout_extension_pct"), 0.25, signal.breakout_buffer_pct, signal.breakout_buffer_pct * 2.0),
            };
        } else {
            result.strength_components = {
                fall_component("bottom_distance_pct", required_pattern_number(inputs, "bottom_distance_pct"), 0.25, signal.bottom_tolerance_pct, 0.0),
                rise_component("rebound_up_day_ratio", required_pattern_number(inputs, "rebound_up_day_ratio"), 0.20, signal.rebound_up_day_ratio_min, 1.0),
                rise_component("breakout_volume_ratio", required_pattern_number(inputs, "breakout_volume_ratio"), 0.20, signal.breakout_volume_ratio_min, signal.breakout_volume_ratio_min * 2.0),
                rise_component("breakout_extension_pct", required_pattern_number(inputs, "breakout_extension_pct"), 0.15, signal.breakout_buffer_pct, signal.breakout_buffer_pct * 2.0),
                fall_component("retest_volume_ratio", required_pattern_number(inputs, "retest_volume_ratio"), 0.20, signal.retest_volume_ratio_max, 0.0),
            };
        }
    } else {
        result.strength_model_version = config.strategy_type + ":" + decision.setup.stage_key + ":v1";
        result.strength_components = {
            rise_component("structure_quality", required_pattern_number(inputs, "structure_quality"), 0.25, 0.0, 1.0),
            rise_component("price_confirmation", required_pattern_number(inputs, "price_confirmation"), 0.25, 0.0, 1.0),
            rise_component("volume_quality", required_pattern_number(inputs, "volume_quality"), 0.25, 0.0, 1.0),
            rise_component("stage_confirmation", required_pattern_number(inputs, "stage_confirmation"), 0.25, 0.0, 1.0),
        };
    }
    result.strength_score = weighted_strength(result.strength_components);
    result.passes_threshold = result.strength_score >= config.minimum_strength;
}

std::optional<SignalRecord> evaluate_pattern_signal(
    const DatasetView& dataset,
    py::ssize_t row,
    const StrategyConfig& strategy,
    const PatternState& state,
    const Position* position
) {
    const PatternConfig& config = *strategy.pattern;
    const std::int32_t symbol_id = static_cast<std::int32_t>(dataset.integers.at(row, kSymbolId));
    if (symbol_id < 0 || static_cast<std::size_t>(symbol_id) >= dataset.symbols.size()) {
        throw std::invalid_argument("prepared dataset symbol_id is out of range");
    }
    const PatternPositionView position_view{
        position ? position->quantity : 0.0,
        position ? std::optional<double>(position->average_entry_price) : std::nullopt,
        position && position->pattern_setup ? &*position->pattern_setup : nullptr,
    };
    const auto decision = evaluate_pattern(
        config, dataset.symbols[static_cast<std::size_t>(symbol_id)], state, position_view
    );
    if (!decision) return std::nullopt;
    SignalRecord result{
        dataset.integers.at(row, kSessionIndex), dataset.integers.at(row, kInstrumentId),
        dataset.integers.at(row, kTimestampUs), symbol_id,
        decision->buy ? Action::Buy : Action::Sell,
        decision->score.value_or(std::numeric_limits<double>::quiet_NaN()),
        std::numeric_limits<double>::quiet_NaN(), 0, true, decision->reason,
    };
    result.dataset_row = row;
    result.position = position_view.quantity;
    result.average_entry_price = position_view.average_entry_price.value_or(
        std::numeric_limits<double>::quiet_NaN()
    );
    result.pattern_setup = decision->setup;
    result.pattern_setup_json = pattern_setup_json(decision->setup);
    attach_pattern_strength(result, config, *decision);
    result.metadata_json = pattern_metadata_json(
        config, state.bars.back(), position_view, *decision
    );
    return result;
}

std::string percent_reason(const std::string& prefix, double value, const std::string& suffix) {
    std::ostringstream output;
    output << prefix << std::fixed << std::setprecision(1) << value * 100.0 << "%" << suffix;
    return output.str();
}

std::optional<SignalRecord> evaluate_signal(
    const DatasetView& dataset,
    py::ssize_t row,
    const StrategyConfig& config,
    const Position* position,
    std::int64_t processed_session
) {
    const std::int64_t session = dataset.integers.at(row, kSessionIndex);
    const std::int64_t instrument = dataset.integers.at(row, kInstrumentId);
    const std::int32_t symbol = static_cast<std::int32_t>(dataset.integers.at(row, kSymbolId));
    if (symbol < 0 || static_cast<std::size_t>(symbol) >= dataset.symbols.size()) {
        throw std::invalid_argument("prepared dataset symbol_id is out of range");
    }
    const double quantity = position == nullptr ? 0.0 : position->quantity;
    const std::optional<double> average_entry = position == nullptr
        ? std::nullopt : std::optional<double>(position->average_entry_price);
    const std::optional<double> close = finite(dataset.floats.at(row, kClose));
    const std::optional<double> atr = finite(dataset.floats.at(row, kAtr14));
    std::optional<Action> action;
    std::string reason;
    double raw_score = 0.0;
    double strength_score = std::numeric_limits<double>::quiet_NaN();
    bool trend_crossover = false;
    std::int64_t position_holding_days = -1;
    std::string strength_model_version;
    std::vector<StrengthComponentAudit> strength_components;

    if (config.kind == StrategyKind::Trend) {
        if (quantity > 0.0 && close && average_entry && *average_entry > 0.0
            && *close <= *average_entry * (1.0 - config.stop_loss_pct)) {
            action = Action::Sell;
            reason = "price fell below the fixed stop-loss threshold";
            raw_score = std::abs((*average_entry - *close) / *average_entry);
        } else if (quantity > 0.0 && close && average_entry && *average_entry > 0.0
            && atr && *atr > 0.0 && *close <= *average_entry - config.stop_loss_atr * *atr) {
            action = Action::Sell;
            reason = "price hit the ATR stop-loss threshold";
            raw_score = std::abs((*average_entry - *close) / *average_entry);
        } else if (quantity > 0.0 && close && average_entry && *average_entry > 0.0
            && atr && *atr > 0.0 && *close >= *average_entry + config.take_profit_atr * *atr) {
            action = Action::Sell;
            reason = "price reached the ATR take-profit threshold";
            raw_score = std::abs((*close - *average_entry) / *average_entry);
        } else {
            const double volume = dataset.floats.at(row, kVolume);
            const double average_volume = dataset.floats.at(row, kVolumeSma20);
            if (!std::isfinite(volume) || !std::isfinite(average_volume) || average_volume <= 0.0
                || volume < config.volume_multiplier * average_volume) return std::nullopt;
            const double fast = dataset.floats.at(row, config.fast_column);
            const double slow = dataset.floats.at(row, config.slow_column);
            const double previous_fast = dataset.floats.at(row, config.previous_fast_column);
            const double previous_slow = dataset.floats.at(row, config.previous_slow_column);
            if (!std::isfinite(fast) || !std::isfinite(slow)
                || !std::isfinite(previous_fast) || !std::isfinite(previous_slow)) return std::nullopt;
            if (previous_fast <= previous_slow && fast > slow) {
                action = Action::Buy;
                reason = config.fast_key + " crossed above " + config.slow_key;
            } else if (previous_fast >= previous_slow && fast < slow) {
                action = Action::Sell;
                reason = config.fast_key + " crossed below " + config.slow_key;
            } else {
                return std::nullopt;
            }
            trend_crossover = true;
            raw_score = std::abs(fast - slow);
            if (*action == Action::Buy) {
                if (!atr || *atr <= 0.0) throw std::invalid_argument("trend BUY signal is missing finite ATR strength input");
                const double separation = (fast - slow) / *atr;
                const double impulse = ((fast - slow) - (previous_fast - previous_slow)) / *atr;
                const double volume_ratio = volume / average_volume;
                strength_model_version = "trend:v1";
                strength_components = {
                    rise_component("separation_atr", separation, 0.60, 0.0, 0.5),
                    rise_component("crossover_impulse_atr", impulse, 0.20, 0.0, 0.5),
                    rise_component(
                        "volume_ratio", volume_ratio, 0.20,
                        config.volume_multiplier, config.volume_multiplier * 2.0
                    ),
                };
                strength_score = weighted_strength(strength_components);
            }
        }
    } else if (config.kind == StrategyKind::MeanReversion) {
        const std::optional<double> zscore = finite(dataset.floats.at(row, config.zscore_column));
        const std::int64_t holding_days = position == nullptr
            ? 0 : std::max<std::int64_t>(processed_session - position->entry_session, 0);
        if (position != nullptr) position_holding_days = holding_days;
        if (quantity > 0.0 && close && average_entry && *average_entry > 0.0
            && *close <= *average_entry * (1.0 - config.stop_loss_pct)) {
            action = Action::Sell;
            reason = percent_reason("price fell below the ", config.stop_loss_pct, " stop-loss threshold");
        } else if (quantity > 0.0 && close && average_entry && *average_entry > 0.0
            && *close >= *average_entry * (1.0 + config.take_profit_pct)) {
            action = Action::Sell;
            reason = percent_reason("price reached the ", config.take_profit_pct, " take-profit threshold");
        } else if (quantity > 0.0 && config.max_holding_days > 0
            && holding_days >= config.max_holding_days) {
            action = Action::Sell;
            reason = "position reached the " + std::to_string(config.max_holding_days) + "-day max holding period";
        } else if (quantity > 0.0 && zscore && *zscore >= -config.zscore_exit) {
            action = Action::Sell;
            reason = "zscore_" + std::to_string(config.zscore_lookback) + " reverted above exit threshold";
        } else if (!zscore) {
            return std::nullopt;
        } else if (*zscore <= -config.zscore_entry) {
            action = Action::Buy;
            reason = "zscore_" + std::to_string(config.zscore_lookback) + " below negative entry threshold";
        } else if (*zscore >= config.zscore_entry) {
            action = Action::Sell;
            reason = "zscore_" + std::to_string(config.zscore_lookback) + " above positive entry threshold";
        } else {
            return std::nullopt;
        }
        raw_score = zscore ? std::abs(*zscore) : 0.0;
        if (*action == Action::Buy) {
            strength_model_version = "mean_reversion:v1";
            strength_components = {
                rise_component(
                    "absolute_zscore", std::abs(*zscore), 1.0,
                    config.zscore_entry, config.zscore_entry * 2.0
                )
            };
            strength_score = weighted_strength(strength_components);
        }
    } else {
        const std::optional<double> sma = finite(dataset.floats.at(row, kSma20));
        const std::optional<double> return_20d = finite(dataset.floats.at(row, kReturn20d));
        const std::optional<double> volume = finite(dataset.floats.at(row, kVolume));
        const std::optional<double> average_volume = finite(dataset.floats.at(row, kVolumeSma20));
        if (!close || !sma || *sma <= 0.0 || !return_20d || !volume
            || !average_volume || *average_volume <= 0.0) return std::nullopt;
        const double threshold = *sma * (1.0 + config.breakout_buffer_pct);
        const double volume_ratio = *volume / *average_volume;
        if (quantity > 0.0 && average_entry && *average_entry > 0.0
            && *close <= *average_entry * (1.0 - config.stop_loss_pct)) {
            action = Action::Sell;
            reason = percent_reason("price fell below the ", config.stop_loss_pct, " stop-loss threshold");
        } else if (quantity > 0.0 && average_entry && *average_entry > 0.0
            && *close >= *average_entry * (1.0 + config.take_profit_pct)) {
            action = Action::Sell;
            reason = percent_reason("price reached the ", config.take_profit_pct, " take-profit threshold");
        } else if (quantity > 0.0 && (*close < *sma || *return_20d <= config.exit_return_20d)) {
            action = Action::Sell;
            reason = "20-day momentum or SMA20 support failed";
        } else if (quantity <= 0.0 && *close >= threshold
            && *return_20d >= config.minimum_return_20d
            && volume_ratio >= config.volume_multiplier) {
            action = Action::Buy;
            reason = "adjusted close confirmed a volume-backed 20-day momentum breakout";
        } else {
            return std::nullopt;
        }
        const double extension = *close / *sma - 1.0;
        raw_score = *return_20d + extension + volume_ratio;
        if (*action == Action::Buy) {
            strength_model_version = "momentum_breakout:v1";
            strength_components = {
                rise_component(
                    "return_20d", *return_20d, 0.40,
                    config.minimum_return_20d, config.minimum_return_20d * 2.0
                ),
                rise_component(
                    "price_extension", extension, 0.35,
                    config.breakout_buffer_pct, config.breakout_buffer_pct * 2.0
                ),
                rise_component(
                    "volume_ratio", volume_ratio, 0.25,
                    config.volume_multiplier, config.volume_multiplier * 2.0
                ),
            };
            strength_score = weighted_strength(strength_components);
        }
    }

    SignalRecord result{
        session, instrument, dataset.integers.at(row, kTimestampUs), symbol,
        *action, raw_score, strength_score, 0, true, std::move(reason)
    };
    result.dataset_row = row;
    result.position = quantity;
    result.average_entry_price = average_entry.value_or(
        std::numeric_limits<double>::quiet_NaN()
    );
    result.position_holding_days = position_holding_days;
    result.trend_crossover = trend_crossover;
    result.strength_model_version = std::move(strength_model_version);
    result.strength_components = std::move(strength_components);
    if (result.action == Action::Buy) {
        result.passes_threshold = result.strength_score >= config.minimum_strength;
    }
    return result;
}

void append_json_string(std::string& output, const std::string& value) {
    static constexpr char hex[] = "0123456789abcdef";
    output.push_back('"');
    for (const unsigned char character : value) {
        switch (character) {
            case '"': output += "\\\""; break;
            case '\\': output += "\\\\"; break;
            case '\b': output += "\\b"; break;
            case '\f': output += "\\f"; break;
            case '\n': output += "\\n"; break;
            case '\r': output += "\\r"; break;
            case '\t': output += "\\t"; break;
            default:
                if (character < 0x20) {
                    output += "\\u00";
                    output.push_back(hex[character >> 4]);
                    output.push_back(hex[character & 0x0f]);
                } else {
                    output.push_back(static_cast<char>(character));
                }
        }
    }
    output.push_back('"');
}

void append_json_number(std::string& output, double value) {
    if (!std::isfinite(value)) {
        output += "null";
        return;
    }
    std::ostringstream encoded;
    encoded.imbue(std::locale::classic());
    encoded << std::setprecision(std::numeric_limits<double>::max_digits10) << value;
    output += encoded.str();
}

void append_json_key(std::string& output, const std::string& key, bool& first) {
    if (!first) output.push_back(',');
    first = false;
    append_json_string(output, key);
    output.push_back(':');
}

void append_json_number_field(
    std::string& output,
    const std::string& key,
    double value,
    bool& first
) {
    append_json_key(output, key, first);
    append_json_number(output, value);
}

std::string strength_level(double score) {
    if (score < 50.0) return "weak";
    if (score < 70.0) return "medium";
    if (score < 85.0) return "strong";
    return "very_strong";
}

std::string build_strength_json(
    const SignalRecord& signal,
    const StrategyConfig& config
) {
    if (signal.action != Action::Buy || signal.strength_model_version.empty()) return {};
    std::string output = "{";
    bool first = true;
    append_json_number_field(output, "score", signal.strength_score, first);
    append_json_key(output, "level", first);
    append_json_string(output, strength_level(signal.strength_score));
    append_json_number_field(output, "threshold", config.minimum_strength, first);
    append_json_key(output, "passes_threshold", first);
    output += signal.passes_threshold ? "true" : "false";
    append_json_key(output, "rank", first);
    output += std::to_string(signal.strength_rank);
    append_json_key(output, "model_version", first);
    append_json_string(output, signal.strength_model_version);
    append_json_key(output, "components", first);
    output.push_back('[');
    bool first_component = true;
    for (const StrengthComponentAudit& component : signal.strength_components) {
        if (!first_component) output.push_back(',');
        first_component = false;
        output += "{";
        bool first_field = true;
        append_json_key(output, "key", first_field);
        append_json_string(output, component.key);
        append_json_number_field(output, "raw_value", component.raw_value, first_field);
        append_json_number_field(
            output, "normalized_score", component.normalized_score, first_field
        );
        append_json_number_field(output, "weight", component.weight, first_field);
        output.push_back('}');
    }
    output += "]}";
    return output;
}

std::string append_strength_json(
    const std::string& metadata_json,
    const std::string& strength_json
) {
    if (strength_json.empty()) return metadata_json;
    if (metadata_json.empty() || metadata_json.back() != '}') {
        throw std::runtime_error("signal metadata JSON is invalid");
    }
    std::string output = metadata_json;
    output.pop_back();
    output += ",\"strength\":" + strength_json + "}";
    return output;
}

support_resistance::Bar support_bar(
    const DatasetView& dataset,
    py::ssize_t row
) {
    const double high = dataset.floats.at(row, kHigh);
    const double low = dataset.floats.at(row, kLow);
    const double close = dataset.floats.at(row, kClose);
    double atr = dataset.floats.at(row, kAtr14);
    if (!std::isfinite(atr) || atr <= 0.0) {
        atr = std::isfinite(high) && std::isfinite(low) && std::isfinite(close)
            ? std::max(high - low, close * 0.005)
            : std::numeric_limits<double>::quiet_NaN();
    }
    const double volume = dataset.floats.at(row, kVolume);
    const double volume_average = dataset.floats.at(row, kVolumeSma20);
    return {
        static_cast<std::int32_t>(dataset.integers.at(row, kDateOrdinal)),
        dataset.integers.at(row, kTimestampUs),
        dataset.floats.at(row, kOpen),
        high,
        low,
        close,
        std::isfinite(volume) ? volume : 0.0,
        std::isfinite(volume_average) ? volume_average : 0.0,
        atr,
    };
}

std::optional<SignalRecord> evaluate_support_resistance_signal(
    const DatasetView& dataset,
    py::ssize_t row,
    const StrategyConfig& strategy,
    const Position* position,
    std::int64_t formal_day_index,
    support_resistance::SymbolState& state,
    bool emit_signals
) {
    const support_resistance::Bar bar = support_bar(dataset, row);
    support_resistance::PositionView position_view;
    if (position != nullptr) {
        position_view.quantity = position->quantity;
        position_view.average_entry_price = position->average_entry_price;
        position_view.holding_days = static_cast<int>(std::max(
            formal_day_index - position->entry_session,
            static_cast<std::int64_t>(0)
        ));
        position_view.entry_signal_features = position->support_entry_signal_features;
    }
    const std::optional<support_resistance::Decision> decision = support_resistance::advance_symbol(
        state, bar, position_view, strategy.support_resistance, emit_signals
    );
    if (!emit_signals || !decision) return std::nullopt;
    const Action action = decision->action == support_resistance::Action::Buy
        ? Action::Buy : Action::Sell;
    const double score = decision->score.value_or(std::numeric_limits<double>::quiet_NaN());
    SignalRecord result{
        dataset.integers.at(row, kSessionIndex),
        dataset.integers.at(row, kInstrumentId),
        dataset.integers.at(row, kTimestampUs),
        static_cast<std::int32_t>(dataset.integers.at(row, kSymbolId)),
        action,
        score,
        std::numeric_limits<double>::quiet_NaN(),
        0,
        true,
        decision->reason,
    };
    result.dataset_row = row;
    result.position = position == nullptr ? 0.0 : position->quantity;
    result.average_entry_price = position == nullptr
        ? std::numeric_limits<double>::quiet_NaN()
        : position->average_entry_price;
    result.position_holding_days = position == nullptr
        ? -1
        : std::max(
            formal_day_index - position->entry_session,
            static_cast<std::int64_t>(0)
        );

    result.support_metadata = decision->support_resistance;
    result.metadata_json = support_resistance::json(support_resistance::object({
        {"close", bar.close},
        {"open", bar.open},
        {"high", bar.high},
        {"low", bar.low},
        {"atr_14", bar.atr_14},
        {"position", result.position},
        {"avg_entry_price", position == nullptr
            ? support_resistance::JsonValue(nullptr)
            : support_resistance::JsonValue(result.average_entry_price)},
        {"support_resistance", result.support_metadata},
    }));

    if (action == Action::Buy && decision->strength) {
        const support_resistance::Strength& strength = *decision->strength;
        result.strength_score = strength.score;
        result.passes_threshold = strength.passes_threshold;
        result.strength_model_version = strength.model_version;
        for (const support_resistance::StrengthComponent& component : strength.components) {
            result.strength_components.push_back({
                component.key,
                component.raw_value,
                component.normalized_score,
                component.weight,
            });
        }
        if (decision->entry_channel) {
            const support_resistance::EntryChannel& channel = *decision->entry_channel;
            result.support_entry_channel = channel;
            result.support_entry_channel_json = support_resistance::json(
                support_resistance::entry_channel_json(channel)
            );
            if (decision->setup) {
                result.support_setup_kind = decision->setup;
                result.support_setup = std::string(support_resistance::name(*decision->setup));
            }
            if (channel.valid) {
                result.has_entry_channel = true;
                result.entry_channel_lower = channel.lower;
                result.entry_channel_upper = channel.upper;
                result.entry_channel_lower_slope = channel.lower_slope_per_session;
                result.entry_channel_upper_slope = channel.upper_slope_per_session;
            }
        }
    }
    return result;
}

std::string build_signal_metadata_json(
    const DatasetView& dataset,
    const SignalRecord& signal,
    const StrategyConfig& config,
    const std::string& strength_json
) {
    const py::ssize_t row = signal.dataset_row;
    std::string output = "{";
    bool first = true;
    append_json_number_field(output, "close", dataset.floats.at(row, kClose), first);
    if (config.kind != StrategyKind::MomentumBreakout) {
        append_json_number_field(output, "atr_14", dataset.floats.at(row, kAtr14), first);
    }
    if (config.kind == StrategyKind::Trend) {
        append_json_number_field(output, "position", signal.position, first);
        append_json_number_field(
            output, "avg_entry_price", signal.average_entry_price, first
        );
        append_json_key(output, "config", first);
        output += "{";
        bool config_first = true;
        append_json_number_field(
            output, "volume_multiplier", config.volume_multiplier, config_first
        );
        append_json_number_field(output, "atr_multiplier", config.atr_multiplier, config_first);
        append_json_number_field(output, "stop_loss_pct", config.stop_loss_pct, config_first);
        append_json_number_field(output, "stop_loss_atr", config.stop_loss_atr, config_first);
        append_json_number_field(output, "take_profit_atr", config.take_profit_atr, config_first);
        output.push_back('}');
        if (signal.trend_crossover) {
            const double fast = dataset.floats.at(row, config.fast_column);
            const double slow = dataset.floats.at(row, config.slow_column);
            const double previous_fast = dataset.floats.at(row, config.previous_fast_column);
            const double previous_slow = dataset.floats.at(row, config.previous_slow_column);
            const double atr = dataset.floats.at(row, kAtr14);
            const double volume = dataset.floats.at(row, kVolume);
            const double average_volume = dataset.floats.at(row, kVolumeSma20);
            append_json_key(output, "strength_inputs", first);
            output += "{";
            bool inputs_first = true;
            append_json_number_field(
                output, "separation_atr", atr > 0.0 ? (fast - slow) / atr
                    : std::numeric_limits<double>::quiet_NaN(), inputs_first
            );
            append_json_number_field(
                output, "crossover_impulse_atr",
                atr > 0.0 ? ((fast - slow) - (previous_fast - previous_slow)) / atr
                    : std::numeric_limits<double>::quiet_NaN(),
                inputs_first
            );
            append_json_number_field(
                output, "volume_ratio", volume / average_volume, inputs_first
            );
            output.push_back('}');
        }
    } else if (config.kind == StrategyKind::MeanReversion) {
        append_json_number_field(output, "rsi_14", dataset.floats.at(row, kRsi14), first);
        append_json_number_field(
            output,
            "zscore_" + std::to_string(config.zscore_lookback),
            dataset.floats.at(row, config.zscore_column),
            first
        );
        append_json_number_field(output, "position", signal.position, first);
        append_json_number_field(
            output, "avg_entry_price", signal.average_entry_price, first
        );
        append_json_key(output, "position_holding_days", first);
        output += signal.position_holding_days < 0
            ? "null" : std::to_string(signal.position_holding_days);
        append_json_key(output, "config", first);
        output += "{";
        bool config_first = true;
        append_json_key(output, "lookback_window", config_first);
        output += std::to_string(config.zscore_lookback);
        append_json_number_field(output, "zscore_entry", config.zscore_entry, config_first);
        append_json_number_field(output, "zscore_exit", config.zscore_exit, config_first);
        append_json_number_field(output, "stop_loss_pct", config.stop_loss_pct, config_first);
        append_json_number_field(output, "take_profit_pct", config.take_profit_pct, config_first);
        append_json_key(output, "max_holding_days", config_first);
        output += std::to_string(config.max_holding_days);
        output.push_back('}');
        append_json_key(output, "strength_inputs", first);
        output += "{";
        bool inputs_first = true;
        const double zscore = dataset.floats.at(row, config.zscore_column);
        append_json_number_field(
            output, "absolute_zscore", std::isfinite(zscore) ? std::abs(zscore) : zscore,
            inputs_first
        );
        output.push_back('}');
    } else {
        const double sma = dataset.floats.at(row, kSma20);
        const double return_20d = dataset.floats.at(row, kReturn20d);
        const double volume = dataset.floats.at(row, kVolume);
        const double average_volume = dataset.floats.at(row, kVolumeSma20);
        const double volume_ratio = volume / average_volume;
        const double price_extension = dataset.floats.at(row, kClose) / sma - 1.0;
        append_json_number_field(output, "sma_20", sma, first);
        append_json_number_field(output, "ret_20d", return_20d, first);
        append_json_number_field(output, "volume", volume, first);
        append_json_number_field(output, "volume_sma_20", average_volume, first);
        append_json_number_field(output, "volume_ratio", volume_ratio, first);
        append_json_number_field(
            output, "breakout_threshold", sma * (1.0 + config.breakout_buffer_pct), first
        );
        append_json_number_field(output, "position", signal.position, first);
        append_json_number_field(
            output, "avg_entry_price", signal.average_entry_price, first
        );
        append_json_key(output, "price_semantics", first);
        append_json_string(output, "forward_adjusted_fallback_unadjusted");
        append_json_key(output, "config", first);
        output += "{";
        bool config_first = true;
        append_json_number_field(
            output, "minimum_return_20d", config.minimum_return_20d, config_first
        );
        append_json_number_field(
            output, "breakout_buffer_pct", config.breakout_buffer_pct, config_first
        );
        append_json_number_field(
            output, "volume_multiplier", config.volume_multiplier, config_first
        );
        append_json_number_field(
            output, "exit_return_20d", config.exit_return_20d, config_first
        );
        append_json_number_field(output, "stop_loss_pct", config.stop_loss_pct, config_first);
        append_json_number_field(output, "take_profit_pct", config.take_profit_pct, config_first);
        output.push_back('}');
        append_json_key(output, "strength_inputs", first);
        output += "{";
        bool inputs_first = true;
        append_json_number_field(output, "return_20d", return_20d, inputs_first);
        append_json_number_field(output, "price_extension", price_extension, inputs_first);
        append_json_number_field(output, "volume_ratio", volume_ratio, inputs_first);
        output.push_back('}');
    }
    if (!strength_json.empty()) {
        append_json_key(output, "strength", first);
        output += strength_json;
    }
    output.push_back('}');
    return output;
}

std::string build_entry_signal_features_json(
    const std::string& metadata_json,
    const std::string& strength_json,
    const std::string& setup_json = "{}"
) {
    if (metadata_json.empty() || metadata_json.back() != '}') {
        throw std::runtime_error("signal metadata JSON is invalid");
    }
    std::string output = metadata_json;
    output.pop_back();
    output += ",\"entry_history\":[{\"setup\":";
    output += setup_json;
    output += ",\"strength\":";
    output += strength_json.empty() ? "{}" : strength_json;
    output += "}]}";
    return output;
}

std::string build_merged_entry_signal_features_json(
    const std::string& metadata_json,
    const std::vector<std::string>& history
) {
    if (metadata_json.empty() || metadata_json.back() != '}') {
        throw std::runtime_error("signal metadata JSON is invalid");
    }
    std::string output = metadata_json;
    output.pop_back();
    output += ",\"entry_history\":[";
    for (std::size_t index = 0; index < history.size(); ++index) {
        if (index > 0U) output.push_back(',');
        output += history[index];
    }
    output += "]}";
    return output;
}

std::string entry_history_item(const SignalRecord& signal) {
    return "{\"setup\":" + (signal.pattern_setup_json.empty() ? "{}" : signal.pattern_setup_json)
        + ",\"strength\":" + (signal.strength_json.empty() ? "{}" : signal.strength_json) + "}";
}

std::string iso_date_from_ordinal(std::int64_t ordinal) {
    std::int64_t days = ordinal - 719'163;
    days += 719'468;
    const std::int64_t era = (days >= 0 ? days : days - 146'096) / 146'097;
    const unsigned day_of_era = static_cast<unsigned>(days - era * 146'097);
    const unsigned year_of_era = (
        day_of_era - day_of_era / 1'460 + day_of_era / 36'524 - day_of_era / 146'096
    ) / 365;
    std::int64_t year = static_cast<std::int64_t>(year_of_era) + era * 400;
    const unsigned day_of_year = day_of_era - (
        365 * year_of_era + year_of_era / 4 - year_of_era / 100
    );
    const unsigned month_parameter = (5 * day_of_year + 2) / 153;
    const unsigned day = day_of_year - (153 * month_parameter + 2) / 5 + 1;
    const int month = static_cast<int>(month_parameter) + (month_parameter < 10 ? 3 : -9);
    year += month <= 2;
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << std::setfill('0') << std::setw(4) << year << '-'
        << std::setw(2) << month << '-' << std::setw(2) << day;
    return output.str();
}

std::string positions_json(
    const DatasetView& dataset,
    const std::map<std::int64_t, Position>& positions,
    const std::map<std::int64_t, double>& marks
) {
    std::string output = "{";
    bool first_position = true;
    for (const auto& [instrument, position] : positions) {
        const auto mark = marks.find(instrument);
        if (mark == marks.end()) continue;
        if (!first_position) output.push_back(',');
        first_position = false;
        append_json_string(
            output,
            dataset.symbols.at(static_cast<std::size_t>(position.symbol_id))
        );
        output += ":{";
        bool first = true;
        append_json_number_field(output, "qty", position.quantity, first);
        append_json_key(output, "instrument_id", first);
        output += std::to_string(instrument);
        append_json_number_field(
            output, "avg_entry_price", position.average_entry_price, first
        );
        append_json_key(output, "entry_trade_date", first);
        append_json_string(output, iso_date_from_ordinal(position.entry_date_ordinal));
        append_json_key(output, "entry_signal_features", first);
        output += position.entry_signal_features_json.empty()
            ? "null" : position.entry_signal_features_json;
        append_json_number_field(output, "close", mark->second, first);
        append_json_number_field(
            output, "market_value", position.quantity * mark->second, first
        );
        append_json_key(output, "latest_signal", first);
        output += "null}";
    }
    output.push_back('}');
    return output;
}

std::string snapshot_metrics_json(const KernelResult& result) {
    std::string output = "{";
    bool first = true;
    append_json_key(output, "holdings_count", first);
    output += std::to_string(result.equity_holdings_count.back());
    append_json_key(output, "signal_count_cumulative", first);
    output += std::to_string(result.signal_session_index.size());
    append_json_key(output, "trade_count_cumulative", first);
    output += std::to_string(result.trade_session_index.size());
    append_json_number_field(output, "fees_cumulative", result.total_fees, first);
    append_json_number_field(output, "slippage_cumulative", result.total_slippage, first);
    append_json_number_field(
        output, "delisting_zero_write_off", result.delisting_zero_write_off, first
    );
    if (result.has_universe_membership && !result.universe_session_index.empty()) {
        append_json_key(output, "universe", first);
        output += "{";
        bool first_universe = true;
        const auto append_count = [&](const char* key, std::int32_t value) {
            append_json_key(output, key, first_universe);
            output += std::to_string(value);
        };
        append_count("eligible", result.universe_eligible_count.back());
        append_count("excluded_asset_type", result.universe_excluded_asset_type.back());
        append_count("excluded_exchange", result.universe_excluded_exchange.back());
        append_count("excluded_before_listing", result.universe_excluded_before_listing.back());
        append_count("excluded_after_delisting", result.universe_excluded_after_delisting.back());
        append_count("excluded_price", result.universe_excluded_price.back());
        append_count("excluded_liquidity", result.universe_excluded_liquidity.back());
        append_count("excluded_history", result.universe_excluded_history.back());
        output.push_back('}');
    }
    output.push_back('}');
    return output;
}

double commission(double notional, const CostConfig& costs) {
    if (notional <= 0.0) return 0.0;
    return std::max(notional * costs.commission_bps / 10'000.0, costs.commission_min);
}

struct BuyEstimate {
    double quantity;
    double price;
    double fee;
    double notional;
};

BuyEstimate estimate_buy(double cash, double mark, const CostConfig& costs) {
    const double price = mark * (1.0 + costs.slippage_bps / 10'000.0);
    if (cash <= 0.0 || price <= 0.0) return {0.0, price, 0.0, 0.0};
    if (costs.commission_bps <= 0.0) {
        double quantity = std::max((cash - costs.commission_min) / price, 0.0);
        if (quantity <= 0.0) return {0.0, price, 0.0, 0.0};
        double notional = quantity * price;
        double fee = commission(notional, costs);
        if (notional + fee > cash) {
            quantity = std::max((cash - fee) / price, 0.0);
            notional = quantity * price;
            fee = commission(notional, costs);
        }
        return {quantity, price, fee, notional};
    }
    const double quantity = cash / (price * (1.0 + costs.commission_bps / 10'000.0));
    const double notional = quantity * price;
    const double fee = commission(notional, costs);
    if (notional > 0.0 && fee >= costs.commission_min) return {quantity, price, fee, notional};
    if (cash <= costs.commission_min) return {0.0, price, 0.0, 0.0};
    double minimum_quantity = (cash - costs.commission_min) / price;
    double minimum_notional = std::max(minimum_quantity, 0.0) * price;
    double minimum_fee = commission(minimum_notional, costs);
    if (minimum_notional + minimum_fee > cash) {
        minimum_quantity = std::max((cash - minimum_fee) / price, 0.0);
        minimum_notional = minimum_quantity * price;
        minimum_fee = commission(minimum_notional, costs);
    }
    return {minimum_quantity, price, minimum_fee, minimum_notional};
}

void append_signal(KernelResult& result, const SignalRecord& signal) {
    result.signal_session_index.push_back(signal.session_index);
    result.signal_instrument_id.push_back(signal.instrument_id);
    result.signal_timestamp_us.push_back(signal.timestamp_us);
    result.signal_symbol_id.push_back(signal.symbol_id);
    result.signal_action.push_back(static_cast<std::int8_t>(signal.action));
    result.signal_score.push_back(signal.score);
    result.signal_strength_score.push_back(signal.strength_score);
    result.signal_strength_rank.push_back(signal.strength_rank);
    result.signal_passes_threshold.push_back(signal.passes_threshold ? 1U : 0U);
    result.signal_reason.push_back(signal.reason);
    result.signal_metadata_json.push_back(signal.metadata_json);
}

void append_trade(
    KernelResult& result,
    const SessionRange& session,
    const SignalRecord& signal,
    std::int32_t symbol_id,
    double quantity,
    double price,
    double fee,
    double reference,
    double slippage,
    std::int64_t execution_timestamp_us,
    double position_quantity_before,
    double position_quantity_after,
    double position_average_entry_price_after,
    double stage_target_pct,
    double slippage_bps,
    double gross_notional,
    double net_cash_flow,
    const std::string& entry_signal_features_json,
    const std::string& setup_id,
    std::int8_t stage_index,
    const std::string& stage_key
) {
    result.trade_session_index.push_back(session.session_index);
    result.trade_signal_session_index.push_back(signal.session_index);
    result.trade_instrument_id.push_back(signal.instrument_id);
    result.trade_symbol_id.push_back(symbol_id);
    result.trade_side.push_back(static_cast<std::int8_t>(signal.action));
    result.trade_quantity.push_back(quantity);
    result.trade_price.push_back(price);
    result.trade_fee.push_back(fee);
    result.trade_reference_price.push_back(reference);
    result.trade_slippage_cost.push_back(slippage);
    result.trade_execution_timestamp_us.push_back(execution_timestamp_us);
    result.trade_signal_timestamp_us.push_back(signal.timestamp_us);
    result.trade_execution_date_ordinal.push_back(session.date_ordinal);
    result.trade_reason.push_back(signal.reason);
    result.trade_entry_signal_features_json.push_back(entry_signal_features_json);
    result.trade_setup_id.push_back(setup_id);
    result.trade_stage_index.push_back(stage_index);
    result.trade_stage_key.push_back(stage_key);
    result.trade_position_quantity_before.push_back(position_quantity_before);
    result.trade_position_quantity_after.push_back(position_quantity_after);
    result.trade_position_average_entry_price_after.push_back(
        position_average_entry_price_after
    );
    result.trade_stage_target_pct.push_back(stage_target_pct);
    result.trade_slippage_bps.push_back(slippage_bps);
    result.trade_gross_notional.push_back(gross_notional);
    result.trade_net_cash_flow.push_back(net_cash_flow);
}

double gross_exposure(
    const std::map<std::int64_t, Position>& positions,
    const std::map<std::int64_t, double>& marks
) {
    double result = 0.0;
    for (const auto& [instrument, position] : positions) {
        const auto mark = marks.find(instrument);
        if (mark != marks.end()) result += position.quantity * mark->second;
    }
    return result;
}

std::shared_ptr<KernelResult> run_native_backtest(
    const DatasetView& dataset,
    const StrategyConfig& strategy,
    const std::optional<UniversePolicy>& universe_policy,
    const std::vector<SessionRange>& warmup_sessions,
    const std::vector<SessionRange>& sessions,
    double initial_cash,
    const CostConfig& costs,
    const py::object& control_callback
) {
    auto result = std::make_shared<KernelResult>();
    result->initial_cash = initial_cash;
    result->symbols = dataset.symbols;
    result->trading_days = static_cast<std::int64_t>(sessions.size());
    result->has_universe_membership = universe_policy.has_value();
    double cash = initial_cash;
    double peak_equity = initial_cash;
    std::map<std::int64_t, Position> positions;
    std::map<std::int64_t, double> last_marks;
    std::map<std::int64_t, std::int64_t> delisted_ordinals;
    std::map<std::int64_t, PatternState> pattern_states;
    std::map<std::int64_t, support_resistance::SymbolState> support_states;
    std::map<std::int64_t, std::int32_t> latest_symbol_ids;
    std::vector<SignalRecord> pending;
    const bool has_control_callback = !control_callback.is_none();
    for (py::ssize_t row = 0; row < dataset.integers.rows; ++row) {
        const std::int64_t instrument = dataset.integers.at(row, kInstrumentId);
        latest_symbol_ids[instrument] = static_cast<std::int32_t>(
            dataset.integers.at(row, kSymbolId)
        );
        const std::int64_t delisted = dataset.integers.at(row, kDelistedOrdinal);
        if (delisted != kDateSentinel) delisted_ordinals[instrument] = delisted;
        if (strategy.kind == StrategyKind::SupportResistance
            && !support_states.contains(instrument)) {
            support_resistance::SymbolState state;
            const std::string instrument_key = std::to_string(instrument);
            const std::int64_t symbol_id = dataset.integers.at(row, kSymbolId);
            const std::string symbol = dataset.symbols.at(static_cast<std::size_t>(symbol_id));
            if (dataset.support_resistance_hydration.contains(py::str(instrument_key))) {
                hydrate_support_resistance_symbol_state(
                    state,
                    py::cast<py::dict>(
                        dataset.support_resistance_hydration[py::str(instrument_key)]
                    )
                );
            } else if (dataset.support_resistance_hydration.contains(py::str(symbol))) {
                hydrate_support_resistance_symbol_state(
                    state,
                    py::cast<py::dict>(dataset.support_resistance_hydration[py::str(symbol)])
                );
            }
            support_states.emplace(instrument, std::move(state));
        }
    }

    {
        py::gil_scoped_release release;
        if (is_pattern_strategy(strategy)) {
            for (const SessionRange& session : warmup_sessions) {
                for (py::ssize_t row = session.begin; row < session.end; ++row) {
                    append_pattern_bar(
                        pattern_states[dataset.integers.at(row, kInstrumentId)],
                        *strategy.pattern,
                        dataset_pattern_bar(dataset, row)
                    );
                }
            }
        } else if (strategy.kind == StrategyKind::SupportResistance) {
            for (const SessionRange& session : warmup_sessions) {
                for (py::ssize_t row = session.begin; row < session.end; ++row) {
                    const std::int64_t instrument = dataset.integers.at(row, kInstrumentId);
                    static_cast<void>(evaluate_support_resistance_signal(
                        dataset,
                        row,
                        strategy,
                        nullptr,
                        -1,
                        support_states.at(instrument),
                        false
                    ));
                }
            }
        }
        for (std::size_t day = 0; day < sessions.size(); ++day) {
            const SessionRange& session = sessions[day];
            std::map<std::int64_t, py::ssize_t> rows;
            for (py::ssize_t row = session.begin; row < session.end; ++row) {
                rows.emplace(dataset.integers.at(row, kInstrumentId), row);
            }
            std::vector<UniverseExclusion> universe_exclusions;
            if (universe_policy) {
                universe_exclusions.reserve(static_cast<std::size_t>(session.end - session.begin));
                for (py::ssize_t row = session.begin; row < session.end; ++row) {
                    universe_exclusions.push_back(universe_exclusion(dataset, row, *universe_policy));
                }
                append_universe_membership(*result, session, universe_exclusions);
            }

            const auto split_day = dataset.split_adjustments.find(session.date_ordinal);
            if (split_day != dataset.split_adjustments.end()) {
                for (const auto& [instrument, factor] : split_day->second) {
                    const auto found = positions.find(instrument);
                    if (found == positions.end() || std::abs(factor - 1.0) <= 1e-12) continue;
                    found->second.quantity *= factor;
                    found->second.average_entry_price /= factor;
                }
            }

            for (auto iterator = positions.begin(); iterator != positions.end();) {
                const auto delisted = delisted_ordinals.find(iterator->first);
                if (rows.contains(iterator->first) || delisted == delisted_ordinals.end()
                    || session.date_ordinal <= delisted->second) {
                    ++iterator;
                    continue;
                }
                const double stale = iterator->second.quantity * last_marks[iterator->first];
                result->delisting_zero_write_off += stale;
                last_marks.erase(iterator->first);
                iterator = positions.erase(iterator);
            }

            std::map<std::int64_t, double> execution_marks;
            for (const auto& [instrument, position] : positions) {
                const auto row = rows.find(instrument);
                const std::optional<double> open = row == rows.end()
                    ? std::nullopt : finite(dataset.floats.at(row->second, kOpen));
                execution_marks[instrument] = open ? *open : last_marks[instrument];
            }

            for (const SignalRecord& signal : pending) {
                if (signal.action != Action::Sell) continue;
                const auto position = positions.find(signal.instrument_id);
                const auto row = rows.find(signal.instrument_id);
                if (position == positions.end() || row == rows.end()) continue;
                const std::optional<double> mark = finite(dataset.floats.at(row->second, kOpen));
                if (!mark) continue;
                const double price = *mark * (1.0 - costs.slippage_bps / 10'000.0);
                const double notional = position->second.quantity * price;
                const double fee = commission(notional, costs);
                const double proceeds = std::max(notional - fee, 0.0);
                const double slippage = position->second.quantity * std::max(*mark - price, 0.0);
                cash += proceeds;
                result->total_fees += fee;
                result->total_slippage += slippage;
                append_trade(
                    *result, session, signal,
                    static_cast<std::int32_t>(dataset.integers.at(row->second, kSymbolId)),
                    position->second.quantity, price, fee, *mark, slippage,
                    dataset.integers.at(row->second, kTimestampUs),
                    position->second.quantity, 0.0,
                    std::numeric_limits<double>::quiet_NaN(),
                    std::numeric_limits<double>::quiet_NaN(),
                    costs.slippage_bps, notional, proceeds, "", "", 0, ""
                );
                positions.erase(position);
                last_marks.erase(signal.instrument_id);
                execution_marks.erase(signal.instrument_id);
            }

            const double equity_before = cash + gross_exposure(positions, execution_marks);
            std::vector<const SignalRecord*> buys;
            std::map<std::pair<std::int64_t, std::string>, const SignalRecord*> staged_buys;
            for (const SignalRecord& signal : pending) {
                if (signal.action != Action::Buy) continue;
                if (!signal.pattern_setup) {
                    buys.push_back(&signal);
                    continue;
                }
                const auto key = std::pair(signal.instrument_id, signal.pattern_setup->setup_id);
                const auto found = staged_buys.find(key);
                if (found == staged_buys.end()
                    || found->second->pattern_setup->stage_index < signal.pattern_setup->stage_index) {
                    staged_buys[key] = &signal;
                }
            }
            for (const auto& [key, signal] : staged_buys) buys.push_back(signal);
            std::sort(buys.begin(), buys.end(), [&dataset](const SignalRecord* left, const SignalRecord* right) {
                return std::tuple(
                    left->strength_rank == 0 ? std::numeric_limits<std::int32_t>::max() : left->strength_rank,
                    -left->strength_score,
                    left->instrument_id,
                    dataset.symbols[static_cast<std::size_t>(left->symbol_id)]
                ) < std::tuple(
                    right->strength_rank == 0 ? std::numeric_limits<std::int32_t>::max() : right->strength_rank,
                    -right->strength_score,
                    right->instrument_id,
                    dataset.symbols[static_cast<std::size_t>(right->symbol_id)]
                );
            });
            for (const SignalRecord* signal : buys) {
                if (!signal->passes_threshold) continue;
                auto position = positions.find(signal->instrument_id);
                if (position != positions.end()) {
                    if (!signal->pattern_setup || !position->second.pattern_setup
                        || signal->pattern_setup->setup_id != position->second.pattern_setup->setup_id
                        || signal->pattern_setup->stage_index < position->second.pattern_setup->stage_index) continue;
                } else if (positions.size() >= static_cast<std::size_t>(strategy.max_positions)) {
                    continue;
                }
                const auto row = rows.find(signal->instrument_id);
                if (row == rows.end()) continue;
                const std::optional<double> mark = finite(dataset.floats.at(row->second, kOpen));
                if (!mark || *mark <= 0.0) continue;
                const double current_quantity = position == positions.end() ? 0.0 : position->second.quantity;
                const double target_fraction = signal->pattern_setup
                    ? signal->pattern_setup->stage_target_pct : 1.0;
                const double desired = equity_before * strategy.position_size_pct * target_fraction;
                const double incremental = std::max(desired - current_quantity * *mark, 0.0);
                const double target = std::min(cash, incremental);
                const BuyEstimate order = estimate_buy(target, *mark, costs);
                const double cash_out = order.notional + order.fee;
                if (order.quantity <= 0.0 || cash_out <= 0.0 || cash_out > cash) continue;
                if (strategy.kind == StrategyKind::SupportResistance) {
                    const double projected_lower = signal->entry_channel_lower
                        + signal->entry_channel_lower_slope;
                    const double projected_upper = signal->entry_channel_upper
                        + signal->entry_channel_upper_slope;
                    std::string rejection_reason;
                    if (!signal->has_entry_channel) {
                        rejection_reason = "missing_valid_entry_channel";
                    } else if (!std::isfinite(projected_lower)
                        || !std::isfinite(projected_upper)
                        || projected_lower <= 0.0 || projected_upper <= 0.0) {
                        rejection_reason = "invalid_channel_projection_values";
                    } else if (projected_lower >= projected_upper) {
                        rejection_reason = "projected_inner_edges_crossed";
                    } else if (order.price < projected_lower || order.price > projected_upper) {
                        rejection_reason = "entry_price_outside_valid_channel";
                    }
                    if (!rejection_reason.empty()) {
                        if (!signal->support_entry_channel || !signal->support_setup_kind) {
                            throw std::runtime_error(
                                "support/resistance BUY signal is missing typed execution state"
                            );
                        }
                        support_resistance::record_execution_rejection(
                            support_states.at(signal->instrument_id),
                            *signal->support_entry_channel,
                            *signal->support_setup_kind,
                            static_cast<std::int32_t>(
                                dataset.integers.at(signal->dataset_row, kDateOrdinal)
                            ),
                            static_cast<std::int32_t>(session.date_ordinal),
                            *mark,
                            order.price,
                            rejection_reason
                        );
                        continue;
                    }
                }
                cash -= cash_out;
                const double slippage = order.quantity * std::max(order.price - *mark, 0.0);
                const double new_quantity = current_quantity + order.quantity;
                const double current_average = position == positions.end()
                    ? 0.0 : position->second.average_entry_price;
                const double new_average = (
                    current_quantity * current_average + order.quantity * order.price
                ) / new_quantity;
                if (position == positions.end()) {
                    Position created{
                        new_quantity, new_average, static_cast<std::int64_t>(day),
                        static_cast<std::int32_t>(dataset.integers.at(row->second, kSymbolId)),
                        session.date_ordinal, signal->pattern_setup, {}, "", std::nullopt,
                    };
                    position = positions.emplace(signal->instrument_id, std::move(created)).first;
                } else {
                    position->second.quantity = new_quantity;
                    position->second.average_entry_price = new_average;
                    position->second.symbol_id = static_cast<std::int32_t>(
                        dataset.integers.at(row->second, kSymbolId)
                    );
                    position->second.pattern_setup = signal->pattern_setup;
                }
                position->second.entry_history_json.push_back(entry_history_item(*signal));
                position->second.entry_signal_features_json = build_merged_entry_signal_features_json(
                    signal->metadata_json, position->second.entry_history_json
                );
                if (strategy.kind == StrategyKind::SupportResistance) {
                    position->second.support_entry_signal_features = support_resistance::object({
                        {"support_resistance", signal->support_metadata},
                    });
                }
                result->total_fees += order.fee;
                result->total_slippage += slippage;
                append_trade(
                    *result, session, *signal,
                    static_cast<std::int32_t>(dataset.integers.at(row->second, kSymbolId)),
                    order.quantity, order.price, order.fee, *mark, slippage,
                    dataset.integers.at(row->second, kTimestampUs),
                    current_quantity, new_quantity, new_average,
                    signal->pattern_setup
                        ? target_fraction : std::numeric_limits<double>::quiet_NaN(),
                    costs.slippage_bps, order.notional, -cash_out,
                    position->second.entry_signal_features_json,
                    signal->pattern_setup ? signal->pattern_setup->setup_id : "",
                    static_cast<std::int8_t>(signal->pattern_setup ? signal->pattern_setup->stage_index : 0),
                    signal->pattern_setup ? signal->pattern_setup->stage_key : ""
                );
            }

            std::vector<SignalRecord> current;
            current.reserve(static_cast<std::size_t>(session.end - session.begin));
            for (py::ssize_t row = session.begin; row < session.end; ++row) {
                const std::int64_t instrument = dataset.integers.at(row, kInstrumentId);
                const auto position = positions.find(instrument);
                std::optional<SignalRecord> decision;
                if (is_pattern_strategy(strategy)) {
                    PatternState& state = pattern_states[instrument];
                    append_pattern_bar(state, *strategy.pattern, dataset_pattern_bar(dataset, row));
                    decision = evaluate_pattern_signal(
                        dataset, row, strategy, state,
                        position == positions.end() ? nullptr : &position->second
                    );
                } else if (strategy.kind == StrategyKind::SupportResistance) {
                    decision = evaluate_support_resistance_signal(
                        dataset,
                        row,
                        strategy,
                        position == positions.end() ? nullptr : &position->second,
                        static_cast<std::int64_t>(day),
                        support_states.at(instrument),
                        true
                    );
                } else {
                    decision = evaluate_signal(
                        dataset, row, strategy,
                        position == positions.end() ? nullptr : &position->second,
                        static_cast<std::int64_t>(day)
                    );
                }
                if (!decision) continue;
                const std::size_t session_row = static_cast<std::size_t>(row - session.begin);
                if (decision->action == Action::Buy && universe_policy
                    && universe_exclusions[session_row] != UniverseExclusion::None) {
                    continue;
                }
                current.push_back(*decision);
            }
            std::vector<SignalRecord*> entries;
            for (SignalRecord& signal : current) {
                if (signal.action == Action::Buy) entries.push_back(&signal);
            }
            std::sort(entries.begin(), entries.end(), [&dataset](const SignalRecord* left, const SignalRecord* right) {
                return std::tuple(
                    -left->strength_score,
                    left->instrument_id,
                    dataset.symbols[static_cast<std::size_t>(left->symbol_id)]
                ) < std::tuple(
                    -right->strength_score,
                    right->instrument_id,
                    dataset.symbols[static_cast<std::size_t>(right->symbol_id)]
                );
            });
            for (std::size_t index = 0; index < entries.size(); ++index) {
                entries[index]->strength_rank = static_cast<std::int32_t>(index + 1);
            }
            for (SignalRecord& signal : current) {
                const std::string strength_json = build_strength_json(signal, strategy);
                signal.strength_json = strength_json;
                if (is_pattern_strategy(strategy)
                    || strategy.kind == StrategyKind::SupportResistance) {
                    signal.metadata_json = append_strength_json(signal.metadata_json, strength_json);
                } else {
                    signal.metadata_json = build_signal_metadata_json(
                        dataset, signal, strategy, strength_json
                    );
                }
                if (signal.action == Action::Buy) {
                    signal.entry_signal_features_json = build_entry_signal_features_json(
                        signal.metadata_json, strength_json,
                        signal.pattern_setup_json.empty() ? "{}" : signal.pattern_setup_json
                    );
                }
                append_signal(*result, signal);
            }
            pending = std::move(current);

            for (auto& [instrument, position] : positions) {
                const auto row = rows.find(instrument);
                if (row != rows.end()) {
                    position.symbol_id = static_cast<std::int32_t>(dataset.integers.at(row->second, kSymbolId));
                    const std::optional<double> close = finite(dataset.floats.at(row->second, kClose));
                    if (close) last_marks[instrument] = *close;
                    else if (!last_marks.contains(instrument)) {
                        const std::optional<double> open = finite(dataset.floats.at(row->second, kOpen));
                        if (open) last_marks[instrument] = *open;
                    }
                }
            }
            const double exposure = gross_exposure(positions, last_marks);
            const double equity = cash + exposure;
            peak_equity = std::max(peak_equity, equity);
            const double drawdown = peak_equity <= 0.0 ? 0.0 : (peak_equity - equity) / peak_equity;
            result->max_drawdown = std::max(result->max_drawdown, drawdown);
            result->equity_session_index.push_back(session.session_index);
            result->equity_timestamp_us.push_back(session.timestamp_us);
            result->equity_cash.push_back(cash);
            result->equity_value.push_back(equity);
            result->equity_drawdown.push_back(drawdown);
            result->equity_gross_exposure.push_back(exposure);
            result->equity_holdings_count.push_back(static_cast<std::int32_t>(positions.size()));
            for (const auto& [instrument, position] : positions) {
                const double close = last_marks[instrument];
                result->position_session_index.push_back(session.session_index);
                result->position_instrument_id.push_back(instrument);
                result->position_symbol_id.push_back(position.symbol_id);
                result->position_quantity.push_back(position.quantity);
                result->position_average_entry_price.push_back(position.average_entry_price);
                result->position_close.push_back(close);
                result->position_market_value.push_back(position.quantity * close);
                result->position_entry_date_ordinal.push_back(position.entry_date_ordinal);
                result->position_entry_signal_features_json.push_back(
                    position.entry_signal_features_json
                );
            }
            result->equity_positions_json.push_back(
                positions_json(dataset, positions, last_marks)
            );
            result->equity_metrics_json.push_back(snapshot_metrics_json(*result));

            if (has_control_callback) {
                py::gil_scoped_acquire acquire;
                if (py::cast<bool>(control_callback(day + 1, sessions.size()))) {
                    throw BacktestCancelled("native backtest cancellation requested");
                }
            }
        }
    }
    if (strategy.kind == StrategyKind::SupportResistance) {
        for (const auto& [instrument, state] : support_states) {
            const std::int32_t symbol_id = latest_symbol_ids.at(instrument);
            for (const support_resistance::JsonObject& event : state.events) {
                result->support_event_instrument_id.push_back(instrument);
                result->support_event_symbol_id.push_back(symbol_id);
                result->support_event_json.push_back(support_resistance::json(event));
            }
            for (const support_resistance::JsonObject& version : state.zone_versions) {
                result->support_zone_instrument_id.push_back(instrument);
                result->support_zone_symbol_id.push_back(symbol_id);
                result->support_zone_version_json.push_back(support_resistance::json(version));
            }
            for (const support_resistance::JsonObject& version : state.regime_versions) {
                result->support_regime_instrument_id.push_back(instrument);
                result->support_regime_symbol_id.push_back(symbol_id);
                result->support_regime_version_json.push_back(support_resistance::json(version));
            }
        }
    }
    result->final_equity = result->equity_value.empty() ? initial_cash : result->equity_value.back();
    result->total_return = initial_cash == 0.0 ? 0.0 : result->final_equity / initial_cash - 1.0;
    return result;
}

std::shared_ptr<KernelResult> run_backtest_binding(
    const py::object& dataset,
    const py::dict& strategy,
    const py::dict& options,
    const py::object& control_callback
) {
    DatasetView view = parse_dataset(dataset);
    const StrategyConfig config = parse_strategy(strategy);
    const std::optional<UniversePolicy> universe_policy = parse_universe_policy(strategy);
    if (universe_policy && (view.asset_types.empty() || view.exchanges.empty())) {
        throw std::invalid_argument(
            "dynamic universe requires prepared dataset asset/exchange mappings"
        );
    }
    const double initial_cash = number_or(options, "initial_cash", 100'000.0);
    CostConfig costs{
        number_or(options, "commission_bps", 1.0),
        number_or(options, "commission_min", 1.0),
        number_or(options, "slippage_bps", 5.0),
    };
    if (!std::isfinite(initial_cash) || initial_cash <= 0.0) {
        throw std::invalid_argument("initial_cash must be positive and finite");
    }
    if (!std::isfinite(costs.commission_bps) || !std::isfinite(costs.commission_min)
        || !std::isfinite(costs.slippage_bps) || costs.commission_bps < 0.0
        || costs.commission_min < 0.0 || costs.slippage_bps < 0.0) {
        throw std::invalid_argument("native backtest costs must be non-negative");
    }
    const std::int64_t start = option_ordinal(
        options, "start_ordinal", "start_date", std::numeric_limits<std::int64_t>::min()
    );
    const std::int64_t end = option_ordinal(
        options, "end_ordinal", "end_date", std::numeric_limits<std::int64_t>::max()
    );
    if (end < start) throw std::invalid_argument("end_date must be on or after start_date");
    const std::vector<SessionRange> all_sessions = session_ranges(
        view, std::numeric_limits<std::int64_t>::min(), end
    );
    std::vector<SessionRange> warmup_sessions;
    std::vector<SessionRange> sessions;
    for (const SessionRange& session : all_sessions) {
        if (session.date_ordinal < start) warmup_sessions.push_back(session);
        else sessions.push_back(session);
    }
    if (sessions.empty()) {
        throw std::invalid_argument("prepared dataset has no rows inside the backtest window");
    }
    attach_history_sessions(view, all_sessions);
    return run_native_backtest(
        view, config, universe_policy, warmup_sessions, sessions,
        initial_cash, costs, control_callback
    );
}

template <typename T>
py::array_t<T> vector_view(const py::object& owner, std::vector<T>& values) {
    py::array_t<T> result(
        {static_cast<py::ssize_t>(values.size())},
        {static_cast<py::ssize_t>(sizeof(T))},
        values.data(),
        owner
    );
    result.attr("setflags")(py::arg("write") = false);
    return result;
}

}  // namespace

void bind_backtest(py::module_& module) {
    py::register_exception<BacktestCancelled>(module, "BacktestCancelledError");
    py::class_<KernelResult, std::shared_ptr<KernelResult>>(module, "KernelResult")
        .def_property_readonly("summary", [](py::object owner) {
            const KernelResult& value = owner.cast<const KernelResult&>();
            py::dict result;
            result["initial_cash"] = value.initial_cash;
            result["final_equity"] = value.final_equity;
            result["total_return"] = value.total_return;
            result["max_drawdown"] = value.max_drawdown;
            result["signal_count"] = value.signal_session_index.size();
            result["trade_count"] = value.trade_session_index.size();
            result["total_fees"] = value.total_fees;
            result["total_slippage"] = value.total_slippage;
            result["trading_days"] = value.trading_days;
            result["delisting_zero_write_off"] = value.delisting_zero_write_off;
            return result;
        })
        .def_property_readonly("symbols", [](const KernelResult& value) { return value.symbols; })
        .def_property_readonly("signals", [](py::object owner) {
            KernelResult& value = owner.cast<KernelResult&>();
            py::dict result;
            result["session_index"] = vector_view(owner, value.signal_session_index);
            result["instrument_id"] = vector_view(owner, value.signal_instrument_id);
            result["timestamp_us"] = vector_view(owner, value.signal_timestamp_us);
            result["symbol_id"] = vector_view(owner, value.signal_symbol_id);
            result["action"] = vector_view(owner, value.signal_action);
            result["score"] = vector_view(owner, value.signal_score);
            result["strength_score"] = vector_view(owner, value.signal_strength_score);
            result["strength_rank"] = vector_view(owner, value.signal_strength_rank);
            result["passes_threshold"] = vector_view(owner, value.signal_passes_threshold);
            result["reason"] = value.signal_reason;
            result["metadata_json"] = value.signal_metadata_json;
            return result;
        })
        .def_property_readonly("trades", [](py::object owner) {
            KernelResult& value = owner.cast<KernelResult&>();
            py::dict result;
            result["session_index"] = vector_view(owner, value.trade_session_index);
            result["signal_session_index"] = vector_view(owner, value.trade_signal_session_index);
            result["instrument_id"] = vector_view(owner, value.trade_instrument_id);
            result["symbol_id"] = vector_view(owner, value.trade_symbol_id);
            result["side"] = vector_view(owner, value.trade_side);
            result["quantity"] = vector_view(owner, value.trade_quantity);
            result["price"] = vector_view(owner, value.trade_price);
            result["fee"] = vector_view(owner, value.trade_fee);
            result["reference_price"] = vector_view(owner, value.trade_reference_price);
            result["slippage_cost"] = vector_view(owner, value.trade_slippage_cost);
            result["execution_timestamp_us"] = vector_view(
                owner, value.trade_execution_timestamp_us
            );
            result["signal_timestamp_us"] = vector_view(owner, value.trade_signal_timestamp_us);
            result["execution_date_ordinal"] = vector_view(
                owner, value.trade_execution_date_ordinal
            );
            result["reason"] = value.trade_reason;
            result["entry_signal_features_json"] = value.trade_entry_signal_features_json;
            result["setup_id"] = value.trade_setup_id;
            result["stage_index"] = vector_view(owner, value.trade_stage_index);
            result["stage_key"] = value.trade_stage_key;
            result["position_quantity_before"] = vector_view(
                owner, value.trade_position_quantity_before
            );
            result["position_quantity_after"] = vector_view(
                owner, value.trade_position_quantity_after
            );
            result["position_average_entry_price_after"] = vector_view(
                owner, value.trade_position_average_entry_price_after
            );
            result["stage_target_pct"] = vector_view(owner, value.trade_stage_target_pct);
            result["slippage_bps"] = vector_view(owner, value.trade_slippage_bps);
            result["gross_notional"] = vector_view(owner, value.trade_gross_notional);
            result["net_cash_flow"] = vector_view(owner, value.trade_net_cash_flow);
            return result;
        })
        .def_property_readonly("equity", [](py::object owner) {
            KernelResult& value = owner.cast<KernelResult&>();
            py::dict result;
            result["session_index"] = vector_view(owner, value.equity_session_index);
            result["timestamp_us"] = vector_view(owner, value.equity_timestamp_us);
            result["cash"] = vector_view(owner, value.equity_cash);
            result["equity"] = vector_view(owner, value.equity_value);
            result["drawdown"] = vector_view(owner, value.equity_drawdown);
            result["gross_exposure"] = vector_view(owner, value.equity_gross_exposure);
            result["holdings_count"] = vector_view(owner, value.equity_holdings_count);
            result["positions_json"] = value.equity_positions_json;
            result["metrics_json"] = value.equity_metrics_json;
            return result;
        })
        .def_property_readonly("positions", [](py::object owner) {
            KernelResult& value = owner.cast<KernelResult&>();
            py::dict result;
            result["session_index"] = vector_view(owner, value.position_session_index);
            result["instrument_id"] = vector_view(owner, value.position_instrument_id);
            result["symbol_id"] = vector_view(owner, value.position_symbol_id);
            result["quantity"] = vector_view(owner, value.position_quantity);
            result["average_entry_price"] = vector_view(owner, value.position_average_entry_price);
            result["close"] = vector_view(owner, value.position_close);
            result["market_value"] = vector_view(owner, value.position_market_value);
            result["entry_date_ordinal"] = vector_view(owner, value.position_entry_date_ordinal);
            result["entry_signal_features_json"] = value.position_entry_signal_features_json;
            return result;
        })
        .def_property_readonly("universe_membership", [](py::object owner) -> py::object {
            KernelResult& value = owner.cast<KernelResult&>();
            if (!value.has_universe_membership) return py::none();
            py::dict result;
            result["session_index"] = vector_view(owner, value.universe_session_index);
            result["date_ordinal"] = vector_view(owner, value.universe_date_ordinal);
            result["eligible_count"] = vector_view(owner, value.universe_eligible_count);
            result["excluded_asset_type"] = vector_view(owner, value.universe_excluded_asset_type);
            result["excluded_exchange"] = vector_view(owner, value.universe_excluded_exchange);
            result["excluded_before_listing"] = vector_view(
                owner, value.universe_excluded_before_listing
            );
            result["excluded_after_delisting"] = vector_view(
                owner, value.universe_excluded_after_delisting
            );
            result["excluded_price"] = vector_view(owner, value.universe_excluded_price);
            result["excluded_liquidity"] = vector_view(owner, value.universe_excluded_liquidity);
            result["excluded_history"] = vector_view(owner, value.universe_excluded_history);
            return result;
        })
        .def_property_readonly("support_resistance", [](py::object owner) -> py::object {
            KernelResult& value = owner.cast<KernelResult&>();
            if (value.support_event_json.empty()
                && value.support_zone_version_json.empty()
                && value.support_regime_version_json.empty()) {
                return py::none();
            }
            py::dict events;
            events["instrument_id"] = vector_view(owner, value.support_event_instrument_id);
            events["symbol_id"] = vector_view(owner, value.support_event_symbol_id);
            events["payload_json"] = value.support_event_json;
            py::dict zones;
            zones["instrument_id"] = vector_view(owner, value.support_zone_instrument_id);
            zones["symbol_id"] = vector_view(owner, value.support_zone_symbol_id);
            zones["payload_json"] = value.support_zone_version_json;
            py::dict regimes;
            regimes["instrument_id"] = vector_view(owner, value.support_regime_instrument_id);
            regimes["symbol_id"] = vector_view(owner, value.support_regime_symbol_id);
            regimes["payload_json"] = value.support_regime_version_json;
            py::dict result;
            result["events"] = std::move(events);
            result["zone_versions"] = std::move(zones);
            result["regime_versions"] = std::move(regimes);
            return std::move(result);
        });
    module.def(
        "run_backtest",
        &run_backtest_binding,
        py::arg("dataset"),
        py::arg("strategy"),
        py::arg("options") = py::dict(),
        py::arg("control_callback") = py::none()
    );
}

}  // namespace quant_kernel
