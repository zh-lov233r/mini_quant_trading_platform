# 放应用入口层的东西
# 创建 FastAPI 实例，配置 CORS，挂载路由，设置启动/停止事件等


# backends/src/main.py
from datetime import datetime, timezone
import os
import logging
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

# 引入 API routers 和数据库初始化函数
from src.api.backtests import router as backtests_router
from src.api.market_data import router as market_data_router
from src.api.paper_accounts import router as paper_accounts_router
from src.api.paper_trading import router as paper_trading_router
from src.api.research import agent_router as agent_research_router
from src.api.research import router as research_router
from src.api.stock_baskets import router as stock_baskets_router
from src.api.strategy_allocations import router as strategy_allocations_router
from src.api.strategies import agent_router as agent_strategies_router
from src.api.strategies import router as strategies_router
from src.core.db import SessionLocal, engine, ensure_extensions, ensure_strategy_allocation_schema
from src.services.native_runtime_service import validate_native_runtime
from src.services.paper_trading_scheduler import PaperTradingDailyScheduler
from src.services.research_experiment_service import ResearchExperimentWorker
from src.services.stock_basket_service import ensure_default_common_stock_basket
from src.services.backtest_worker_status_service import load_backtest_worker_status
from src.services.backtest_worker_config import resolve_backtest_worker_concurrency

# -----------------------------
# 基本配置（可用环境变量覆盖）
# -----------------------------
APP_NAME = os.getenv("APP_NAME", "Quant Backend")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

# -----------------------------
# 日志配置
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("main")

# -----------------------------
# 创建 FastAPI 应用
# -----------------------------
app = FastAPI(title=APP_NAME)

# CORS：允许前端（Next.js 默认 3000 端口）访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载你的业务路由
app.include_router(strategies_router)
app.include_router(agent_strategies_router)
app.include_router(backtests_router)
app.include_router(market_data_router)
app.include_router(stock_baskets_router)
app.include_router(paper_accounts_router)
app.include_router(strategy_allocations_router)
app.include_router(paper_trading_router)
app.include_router(research_router)
app.include_router(agent_research_router)

# -----------------------------
# 启动/停止事件
# -----------------------------
@app.on_event("startup")
async def on_startup():
    validate_native_runtime(engine)
    resolve_backtest_worker_concurrency()
    ensure_extensions()
    ensure_strategy_allocation_schema()
    db = SessionLocal()
    try:
        ensure_default_common_stock_basket(db)
    finally:
        db.close()
    scheduler = PaperTradingDailyScheduler()
    app.state.paper_trading_scheduler = scheduler
    await scheduler.start()
    research_worker = ResearchExperimentWorker()
    app.state.research_experiment_worker = research_worker
    if os.getenv("RESEARCH_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        await research_worker.start()
    log.info("App started")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler = getattr(app.state, "paper_trading_scheduler", None)
    if scheduler is not None:
        await scheduler.stop()
    research_worker = getattr(app.state, "research_experiment_worker", None)
    if research_worker is not None:
        await research_worker.stop()
    log.info("App stopped")

# -----------------------------
# 基础路由
# -----------------------------
@app.get("/")
def root():
    return {"app": APP_NAME, "message": "OK"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response):
    db = SessionLocal()
    try:
        worker_status = load_backtest_worker_status(db, checked_at=datetime.now(timezone.utc))
    except Exception:
        log.exception("Readiness check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "backtest_automation_available": False}
    finally:
        db.close()
    if not worker_status["automation_available"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "backtest_automation_available": False}
    return {"status": "ready", "backtest_automation_available": True}
