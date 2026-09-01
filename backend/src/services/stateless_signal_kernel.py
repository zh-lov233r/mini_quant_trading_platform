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


def vectorized_stateless_candidates(
    runtime: dict[str, Any],
    market_data_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Keep only rows that can emit a stateless signal or manage an open position.

    The shared strategy handler remains the sole owner of final events, reasons,
    metadata, exits, and ordering. This kernel only avoids invoking it for rows
    that provably cannot emit an event.
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
    keep = np.isfinite(positions) & (positions != 0)

    if strategy_type == "trend":
        signal = runtime["params"]["signal"]
        fast = signal["fast_indicator"]
        slow = signal["slow_indicator"]
        fast_key = f"{fast['kind']}_{fast['window']}"
        slow_key = f"{slow['kind']}_{slow['window']}"
        volume = _numeric_column(snapshots, "volume")
        average_volume = _numeric_column(snapshots, "volume_sma_20")
        fast_now = _numeric_column(snapshots, fast_key)
        slow_now = _numeric_column(snapshots, slow_key)
        previous_fast = _numeric_column(snapshots, f"prev_{fast_key}")
        previous_slow = _numeric_column(snapshots, f"prev_{slow_key}")
        valid = (
            np.isfinite(volume)
            & np.isfinite(average_volume)
            & (average_volume > 0)
            & np.isfinite(fast_now)
            & np.isfinite(slow_now)
            & np.isfinite(previous_fast)
            & np.isfinite(previous_slow)
        )
        volume_ok = volume >= float(signal["volume_multiplier"]) * average_volume
        crossed = ((previous_fast <= previous_slow) & (fast_now > slow_now)) | (
            (previous_fast >= previous_slow) & (fast_now < slow_now)
        )
        keep |= valid & volume_ok & crossed
    elif strategy_type == "mean_reversion":
        signal = runtime["params"]["signal"]
        lookback = int(signal["lookback_window"])
        zscore = _numeric_column(snapshots, f"zscore_{lookback}")
        keep |= np.isfinite(zscore) & (np.abs(zscore) >= float(signal["zscore_entry"]))
    else:
        signal = runtime["params"]["signal"]
        close = _numeric_column(snapshots, "close")
        sma_20 = _numeric_column(snapshots, "sma_20")
        return_20d = _numeric_column(snapshots, "ret_20d")
        volume = _numeric_column(snapshots, "volume")
        average_volume = _numeric_column(snapshots, "volume_sma_20")
        valid = (
            np.isfinite(close)
            & np.isfinite(sma_20)
            & (sma_20 > 0)
            & np.isfinite(return_20d)
            & np.isfinite(volume)
            & np.isfinite(average_volume)
            & (average_volume > 0)
        )
        keep |= (
            valid
            & (positions <= 0)
            & (close >= sma_20 * (1.0 + float(signal["breakout_buffer_pct"])))
            & (return_20d >= float(signal["minimum_return_20d"]))
            & (volume >= float(signal["volume_multiplier"]) * average_volume)
        )
    return {
        symbol: snapshot
        for symbol, snapshot, selected in zip(symbols, snapshots, keep, strict=True)
        if bool(selected)
    }
