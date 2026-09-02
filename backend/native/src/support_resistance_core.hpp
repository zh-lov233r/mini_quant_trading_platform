#pragma once

#include <compare>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

namespace quant_kernel::support_resistance {

struct JsonValue;
using JsonArray = std::vector<JsonValue>;
using JsonObject = std::vector<std::pair<std::string, JsonValue>>;

struct JsonValue {
    using Value = std::variant<
        std::nullptr_t,
        bool,
        std::int64_t,
        double,
        std::string,
        JsonArray,
        JsonObject
    >;

    Value value = nullptr;

    JsonValue() = default;
    JsonValue(std::nullptr_t) : value(nullptr) {}
    JsonValue(bool input) : value(input) {}
    JsonValue(int input) : value(static_cast<std::int64_t>(input)) {}
    JsonValue(std::size_t input) : value(static_cast<std::int64_t>(input)) {}
    JsonValue(std::int64_t input) : value(input) {}
    JsonValue(double input) : value(input) {}
    JsonValue(const char* input) : value(std::string(input)) {}
    JsonValue(std::string input) : value(std::move(input)) {}
    JsonValue(JsonArray input) : value(std::move(input)) {}
    JsonValue(JsonObject input) : value(std::move(input)) {}
};

JsonObject object(std::initializer_list<std::pair<std::string, JsonValue>> values = {});
void set(JsonObject& value, std::string key, JsonValue item);
const JsonValue* find(const JsonObject& value, const std::string& key);
std::string json(const JsonValue& value);
std::string iso_date(std::int32_t ordinal);
std::int32_t date_ordinal(const std::string& value);

enum class PivotKind { Low, High };
enum class ZoneRole { Support, Resistance };
enum class ZoneStatus { Active, Expired };
enum class Regime { Uptrend, Downtrend, Range, Transition };
enum class Setup { SupportBounce, ResistanceBreakout, BreakoutRetest };
enum class Action { Buy, Sell };

std::string_view name(PivotKind value);
std::string_view name(ZoneRole value);
std::string_view name(ZoneStatus value);
std::string_view name(Regime value);
std::string_view name(Setup value);

struct Config {
    int pivot_left_bars = 3;
    int pivot_right_bars = 3;
    int detection_window = 120;
    int min_line_pivots = 3;
    int min_line_span_sessions = 10;
    double line_inlier_tolerance_atr = 0.75;
    double max_abs_slope_atr_per_session = 0.25;
    double zone_half_width_atr = 0.5;
    double decay_half_life = 60.0;
    double bounce_confirmation_atr = 0.25;
    double breakout_confirmation_atr = 0.5;
    double breakout_volume_ratio_min = 1.5;
    bool support_bounce_enabled = true;
    bool resistance_breakout_enabled = true;
    bool breakout_retest_enabled = true;
    int retest_window = 10;
    double retest_volume_ratio_max = 0.8;
    int score_outcome_window = 20;
    double score_target_atr = 3.0;
    double score_stop_atr = 1.5;
    double min_strength_score = 50.0;
    int max_holding_days = 40;
    double min_reward_risk = 1.5;
    double stop_loss_atr = 1.5;
    double max_loss_pct = 0.08;
    double take_profit_atr = 3.0;
};

struct Bar {
    std::int32_t date_ordinal = 0;
    std::int64_t timestamp_us = 0;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    double volume = 0.0;
    double volume_sma_20 = 0.0;
    double atr_14 = 0.0;
};

struct Pivot {
    std::string pivot_key;
    PivotKind kind = PivotKind::Low;
    int session_index = 0;
    std::int32_t trade_date_ordinal = 0;
    std::int32_t confirmed_on_ordinal = 0;
    double price = 0.0;
    double atr = 0.0;
};

struct Zone {
    std::string zone_key;
    PivotKind source_kind = PivotKind::Low;
    ZoneRole role = ZoneRole::Support;
    ZoneStatus status = ZoneStatus::Active;
    double center = 0.0;
    double lower = 0.0;
    double upper = 0.0;
    double atr = 0.0;
    std::vector<std::string> pivot_keys;
    int pivot_count = 0;
    int touch_count = 0;
    std::int32_t first_pivot_date_ordinal = 0;
    std::int32_t last_pivot_date_ordinal = 0;
    std::int32_t valid_from_ordinal = 0;
    int anchor_session_index = 0;
    double anchor_center = 0.0;
    double anchor_lower = 0.0;
    double anchor_upper = 0.0;
    double slope_per_session = 0.0;
    double fit_residual_atr = 0.0;
    double recency_weight = 0.0;
    bool last_inside = false;
    std::int32_t timeline_effective_from_ordinal = 0;
};

struct BreakoutRecord {
    std::string zone_key;
    std::int32_t breakout_date_ordinal = 0;
    int breakout_session_index = 0;
    double breakout_volume = 0.0;
};

struct PendingOutcome {
    Setup setup = Setup::SupportBounce;
    std::string zone_key;
    std::int32_t origin_date_ordinal = 0;
    int origin_session_index = 0;
    double target = 0.0;
    double stop = 0.0;
};

struct SetupStats {
    int wins = 0;
    int losses = 0;
    int censored = 0;

    [[nodiscard]] int resolved() const { return wins + losses; }
    [[nodiscard]] double posterior() const {
        return (static_cast<double>(wins) + 1.0)
            / (static_cast<double>(resolved()) + 2.0);
    }
};

struct EntryChannel {
    bool valid = false;
    std::string reason_code;
    std::int32_t frozen_on_ordinal = 0;
    std::string support_zone_key;
    std::string resistance_zone_key;
    double lower = 0.0;
    double upper = 0.0;
    double lower_slope_per_session = 0.0;
    double upper_slope_per_session = 0.0;
    double signal_close = 0.0;
    int projected_sessions = 0;
    std::optional<Zone> support_zone;
    std::optional<Zone> resistance_zone;
    std::string semantics = "support_upper_to_resistance_lower_v1";
};

struct RegimeEvidence {
    Regime regime = Regime::Transition;
    std::string reason_code;
    std::optional<std::string> lower_zone_key;
    std::optional<std::string> upper_zone_key;
    JsonObject payload;
};

struct StrengthComponent {
    std::string key;
    double raw_value = 0.0;
    double normalized_score = 0.0;
    double weight = 0.0;
};

struct Strength {
    double score = 0.0;
    double threshold = 0.0;
    bool passes_threshold = false;
    std::string model_version;
    std::vector<StrengthComponent> components;
};

struct Candidate {
    Setup setup = Setup::SupportBounce;
    std::string zone_key;
    Zone zone;
    double score = 0.0;
    JsonObject score_evidence;
    bool entry_eligible = false;
    bool risk_eligible = false;
    bool regime_eligible = false;
    bool channel_eligible = false;
    std::optional<std::string> rejection_reason;
    double stop_price = 0.0;
    double target_price = 0.0;
    double reward_risk = 0.0;
    std::string reason;
    JsonObject strength_inputs;
    Strength strength;
    Regime regime = Regime::Transition;
    JsonObject regime_evidence;
    EntryChannel entry_channel;
};

struct CachedZoneVersion {
    int version = 0;
    std::int32_t effective_from_ordinal = 0;
    std::optional<std::int32_t> effective_to_ordinal;
    Zone zone;
};

struct CachedRegimeVersion {
    int version = 0;
    std::int32_t effective_from_ordinal = 0;
    Regime regime = Regime::Transition;
    std::optional<std::string> lower_zone_key;
    std::optional<std::string> upper_zone_key;
    std::string reason_code;
    JsonObject evidence;
};

struct LifecycleEventKey {
    std::int32_t date_ordinal = 0;
    std::string event_type;
    std::string zone_key;

    auto operator<=>(const LifecycleEventKey&) const = default;
};

struct PositionView {
    double quantity = 0.0;
    std::optional<double> average_entry_price;
    std::optional<int> holding_days;
    std::optional<JsonObject> entry_signal_features;
};

struct Decision {
    Action action = Action::Buy;
    std::string reason;
    std::optional<double> score;
    JsonObject support_resistance;
    std::optional<Strength> strength;
    std::optional<EntryChannel> entry_channel;
    std::optional<Setup> setup;
};

struct LineFit {
    std::vector<Pivot> inliers;
    double center = 0.0;
    double slope = 0.0;
    double residual_atr = 0.0;
    double total_weight = 0.0;
};

struct SymbolState {
    std::vector<Bar> history;
    std::vector<Pivot> pivots;
    std::map<std::string, Zone> zones;
    std::map<std::string, BreakoutRecord> breakouts;
    std::vector<PendingOutcome> pending_outcomes;
    std::map<Setup, SetupStats> stats{
        {Setup::SupportBounce, {}},
        {Setup::ResistanceBreakout, {}},
        {Setup::BreakoutRetest, {}},
    };
    std::vector<JsonObject> events;
    std::vector<JsonObject> zone_versions;
    std::map<std::string, std::string> version_signatures;
    std::vector<CachedZoneVersion> cached_zone_timeline;
    std::vector<JsonObject> regime_versions;
    Regime current_regime = Regime::Transition;
    JsonObject current_regime_evidence;
    std::vector<CachedRegimeVersion> cached_regime_timeline;
    std::vector<LifecycleEventKey> cached_lifecycle_events;
    std::optional<EntryChannel> current_entry_channel;
};

double stored_zone_price(double value);
bool valid_zone_values(double center, double lower, double upper, double atr, double slope);
Zone project_zone(const Zone& zone, int session_index);
std::string new_zone_key(PivotKind source_kind, const std::vector<Pivot>& pivots);
std::string revived_zone_key(const std::string& zone_key, std::int32_t effective_date_ordinal);
EntryChannel build_entry_channel(
    const std::vector<Zone>& zones,
    double close,
    std::int32_t trade_date_ordinal
);
EntryChannel project_entry_channel(const EntryChannel& channel, int sessions = 1);
std::pair<bool, std::string> entry_price_is_inside_channel(
    const std::optional<EntryChannel>& channel,
    double price
);
JsonObject zone_json(const Zone& zone);
JsonObject entry_channel_json(const EntryChannel& channel);
JsonObject strength_json(const Strength& strength);
JsonObject candidate_json(const Candidate& candidate);
std::optional<LineFit> fit_pivots(
    const std::vector<Pivot>& pivots,
    int current_index,
    const Config& config
);
RegimeEvidence classify_regime(
    const SymbolState& state,
    const std::vector<Zone>& zones,
    const Bar& bar,
    const Config& config
);
void record_regime(
    SymbolState& state,
    std::int32_t effective_from,
    const RegimeEvidence& evidence
);
void rebuild(SymbolState& state, const Bar& bar, const Config& config);
std::optional<Zone> match_existing_zone(
    const std::vector<Zone>& old_zones,
    PivotKind source_kind,
    double center,
    double half_width,
    const std::vector<std::string>& pivot_keys
);
void record_zone(
    SymbolState& state,
    const Zone& zone,
    std::int32_t effective_date,
    ZoneStatus status
);
std::optional<Decision> advance_symbol(
    SymbolState& state,
    const Bar& bar,
    const PositionView& position,
    const Config& config,
    bool emit_signals = true
);
void record_execution_rejection(
    SymbolState& state,
    const EntryChannel& entry_channel,
    Setup setup,
    std::int32_t signal_date_ordinal,
    std::int32_t execution_date_ordinal,
    double reference_open,
    double simulated_execution_price,
    const std::string& reason_code
);

}  // namespace quant_kernel::support_resistance
