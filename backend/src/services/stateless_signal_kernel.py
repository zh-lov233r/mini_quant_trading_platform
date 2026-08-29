from __future__ import annotations

from typing import Any

import numpy as np


def _numeric_column(
    snapshots: list[dict[str, Any]],
    field: str,
) -> np.ndarray[Any, np.dtype[np.float64]]:
    return np.asarray(
        [
            float(snapshot[field])
            if snapshot.get(field) is not None
            else np.nan
            for snapshot in snapshots
        ],
        dtype=np.float64,
    )


def vectorized_stateless_prefilter(
    runtime: dict[str, Any],
    market_data_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Remove rows that cannot enter any stateless signal branch.

    The original strategy handler still evaluates every trading rule and emits
    events. This NumPy kernel only performs a conservative availability mask,
    and always retains open positions, so event values and ordering stay owned
    by the shared backtest/paper strategy implementation.
    """
    strategy_type = str(runtime.get("strategy_type") or "")
    if strategy_type not in {"trend", "mean_reversion", "momentum_breakout"}:
        return market_data_by_symbol
    items = list(market_data_by_symbol.items())
    if not items:
        return market_data_by_symbol
    symbols = [item[0] for item in items]
    snapshots = [item[1] for item in items]
    positions = _numeric_column(snapshots, "position")
    keep = np.isfinite(positions) & (positions > 0)

    if strategy_type == "trend":
        signal = runtime["params"]["signal"]
        fast = signal["fast_indicator"]
        slow = signal["slow_indicator"]
        fast_key = f"{fast['kind']}_{fast['window']}"
        slow_key = f"{slow['kind']}_{slow['window']}"
        required = (
            "close",
            "volume",
            "volume_sma_20",
            fast_key,
            slow_key,
            f"prev_{fast_key}",
            f"prev_{slow_key}",
        )
    elif strategy_type == "mean_reversion":
        lookback = int(runtime["params"]["signal"]["lookback_window"])
        required = ("close", f"zscore_{lookback}")
    else:
        required = ("close", "volume", "volume_sma_20", "ret_20d")

    available = np.ones(len(snapshots), dtype=np.bool_)
    for field in required:
        available &= np.isfinite(_numeric_column(snapshots, field))
    keep |= available
    return {
        symbol: snapshot
        for symbol, snapshot, selected in zip(symbols, snapshots, keep, strict=True)
        if bool(selected)
    }
