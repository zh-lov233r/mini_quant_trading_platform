# 数据模型; Data Model
# Pydantic 模型; 数据类型验证

from pydantic import BaseModel
from datetime import date
from typing import Dict, Any

class BacktestParameters(BaseModel):
    strategy_type: str
    symbol: str
    timeframe: str
    start_date: date
    end_date: date
    initial_capital: float = 10000.0
    parameters: Dict[str, Any]  # 策略特定参数
    commission: float = 0.0005
    slippage: float = 0.001

class BacktestResult(BaseModel):
    task_id: str
    status: str
    metrics: Dict[str, float]
    equity_curve: list




