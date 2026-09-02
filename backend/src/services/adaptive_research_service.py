from __future__ import annotations

import copy
import math
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.tables import (
    ExperimentCandidate,
    ExperimentRound,
    ExperimentTrial,
    ResearchExperiment,
    Strategy,
    StrategyAllocation,
    StrategyRun,
)
from src.schemas.research import (
    AdaptiveCandidateProposal,
    AdaptiveExperimentSpec,
    CandidatePromotionRequest,
    CategoryStudyValidationRequest,
    ControllerFailureRequest,
    ExperimentRoundSubmit,
)
from src.services.research_experiment_service import (
    ExperimentConflictError,
    ExperimentNotFoundError,
    _load_universe,
    _refresh_progress,
    build_experiment_report,
    calculate_data_fingerprint,
    canonical_hash,
)
from src.services.backtest_job_service import enqueue_backtest_job
from src.services.strategy_registry import (
    build_strategy_catalog,
    extract_description,
    is_engine_ready,
    normalize_strategy_params,
)
from src.services.strategy_service import create_strategy_version, validate_strategy_params


MAX_ADAPTIVE_ROUNDS = 5
MAX_ADAPTIVE_TRIALS = 100
MAX_CANDIDATES_PER_ROUND = 5
_ALLOWED_PREFIXES = ("signal.", "risk.")
_TERMINAL = {"completed", "partially_failed", "failed", "cancelled", "data_changed"}
VERIFICATION_METRIC_TOLERANCE = 1e-10


def _catalog_item(strategy_type: str) -> dict[str, Any]:
    item = next(
        (entry for entry in build_strategy_catalog() if entry["strategy_type"] == strategy_type),
        None,
    )
    if item is None or not item.get("engine_ready"):
        raise ValueError(f"strategy type has no engine handler: {strategy_type}")
    return item


def _get_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"parameter path does not exist: {path}")
        current = current[part]
    return current


def _apply_scalar_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for path in sorted(overrides):
        if not path.startswith(_ALLOWED_PREFIXES):
            raise ValueError(f"parameter path is not allowed: {path}")
        existing = _get_path(result, path)
        replacement = overrides[path]
        if isinstance(existing, (dict, list)) or isinstance(replacement, (dict, list)):
            raise ValueError(f"only existing scalar parameters may be changed: {path}")
        current = result
        parts = path.split(".")
        for part in parts[:-1]:
            current = current[part]
        current[parts[-1]] = copy.deepcopy(replacement)
    return result


def _normalize_candidate(
    db: Session,
    *,
    strategy_type: str,
    base_params: dict[str, Any],
    overrides: dict[str, Any],
    symbols: list[str],
    basket_id: UUID | None,
    universe_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = _apply_scalar_overrides(base_params, overrides)
    params = validate_strategy_params(
        db,
        strategy_type=strategy_type,
        params=params,
        description=extract_description(params),
    )
    if universe_policy is not None:
        params["universe"]["symbols"] = []
        params["universe"]["selection_mode"] = "point_in_time_liquid"
        params["universe"]["policy"] = universe_policy
    else:
        params["universe"]["symbols"] = symbols
        params["universe"]["selection_mode"] = "stock_basket" if basket_id else "manual"
    return params


def validate_category_study(
    db: Session,
    payload: CategoryStudyValidationRequest,
) -> dict[str, Any]:
    catalog = _catalog_item(payload.strategy_type)
    if payload.validation_protocol is not None:
        from src.services.support_resistance_validation_service import (
            validate_effectiveness_study_request,
        )

        return validate_effectiveness_study_request(db, payload)
    symbols, universe = _load_universe(db, payload)
    base_params = _apply_scalar_overrides(catalog["defaults"], payload.strategy.overrides)
    base_params = validate_strategy_params(
        db,
        strategy_type=payload.strategy_type,
        params=base_params,
        description=payload.strategy.description,
    )
    universe_policy = (
        payload.universe_policy.model_dump(mode="json", by_alias=True)
        if payload.universe_policy
        else None
    )
    if universe_policy is not None:
        base_params["universe"]["symbols"] = []
        base_params["universe"]["selection_mode"] = "point_in_time_liquid"
        base_params["universe"]["policy"] = universe_policy
    else:
        base_params["universe"]["symbols"] = symbols
        base_params["universe"]["selection_mode"] = "stock_basket" if payload.basket_id else "manual"
    if not is_engine_ready(payload.strategy_type, base_params):
        raise ValueError("generated strategy is not engine-ready")

    seen: set[str] = set()
    normalized_candidates: list[dict[str, Any]] = []
    for proposal in payload.initial_candidates:
        params = _normalize_candidate(
            db,
            strategy_type=payload.strategy_type,
            base_params=base_params,
            overrides=proposal.overrides,
            symbols=symbols,
            basket_id=payload.basket_id,
            universe_policy=universe_policy,
        )
        params_hash = canonical_hash(params)
        if params_hash in seen:
            raise ValueError(f"duplicate initial candidate: {params_hash[:12]}")
        seen.add(params_hash)
        normalized_candidates.append(
            {
                "overrides": dict(sorted(proposal.overrides.items())),
                "rationale": proposal.rationale,
                "params": params,
                "paramsHash": params_hash,
            }
        )

    assert payload.search_policy is not None
    assert payload.in_sample is not None and payload.out_of_sample is not None
    multiplier = 2 * len(payload.cost_scenarios)
    first_round_trials = len(normalized_candidates) * multiplier
    if first_round_trials > payload.search_policy.max_trials:
        raise ValueError("initialCandidates exceed the actual backtest trial budget")

    metadata = base_params.setdefault("metadata", {})
    proposal_for_hash = payload.model_dump(mode="json", by_alias=True)
    proposal_hash = canonical_hash(proposal_for_hash)
    metadata["research_origin"] = {
        "workflowRunId": payload.workflow_run_id,
        "strategyType": payload.strategy_type,
        "purpose": "adaptive_category_research",
        "proposalHash": proposal_hash,
    }
    normalized_spec = {
        "researchMode": "adaptive_category",
        "name": payload.name,
        "hypothesis": payload.hypothesis,
        "strategyType": payload.strategy_type,
        "basketId": str(payload.basket_id) if payload.basket_id else None,
        "symbols": [] if universe_policy is not None or payload.basket_id else symbols,
        "universePolicy": universe_policy,
        "inSample": payload.in_sample.model_dump(mode="json", by_alias=True),
        "outOfSample": payload.out_of_sample.model_dump(mode="json", by_alias=True),
        "costScenarios": [item.model_dump(mode="json", by_alias=True) for item in payload.cost_scenarios],
        "initialCash": payload.initial_cash,
        "benchmarkSymbol": payload.benchmark_symbol,
        "stopPolicy": payload.stop_policy.model_dump(mode="json", by_alias=True) if payload.stop_policy else None,
        "searchPolicy": payload.search_policy.model_dump(mode="json", by_alias=True),
        "initialCandidates": normalized_candidates,
    }
    return {
        "valid": True,
        "normalizedStrategy": {
            "name": payload.strategy.name,
            "description": payload.strategy.description,
            "strategy_type": payload.strategy_type,
            "params": base_params,
            "status": "draft",
        },
        "normalizedSpec": normalized_spec,
        "firstRoundTrialCount": first_round_trials,
        "maximumTrialCount": payload.search_policy.max_trials,
        "universeSymbols": symbols,
        "universeSummary": universe,
        "proposalHash": proposal_hash,
        "warnings": [],
    }


def _trial_definitions(
    *,
    spec: AdaptiveExperimentSpec,
    candidate: ExperimentCandidate,
    start_ordinal: int,
) -> list[ExperimentTrial]:
    result: list[ExperimentTrial] = []
    available_windows = {
        "in_sample": (spec.in_sample.start_date, spec.in_sample.end_date),
        "out_of_sample": (spec.out_of_sample.start_date, spec.out_of_sample.end_date),
    }
    windows = [
        (sample_kind, *available_windows[sample_kind])
        for sample_kind in spec.sample_kinds
    ]
    for sample_kind, start_date, end_date in windows:
        for scenario in sorted(spec.cost_scenarios, key=lambda item: item.name.lower()):
            trial_key = canonical_hash(
                {
                    "candidateId": str(candidate.id),
                    "sampleKind": sample_kind,
                    "costScenario": scenario.model_dump(mode="json", by_alias=True),
                }
            )
            result.append(
                ExperimentTrial(
                    experiment_id=candidate.experiment_id,
                    candidate_id=candidate.id,
                    trial_key=trial_key,
                    ordinal=start_ordinal + len(result),
                    status="queued",
                    sample_kind=sample_kind,
                    cost_scenario=scenario.name,
                    params=candidate.params,
                    params_hash=candidate.params_hash,
                    window_start=start_date,
                    window_end=end_date,
                    cost_config=scenario.model_dump(mode="json", by_alias=True),
                )
            )
    return result


def _create_round(
    db: Session,
    *,
    experiment: ResearchExperiment,
    spec: AdaptiveExperimentSpec,
    round_ordinal: int,
    proposals: Iterable[AdaptiveCandidateProposal],
) -> ExperimentRound:
    strategy = db.get(Strategy, spec.strategy_id)
    if strategy is None:
        raise ValueError("strategy not found")
    if strategy.strategy_type != spec.strategy_type:
        raise ValueError("strategyType does not match the created draft")
    base_params = normalize_strategy_params(
        strategy.strategy_type,
        strategy.params,
        extract_description(strategy.params),
    )
    symbols, _ = _load_universe(db, spec)
    universe_policy = (
        spec.universe_policy.model_dump(mode="json", by_alias=True)
        if spec.universe_policy
        else None
    )
    existing_hashes = set(
        db.execute(
            select(ExperimentCandidate.params_hash).where(
                ExperimentCandidate.experiment_id == experiment.id
            )
        ).scalars()
    )
    normalized: list[tuple[AdaptiveCandidateProposal, dict[str, Any], str]] = []
    for proposal in proposals:
        params = _normalize_candidate(
            db,
            strategy_type=strategy.strategy_type,
            base_params=base_params,
            overrides=proposal.overrides,
            symbols=symbols,
            basket_id=spec.basket_id,
            universe_policy=universe_policy,
        )
        params_hash = canonical_hash(params)
        if params_hash in existing_hashes or any(item[2] == params_hash for item in normalized):
            continue
        normalized.append((proposal, params, params_hash))
    if not normalized:
        raise ValueError("no_novel_candidates")

    per_candidate = len(spec.sample_kinds) * len(spec.cost_scenarios)
    current_trials = int(
        db.execute(
            select(func.count()).select_from(ExperimentTrial).where(
                ExperimentTrial.experiment_id == experiment.id
            )
        ).scalar_one()
    )
    remaining = spec.search_policy.max_trials - current_trials
    if remaining < per_candidate:
        raise ExperimentConflictError("max_trials_reached")
    normalized = normalized[: min(MAX_CANDIDATES_PER_ROUND, remaining // per_candidate)]

    round_row = ExperimentRound(
        experiment_id=experiment.id,
        ordinal=round_ordinal,
        status="queued",
        proposal={
            "candidates": [
                {
                    "overrides": dict(sorted(item[0].overrides.items())),
                    "rationale": item[0].rationale,
                    "paramsHash": item[2],
                }
                for item in normalized
            ]
        },
    )
    db.add(round_row)
    db.flush()
    next_trial_ordinal = current_trials
    for ordinal, (proposal, params, params_hash) in enumerate(normalized):
        candidate = ExperimentCandidate(
            experiment_id=experiment.id,
            round_id=round_row.id,
            ordinal=ordinal,
            parameter_overrides=dict(sorted(proposal.overrides.items())),
            params=params,
            params_hash=params_hash,
            rationale=proposal.rationale,
        )
        db.add(candidate)
        db.flush()
        trials = _trial_definitions(
            spec=spec,
            candidate=candidate,
            start_ordinal=next_trial_ordinal,
        )
        db.add_all(trials)
        next_trial_ordinal += len(trials)
    return round_row


def create_category_study(
    db: Session,
    *,
    workflow_run_id: str,
    spec: AdaptiveExperimentSpec,
    idempotency_key: str,
    parent_experiment_id: UUID | None = None,
    study_kind: str = "adaptive_category",
) -> ResearchExperiment:
    existing = db.execute(
        select(ResearchExperiment).where(ResearchExperiment.idempotency_key == idempotency_key)
    ).scalars().first()
    request_hash = canonical_hash(spec.model_dump(mode="json", by_alias=True))
    if existing is not None:
        if existing.workflow_run_id == workflow_run_id and (existing.run_manifest or {}).get("requestHash") == request_hash:
            return existing
        raise ExperimentConflictError("idempotency key was used with a different category study")

    strategy = db.get(Strategy, spec.strategy_id)
    if strategy is None or strategy.status != "draft":
        raise ValueError("category study requires the auto-created draft strategy")
    if strategy.strategy_type != spec.strategy_type:
        raise ValueError("strategyType does not match the draft strategy")
    base_params = validate_strategy_params(
        db,
        strategy_type=strategy.strategy_type,
        params=strategy.params,
        description=extract_description(strategy.params),
    )
    if not is_engine_ready(strategy.strategy_type, base_params):
        raise ValueError("draft strategy is not engine-ready")
    symbols, universe = _load_universe(db, spec)
    start_date = min(spec.in_sample.start_date, spec.out_of_sample.start_date)
    end_date = max(spec.in_sample.end_date, spec.out_of_sample.end_date)
    fingerprint = calculate_data_fingerprint(
        db,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        universe_policy=(
            spec.universe_policy.model_dump(mode="json", by_alias=True)
            if spec.universe_policy
            else None
        ),
    )
    now = datetime.now(UTC)
    experiment = ResearchExperiment(
        parent_experiment_id=parent_experiment_id,
        study_kind=study_kind,
        workflow_run_id=workflow_run_id,
        idempotency_key=idempotency_key,
        status="queued",
        spec=spec.model_dump(mode="json", by_alias=True) | {"researchMode": "adaptive_category"},
        run_manifest={
            "requestHash": request_hash,
            "strategyId": str(strategy.id),
            "strategyVersion": strategy.version,
            "strategyType": strategy.strategy_type,
            "universe": universe,
            "dataFingerprint": fingerprint,
            "policyStartedAt": now.isoformat(),
            "tokenUsage": {},
        },
        progress={},
    )
    db.add(experiment)
    db.flush()
    _create_round(
        db,
        experiment=experiment,
        spec=spec,
        round_ordinal=1,
        proposals=spec.initial_candidates,
    )
    _refresh_progress(db, experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def submit_experiment_round(
    db: Session,
    experiment_id: UUID,
    payload: ExperimentRoundSubmit,
    *,
    idempotency_key: str,
) -> ResearchExperiment:
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(str(experiment_id))
    if experiment.workflow_run_id != payload.workflow_run_id:
        raise ExperimentConflictError("workflow does not own this experiment")
    if experiment.status != "waiting_agent":
        raise ExperimentConflictError(f"experiment is not waiting for a round: {experiment.status}")
    manifest = dict(experiment.run_manifest or {})
    round_keys = dict(manifest.get("roundIdempotencyKeys") or {})
    key_slot = str(payload.round_ordinal)
    if key_slot in round_keys:
        if round_keys[key_slot] == idempotency_key:
            return experiment
        raise ExperimentConflictError("round ordinal already submitted")
    spec = AdaptiveExperimentSpec.model_validate(experiment.spec)
    existing_rounds = int(
        db.execute(
            select(func.count()).select_from(ExperimentRound).where(
                ExperimentRound.experiment_id == experiment.id
            )
        ).scalar_one()
    )
    if payload.round_ordinal != existing_rounds + 1 or payload.round_ordinal > spec.search_policy.max_rounds:
        raise ExperimentConflictError("round ordinal is out of sequence")
    _create_round(
        db,
        experiment=experiment,
        spec=spec,
        round_ordinal=payload.round_ordinal,
        proposals=payload.candidates,
    )
    round_keys[key_slot] = idempotency_key
    manifest["roundIdempotencyKeys"] = round_keys
    experiment.run_manifest = manifest
    experiment.status = "queued"
    experiment.error_code = None
    experiment.error_message = None
    _refresh_progress(db, experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def _finite(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _candidate_aggregate(candidate: ExperimentCandidate) -> dict[str, Any]:
    completed = [item for item in candidate.trials if item.status == "completed"]
    indexed = {(item.sample_kind, item.cost_scenario.lower()): item for item in completed}
    base_oos = indexed.get(("out_of_sample", "base"))
    base_is = indexed.get(("in_sample", "base"))
    oos = base_oos.metrics if base_oos else {}
    ins = base_is.metrics if base_is else {}
    stress_returns = [
        _finite(item.metrics.get("total_return"))
        for item in completed
        if item.sample_kind == "out_of_sample" and item.cost_scenario.lower() != "base"
    ]
    stress_returns = [value for value in stress_returns if value is not None]
    base_return = _finite(oos.get("total_return"))
    is_return = _finite(ins.get("total_return"))
    return {
        "oos_total_return": base_return,
        "oos_annualized_return": _finite(oos.get("annualized_return")),
        "oos_sharpe": _finite(oos.get("sharpe")),
        "oos_sortino": _finite(oos.get("sortino")),
        "oos_excess_return": _finite(oos.get("excess_return")),
        "oos_max_drawdown": _finite(oos.get("max_drawdown")),
        "oos_turnover": _finite(oos.get("turnover")),
        "pnl_concentration": _finite(oos.get("pnl_concentration")),
        "cost_decay": (
            base_return - min(stress_returns)
            if base_return is not None and stress_returns
            else None
        ),
        "is_oos_abs_gap": (
            abs(is_return - base_return)
            if is_return is not None and base_return is not None
            else None
        ),
        "completedTrials": len(completed),
        "failedTrials": sum(item.status == "failed" for item in candidate.trials),
    }


def nondominated_sort(
    rows: list[tuple[str, dict[str, Any]]],
    objectives: list[dict[str, Any]],
) -> dict[str, int | None]:
    valid = {
        key: values
        for key, values in rows
        if all(_finite(values.get(item["metric"])) is not None for item in objectives)
    }
    ranks: dict[str, int | None] = {key: None for key, _ in rows}
    remaining = set(valid)
    rank = 1
    while remaining:
        frontier: list[str] = []
        for candidate_key in sorted(remaining):
            candidate = valid[candidate_key]
            dominated = False
            for other_key in remaining:
                if other_key == candidate_key:
                    continue
                other = valid[other_key]
                at_least_as_good = True
                strictly_better = False
                for objective in objectives:
                    metric = objective["metric"]
                    left = float(other[metric])
                    right = float(candidate[metric])
                    if objective["direction"] == "maximize":
                        at_least_as_good &= left >= right
                        strictly_better |= left > right
                    else:
                        at_least_as_good &= left <= right
                        strictly_better |= left < right
                if at_least_as_good and strictly_better:
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate_key)
        for key in frontier:
            ranks[key] = rank
            remaining.remove(key)
        rank += 1
    return ranks


def build_adaptive_report(db: Session, experiment: ResearchExperiment) -> dict[str, Any]:
    report = build_experiment_report(db, experiment)
    candidates = list(
        db.execute(
            select(ExperimentCandidate)
            .where(ExperimentCandidate.experiment_id == experiment.id)
            .order_by(ExperimentCandidate.pareto_rank.nulls_last(), ExperimentCandidate.params_hash)
        ).scalars()
    )
    rounds = list(
        db.execute(
            select(ExperimentRound)
            .where(ExperimentRound.experiment_id == experiment.id)
            .order_by(ExperimentRound.ordinal)
        ).scalars()
    )
    report["adaptiveResearch"] = {
        "rounds": [
            {
                "id": str(item.id),
                "ordinal": item.ordinal,
                "status": item.status,
                "resultSummary": item.result_summary or {},
            }
            for item in rounds
        ],
        "candidates": [
            {
                "id": str(item.id),
                "roundId": str(item.round_id),
                "paramsHash": item.params_hash,
                "overrides": item.parameter_overrides or {},
                "metrics": item.aggregate_metrics or {},
                "paretoRank": item.pareto_rank,
                "promotedStrategyId": str(item.promoted_strategy_id) if item.promoted_strategy_id else None,
            }
            for item in candidates
        ],
    }
    return report


def finalize_adaptive_round_if_ready(db: Session, experiment: ResearchExperiment) -> bool:
    if (experiment.spec or {}).get("researchMode") != "adaptive_category":
        return False
    active = int(
        db.execute(
            select(func.count()).select_from(ExperimentTrial).where(
                ExperimentTrial.experiment_id == experiment.id,
                ExperimentTrial.status.in_({"queued", "running"}),
            )
        ).scalar_one()
    )
    if active:
        return True

    candidates = list(
        db.execute(
            select(ExperimentCandidate)
            .where(ExperimentCandidate.experiment_id == experiment.id)
            .order_by(ExperimentCandidate.params_hash)
        ).scalars()
    )
    for candidate in candidates:
        candidate.aggregate_metrics = _candidate_aggregate(candidate)
    objectives = list((experiment.spec.get("searchPolicy") or {}).get("objectives") or [])
    ranks = nondominated_sort(
        [(item.params_hash, item.aggregate_metrics or {}) for item in candidates],
        objectives,
    )
    for candidate in candidates:
        candidate.pareto_rank = ranks[candidate.params_hash]

    current_round = db.execute(
        select(ExperimentRound)
        .where(ExperimentRound.experiment_id == experiment.id)
        .order_by(ExperimentRound.ordinal.desc())
        .limit(1)
    ).scalar_one()
    current_round.status = "completed" if any(item.pareto_rank is not None for item in current_round.candidates) else "failed"
    current_round.finished_at = datetime.now(UTC)
    current_round.result_summary = {
        "candidateCount": len(current_round.candidates),
        "validObjectiveCandidateCount": sum(item.pareto_rank is not None for item in current_round.candidates),
        "paretoFrontier": [
            item.params_hash for item in candidates if item.pareto_rank == 1
        ],
    }
    _refresh_progress(db, experiment)

    manifest = dict(experiment.run_manifest or {})
    termination = manifest.get("termination")
    if isinstance(termination, dict) or experiment.status in {"cancel_requested", "data_changed"}:
        return False
    spec = AdaptiveExperimentSpec.model_validate(experiment.spec)
    trial_count = int((experiment.progress or {}).get("total") or 0)
    stop_reason: str | None = None
    if not any(item.pareto_rank is not None for item in candidates):
        stop_reason = "no_valid_candidates"
    elif current_round.ordinal >= spec.search_policy.max_rounds:
        stop_reason = "max_rounds_reached"
    elif trial_count >= spec.search_policy.max_trials:
        stop_reason = "max_trials_reached"
    if stop_reason:
        manifest["termination"] = {
            "reason": stop_reason,
            "earlyStopped": stop_reason not in {"max_rounds_reached", "max_trials_reached"},
            "triggeredConditions": [],
            "stoppedAt": datetime.now(UTC).isoformat(),
        }
        experiment.run_manifest = manifest
        completed = int((experiment.progress or {}).get("completed") or 0)
        failed = int((experiment.progress or {}).get("failed") or 0)
        experiment.status = "partially_failed" if completed and failed else ("completed" if completed else "failed")
        experiment.finished_at = datetime.now(UTC)
        experiment.report = build_adaptive_report(db, experiment)
        return True

    experiment.status = "waiting_agent"
    experiment.finished_at = None
    experiment.report = build_adaptive_report(db, experiment)
    return True


def record_controller_failure(
    db: Session,
    experiment_id: UUID,
    payload: ControllerFailureRequest,
    *,
    idempotency_key: str,
) -> ResearchExperiment:
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(str(experiment_id))
    if experiment.workflow_run_id != payload.workflow_run_id:
        raise ExperimentConflictError("workflow does not own this experiment")
    manifest = dict(experiment.run_manifest or {})
    existing = manifest.get("controllerFailure")
    if isinstance(existing, dict):
        if existing.get("idempotencyKey") == idempotency_key:
            return experiment
        raise ExperimentConflictError("controller failure already recorded")
    manifest["controllerFailure"] = {
        "idempotencyKey": idempotency_key,
        "code": payload.code,
        "message": payload.message,
        "recordedAt": datetime.now(UTC).isoformat(),
    }
    manifest["termination"] = {
        "reason": "controller_failed",
        "earlyStopped": True,
        "triggeredConditions": [{"code": payload.code}],
        "stoppedAt": datetime.now(UTC).isoformat(),
    }
    experiment.run_manifest = manifest
    for trial in experiment.trials:
        if trial.status == "queued":
            trial.status = "cancelled"
            trial.error_code = "controller_failed"
            trial.error_message = payload.message
            trial.finished_at = datetime.now(UTC)
    _refresh_progress(db, experiment)
    completed = int((experiment.progress or {}).get("completed") or 0)
    experiment.status = "partially_failed" if completed else "failed"
    experiment.error_code = "controller_failed"
    experiment.error_message = payload.message
    experiment.finished_at = datetime.now(UTC)
    experiment.report = build_adaptive_report(db, experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def promote_candidates(
    db: Session,
    experiment_id: UUID,
    payload: CandidatePromotionRequest,
    *,
    idempotency_key: str,
) -> list[Strategy]:
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(str(experiment_id))
    if experiment.workflow_run_id != payload.workflow_run_id:
        raise ExperimentConflictError("workflow does not own this experiment")
    if experiment.status not in _TERMINAL:
        raise ExperimentConflictError("experiment is not terminal")
    if not payload.candidate_ids:
        return []
    candidates = list(
        db.execute(
            select(ExperimentCandidate).where(
                ExperimentCandidate.experiment_id == experiment.id,
                ExperimentCandidate.id.in_(payload.candidate_ids),
            )
        ).scalars()
    )
    if len(candidates) != len(set(payload.candidate_ids)):
        raise ValueError("one or more candidates do not belong to the experiment")
    if any(item.pareto_rank not in {1, 2} for item in candidates):
        raise ValueError("only Pareto rank 1-2 candidates can be promoted")
    candidates.sort(key=lambda item: (item.pareto_rank or 99, item.params_hash))
    verification_pending = False
    for candidate in candidates:
        aggregate = dict(candidate.aggregate_metrics or {})
        verification = aggregate.get("verification")
        if isinstance(verification, dict) and verification.get("status") == "completed":
            continue
        if isinstance(verification, dict) and verification.get("status") == "failed":
            raise ExperimentConflictError(
                f"candidate {candidate.id} verification failed; promotion is blocked"
            )
        if isinstance(verification, dict) and verification.get("status") in {"queued", "running"}:
            verification_pending = True
            continue
        base_oos = next(
            (
                item
                for item in candidate.trials
                if item.status == "completed"
                and item.sample_kind == "out_of_sample"
                and item.cost_scenario.lower() == "base"
            ),
            None,
        )
        if base_oos is None or not base_oos.data_fingerprint:
            raise ExperimentConflictError(
                f"candidate {candidate.id} has no completed OOS/base summary evidence"
            )
        strategy = db.get(Strategy, UUID(str((experiment.spec or {})["strategyId"])))
        if strategy is None:
            raise ExperimentConflictError("research strategy no longer exists")
        universe = (experiment.run_manifest or {}).get("universe") or {}
        symbols = list(universe.get("symbols") or [])
        costs = dict(base_oos.cost_config or {})
        run = StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode="backtest",
            status="queued",
            window_start=base_oos.window_start,
            window_end=base_oos.window_end,
            initial_cash=float((experiment.spec or {})["initialCash"]),
            benchmark_symbol=(experiment.spec or {}).get("benchmarkSymbol"),
            config_snapshot={
                **dict(base_oos.params or {}),
                "run_options": {"persist_level": "full", "source": "verification"},
            },
        )
        db.add(run)
        db.flush()
        enqueue_backtest_job(
            db,
            run=run,
            source="verification",
            priority=20,
            payload={
                "strategy_id": str(strategy.id),
                "candidate_id": str(candidate.id),
                "start_date": base_oos.window_start.isoformat(),
                "end_date": base_oos.window_end.isoformat(),
                "initial_cash": float((experiment.spec or {})["initialCash"]),
                "benchmark_symbol": (experiment.spec or {}).get("benchmarkSymbol"),
                "commission_bps": float(costs.get("commissionBps", 0)),
                "commission_min": float(costs.get("commissionMin", 0)),
                "slippage_bps": float(costs.get("slippageBps", 0)),
                "universe_symbols": None if universe.get("universePolicy") else symbols,
                "universe_metadata": universe,
                "universe_policy": universe.get("universePolicy"),
                "runtime_params_override": base_oos.params,
                "persist_level": "full",
                "expected_data_fingerprint": base_oos.data_fingerprint,
                "expected_metrics": dict(base_oos.metrics or {}),
                "metric_tolerance": VERIFICATION_METRIC_TOLERANCE,
            },
        )
        aggregate["verification"] = {
            "status": "queued",
            "runId": str(run.id),
            "sourceTrialId": str(base_oos.id),
            "dataFingerprint": base_oos.data_fingerprint,
        }
        candidate.aggregate_metrics = aggregate
        verification_pending = True
    if verification_pending:
        db.commit()
        raise ExperimentConflictError(
            "full verification runs were queued; promotion remains blocked until they complete"
        )
    promoted: list[Strategy] = []
    for candidate in candidates:
        metadata = candidate.params.setdefault("metadata", {})
        metadata["research_lineage"] = {
            "workflowRunId": experiment.workflow_run_id,
            "experimentId": str(experiment.id),
            "candidateId": str(candidate.id),
            "backtestRunIds": [
                str(item.backtest_run_id) for item in candidate.trials if item.backtest_run_id
            ],
            "paramsHash": candidate.params_hash,
            "verificationRunId": (candidate.aggregate_metrics or {}).get("verification", {}).get("runId"),
        }
        name = f"{(experiment.spec or {}).get('name') or 'Research'} · Pareto R{candidate.pareto_rank} · {candidate.params_hash[:8]}"
        strategy = create_strategy_version(
            db,
            name=name,
            strategy_type=str((experiment.spec or {}).get("strategyType")),
            params=candidate.params,
            description=f"Promoted from adaptive experiment {experiment.id}",
            status="draft",
            idempotency_key=f"{idempotency_key}:{candidate.params_hash}",
        )
        candidate.promoted_strategy_id = strategy.id
        promoted.append(strategy)
    db.commit()
    return promoted


def archive_unused_research_draft(
    db: Session,
    strategy_id: UUID,
    *,
    workflow_run_id: str,
) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ExperimentNotFoundError(str(strategy_id))
    origin = ((strategy.params or {}).get("metadata") or {}).get("research_origin") or {}
    if origin.get("workflowRunId") != workflow_run_id or origin.get("purpose") != "adaptive_category_research":
        raise ExperimentConflictError("strategy is not an auto-created draft owned by this workflow")
    if strategy.status == "archived":
        return strategy
    experiment_refs = sum(
        str((item.spec or {}).get("strategyId") or "") == str(strategy.id)
        for item in db.execute(select(ResearchExperiment)).scalars()
    )
    run_refs = int(
        db.execute(select(func.count()).select_from(StrategyRun).where(StrategyRun.strategy_id == strategy.id)).scalar_one()
    )
    allocation_refs = int(
        db.execute(
            select(func.count()).select_from(StrategyAllocation).where(
                StrategyAllocation.strategy_id == strategy.id
            )
        ).scalar_one()
    )
    if experiment_refs or run_refs or allocation_refs:
        raise ExperimentConflictError("research draft already has lineage or allocation references")
    strategy.status = "archived"
    db.commit()
    db.refresh(strategy)
    return strategy


def list_rounds(db: Session, experiment_id: UUID) -> list[ExperimentRound]:
    if db.get(ResearchExperiment, experiment_id) is None:
        raise ExperimentNotFoundError(str(experiment_id))
    return list(
        db.execute(
            select(ExperimentRound)
            .where(ExperimentRound.experiment_id == experiment_id)
            .order_by(ExperimentRound.ordinal)
        ).scalars()
    )


def list_candidates(db: Session, experiment_id: UUID) -> list[ExperimentCandidate]:
    if db.get(ResearchExperiment, experiment_id) is None:
        raise ExperimentNotFoundError(str(experiment_id))
    return list(
        db.execute(
            select(ExperimentCandidate)
            .where(ExperimentCandidate.experiment_id == experiment_id)
            .order_by(ExperimentCandidate.pareto_rank.nulls_last(), ExperimentCandidate.params_hash)
        ).scalars()
    )
