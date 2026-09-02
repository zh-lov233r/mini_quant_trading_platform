from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any, Callable, Iterable, Iterator, Literal, Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from src.models.tables import Instrument, PortfolioSnapshot, Signal, Transaction


PersistLevel = Literal["summary", "trades", "full"]
COPY_BATCH_SIZE = 5_000
NUMERIC_20_8_ABS_LIMIT = 10**12


class NativeResultValidationError(ValueError):
    pass


class NativePersistenceCancelledError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NativePersistStats:
    signals: int
    transactions: int
    snapshots: int

    @property
    def total(self) -> int:
        return self.signals + self.transactions + self.snapshots


def _strict_json(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            value,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {token}")
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise NativeResultValidationError(f"{label} is not strict JSON") from exc
    if not isinstance(parsed, dict):
        raise NativeResultValidationError(f"{label} must encode an object")
    return parsed


def _same_length(columns: dict[str, Sequence[Any]], label: str) -> int:
    lengths = {name: len(values) for name, values in columns.items()}
    expected = next(iter(lengths.values()), 0)
    if any(length != expected for length in lengths.values()):
        raise NativeResultValidationError(f"{label} column lengths differ: {lengths}")
    return expected


def _finite_numeric(value: Any, label: str, *, nullable: bool = False) -> float | None:
    number = float(value)
    if math.isnan(number) and nullable:
        return None
    if not math.isfinite(number) or abs(number) >= NUMERIC_20_8_ABS_LIMIT:
        raise NativeResultValidationError(f"{label} exceeds the finite NUMERIC(20,8) domain")
    return number


def _symbol(symbols: Sequence[str], symbol_id: int, label: str) -> str:
    if symbol_id < 0 or symbol_id >= len(symbols):
        raise NativeResultValidationError(f"{label} symbol_id is outside the dictionary")
    value = str(symbols[symbol_id]).strip().upper()
    if not value:
        raise NativeResultValidationError(f"{label} symbol is empty")
    return value


def _chunks(rows: Sequence[tuple[Any, ...]], size: int) -> Iterator[Sequence[tuple[Any, ...]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _downsample_indices(equity: Sequence[float], max_points: int = 600) -> list[int]:
    count = len(equity)
    if count <= max_points:
        return list(range(count))
    interior = list(range(1, count - 1))
    bucket_count = max(1, (max_points - 2) // 2)
    bucket_size = max(1, math.ceil(len(interior) / bucket_count))
    selected = [0]
    for start in range(0, len(interior), bucket_size):
        bucket = interior[start : start + bucket_size]
        low = min(bucket, key=lambda index: (float(equity[index]), index))
        high = max(bucket, key=lambda index: (float(equity[index]), index))
        for index in sorted({low, high}):
            if len(selected) < max_points - 1:
                selected.append(index)
    selected.append(count - 1)
    return selected[: max_points - 1] + [count - 1] if len(selected) > max_points else selected


def _timestamp(timestamp_us: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp_us) / 1_000_000, tz=timezone.utc)


def _validate_instrument_references(db: Session, values: set[int]) -> None:
    if not values:
        return
    existing = set(
        db.scalars(select(Instrument.id).where(Instrument.id.in_(sorted(values)))).all()
    )
    missing = sorted(values - {int(value) for value in existing})
    if missing:
        preview = ", ".join(str(value) for value in missing[:10])
        raise NativeResultValidationError(f"native result references unknown instruments: {preview}")


def _prepared_rows(
    db: Session,
    *,
    run_id: UUID,
    strategy_id: UUID,
    result: Any,
    persist_level: PersistLevel,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    symbols = list(result.symbols)
    signals = result.signals
    trades = result.trades
    equity = result.equity
    signal_count = _same_length(dict(signals), "signals")
    trade_count = _same_length(dict(trades), "trades")
    equity_count = _same_length(dict(equity), "equity")
    instrument_references: set[int] = set()

    signal_rows: list[tuple[Any, ...]] = []
    if persist_level == "full":
        for index in range(signal_count):
            action = {1: "BUY", -1: "SELL"}.get(int(signals["action"][index]))
            if action is None:
                raise NativeResultValidationError("signal action is invalid")
            instrument_id = int(signals["instrument_id"][index])
            instrument_references.add(instrument_id)
            signal_rows.append(
                (
                    uuid4(),
                    run_id,
                    strategy_id,
                    instrument_id,
                    _timestamp(int(signals["timestamp_us"][index])),
                    _symbol(symbols, int(signals["symbol_id"][index]), "signal"),
                    action,
                    _finite_numeric(signals["score"][index], "signal.score", nullable=True),
                    str(signals["reason"][index]),
                    json.dumps(
                        _strict_json(signals["metadata_json"][index], "signal.metadata_json"),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )

    transaction_rows: list[tuple[Any, ...]] = []
    if persist_level in {"trades", "full"}:
        for index in range(trade_count):
            side = {1: "BUY", -1: "SELL"}.get(int(trades["side"][index]))
            if side is None:
                raise NativeResultValidationError("transaction side is invalid")
            quantity = _finite_numeric(trades["quantity"][index], "transaction.qty")
            price = _finite_numeric(trades["price"][index], "transaction.price")
            if quantity is None or quantity <= 0 or price is None or price < 0:
                raise NativeResultValidationError("transaction quantity and price are invalid")
            instrument_id = int(trades["instrument_id"][index])
            instrument_references.add(instrument_id)
            entry_features = str(trades["entry_signal_features_json"][index] or "")
            meta = {
                "reason": str(trades["reason"][index]),
                "source": "backtest",
                "signal_ts": _timestamp(int(trades["signal_timestamp_us"][index])).isoformat(),
                "execution_trade_date_ordinal": int(trades["execution_date_ordinal"][index]),
                "reference_price": _finite_numeric(
                    trades["reference_price"][index], "transaction.reference_price"
                ),
                "slippage_bps": _finite_numeric(
                    trades["slippage_bps"][index], "transaction.slippage_bps"
                ),
                "slippage_cost": _finite_numeric(
                    trades["slippage_cost"][index], "transaction.slippage_cost"
                ),
                "gross_notional": _finite_numeric(
                    trades["gross_notional"][index], "transaction.gross_notional"
                ),
                "net_cash_flow": _finite_numeric(
                    trades["net_cash_flow"][index], "transaction.net_cash_flow"
                ),
                "setup_id": str(trades["setup_id"][index]),
                "stage_index": int(trades["stage_index"][index]),
                "stage_key": str(trades["stage_key"][index]),
                "position_quantity_before": _finite_numeric(
                    trades["position_quantity_before"][index],
                    "transaction.position_quantity_before",
                ),
                "position_quantity_after": _finite_numeric(
                    trades["position_quantity_after"][index],
                    "transaction.position_quantity_after",
                ),
                "position_average_entry_price_after": _finite_numeric(
                    trades["position_average_entry_price_after"][index],
                    "transaction.position_average_entry_price_after",
                    nullable=True,
                ),
                "stage_target_pct": _finite_numeric(
                    trades["stage_target_pct"][index],
                    "transaction.stage_target_pct",
                    nullable=True,
                ),
                "entry_signal_features": (
                    _strict_json(entry_features, "transaction.entry_signal_features_json")
                    if entry_features
                    else None
                ),
            }
            transaction_rows.append(
                (
                    uuid4(),
                    strategy_id,
                    run_id,
                    instrument_id,
                    _timestamp(int(trades["execution_timestamp_us"][index])),
                    _symbol(symbols, int(trades["symbol_id"][index]), "transaction"),
                    side,
                    quantity,
                    price,
                    _finite_numeric(trades["fee"][index], "transaction.fee"),
                    None,
                    json.dumps(meta, separators=(",", ":"), sort_keys=True),
                )
            )

    snapshot_rows: list[tuple[Any, ...]] = []
    indices = list(range(equity_count)) if persist_level == "full" else _downsample_indices(
        equity["equity"]
    )
    for index in indices:
        positions = _strict_json(equity["positions_json"][index], "equity.positions_json")
        metrics = _strict_json(equity["metrics_json"][index], "equity.metrics_json")
        if persist_level != "full":
            positions = {}
            metrics = {**metrics, "downsampled": True}
        snapshot_rows.append(
            (
                uuid4(),
                run_id,
                _timestamp(int(equity["timestamp_us"][index])),
                _finite_numeric(equity["cash"][index], "equity.cash"),
                _finite_numeric(equity["equity"][index], "equity.equity"),
                _finite_numeric(equity["gross_exposure"][index], "equity.gross_exposure"),
                _finite_numeric(equity["gross_exposure"][index], "equity.net_exposure"),
                _finite_numeric(equity["drawdown"][index], "equity.drawdown"),
                json.dumps(positions, separators=(",", ":"), sort_keys=True),
                json.dumps(metrics, separators=(",", ":"), sort_keys=True),
            )
        )
    _validate_instrument_references(db, instrument_references)
    return signal_rows, transaction_rows, snapshot_rows


def _copy_rows(
    db: Session,
    statement: str,
    rows: Sequence[tuple[Any, ...]],
    *,
    cancel_check: Callable[[], bool] | None,
) -> None:
    if not rows:
        return
    raw = db.connection().connection.driver_connection
    with raw.cursor() as cursor:
        for batch in _chunks(rows, COPY_BATCH_SIZE):
            if cancel_check is not None and cancel_check():
                raise NativePersistenceCancelledError(
                    "backtest cancellation requested before COPY batch"
                )
            with cursor.copy(statement) as copy:
                for row in batch:
                    copy.write_row(row)


def persist_native_result(
    db: Session,
    *,
    run_id: UUID,
    strategy_id: UUID,
    result: Any,
    persist_level: PersistLevel,
    cancel_check: Callable[[], bool] | None = None,
) -> NativePersistStats:
    """Validate the complete typed result, then replace one run's details atomically."""
    signal_rows, transaction_rows, snapshot_rows = _prepared_rows(
        db,
        run_id=run_id,
        strategy_id=strategy_id,
        result=result,
        persist_level=persist_level,
    )
    if cancel_check is not None and cancel_check():
        raise NativePersistenceCancelledError(
            "backtest cancellation requested before persistence"
        )
    db.execute(delete(Signal).where(Signal.run_id == run_id))
    db.execute(delete(Transaction).where(Transaction.run_id == run_id))
    db.execute(delete(PortfolioSnapshot).where(PortfolioSnapshot.run_id == run_id))

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        if bind.dialect.driver != "psycopg":
            raise RuntimeError("native COPY persistence requires postgresql+psycopg")
        _copy_rows(
            db,
            "COPY signals (id,run_id,strategy_id,instrument_id,ts,symbol,signal,score,reason,features) FROM STDIN",
            signal_rows,
            cancel_check=cancel_check,
        )
        _copy_rows(
            db,
            "COPY transactions (id,strategy_id,run_id,instrument_id,ts,symbol,side,qty,price,fee,order_id,meta) FROM STDIN",
            transaction_rows,
            cancel_check=cancel_check,
        )
        _copy_rows(
            db,
            "COPY portfolio_snapshots (id,run_id,ts,cash,equity,gross_exposure,net_exposure,drawdown,positions,metrics) FROM STDIN",
            snapshot_rows,
            cancel_check=cancel_check,
        )
    else:
        if signal_rows:
            values = [dict(zip(
                ("id", "run_id", "strategy_id", "instrument_id", "ts", "symbol", "signal", "score", "reason", "features"),
                row,
                strict=True,
            )) for row in signal_rows]
            for value in values:
                value["features"] = json.loads(value["features"])
            db.execute(
                insert(Signal),
                values,
            )
        if transaction_rows:
            values = [dict(zip(
                ("id", "strategy_id", "run_id", "instrument_id", "ts", "symbol", "side", "qty", "price", "fee", "order_id", "meta"),
                row,
                strict=True,
            )) for row in transaction_rows]
            for value in values:
                value["meta"] = json.loads(value["meta"])
            db.execute(
                insert(Transaction),
                values,
            )
        if snapshot_rows:
            values = [dict(zip(
                ("id", "run_id", "ts", "cash", "equity", "gross_exposure", "net_exposure", "drawdown", "positions", "metrics"),
                row,
                strict=True,
            )) for row in snapshot_rows]
            for value in values:
                value["positions"] = json.loads(value["positions"])
                value["metrics"] = json.loads(value["metrics"])
            db.execute(
                insert(PortfolioSnapshot),
                values,
            )
    return NativePersistStats(
        signals=len(signal_rows),
        transactions=len(transaction_rows),
        snapshots=len(snapshot_rows),
    )
