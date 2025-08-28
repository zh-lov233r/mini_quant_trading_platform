# 直接请求 Polygon 的 SMA / EMA 指标端点
import os, requests
from typing import Literal

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
BASE = "https://api.polygon.io"

def _get(url: str, params: dict):
    params = {**params, "apiKey": POLYGON_API_KEY}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def fetch_sma(symbol: str, window: int, *,
              timespan: Literal["day","minute","hour","week","month"]="day",
              series_type: Literal["close","open","high","low","volume","vwap"]="close",
              limit: int = 2, adjusted: bool=True, expand_underlying: bool=False):
    url = f"{BASE}/v1/indicators/sma/{symbol.upper()}"
    return _get(url, {
        "timespan": timespan,
        "window": window,
        "series_type": series_type,
        "adjusted": str(adjusted).lower(),
        "limit": limit,
        "expand_underlying": str(expand_underlying).lower(),
        "order": "desc"
    })

def fetch_ema(symbol: str, window: int, *,
              timespan: Literal["day","minute","hour","week","month"]="day",
              series_type: Literal["close","open","high","low","volume","vwap"]="close",
              limit: int = 2, adjusted: bool=True, expand_underlying: bool=False):
    url = f"{BASE}/v1/indicators/ema/{symbol.upper()}"
    return _get(url, {
        "timespan": timespan,
        "window": window,
        "series_type": series_type,
        "adjusted": str(adjusted).lower(),
        "limit": limit,
        "expand_underlying": str(expand_underlying).lower(),
        "order": "desc"
    })
