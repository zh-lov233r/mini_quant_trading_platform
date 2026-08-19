from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.core.agent_auth import require_agent_service
from src.core.db import get_db
from src.models.tables import ExperimentTrial, ResearchExperiment
from src.schemas.research import (
    ExperimentCreate,
    ExperimentOut,
    ExperimentSpec,
    ExperimentValidationOut,
    TrialOut,
)
from src.services.research_experiment_service import (
    ExperimentConflictError,
    ExperimentDataIncompleteError,
    ExperimentNotFoundError,
    cancel_experiment,
    create_experiment,
    get_experiment,
    list_experiments,
    list_trials,
    validate_experiment,
)


router = APIRouter(prefix="/api/research", tags=["research"])
agent_router = APIRouter(
    prefix="/api/agent/research",
    tags=["agent-integration"],
    dependencies=[Depends(require_agent_service)],
)


def _experiment_out(experiment: ResearchExperiment) -> ExperimentOut:
    return ExperimentOut(
        id=experiment.id,
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
        dataFingerprint=trial.data_fingerprint,
        backtestRunId=trial.backtest_run_id,
        metrics=trial.metrics or {},
        attempt=trial.attempt,
        errorCode=trial.error_code,
        errorMessage=trial.error_message,
    )


@agent_router.post("/experiments/validate", response_model=ExperimentValidationOut)
def validate_research_experiment(payload: ExperimentSpec, db: Session = Depends(get_db)):
    try:
        return ExperimentValidationOut(**validate_experiment(db, payload))
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


@agent_router.post("/experiments", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED)
def create_research_experiment(
    payload: ExperimentCreate,
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    if os.getenv("RESEARCH_WORKER_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_unavailable", "message": "Research worker is disabled."},
        )
    try:
        experiment = create_experiment(
            db,
            workflow_run_id=payload.workflow_run_id,
            spec=payload.spec,
            idempotency_key=idempotency_key,
        )
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


@agent_router.post("/experiments/{experiment_id}/cancel", response_model=ExperimentOut)
def cancel_research_experiment(experiment_id: UUID, db: Session = Depends(get_db)):
    try:
        return _experiment_out(cancel_experiment(db, experiment_id))
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc


@router.get("/experiments", response_model=list[ExperimentOut])
def get_research_experiments(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
):
    return [_experiment_out(item) for item in list_experiments(db, limit=limit)]


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
