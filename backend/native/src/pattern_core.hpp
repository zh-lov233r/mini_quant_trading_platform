#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace quant_kernel {

enum class PatternKind {
    IslandReversal,
    DoubleBottom,
    HeadShouldersBottom,
    RoundedBottom,
    VReversal,
};

using PatternValue = std::variant<std::monostate, bool, std::int64_t, double, std::string, std::vector<std::string>>;
using PatternObject = std::map<std::string, PatternValue>;

struct PatternBar {
    std::int64_t date_ordinal = 0;
    std::int64_t timestamp_us = 0;
    std::optional<double> open;
    std::optional<double> high;
    std::optional<double> low;
    std::optional<double> close;
    std::optional<double> volume;
    std::optional<double> atr_14;
    std::optional<double> volume_sma_20;
    std::optional<double> return_20d;
    std::optional<double> return_60d;
    std::optional<double> sma_20;
    std::optional<double> sma_50;
};

struct PatternRiskConfig {
    int max_positions = 6;
    double position_size_pct = 0.15;
    double stage_targets[3] = {0.20, 0.50, 1.00};
    double stop_loss_atr = 1.5;
    double max_loss_pct = 0.08;
    double take_profit_atr = 3.0;
};

struct IslandConfig {
    int downtrend_lookback;
    double downtrend_min_drop_pct;
    double left_gap_min_pct;
    double right_gap_min_pct;
    int min_island_bars;
    int max_island_bars;
    double left_volume_ratio_max;
    double right_volume_ratio_min;
    int retest_window;
    double retest_volume_ratio_max;
    double support_tolerance_pct;
};

struct DoubleBottomConfig {
    int downtrend_lookback;
    double downtrend_min_drop_pct;
    double downtrend_max_up_day_ratio;
    double downtrend_min_r_squared;
    int min_bottom_spacing;
    int max_bottom_spacing;
    int left_bottom_before_bars;
    int left_bottom_after_bars;
    double bottom_tolerance_pct;
    double neckline_min_rebound_pct;
    double rebound_up_day_ratio_min;
    double second_bottom_volume_ratio_max;
    double breakout_volume_ratio_min;
    int max_breakout_bars_after_right_bottom;
    double breakout_buffer_pct;
    int retest_window;
    double retest_volume_ratio_max;
    double support_tolerance_pct;
};

struct HeadShouldersConfig {
    int downtrend_lookback;
    double downtrend_min_drop_pct;
    int pivot_left_bars;
    int pivot_right_bars;
    int min_segment_bars;
    int max_segment_bars;
    double shoulder_tolerance_pct;
    double head_depth_min_pct;
    double head_volume_ratio_max;
    double right_shoulder_volume_ratio_max;
    double breakout_volume_ratio_min;
    double breakout_buffer_pct;
};

struct RoundedBottomConfig {
    int min_lookback;
    int max_lookback;
    double min_depth_pct;
    double min_r_squared;
    double vertex_position_min;
    double vertex_position_max;
    int pivot_left_bars;
    int pivot_right_bars;
    int min_pullback_spacing;
    double right_volume_ratio_min;
    double pullback_volume_ratio_max;
    double breakout_volume_ratio_min;
    double breakout_buffer_pct;
};

struct VReversalConfig {
    int downtrend_lookback;
    double downtrend_min_drop_pct;
    int pivot_max_bars;
    double reversal_min_return_pct;
    double reversal_min_atr;
    double pivot_volume_ratio_min;
    int continuation_window;
    double continuation_volume_ratio_min;
    int consolidation_min_bars;
    int consolidation_max_bars;
    double breakout_volume_ratio_min;
    int retest_window;
    double retest_volume_ratio_max;
    double support_tolerance_pct;
    double bearish_reversal_volume_ratio_min;
};

using PatternSignalConfig = std::variant<
    IslandConfig,
    DoubleBottomConfig,
    HeadShouldersConfig,
    RoundedBottomConfig,
    VReversalConfig
>;

struct PatternConfig {
    PatternKind kind;
    std::string strategy_type;
    double minimum_strength = 50.0;
    int history_limit = 40;
    PatternRiskConfig risk;
    PatternSignalConfig signal;
};

struct IslandSetupPayload { PatternObject fields; };
struct DoubleBottomSetupPayload { PatternObject fields; };
struct HeadShouldersSetupPayload { PatternObject fields; };
struct RoundedBottomSetupPayload { PatternObject fields; };
struct VReversalSetupPayload { PatternObject fields; };

using PatternSetupPayload = std::variant<
    IslandSetupPayload,
    DoubleBottomSetupPayload,
    HeadShouldersSetupPayload,
    RoundedBottomSetupPayload,
    VReversalSetupPayload
>;

struct PatternSetup {
    std::string pattern_type;
    std::string setup_id;
    int stage_index = 0;
    std::string stage_key;
    double stage_target_pct = 1.0;
    PatternObject anchors;
    std::optional<double> invalidation_price;
    std::optional<std::string> exit_stage;
    PatternSetupPayload payload;
};

struct PatternDecision {
    bool buy = false;
    std::string reason;
    PatternSetup setup;
    std::optional<double> score;
    PatternObject strength_inputs;
};

struct DoubleBottomLeftCandidate {
    int left_index;
    double left_low;
};

struct DoubleBottomRightCandidate {
    int left_index;
    int neckline_index;
    int right_index;
    double left_low;
    double right_low;
    double neckline;
    double distance;
    double rebound_ratio;
};

struct DoubleBottomPattern {
    int left_index;
    int neckline_index;
    int right_index;
    int breakout_index;
    double left_low;
    double right_low;
    double neckline;
    double breakout_close;
    double breakout_volume;
    double breakout_volume_ratio;
    double distance;
    double rebound_ratio;
};

struct PatternState {
    std::vector<PatternBar> bars;
    std::vector<DoubleBottomLeftCandidate> double_bottom_left;
    std::vector<DoubleBottomRightCandidate> double_bottom_right;
    std::optional<DoubleBottomPattern> double_bottom_best;
};

struct PatternPositionView {
    double quantity = 0.0;
    std::optional<double> average_entry_price;
    const PatternSetup* setup = nullptr;
};

void append_pattern_bar(PatternState& state, const PatternConfig& config, PatternBar bar);
std::optional<PatternDecision> evaluate_pattern(
    const PatternConfig& config,
    const std::string& symbol,
    const PatternState& state,
    const PatternPositionView& position
);

std::string pattern_setup_json(const PatternSetup& setup);
std::string pattern_strength_inputs_json(const PatternObject& inputs);
std::string pattern_metadata_json(
    const PatternConfig& config,
    const PatternBar& current,
    const PatternPositionView& position,
    const PatternDecision& decision,
    const std::string& strength_json = {}
);

std::optional<double> pattern_number(const PatternObject& object, const std::string& key);
std::optional<std::string> pattern_text(const PatternObject& object, const std::string& key);

}  // namespace quant_kernel
