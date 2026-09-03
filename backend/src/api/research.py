from __future__ import annotations

from datetime import datetime, timezone
import os
from uuid import UUID

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.agent_auth import require_agent_service
from src.core.db import get_db
from src.models.tables import ExperimentCandidate, ExperimentRound, ExperimentTrial, ResearchExperiment
from src.schemas.research import (
    CandidatePromotionOut,
    CandidatePromotionRequest,
    CategoryStudyCreate,
    CategoryStudyValidationOut,
    CategoryStudyValidationRequest,
    ControllerFailureRequest,
    ExperimentCandidateOut,
    ExperimentOut,
    ExperimentRoundOut,
    ExperimentRoundSubmit,
    ExperimentTokenUsageUpdate,
    TERMINAL_EXPERIMENT_STATUSES,
    TrialOut,
    SupportResistanceEffectivenessSpec,
)
from src.services.adaptive_research_service import (
    create_category_study,
    list_candidates,
    list_rounds,
    promote_candidates,
    record_controller_failure,
    submit_experiment_round,
    validate_category_study,
)
from src.services.research_experiment_service import (
    ExperimentConflictError,
    ExperimentDataIncompleteError,
    ExperimentNotFoundError,
    cancel_experiment,
    cancel_trial,
    get_experiment,
    list_experiments,
    list_trials,
    update_experiment_token_usage,
)
from src.services.backtest_job_service import delete_terminal_backtest_run
from src.services.market_data_maintenance_service import MarketDataMaintenanceError
from src.api.backtests import BacktestDeleteOut


router = APIRouter(prefix="/api/research", tags=["research"])
agent_router = APIRouter(
    prefix="/api/agent/research",
    tags=["agent-integration"],
    dependencies=[Depends(require_agent_service)],
)


class ResearchWorkerStatusOut(BaseModel):
    enabled: bool
    state: Literal["disabled", "idle", "running", "stopping", "failed"]
    configured_concurrency: int = Field(ge=1)
    active_trials: int = Field(ge=0)
    available_slots: int = Field(ge=0)
    queued_trials: int = Field(ge=0)
    checked_at: datetime


def _experiment_out(experiment: ResearchExperiment) -> ExperimentOut:
    return ExperimentOut(
        id=experiment.id,
        parentExperimentId=experiment.parent_experiment_id,
        studyKind=experiment.study_kind,
        workflowRunId=experiment.workflow_run_id,
        status=experiment.status,
        spec=experiment.spec or {},
        runManifest=experiment.run_manifest or {},
        progress=experiment.progress or {},
        report=experiment.report or {},
        errorCode=experiment.error_code,
        errorMessage=experiment.error_message,
        startedAt=experiment.started_at,
        finishedAt=experiment.finished_at,
        createdAt=experiment.created_at,
        updatedAt=experiment.updated_at,
    )


def _trial_out(trial: ExperimentTrial) -> TrialOut:
    deleted_at = (trial.metrics or {}).get("backtest_deleted_at")
    return TrialOut(
        id=trial.id,
        trialKey=trial.trial_key,
        ordinal=trial.ordinal,
        status=trial.status,
        sampleKind=trial.sample_kind,
        costScenario=trial.cost_scenario,
        params=trial.params or {},
        paramsHash=trial.params_hash,
        windowStart=trial.window_start,
        windowEnd=trial.window_end,
        costConfig=trial.cost_config or {},
        backtestRunId=trial.backtest_run_id,
        backtestDeletedAt=deleted_at,
        candidateId=trial.candidate_id,
        metrics=trial.metrics or {},
        attempt=trial.attempt,
        errorCode=trial.error_code,
        errorMessage=trial.error_message,
        cancelRequestedAt=trial.cancel_requested_at,
    )


def _round_out(item: ExperimentRound) -> ExperimentRoundOut:
    return ExperimentRoundOut(
        id=item.id,
        experimentId=item.experiment_id,
        ordinal=item.ordinal,
        status=item.status,
        proposal=item.proposal or {},
        validationIssues=item.validation_issues or [],
        resultSummary=item.result_summary or {},
        startedAt=item.started_at,
        finishedAt=item.finished_at,
    )


def _candidate_out(item: ExperimentCandidate) -> ExperimentCandidateOut:
    return ExperimentCandidateOut(
        id=item.id,
        experimentId=item.experiment_id,
        roundId=item.round_id,
        ordinal=item.ordinal,
        overrides=item.parameter_overrides or {},
        params=item.params or {},
        paramsHash=item.params_hash,
        rationale=item.rationale,
        aggregateMetrics=item.aggregate_metrics or {},
        paretoRank=item.pareto_rank,
        promotedStrategyId=item.promoted_strategy_id,
    )


@agent_router.post("/category-studies/validate", response_model=CategoryStudyValidationOut)
def validate_research_category_study(
    payload: CategoryStudyValidationRequest,
    db: Session = Depends(get_db),
):
    try:
        return CategoryStudyValidationOut(**validate_category_study(db, payload))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_spec", "message": str(exc)},
        ) from exc
    except ExperimentDataIncompleteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "data_incomplete", "message": str(exc), "issues": []},
        ) from exc


@agent_router.post("/category-studies", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED)
def create_research_category_study(
    payload: CategoryStudyCreate,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    if os.getenv("RESEARCH_WORKER_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_unavailable", "message": "Research worker is disabled."},
        )
    try:
        if isinstance(payload.spec, SupportResistanceEffectivenessSpec):
            from src.services.support_resistance_validation_service import (
                create_effectiveness_study,
            )

            experiment = create_effectiveness_study(
                db,
                workflow_run_id=payload.workflow_run_id,
                spec=payload.spec,
                idempotency_key=idempotency_key,
            )
        else:
            experiment = create_category_study(
                db,
                workflow_run_id=payload.workflow_run_id,
                spec=payload.spec,
                idempotency_key=idempotency_key,
            )
    except MarketDataMaintenanceError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_spec", "message": str(exc)}) from exc
    except ExperimentDataIncompleteError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "data_incomplete", "message": str(exc), "issues": []},
        ) from exc
    except ExperimentConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict", "message": str(exc)}) from exc
    return _experiment_out(experiment)


@agent_router.post("/experiments/{experiment_id}/rounds", response_model=ExperimentOut)
def submit_research_round(
    experiment_id: UUID,
    payload: ExperimentRoundSubmit,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    try:
        return _experiment_out(
            submit_experiment_round(
                db,
                experiment_id,
                payload,
                idempotency_key=idempotency_key,
            )
        )
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_candidates", "message": str(exc)}) from exc
    except ExperimentConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "round_conflict", "message": str(exc)}) from exc


@agent_router.post("/experiments/{experiment_id}/controller-failure", response_model=ExperimentOut)
def fail_research_controller(
    experiment_id: UUID,
    payload: ControllerFailureRequest,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    try:
        return _experiment_out(
            record_controller_failure(
                db,
                experiment_id,
                payload,
                idempotency_key=idempotency_key,
            )
        )
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    except ExperimentConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "controller_conflict", "message": str(exc)}) from exc


@agent_router.post("/experiments/{experiment_id}/candidates/promote", response_model=CandidatePromotionOut)
def promote_research_candidates(
    experiment_id: UUID,
    payload: CandidatePromotionRequest,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    try:
        strategies = promote_candidates(
            db,
            experiment_id,
            payload,
            idempotency_key=idempotency_key,
        )
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    except MarketDataMaintenanceError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_candidates", "message": str(exc)}) from exc
    except ExperimentConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "promotion_conflict", "message": str(exc)}) from exc
    return CandidatePromotionOut(
        experimentId=experiment_id,
        strategyIds=[item.id for item in strategies],
    )


@agent_router.post("/experiments/{experiment_id}/cancel", response_model=ExperimentOut)
def cancel_research_experiment(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        return _experiment_out(cancel_experiment(db, experiment_id))
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc


@agent_router.post("/experiments/{experiment_id}/usage", response_model=ExperimentOut)
def update_research_experiment_usage(
    experiment_id: UUID,
    payload: ExperimentTokenUsageUpdate,
    db: Session = Depends(get_db),
):
    try:
        return _experiment_out(update_experiment_token_usage(db, experiment_id, payload))
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    except ExperimentConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "workflow_conflict", "message": str(exc)},
        ) from exc


@agent_router.post("/experiments/{experiment_id}/report/retry", response_model=ExperimentOut)
def retry_effectiveness_report(experiment_id: UUID, db: Session = Depends(get_db)):
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    if experiment.study_kind != "support_resistance_effectiveness_v3":
        raise HTTPException(status_code=422, detail="experiment is not an effectiveness study")
    from src.services.support_resistance_validation_report_service import (
        finalize_validation_report,
    )

    finalize_validation_report(db, experiment)
    db.refresh(experiment)
    return _experiment_out(experiment)


@router.get("/experiments", response_model=list[ExperimentOut])
def get_research_experiments(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    return [_experiment_out(item) for item in list_experiments(db, limit=limit)]


@router.get("/worker-status", response_model=ResearchWorkerStatusOut)
def get_research_worker_status(request: Request, db: Session = Depends(get_db)):
    enabled = os.getenv("RESEARCH_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    worker = getattr(request.app.state, "research_experiment_worker", None)
    snapshot = worker.status_snapshot(enabled=enabled) if worker is not None else {
        "enabled": enabled,
        "state": "failed" if enabled else "disabled",
        "configured_concurrency": max(1, int(os.getenv("RESEARCH_WORKER_CONCURRENCY", "2"))),
        "active_trials": 0,
        "available_slots": 0,
    }
    snapshot["queued_trials"] = int(
        db.execute(
            select(func.count()).select_from(ExperimentTrial).where(ExperimentTrial.status == "queued")
        ).scalar_one()
    )
    snapshot["checked_at"] = datetime.now(timezone.utc)
    return ResearchWorkerStatusOut(**snapshot)


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_research_experiment(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        return _experiment_out(get_experiment(db, experiment_id))
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc


@router.get("/experiments/{experiment_id}/trials", response_model=list[TrialOut])
def get_research_trials(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        return [_trial_out(item) for item in list_trials(db, experiment_id)]
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc


@router.post(
    "/experiments/{experiment_id}/trials/{trial_id}/cancel",
    response_model=TrialOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_research_trial(experiment_id: UUID, trial_id: UUID, db: Session = Depends(get_db)):
    try:
        return _trial_out(cancel_trial(db, experiment_id, trial_id))
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="research trial not found") from exc


@router.get("/experiments/{experiment_id}/children", response_model=list[ExperimentOut])
def get_research_children(experiment_id: UUID, db: Session = Depends(get_db)):
    from src.services.support_resistance_validation_service import list_child_experiments

    parent = db.get(ResearchExperiment, experiment_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return [_experiment_out(item) for item in list_child_experiments(db, experiment_id)]


@router.get("/experiments/{experiment_id}/rounds", response_model=list[ExperimentRoundOut])
def get_research_rounds(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        return [_round_out(item) for item in list_rounds(db, experiment_id)]
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc


@router.get("/experiments/{experiment_id}/candidates", response_model=list[ExperimentCandidateOut])
def get_research_candidates(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        return [_candidate_out(item) for item in list_candidates(db, experiment_id)]
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc


@router.delete(
    "/experiments/{experiment_id}/backtests/{run_id}",
    response_model=BacktestDeleteOut,
)
def delete_research_backtest(
    experiment_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
):
    experiment = db.execute(
        select(ResearchExperiment)
        .where(ResearchExperiment.id == experiment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    if experiment.status not in TERMINAL_EXPERIMENT_STATUSES:
        raise HTTPException(status_code=409, detail="active experiments cannot delete backtests")

    trial = db.execute(
        select(ExperimentTrial)
        .where(
            ExperimentTrial.experiment_id == experiment_id,
            ExperimentTrial.backtest_run_id == run_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    candidate = None
    expected_source: Literal["research", "verification"]
    if trial is not None:
        expected_source = "research"
    else:
        candidates = db.execute(
            select(ExperimentCandidate)
            .where(ExperimentCandidate.experiment_id == experiment_id)
            .with_for_update()
        ).scalars()
        candidate = next(
            (
                item
                for item in candidates
                if str(((item.aggregate_metrics or {}).get("verification") or {}).get("runId"))
                == str(run_id)
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="backtest does not belong to experiment")
        expected_source = "verification"

    try:
        delete_terminal_backtest_run(db, run_id, expected_source=expected_source)
        deleted_at = datetime.now(timezone.utc).isoformat()
        if trial is not None:
            trial.backtest_run_id = None
            trial.metrics = {**dict(trial.metrics or {}), "backtest_deleted_at": deleted_at}
        else:
            aggregate = dict(candidate.aggregate_metrics or {})
            verification = dict(aggregate.get("verification") or {})
            verification.update({"runId": None, "deletedAt": deleted_at})
            aggregate["verification"] = verification
            candidate.aggregate_metrics = aggregate
        db.flush()
        if (experiment.spec or {}).get("researchMode") == "adaptive_category":
            from src.services.adaptive_research_service import build_adaptive_report

            experiment.report = build_adaptive_report(db, experiment)
        else:
            from src.services.research_experiment_service import build_experiment_report

            experiment.report = build_experiment_report(db, experiment)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BacktestDeleteOut(run_id=run_id)


@router.get("/experiments/{experiment_id}/report", response_model=dict)
def get_research_report(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        experiment = get_experiment(db, experiment_id)
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    if not experiment.report:
        raise HTTPException(
            status_code=409,
            detail={"code": "report_not_ready", "message": "Experiment report is not ready."},
        )
    return experiment.report


@router.get("/experiments/{experiment_id}/report-artifacts/{artifact_kind}")
def download_research_report_artifact(
    experiment_id: UUID,
    artifact_kind: Literal["json", "markdownZh", "markdownEn", "pdfZh", "pdfEn"],
    db: Session = Depends(get_db),
):
    experiment = db.get(ResearchExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    files = (((experiment.run_manifest or {}).get("reportArtifacts") or {}).get("files") or {})
    item = files.get(artifact_kind) or {}
    raw_path = item.get("path")
    if not raw_path:
        raise HTTPException(status_code=409, detail={"code": "artifact_not_ready"})
    path = Path(str(raw_path)).resolve()
    root = Path(__file__).resolve().parents[3] / "output"
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="invalid report artifact path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report artifact not found")
    media_type = "application/pdf" if artifact_kind.startswith("pdf") else (
        "application/json" if artifact_kind == "json" else "text/markdown; charset=utf-8"
    )
    return FileResponse(path, media_type=media_type, filename=path.name)
