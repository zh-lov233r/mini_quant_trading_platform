# apps/api/src/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import logging
import uvicorn

from routes import marketdata, backtests, trading

# 初始化日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quant-api")

# 创建 FastAPI 实例
app = FastAPI(
    title="Quant Trading API Gateway",
    description="API Gateway for Market Data, Backtest, and Trading Services",
    version="1.0.0",
    docs_url="/docs",       # OpenAPI 文档路径
    redoc_url="/redoc"      # ReDoc 文档路径
)

# 允许跨域（方便前端调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 部署生产环境可替换成指定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
app.include_router(marketdata.router, prefix="/api/market", tags=["Market Data"])
app.include_router(backtests.router, prefix="/api/backtests", tags=["Backtests"])
app.include_router(trading.router, prefix="/api/trades", tags=["Trading"])

# 健康检查
@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

# 请求日志中间件（可选）
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url}")
    response = await call_next(request)
    logger.info(f"Completed: status_code={response.status_code}")
    return response

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True  # 开发环境用，生产部署可去掉
    )









