from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DateWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")

    @model_validator(mode="after")
    def validate_dates(self) -> "DateWindow":
        if self.end_date < self.start_date:
            raise ValueError("endDate must be on or after startDate")
        return self


class CostScenario(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    commission_bps: float = Field(default=0, ge=0, alias="commissionBps")
    commission_min: float = Field(default=0, ge=0, alias="commissionMin")
    slippage_bps: float = Field(default=0, ge=0, alias="slippageBps")


class PointInTimeUniversePolicy(BaseModel):
    """Causal daily universe membership evaluated at the signal close."""

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["point_in_time_liquid"] = "point_in_time_liquid"
    asset_types: list[str] = Field(default_factory=lambda: ["CS"], alias="assetTypes")
    exchanges: list[str] = Field(
        default_factory=lambda: ["XNAS", "XNYS", "XASE"]
    )
    min_unadjusted_close: float = Field(default=5.0, gt=0, alias="minUnadjustedClose")
    min_dollar_volume_20: float = Field(
        default=10_000_000.0,
        gt=0,
        alias="minDollarVolume20",
    )
    min_history_sessions: int = Field(default=200, ge=20, le=252, alias="minHistorySessions")
    membership_as_of: Literal["signal_close"] = Field(
        default="signal_close",
        alias="membershipAsOf",
    )
    existing_position_policy: Literal["exit_only"] = Field(
        default="exit_only",
        alias="existingPositionPolicy",
    )
    delisting_value_policy: Literal["zero_with_last_close_sensitivity"] = Field(
        default="zero_with_last_close_sensitivity",
        alias="delistingValuePolicy",
    )

    @model_validator(mode="after")
    def normalize_policy(self) -> "PointInTimeUniversePolicy":
        self.asset_types = sorted({str(value).strip().upper() for value in self.asset_types if str(value).strip()})
        self.exchanges = sorted({str(value).strip().upper() for value in self.exchanges if str(value).strip()})
        if not self.asset_types or not self.exchanges:
            raise ValueError("universePolicy assetTypes and exchanges must be non-empty")
        return self


class SupportResistanceValidationProtocol(BaseModel):
    """Pre-registered, bounded effectiveness protocol for pivot-slope-atr-v2."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["support_resistance_effectiveness_v2"] = (
        "support_resistance_effectiveness_v2"
    )
    max_backtests: Literal[200] = Field(default=200, alias="maxBacktests")
    bootstrap_seed: Literal[20260828] = Field(default=20260828, alias="bootstrapSeed")
    bootstrap_replicates: Literal[10000] = Field(default=10000, alias="bootstrapReplicates")
    event_horizons: tuple[Literal[1], Literal[5], Literal[10], Literal[20], Literal[40]] = Field(
        default=(1, 5, 10, 20, 40),
        alias="eventHorizons",
    )
    dedupe_sessions: Literal[40] = Field(default=40, alias="dedupeSessions")
    report_formats: tuple[
        Literal["json"], Literal["markdown_zh"], Literal["markdown_en"], Literal["pdf_zh"], Literal["pdf_en"]
    ] = Field(
        default=("json", "markdown_zh", "markdown_en", "pdf_zh", "pdf_en"),
        alias="reportFormats",
    )


class TargetMetricCondition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metric: Literal["total_return", "sharpe", "max_drawdown", "excess_return"]
    operator: Literal["gte", "lte"] = "gte"
    value: float
    sample_kind: Literal["in_sample", "out_of_sample"] = Field(
        default="out_of_sample",
        alias="sampleKind",
    )
    cost_scenario: str = Field(default="base", min_length=1, max_length=64, alias="costScenario")


class ExperimentStopPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_duration_seconds: int | None = Field(
        default=None,
        ge=60,
        le=7 * 24 * 60 * 60,
        alias="maxDurationSeconds",
    )
    token_budget: int | None = Field(default=None, ge=1000, le=10_000_000, alias="tokenBudget")
    target_metric: TargetMetricCondition | None = Field(default=None, alias="targetMetric")

    @model_validator(mode="after")
    def validate_condition(self) -> "ExperimentStopPolicy":
        if self.max_duration_seconds is None and self.token_budget is None and self.target_metric is None:
            raise ValueError("stopPolicy must contain maxDurationSeconds, tokenBudget, or targetMetric")
        return self


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=2000)
    strategy_id: UUID = Field(alias="strategyId")
    basket_id: UUID | None = Field(default=None, alias="basketId")
    symbols: list[str] = Field(default_factory=list)
    universe_policy: PointInTimeUniversePolicy | None = Field(
        default=None,
        alias="universePolicy",
    )
    in_sample: DateWindow = Field(alias="inSample")
    out_of_sample: DateWindow = Field(alias="outOfSample")
    parameter_grid: dict[str, list[Any]] = Field(default_factory=dict, alias="parameterGrid")
    cost_scenarios: list[CostScenario] = Field(alias="costScenarios", min_length=1, max_length=5)
    initial_cash: float = Field(default=100_000, gt=0, alias="initialCash")
    benchmark_symbol: str | None = Field(default="SPY", alias="benchmarkSymbol")
    stop_policy: ExperimentStopPolicy | None = Field(default=None, alias="stopPolicy")

    @model_validator(mode="after")
    def validate_spec(self) -> "ExperimentSpec":
        if sum((bool(self.basket_id), bool(self.symbols), self.universe_policy is not None)) != 1:
            raise ValueError("provide exactly one of basketId, symbols, or universePolicy")
        if not (
            self.in_sample.end_date < self.out_of_sample.start_date
            or self.out_of_sample.end_date < self.in_sample.start_date
        ):
            raise ValueError("inSample and outOfSample windows must not overlap")
        if len({item.name for item in self.cost_scenarios}) != len(self.cost_scenarios):
            raise ValueError("cost scenario names must be unique")
        scenario_names = {item.name.strip().lower() for item in self.cost_scenarios}
        if "base" not in scenario_names or len(scenario_names) < 2:
            raise ValueError("costScenarios must include base and at least one stress scenario")
        if not self.parameter_grid:
            raise ValueError("parameterGrid must contain at least one bounded parameter")
        for path, values in self.parameter_grid.items():
            if not path or not values:
                raise ValueError("parameterGrid paths and value arrays must be non-empty")
        return self


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    spec: ExperimentSpec


ObjectiveMetric = Literal[
    "oos_total_return",
    "oos_annualized_return",
    "oos_sharpe",
    "oos_sortino",
    "oos_excess_return",
    "oos_max_drawdown",
    "oos_turnover",
    "pnl_concentration",
    "cost_decay",
    "is_oos_abs_gap",
]


class ParetoObjective(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metric: ObjectiveMetric
    direction: Literal["maximize", "minimize"]

    @model_validator(mode="after")
    def validate_direction(self) -> "ParetoObjective":
        minimize = {
            "oos_max_drawdown",
            "oos_turnover",
            "pnl_concentration",
            "cost_decay",
            "is_oos_abs_gap",
        }
        expected = "minimize" if self.metric in minimize else "maximize"
        if self.direction != expected:
            raise ValueError(f"{self.metric} must use direction={expected}")
        return self


class AdaptiveSearchPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_rounds: int = Field(default=3, ge=1, le=5, alias="maxRounds")
    max_trials: int = Field(default=48, ge=4, le=100, alias="maxTrials")
    objectives: list[ParetoObjective] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def unique_objectives(self) -> "AdaptiveSearchPolicy":
        metrics = [item.metric for item in self.objectives]
        if len(metrics) != len(set(metrics)):
            raise ValueError("objectives must be unique")
        return self


class AdaptiveCandidateProposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    overrides: dict[str, Any] = Field(min_length=1, max_length=30)
    rationale: str = Field(default="", max_length=1000)


class CategoryStrategyProposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    strategy_type: Literal[
        "trend",
        "mean_reversion",
        "momentum_breakout",
        "island_reversal",
        "double_bottom",
        "support_resistance",
    ] = Field(alias="strategyType")
    overrides: dict[str, Any] = Field(default_factory=dict, max_length=30)


class CategoryStudyValidationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    goal: str = Field(min_length=1, max_length=4000)
    strategy_type: Literal[
        "trend",
        "mean_reversion",
        "momentum_breakout",
        "island_reversal",
        "double_bottom",
        "support_resistance",
    ] = Field(alias="strategyType")
    strategy: CategoryStrategyProposal
    name: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=2000)
    basket_id: UUID | None = Field(default=None, alias="basketId")
    symbols: list[str] = Field(default_factory=list)
    universe_policy: PointInTimeUniversePolicy | None = Field(
        default=None,
        alias="universePolicy",
    )
    validation_protocol: SupportResistanceValidationProtocol | None = Field(
        default=None,
        alias="validationProtocol",
    )
    in_sample: DateWindow | None = Field(default=None, alias="inSample")
    out_of_sample: DateWindow | None = Field(default=None, alias="outOfSample")
    cost_scenarios: list[CostScenario] = Field(default_factory=list, alias="costScenarios", max_length=5)
    initial_cash: float = Field(default=100_000, gt=0, alias="initialCash")
    benchmark_symbol: str | None = Field(default="SPY", alias="benchmarkSymbol")
    stop_policy: ExperimentStopPolicy | None = Field(default=None, alias="stopPolicy")
    search_policy: AdaptiveSearchPolicy | None = Field(default=None, alias="searchPolicy")
    initial_candidates: list[AdaptiveCandidateProposal] = Field(
        default_factory=list,
        max_length=5,
        alias="initialCandidates",
    )

    @model_validator(mode="after")
    def validate_category_study(self) -> "CategoryStudyValidationRequest":
        if self.strategy.strategy_type != self.strategy_type:
            raise ValueError("Agent cannot change the selected strategyType")
        if sum((bool(self.basket_id), bool(self.symbols), self.universe_policy is not None)) != 1:
            raise ValueError("provide exactly one of basketId, symbols, or universePolicy")
        if self.validation_protocol is not None:
            if self.strategy_type != "support_resistance":
                raise ValueError("validationProtocol is only supported for support_resistance")
            if self.universe_policy is None:
                raise ValueError("effectiveness validation requires universePolicy")
            return self
        if self.in_sample is None or self.out_of_sample is None:
            raise ValueError("adaptive research requires inSample and outOfSample")
        if self.search_policy is None or not self.initial_candidates:
            raise ValueError("adaptive research requires searchPolicy and initialCandidates")
        if not (
            self.in_sample.end_date < self.out_of_sample.start_date
            or self.out_of_sample.end_date < self.in_sample.start_date
        ):
            raise ValueError("inSample and outOfSample windows must not overlap")
        scenario_names = [item.name.strip().lower() for item in self.cost_scenarios]
        if len(scenario_names) != len(set(scenario_names)):
            raise ValueError("cost scenario names must be unique")
        if "base" not in scenario_names or len(scenario_names) < 2:
            raise ValueError("costScenarios must include base and at least one stress scenario")
        return self


class CategoryStudyValidationOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    valid: bool = True
    normalized_strategy: dict[str, Any] = Field(alias="normalizedStrategy")
    normalized_spec: dict[str, Any] = Field(alias="normalizedSpec")
    first_round_trial_count: int = Field(alias="firstRoundTrialCount")
    maximum_trial_count: int = Field(alias="maximumTrialCount")
    universe_symbols: list[str] = Field(alias="universeSymbols")
    universe_summary: dict[str, Any] = Field(default_factory=dict, alias="universeSummary")
    proposal_hash: str = Field(alias="proposalHash")
    warnings: list[str] = Field(default_factory=list)


class AdaptiveExperimentSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=2000)
    strategy_id: UUID = Field(alias="strategyId")
    strategy_type: str = Field(min_length=1, max_length=32, alias="strategyType")
    basket_id: UUID | None = Field(default=None, alias="basketId")
    symbols: list[str] = Field(default_factory=list)
    universe_policy: PointInTimeUniversePolicy | None = Field(
        default=None,
        alias="universePolicy",
    )
    in_sample: DateWindow = Field(alias="inSample")
    out_of_sample: DateWindow = Field(alias="outOfSample")
    cost_scenarios: list[CostScenario] = Field(alias="costScenarios", min_length=2, max_length=5)
    sample_kinds: list[Literal["in_sample", "out_of_sample"]] = Field(
        default_factory=lambda: ["in_sample", "out_of_sample"],
        min_length=1,
        max_length=2,
        alias="sampleKinds",
    )
    initial_cash: float = Field(default=100_000, gt=0, alias="initialCash")
    benchmark_symbol: str | None = Field(default="SPY", alias="benchmarkSymbol")
    stop_policy: ExperimentStopPolicy | None = Field(default=None, alias="stopPolicy")
    search_policy: AdaptiveSearchPolicy = Field(alias="searchPolicy")
    initial_candidates: list[AdaptiveCandidateProposal] = Field(
        min_length=1,
        max_length=5,
        alias="initialCandidates",
    )

    @model_validator(mode="after")
    def validate_universe(self) -> "AdaptiveExperimentSpec":
        if sum((bool(self.basket_id), bool(self.symbols), self.universe_policy is not None)) != 1:
            raise ValueError("provide exactly one of basketId, symbols, or universePolicy")
        if len(self.sample_kinds) != len(set(self.sample_kinds)):
            raise ValueError("sampleKinds must be unique")
        return self


class SupportResistanceEffectivenessSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    research_mode: Literal["support_resistance_effectiveness"] = Field(
        default="support_resistance_effectiveness",
        alias="researchMode",
    )
    name: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=2000)
    strategy_id: UUID = Field(alias="strategyId")
    strategy_type: Literal["support_resistance"] = Field(
        default="support_resistance",
        alias="strategyType",
    )
    universe_policy: PointInTimeUniversePolicy = Field(alias="universePolicy")
    validation_protocol: SupportResistanceValidationProtocol = Field(alias="validationProtocol")
    initial_cash: float = Field(default=100_000, gt=0, alias="initialCash")
    benchmark_symbol: Literal["SPY"] = Field(default="SPY", alias="benchmarkSymbol")
    stop_policy: ExperimentStopPolicy | None = Field(default=None, alias="stopPolicy")


class CategoryStudyCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    spec: AdaptiveExperimentSpec | SupportResistanceEffectivenessSpec


class ExperimentRoundSubmit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    round_ordinal: int = Field(ge=2, le=5, alias="roundOrdinal")
    candidates: list[AdaptiveCandidateProposal] = Field(min_length=1, max_length=5)


class ControllerFailureRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    code: Literal["model_output_invalid", "provider_unavailable", "controller_callback_failed"]
    message: str = Field(min_length=1, max_length=2000)


class CandidatePromotionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=5, alias="candidateIds")


class ExperimentRoundOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    experiment_id: UUID = Field(alias="experimentId")
    ordinal: int
    status: str
    proposal: dict[str, Any]
    validation_issues: list[dict[str, Any]] = Field(alias="validationIssues")
    result_summary: dict[str, Any] = Field(alias="resultSummary")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")


class ExperimentCandidateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    experiment_id: UUID = Field(alias="experimentId")
    round_id: UUID = Field(alias="roundId")
    ordinal: int
    overrides: dict[str, Any]
    params: dict[str, Any]
    params_hash: str = Field(alias="paramsHash")
    rationale: str | None = None
    aggregate_metrics: dict[str, Any] = Field(alias="aggregateMetrics")
    pareto_rank: int | None = Field(default=None, alias="paretoRank")
    promoted_strategy_id: UUID | None = Field(default=None, alias="promotedStrategyId")


class CandidatePromotionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    experiment_id: UUID = Field(alias="experimentId")
    strategy_ids: list[UUID] = Field(alias="strategyIds")


class ExperimentValidationOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    valid: bool = True
    trial_count: int = Field(alias="trialCount")
    normalized_spec: dict[str, Any] = Field(alias="normalizedSpec")
    universe_symbols: list[str] = Field(alias="universeSymbols")
    warnings: list[str] = Field(default_factory=list)
    estimated_cost: dict[str, Any] = Field(default_factory=dict, alias="estimatedCost")


class ExperimentTokenUsageUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_run_id: str = Field(min_length=1, max_length=64, alias="workflowRunId")
    input_tokens: int = Field(default=0, ge=0, alias="inputTokens")
    cached_input_tokens: int = Field(default=0, ge=0, alias="cachedInputTokens")
    output_tokens: int = Field(default=0, ge=0, alias="outputTokens")
    reasoning_output_tokens: int = Field(default=0, ge=0, alias="reasoningOutputTokens")
    total_tokens: int = Field(default=0, ge=0, alias="totalTokens")


class TrialOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    trial_key: str = Field(alias="trialKey")
    ordinal: int
    status: str
    sample_kind: str = Field(alias="sampleKind")
    cost_scenario: str = Field(alias="costScenario")
    params: dict[str, Any]
    params_hash: str = Field(alias="paramsHash")
    window_start: date = Field(alias="windowStart")
    window_end: date = Field(alias="windowEnd")
    cost_config: dict[str, Any] = Field(alias="costConfig")
    data_fingerprint: str | None = Field(default=None, alias="dataFingerprint")
    backtest_run_id: UUID | None = Field(default=None, alias="backtestRunId")
    candidate_id: UUID | None = Field(default=None, alias="candidateId")
    metrics: dict[str, Any]
    attempt: int
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")


class ExperimentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    parent_experiment_id: UUID | None = Field(default=None, alias="parentExperimentId")
    study_kind: str = Field(default="adaptive_category", alias="studyKind")
    workflow_run_id: str = Field(alias="workflowRunId")
    status: str
    spec: dict[str, Any]
    run_manifest: dict[str, Any] = Field(alias="runManifest")
    progress: dict[str, Any]
    report: dict[str, Any]
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


TERMINAL_EXPERIMENT_STATUSES: set[str] = {
    "completed",
    "partially_failed",
    "failed",
    "cancelled",
    "data_changed",
}

ExperimentStatus = Literal[
    "queued",
    "running",
    "waiting_agent",
    "completed",
    "partially_failed",
    "failed",
    "cancel_requested",
    "cancelled",
    "data_changed",
]
