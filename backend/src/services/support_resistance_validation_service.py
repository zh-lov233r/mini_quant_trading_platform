from __future__ import annotations

import copy
import statistics
from datetime import UTC, date, datetime
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tables import ExperimentCandidate, ExperimentTrial, ResearchExperiment, Strategy
from src.schemas.research import (
    AdaptiveCandidateProposal,
    AdaptiveExperimentSpec,
    AdaptiveSearchPolicy,
    CategoryStudyValidationRequest,
    CostScenario,
    ParetoObjective,
    PointInTimeUniversePolicy,
    SupportResistanceEffectivenessSpec,
)
from src.services.backtest_universe_service import resolve_point_in_time_universe
from src.services.research_experiment_service import (
    ExperimentConflictError,
    canonical_hash,
)
from src.services.market_data_maintenance_service import assert_market_data_submission_allowed
from src.services.strategy_registry import (
    build_strategy_catalog,
    extract_description,
    is_engine_ready,
)
from src.services.strategy_service import validate_strategy_params


STUDY_KIND = "support_resistance_effectiveness_v3"
DISCOVERY_IN = (date(2017, 3, 20), date(2019, 12, 31))
DISCOVERY_OUT = (date(2020, 1, 2), date(2020, 12, 31))
ANNUAL_FOLDS = (
    ("annual_2021", date(2017, 3, 20), date(2020, 12, 31), date(2021, 1, 4), date(2021, 12, 31)),
    ("annual_2022", date(2017, 3, 20), date(2021, 12, 31), date(2022, 1, 3), date(2022, 12, 30)),
    ("annual_2023", date(2017, 3, 20), date(2022, 12, 30), date(2023, 1, 3), date(2023, 12, 29)),
)
FINAL_WINDOW = (
    date(2017, 3, 20),
    date(2023, 12, 29),
    date(2024, 1, 2),
    date(2026, 8, 27),
)
BASE_COST = CostScenario(name="base", commissionBps=1, commissionMin=0, slippageBps=2)
STRESS_COST = CostScenario(name="stress", commissionBps=3, commissionMin=0, slippageBps=8)
CACHE_REPLAY_COST = CostScenario(
    name="base_cache_replay",
    commissionBps=1,
    commissionMin=0,
    slippageBps=2,
)
OBJECTIVES = [
    ParetoObjective(metric="oos_excess_return", direction="maximize"),
    ParetoObjective(metric="oos_sharpe", direction="maximize"),
    ParetoObjective(metric="oos_max_drawdown", direction="minimize"),
    ParetoObjective(metric="pnl_concentration", direction="minimize"),
]

DETECTOR_PROFILES: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "default",
        {
            "signal.pivot_left_bars": 3,
            "signal.pivot_right_bars": 3,
            "signal.detection_window": 120,
            "signal.min_line_pivots": 2,
            "signal.min_line_span_sessions": 10,
            "signal.line_inlier_tolerance_atr": 0.75,
            "signal.max_abs_slope_atr_per_session": 0.25,
            "signal.zone_half_width_atr": 0.5,
            "signal.decay_half_life": 60,
        },
    ),
    (
        "fast",
        {
            "signal.pivot_left_bars": 2,
            "signal.pivot_right_bars": 2,
            "signal.detection_window": 60,
            "signal.min_line_pivots": 3,
            "signal.min_line_span_sessions": 8,
            "signal.line_inlier_tolerance_atr": 0.5,
            "signal.max_abs_slope_atr_per_session": 0.25,
            "signal.zone_half_width_atr": 0.25,
            "signal.decay_half_life": 30,
        },
    ),
    (
        "broad",
        {
            "signal.pivot_left_bars": 5,
            "signal.pivot_right_bars": 5,
            "signal.detection_window": 200,
            "signal.min_line_pivots": 4,
            "signal.min_line_span_sessions": 15,
            "signal.line_inlier_tolerance_atr": 1.0,
            "signal.max_abs_slope_atr_per_session": 0.2,
            "signal.zone_half_width_atr": 0.75,
            "signal.decay_half_life": 120,
        },
    ),
    (
        "selective",
        {
            "signal.pivot_left_bars": 3,
            "signal.pivot_right_bars": 3,
            "signal.detection_window": 120,
            "signal.min_line_pivots": 4,
            "signal.min_line_span_sessions": 15,
            "signal.line_inlier_tolerance_atr": 0.5,
            "signal.max_abs_slope_atr_per_session": 0.15,
            "signal.zone_half_width_atr": 0.25,
            "signal.decay_half_life": 60,
        },
    ),
)


def _mode_switches(setup: str) -> dict[str, bool]:
    return {
        "signal.support_bounce_enabled": setup == "support_bounce",
    }


def _trigger_profiles(setup: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    return tuple(
        (label, {"signal.bounce_confirmation_atr": value})
        for label, value in (("loose", 0.10), ("default", 0.25), ("strict", 0.50))
    )


def fixed_mode_candidates(setup: str) -> list[AdaptiveCandidateProposal]:
    if setup not in {"support_bounce"}:
        raise ValueError("only support_bounce has discovery trials")
    candidates: list[AdaptiveCandidateProposal] = []
    for trigger_label, trigger in _trigger_profiles(setup):
        for detector_label, detector in DETECTOR_PROFILES:
            overrides = {**_mode_switches(setup), **detector, **trigger}
            candidates.append(
                AdaptiveCandidateProposal(
                    overrides=overrides,
                    rationale=f"pre-registered {setup} detector={detector_label} trigger={trigger_label}",
                )
            )
    return candidates


def _catalog_defaults() -> dict[str, Any]:
    item = next(
        entry for entry in build_strategy_catalog() if entry["strategy_type"] == "support_resistance"
    )
    return copy.deepcopy(item["defaults"])


def validate_effectiveness_study_request(
    db: Session,
    payload: CategoryStudyValidationRequest,
) -> dict[str, Any]:
    assert payload.universe_policy is not None
    assert payload.validation_protocol is not None
    policy = payload.universe_policy.model_dump(mode="json", by_alias=True)
    resolved = resolve_point_in_time_universe(
        db,
        policy,
        start_date=DISCOVERY_IN[0],
        end_date=FINAL_WINDOW[3],
    )
    params = _catalog_defaults()
    for path, replacement in sorted(payload.strategy.overrides.items()):
        if not path.startswith(("signal.", "risk.")):
            raise ValueError(f"parameter path is not allowed: {path}")
        current = params
        parts = path.split(".")
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                raise ValueError(f"parameter path does not exist: {path}")
            current = current[part]
        if parts[-1] not in current:
            raise ValueError(f"parameter path does not exist: {path}")
        current[parts[-1]] = replacement
    params = validate_strategy_params(
        db,
        strategy_type="support_resistance",
        params=params,
        description=payload.strategy.description,
    )
    params["universe"] = {
        "symbols": [],
        "selection_mode": "point_in_time_liquid",
        "policy": policy,
    }
    if not is_engine_ready("support_resistance", params):
        raise ValueError("generated strategy is not engine-ready")
    proposal_hash = canonical_hash(payload.model_dump(mode="json", by_alias=True))
    params.setdefault("metadata", {})["research_origin"] = {
        "workflowRunId": payload.workflow_run_id,
        "strategyType": "support_resistance",
        "purpose": "support_resistance_effectiveness",
        "proposalHash": proposal_hash,
    }
    normalized_spec = SupportResistanceEffectivenessSpec(
        name=payload.name,
        hypothesis=payload.hypothesis,
        strategyId=UUID(int=0),
        universePolicy=payload.universe_policy,
        validationProtocol=payload.validation_protocol,
        initialCash=payload.initial_cash,
        benchmarkSymbol="SPY",
        stopPolicy=payload.stop_policy,
    ).model_dump(mode="json", by_alias=True)
    normalized_spec["strategyId"] = None
    return {
        "valid": True,
        "normalizedStrategy": {
            "name": payload.strategy.name,
            "description": payload.strategy.description,
            "strategy_type": "support_resistance",
            "params": params,
            "status": "draft",
        },
        "normalizedSpec": normalized_spec,
        "firstRoundTrialCount": 48,
        "maximumTrialCount": 200,
        "universeSymbols": [],
        "universeSummary": {
            "membershipSemantics": "point_in_time_liquid",
            "instrumentCount": len(resolved.instruments),
            "instrumentSetHash": resolved.manifest()["instrument_set_hash"],
            "policy": policy,
        },
        "proposalHash": proposal_hash,
        "warnings": [
            "The final holdout is sealed until all three annual folds are terminal.",
            "No portfolio, scheduler, or order permissions are granted.",
        ],
    }


def _child_spec(
    parent_spec: SupportResistanceEffectivenessSpec,
    *,
    name: str,
    in_start: date,
    in_end: date,
    out_start: date,
    out_end: date,
    proposals: list[AdaptiveCandidateProposal],
    max_rounds: int,
    max_trials: int,
    cost_scenarios: list[CostScenario] | None = None,
    sample_kinds: list[str] | None = None,
) -> AdaptiveExperimentSpec:
    return AdaptiveExperimentSpec(
        name=name,
        hypothesis=parent_spec.hypothesis,
        strategyId=parent_spec.strategy_id,
        strategyType="support_resistance",
        universePolicy=parent_spec.universe_policy,
        symbols=[],
        inSample={"startDate": in_start, "endDate": in_end},
        outOfSample={"startDate": out_start, "endDate": out_end},
        costScenarios=cost_scenarios or [BASE_COST, STRESS_COST],
        sampleKinds=sample_kinds or ["in_sample", "out_of_sample"],
        initialCash=parent_spec.initial_cash,
        benchmarkSymbol="SPY",
        stopPolicy=parent_spec.stop_policy,
        searchPolicy=AdaptiveSearchPolicy(
            maxRounds=max_rounds,
            maxTrials=max_trials,
            objectives=OBJECTIVES,
        ),
        initialCandidates=proposals,
    )


def create_effectiveness_study(
    db: Session,
    *,
    workflow_run_id: str,
    spec: SupportResistanceEffectivenessSpec,
    idempotency_key: str,
) -> ResearchExperiment:
    existing = db.execute(
        select(ResearchExperiment).where(ResearchExperiment.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    request_hash = canonical_hash(spec.model_dump(mode="json", by_alias=True))
    if existing is not None:
        if existing.workflow_run_id == workflow_run_id and (existing.run_manifest or {}).get("requestHash") == request_hash:
            return existing
        raise ExperimentConflictError("idempotency key was used with a different effectiveness study")
    assert_market_data_submission_allowed(db)
    strategy = db.get(Strategy, spec.strategy_id)
    if strategy is None or strategy.status != "draft" or strategy.strategy_type != "support_resistance":
        raise ValueError("effectiveness study requires a draft support_resistance strategy")
    now = datetime.now(UTC)
    parent = ResearchExperiment(
        study_kind=STUDY_KIND,
        workflow_run_id=workflow_run_id,
        idempotency_key=idempotency_key,
        status="queued",
        spec=spec.model_dump(mode="json", by_alias=True),
        run_manifest={
            "requestHash": request_hash,
            "protocolHash": canonical_hash(spec.validation_protocol.model_dump(mode="json", by_alias=True)),
            "strategyId": str(strategy.id),
            "strategyVersion": strategy.version,
            "strategyType": strategy.strategy_type,
            "sealedHoldout": {
                "startDate": FINAL_WINDOW[2].isoformat(),
                "endDate": FINAL_WINDOW[3].isoformat(),
                "openedAt": None,
            },
            "backtestBudget": {"maximum": 200, "scheduled": 48},
            "createdAt": now.isoformat(),
            "reportArtifacts": {"status": "pending"},
        },
        progress={"phase": "discovery", "total": 200, "scheduled": 48, "completed": 0},
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    from src.services.adaptive_research_service import create_category_study

    child_ids: dict[str, str] = {}
    for setup in ("support_bounce",):
        candidates = fixed_mode_candidates(setup)
        child_spec = _child_spec(
            spec,
            name=f"{spec.name} - discovery - {setup}",
            in_start=DISCOVERY_IN[0],
            in_end=DISCOVERY_IN[1],
            out_start=DISCOVERY_OUT[0],
            out_end=DISCOVERY_OUT[1],
            proposals=candidates[:4],
            max_rounds=3,
            max_trials=48,
        )
        child = create_category_study(
            db,
            workflow_run_id=workflow_run_id,
            spec=child_spec,
            idempotency_key=f"{idempotency_key}:discovery:{setup}",
            parent_experiment_id=parent.id,
            study_kind="effectiveness_discovery",
        )
        child.spec = {
            **dict(child.spec or {}),
            "validationPhase": f"discovery:{setup}",
            "scheduledRounds": {
                "2": [item.model_dump(mode="json", by_alias=True) for item in candidates[4:8]],
                "3": [item.model_dump(mode="json", by_alias=True) for item in candidates[8:12]],
            },
        }
        child_ids[setup] = str(child.id)
        db.commit()
    parent = db.get(ResearchExperiment, parent.id)
    assert parent is not None
    manifest = dict(parent.run_manifest or {})
    manifest["children"] = child_ids
    parent.run_manifest = manifest
    db.commit()
    db.refresh(parent)
    return parent


def _trial_metric(candidate: ExperimentCandidate, scenario: str, key: str) -> float | None:
    trial = next(
        (
            item
            for item in candidate.trials
            if item.status == "completed"
            and item.sample_kind == "out_of_sample"
            and item.cost_scenario.lower() == scenario
        ),
        None,
    )
    value = (trial.metrics or {}).get(key) if trial else None
    return float(value) if isinstance(value, (int, float)) else None


def _mode_champion(child: ResearchExperiment) -> ExperimentCandidate | None:
    candidates = [
        item
        for item in child.candidates
        if (_trial_metric(item, "base", "excess_return") or float("-inf")) > 0
        and (_trial_metric(item, "stress", "excess_return") or float("-inf")) > 0
    ]
    if not candidates:
        return None

    def key(item: ExperimentCandidate) -> tuple[Any, ...]:
        base_excess = _trial_metric(item, "base", "excess_return")
        stress_excess = _trial_metric(item, "stress", "excess_return")
        sharpe = _trial_metric(item, "base", "sharpe")
        drawdown = _trial_metric(item, "base", "max_drawdown")
        concentration = _trial_metric(item, "base", "pnl_concentration")
        return (
            -min(
                base_excess if base_excess is not None else float("-inf"),
                stress_excess if stress_excess is not None else float("-inf"),
            ),
            -(sharpe if sharpe is not None else float("-inf")),
            drawdown if drawdown is not None else float("inf"),
            concentration if concentration is not None else float("inf"),
            item.params_hash,
        )

    return min(candidates, key=key)


def _proposal_from_candidate(candidate: ExperimentCandidate, rationale: str) -> AdaptiveCandidateProposal:
    overrides = dict(candidate.parameter_overrides or {})
    if not overrides:
        overrides = {"signal.support_bounce_enabled": True}
    return AdaptiveCandidateProposal(overrides=overrides, rationale=rationale)


def _default_proposal() -> AdaptiveCandidateProposal:
    return AdaptiveCandidateProposal(
        overrides={
            "signal.support_bounce_enabled": True,
        },
        rationale="pre-registered frozen-phase support-bounce default; validity must be established independently",
    )


def _create_fold_children(db: Session, parent: ResearchExperiment) -> None:
    if any(
        str((item.spec or {}).get("validationPhase") or "").startswith("annual_")
        for item in parent.child_experiments
    ):
        return
    spec = SupportResistanceEffectivenessSpec.model_validate(parent.spec)
    discovery = {
        str((item.spec or {}).get("validationPhase", "")).split(":", 1)[-1]: item
        for item in parent.child_experiments
        if str((item.spec or {}).get("validationPhase", "")).startswith("discovery:")
    }
    if set(discovery) != {"support_bounce"} or any(item.status not in {"completed", "partially_failed", "failed"} for item in discovery.values()):
        return
    champions = {setup: _mode_champion(child) for setup, child in discovery.items()}
    if any(candidate is None for candidate in champions.values()):
        parent.error_code = "no_discovery_candidates"
        parent.error_message = "One or more discovery studies produced no eligible candidate evidence."
        from src.services.support_resistance_validation_report_service import (
            finalize_validation_report,
        )

        finalize_validation_report(db, parent)
        return
    proposals = [_default_proposal()] + [
        _proposal_from_candidate(champions[setup], f"frozen discovery champion: {setup}")
        for setup in ("support_bounce",)
        if champions[setup] is not None
    ]
    manifest = dict(parent.run_manifest or {})
    manifest["modeChampions"] = {
        setup: {
            "candidateId": str(candidate.id),
            "paramsHash": candidate.params_hash,
            "overrides": candidate.parameter_overrides or {},
        }
        for setup, candidate in champions.items()
        if candidate is not None
    }
    parent.run_manifest = manifest
    parent.status = "running"
    db.commit()

    from src.services.adaptive_research_service import create_category_study

    fold_children: dict[str, str] = {}
    for phase, in_start, in_end, out_start, out_end in ANNUAL_FOLDS:
        child = create_category_study(
            db,
            workflow_run_id=parent.workflow_run_id,
            spec=_child_spec(
                spec,
                name=f"{spec.name} - {phase}",
                in_start=in_start,
                in_end=in_end,
                out_start=out_start,
                out_end=out_end,
                proposals=proposals,
                max_rounds=1,
                max_trials=8,
            ),
            idempotency_key=f"{parent.idempotency_key}:{phase}",
            parent_experiment_id=parent.id,
            study_kind="effectiveness_annual_fold",
        )
        child.spec = {**dict(child.spec or {}), "validationPhase": phase}
        fold_children[phase] = str(child.id)
        db.commit()
    parent = db.get(ResearchExperiment, parent.id)
    assert parent is not None
    manifest = dict(parent.run_manifest or {})
    manifest["children"] = {**dict(manifest.get("children") or {}), **fold_children}
    manifest["backtestBudget"] = {"maximum": 200, "scheduled": 72}
    parent.run_manifest = manifest
    parent.status = "running"
    parent.progress = {"phase": "annual_folds", "total": 200, "scheduled": 72, "completed": 48}
    db.commit()


def _candidate_annual_metrics(parent: ResearchExperiment, params_hash: str) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for child in parent.child_experiments:
        phase = str((child.spec or {}).get("validationPhase") or "")
        if not phase.startswith("annual_"):
            continue
        candidate = next((item for item in child.candidates if item.params_hash == params_hash), None)
        if candidate is None:
            continue
        base = _trial_metric(candidate, "base", "excess_return")
        stress = _trial_metric(candidate, "stress", "excess_return")
        drawdown = _trial_metric(candidate, "base", "max_drawdown")
        if base is not None:
            result.append(
                {
                    "base_excess": base,
                    "stress_excess": stress if stress is not None else float("-inf"),
                    "drawdown": drawdown if drawdown is not None else float("inf"),
                }
            )
    return result


def _choose_frozen_champion(parent: ResearchExperiment) -> dict[str, Any] | None:
    champion_rows = list(((parent.run_manifest or {}).get("modeChampions") or {}).values())
    eligible: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in champion_rows:
        metrics = _candidate_annual_metrics(parent, str(row["paramsHash"]))
        if len(metrics) != 3 or not all(item["base_excess"] > 0 for item in metrics):
            continue
        base_values = [item["base_excess"] for item in metrics]
        stress_decay = [item["base_excess"] - item["stress_excess"] for item in metrics]
        key = (
            -statistics.median(base_values),
            max(item["drawdown"] for item in metrics),
            max(stress_decay),
            str(row["paramsHash"]),
        )
        eligible.append((key, row))
    return min(eligible, key=lambda item: item[0])[1] if eligible else None


def _create_final_child(db: Session, parent: ResearchExperiment) -> None:
    if any(
        (item.spec or {}).get("validationPhase") == "final_holdout"
        for item in parent.child_experiments
    ):
        return
    annual = [
        item
        for item in parent.child_experiments
        if str((item.spec or {}).get("validationPhase") or "").startswith("annual_")
    ]
    if len(annual) != 3 or any(item.status not in {"completed", "partially_failed", "failed"} for item in annual):
        return
    champion = _choose_frozen_champion(parent)
    proposals = [_default_proposal()]
    if champion is not None:
        proposals.append(
            AdaptiveCandidateProposal(
                overrides=champion["overrides"],
                rationale="sealed frozen non-default champion",
            )
        )
    spec = SupportResistanceEffectivenessSpec.model_validate(parent.spec)
    child = None
    from src.services.adaptive_research_service import create_category_study

    child = create_category_study(
        db,
        workflow_run_id=parent.workflow_run_id,
        spec=_child_spec(
            spec,
            name=f"{spec.name} - final holdout",
            in_start=FINAL_WINDOW[0],
            in_end=FINAL_WINDOW[1],
            out_start=FINAL_WINDOW[2],
            out_end=FINAL_WINDOW[3],
            proposals=proposals,
            max_rounds=1,
            max_trials=8,
            cost_scenarios=[BASE_COST, STRESS_COST, CACHE_REPLAY_COST],
            sample_kinds=["out_of_sample"],
        ),
        idempotency_key=f"{parent.idempotency_key}:final",
        parent_experiment_id=parent.id,
        study_kind="effectiveness_final_holdout",
    )
    child.spec = {**dict(child.spec or {}), "validationPhase": "final_holdout"}
    db.commit()
    parent = db.get(ResearchExperiment, parent.id)
    assert parent is not None
    manifest = dict(parent.run_manifest or {})
    manifest["frozenChampion"] = champion
    manifest["children"] = {**dict(manifest.get("children") or {}), "final_holdout": str(child.id)}
    manifest["sealedHoldout"] = {
        **dict(manifest.get("sealedHoldout") or {}),
        "openedAt": datetime.now(UTC).isoformat(),
        "championHash": champion.get("paramsHash") if champion else None,
    }
    manifest["backtestBudget"] = {
        "maximum": 200,
        "scheduled": 78 if champion else 75,
    }
    parent.run_manifest = manifest
    parent.progress = {
        "phase": "final_holdout",
        "total": 200,
        "scheduled": 78 if champion else 75,
        "completed": 72,
    }
    db.commit()


def _submit_scheduled_round(db: Session, child: ResearchExperiment) -> bool:
    if child.status != "waiting_agent":
        return False
    scheduled = dict((child.spec or {}).get("scheduledRounds") or {})
    next_ordinal = len(child.rounds) + 1
    rows = scheduled.get(str(next_ordinal))
    if not rows:
        return False
    from src.services.adaptive_research_service import _create_round

    spec = AdaptiveExperimentSpec.model_validate(child.spec)
    _create_round(
        db,
        experiment=child,
        spec=spec,
        round_ordinal=next_ordinal,
        proposals=[AdaptiveCandidateProposal.model_validate(item) for item in rows],
    )
    child.status = "queued"
    child.error_code = None
    child.error_message = None
    db.commit()
    return True


def advance_effectiveness_study(db: Session, child_experiment_id: UUID) -> None:
    child = db.get(ResearchExperiment, child_experiment_id)
    if child is None or child.parent_experiment_id is None:
        return
    parent = db.get(ResearchExperiment, child.parent_experiment_id)
    if parent is None or parent.study_kind != STUDY_KIND or parent.status in {
        "completed",
        "partially_failed",
        "failed",
        "cancel_requested",
        "cancelled",
    }:
        return
    if child.status == "cancelled":
        parent.error_code = f"child_{child.status}"
        parent.error_message = f"Child experiment {child.id} ended with status {child.status}."
        from src.services.support_resistance_validation_report_service import (
            finalize_validation_report,
        )

        finalize_validation_report(db, parent)
        return
    if _submit_scheduled_round(db, child):
        return
    phase = str((child.spec or {}).get("validationPhase") or "")
    if phase.startswith("discovery:"):
        _create_fold_children(db, parent)
        return
    if phase.startswith("annual_"):
        _create_final_child(db, parent)
        return
    if phase == "final_holdout" and child.status in {"completed", "partially_failed", "failed"}:
        from src.services.support_resistance_validation_report_service import finalize_validation_report

        finalize_validation_report(db, parent)


def list_child_experiments(db: Session, parent_id: UUID) -> list[ResearchExperiment]:
    parent = db.get(ResearchExperiment, parent_id)
    if parent is None:
        return []
    return list(
        db.execute(
            select(ResearchExperiment)
            .where(ResearchExperiment.parent_experiment_id == parent_id)
            .order_by(ResearchExperiment.created_at, ResearchExperiment.id)
        ).scalars()
    )
