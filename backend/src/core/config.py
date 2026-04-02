from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local-dev dependency
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


def _first_env_value(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


@dataclass(frozen=True)
class Settings:
    alpaca_api_key: str | None
    alpaca_secret_key: str | None
    alpaca_base_url: str
    alpaca_timeout_seconds: float
    db_url: str
    default_strategy: str
    risk_tolerance: float


def get_settings() -> Settings:
    return Settings(
        alpaca_api_key=_first_env_value("ALPACA_API_KEY", "ALPACA_KEY"),
        alpaca_secret_key=_first_env_value("ALPACA_SECRET_KEY", "ALPACA_SECRET"),
        alpaca_base_url=(
            _first_env_value(
                "ALPACA_BASE_URL",
                default="https://paper-api.alpaca.markets",
            )
            or "https://paper-api.alpaca.markets"
        ),
        alpaca_timeout_seconds=float(
            _first_env_value("ALPACA_TIMEOUT_SECONDS", default="20") or "20"
        ),
        db_url=(
            _first_env_value("DB_URL", "DATABASE_URL", default="sqlite:///./trading.db")
            or "sqlite:///./trading.db"
        ),
        default_strategy=(
            _first_env_value("DEFAULT_STRATEGY", default="dual_moving_avg")
            or "dual_moving_avg"
        ),
        risk_tolerance=float(_first_env_value("RISK_TOLERANCE", default="0.02") or "0.02"),
    )


settings = get_settings()
