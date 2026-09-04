"""Read-only market inputs for the shared native support/resistance rules."""
from __future__ import annotations

from collections import deque
from datetime import date, timedelta
import math
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.models.tables import Instrument


def load_support_risk_context(
    db: Session, risk: dict[str, Any], start: date, end: date,
) -> dict[str, Any]:
    context: dict[str, Any] = {"market": {}}
    if risk.get("market_filter_enabled", False):
        ids = db.execute(select(Instrument.id).where(
            Instrument.ticker_canonical == risk["market_filter_symbol"],
        )).scalars().all()
        if len(ids) != 1:
            raise ValueError("market_filter_symbol must resolve to one stored instrument")
        rows = db.execute(text("""
            SELECT dt_ny, COALESCE(close_fa, close_u) AS close
            FROM eod_bars WHERE instrument_id = :instrument
              AND dt_ny BETWEEN :start AND :end ORDER BY dt_ny
        """), {"instrument": ids[0], "start": start - timedelta(days=600), "end": end})
        closes: deque[float] = deque(maxlen=200)
        for day, raw_close in rows:
            close = float(raw_close)
            if not math.isfinite(close) or close <= 0:
                raise ValueError("market filter requires finite positive adjusted closes")
            closes.append(close)
            if day >= start and len(closes) == 200:
                context["market"][str(day.toordinal())] = [close, math.fsum(closes) / 200]
    return context
