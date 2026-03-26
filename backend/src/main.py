# backends/src/main.py
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 引入 API routers 和数据库初始化函数
from src.api.strategies import router as strategies_router
from src.core.db import ensure_extensions

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

# -----------------------------
# 启动/停止事件
# -----------------------------
@app.on_event("startup")
def on_startup():
    ensure_extensions()
    log.info("App started")

@app.on_event("shutdown")
def on_shutdown():
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




