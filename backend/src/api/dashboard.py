from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.schemas.dashboard import DashboardOverview
from src.services.dashboard_service import build_dashboard_overview
from src.services.research_experiment_service import research_worker_snapshot

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/overview", response_model=DashboardOverview)
def get_dashboard_overview(request: Request, db: Session = Depends(get_db)):
    scheduler = getattr(request.app.state, "paper_trading_scheduler", None)
    return build_dashboard_overview(
        db,
        research=research_worker_snapshot(getattr(request.app.state, "research_experiment_worker", None)),
        scheduler=scheduler.status_snapshot() if scheduler is not None else {
            "status": "unknown", "enabled": None, "submit_orders": None,
        },
    )
