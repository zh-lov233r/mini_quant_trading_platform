from __future__ import annotations

from datetime import date
from time import perf_counter
from typing import Any, Callable, Iterator

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause


RowFactory = Callable[[dict[str, Any]], tuple[date, str, dict[str, Any]]]


class MarketDataLoader:
    """Stream ordered feature rows on a dedicated read connection, one day at a time."""

    def __init__(
        self,
        db: Session,
        *,
        statement: TextClause,
        params: dict[str, Any],
        row_factory: RowFactory,
        performance: dict[str, Any],
        fetch_size: int = 5_000,
    ) -> None:
        self._engine = db.get_bind()
        self._statement = statement
        self._params = params
        self._row_factory = row_factory
        self._performance = performance
        self._fetch_size = max(1, int(fetch_size))
        self._connection: Connection | None = None
        self.rows_loaded = 0
        self.loaded_symbols: set[str] = set()
        self._history_sessions_by_instrument: dict[int, int] = {}

    def iter_days(self) -> Iterator[tuple[date, dict[str, dict[str, Any]]]]:
        load_started = perf_counter()
        build_ms = 0.0
        # A pooled connection may retain a previous execution option until the
        # SQLAlchemy proxy is reset. Force client-side execution for the
        # transaction command, then enable server-side streaming only for the
        # market-data SELECT.
        connection = self._engine.connect().execution_options(stream_results=False)
        self._connection = connection
        try:
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql(
                    "SET TRANSACTION READ ONLY",
                    execution_options={"stream_results": False},
                )
            streaming_connection = connection.execution_options(stream_results=True)
            rows = streaming_connection.execute(self._statement, self._params).mappings().yield_per(
                self._fetch_size
            )
            current_date: date | None = None
            current_snapshots: dict[str, dict[str, Any]] = {}
            for raw_row in rows:
                build_started = perf_counter()
                trade_date, symbol, snapshot = self._row_factory(dict(raw_row))
                instrument_id = int(snapshot.get("instrument_id") or 0)
                history_sessions = self._history_sessions_by_instrument.get(instrument_id, 0) + 1
                self._history_sessions_by_instrument[instrument_id] = history_sessions
                snapshot["history_sessions"] = history_sessions
                build_ms += (perf_counter() - build_started) * 1000.0
                self.rows_loaded += 1
                self.loaded_symbols.add(symbol)
                if current_date is not None and trade_date != current_date:
                    yield current_date, current_snapshots
                    current_snapshots = {}
                current_date = trade_date
                existing = current_snapshots.get(symbol)
                if existing is not None and existing.get("instrument_id") != snapshot.get("instrument_id"):
                    raise ValueError(
                        f"symbol {symbol} resolves to multiple instruments on {trade_date}"
                    )
                current_snapshots[symbol] = snapshot
            if current_date is not None:
                yield current_date, current_snapshots
        finally:
            self._performance["load_market_data_ms"] = round(
                (perf_counter() - load_started) * 1000.0,
                3,
            )
            self._performance["build_dataset_ms"] = round(build_ms, 3)
            self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
