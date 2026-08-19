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


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=2000)
    strategy_id: UUID = Field(alias="strategyId")
    basket_id: UUID | None = Field(default=None, alias="basketId")
    symbols: list[str] = Field(default_factory=list)
    in_sample: DateWindow = Field(alias="inSample")
    out_of_sample: DateWindow = Field(alias="outOfSample")
    parameter_grid: dict[str, list[Any]] = Field(default_factory=dict, alias="parameterGrid")
    cost_scenarios: list[CostScenario] = Field(alias="costScenarios", min_length=1, max_length=5)
    initial_cash: float = Field(default=100_000, gt=0, alias="initialCash")
    benchmark_symbol: str | None = Field(default="SPY", alias="benchmarkSymbol")

    @model_validator(mode="after")
    def validate_spec(self) -> "ExperimentSpec":
        if bool(self.basket_id) == bool(self.symbols):
            raise ValueError("provide exactly one of basketId or symbols")
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


class ExperimentValidationOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    valid: bool = True
    trial_count: int = Field(alias="trialCount")
    normalized_spec: dict[str, Any] = Field(alias="normalizedSpec")
    universe_symbols: list[str] = Field(alias="universeSymbols")
    warnings: list[str] = Field(default_factory=list)
    estimated_cost: dict[str, int] = Field(default_factory=dict, alias="estimatedCost")


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
    metrics: dict[str, Any]
    attempt: int
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")


class ExperimentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
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
    "completed",
    "partially_failed",
    "failed",
    "cancel_requested",
    "cancelled",
    "data_changed",
]
