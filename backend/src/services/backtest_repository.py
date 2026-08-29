from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from src.models.tables import PortfolioSnapshot, Signal, Transaction


class BacktestRepository:
    """Run-scoped Core insert buffers; flushing never commits the transaction."""

    def __init__(self, db: Session, *, batch_size: int = 5_000) -> None:
        self.db = db
        self.batch_size = max(1, int(batch_size))
        self._buffers: dict[type[Any], list[dict[str, Any]]] = defaultdict(list)
        self.rows_inserted = 0

    def add_signal(self, values: dict[str, Any]) -> None:
        self._append(Signal, values)

    def add_transaction(self, values: dict[str, Any]) -> None:
        self._append(Transaction, values)

    def add_snapshot(self, values: dict[str, Any]) -> None:
        self._append(PortfolioSnapshot, values)

    def _append(self, model: type[Any], values: dict[str, Any]) -> None:
        buffer = self._buffers[model]
        buffer.append(values)
        if len(buffer) >= self.batch_size:
            self._flush_model(model)

    def _flush_model(self, model: type[Any]) -> None:
        rows = self._buffers[model]
        if not rows:
            return
        self.db.execute(insert(model), rows)
        self.rows_inserted += len(rows)
        rows.clear()

    def flush(self) -> None:
        for model in (Signal, Transaction, PortfolioSnapshot):
            self._flush_model(model)
