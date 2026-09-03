from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import random
from statistics import mean
from typing import Any, Iterable
from xml.sax.saxutils import escape

from sqlalchemy import bindparam, case, func, select, text
from sqlalchemy.orm import Session

from src.models.tables import (
    ExperimentCandidate,
    ExperimentTrial,
    PortfolioSnapshot,
    ResearchExperiment,
    Signal,
    SupportResistanceRegimeVersion,
    SupportResistanceRunEvent,
    SupportResistanceRunMaterialization,
    Transaction,
)


REPORT_DISCLAIMER = (
    "Research evidence only; this report is not a profitability guarantee or evidence of live-trading safety."
)
ZH_STATIC_TEXT = {
    REPORT_DISCLAIMER: "仅供研究证据使用；本报告不是盈利保证，也不是实盘交易安全证明。",
    "The dynamic universe is based only on fields available at the signal close.": "动态股票池仅使用信号日收盘时已知的字段。",
    "Delistings without modeled consideration use zero recovery; last close is reported only as an upper-bound sensitivity.": "无法建模现金对价的退市按零回收计值；最后收盘价仅作为上界敏感性。",
    "A separate same-cost final-holdout replay must match events, signals, transactions, positions, and NAV exactly.": "独立的同成本最终留出重放必须在事件、信号、交易、持仓和 NAV 上完全一致。",
    "No result authorizes portfolio activation, scheduling, or order submission.": "任何结果都不授权激活组合、启用调度或提交订单。",
    "pre-registered all-mode pivot-slope-regime-v3 default; validity must be established independently": "预注册的全模式 pivot-slope-regime-v3 默认策略；必须独立建立有效性证据",
}


def _localized(value: Any, zh: bool) -> str:
    text_value = str(value)
    return ZH_STATIC_TEXT.get(text_value, text_value) if zh else text_value
TERMINAL_CHILD_STATUSES = {"completed", "partially_failed", "failed", "cancelled"}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _oos_trial(candidate: ExperimentCandidate, scenario: str) -> ExperimentTrial | None:
    return next(
        (
            item
            for item in candidate.trials
            if item.status == "completed"
            and item.sample_kind == "out_of_sample"
            and item.cost_scenario.lower() == scenario
        ),
        None,
    )


def _candidate_payload(candidate: ExperimentCandidate) -> dict[str, Any]:
    base = _oos_trial(candidate, "base")
    stress = _oos_trial(candidate, "stress")
    return {
        "candidateId": str(candidate.id),
        "paramsHash": candidate.params_hash,
        "rationale": candidate.rationale,
        "overrides": candidate.parameter_overrides or {},
        "base": dict(base.metrics or {}) if base else None,
        "stress": dict(stress.metrics or {}) if stress else None,
        "baseRunId": str(base.backtest_run_id) if base and base.backtest_run_id else None,
        "stressRunId": str(stress.backtest_run_id) if stress and stress.backtest_run_id else None,
    }


def _normalized_signal_rows(db: Session, run_id: Any) -> list[tuple[Any, ...]]:
    return [
        (
            row.instrument_id,
            row.ts,
            row.symbol,
            row.signal,
            round(float(row.score), 10) if row.score is not None else None,
            row.reason,
            canonical_json(row.features or {}),
        )
        for row in db.execute(
            select(Signal)
            .where(Signal.run_id == run_id)
            .order_by(Signal.ts, Signal.instrument_id, Signal.symbol, Signal.signal)
        ).scalars()
    ]


def _normalized_event_rows(db: Session, run_id: Any) -> list[tuple[Any, ...]]:
    return [
        (
            row.instrument_id,
            row.event_date,
            row.symbol,
            row.event_type,
            row.zone_key,
            row.setup,
            bool(row.selected),
            round(float(row.score), 10) if row.score is not None else None,
            canonical_json(row.payload or {}),
        )
        for row in db.execute(
            select(SupportResistanceRunEvent)
            .where(SupportResistanceRunEvent.run_id == run_id)
            .order_by(
                SupportResistanceRunEvent.event_date,
                SupportResistanceRunEvent.instrument_id,
                SupportResistanceRunEvent.event_type,
                SupportResistanceRunEvent.zone_key,
            )
        ).scalars()
    ]


def _normalized_regime_rows(db: Session, run_id: Any) -> list[tuple[Any, ...]]:
    return [
        (
            row.instrument_id,
            row.symbol,
            row.version,
            row.effective_from,
            row.regime,
            row.lower_zone_key,
            row.upper_zone_key,
            row.reason_code,
            canonical_json(row.evidence or {}),
        )
        for row in db.execute(
            select(SupportResistanceRegimeVersion)
            .join(
                SupportResistanceRunMaterialization,
                SupportResistanceRunMaterialization.materialization_id
                == SupportResistanceRegimeVersion.materialization_id,
            )
            .where(SupportResistanceRunMaterialization.run_id == run_id)
            .order_by(
                SupportResistanceRegimeVersion.instrument_id,
                SupportResistanceRegimeVersion.effective_from,
                SupportResistanceRegimeVersion.version,
            )
        ).scalars()
    ]


def _normalized_transaction_rows(db: Session, run_id: Any) -> list[tuple[Any, ...]]:
    return [
        (
            row.instrument_id,
            row.ts,
            row.symbol,
            row.side,
            round(float(row.qty), 10),
            round(float(row.price), 10),
            round(float(row.fee or 0), 10),
            canonical_json(row.meta or {}),
        )
        for row in db.execute(
            select(Transaction)
            .where(Transaction.run_id == run_id)
            .order_by(
                Transaction.ts,
                case((Transaction.side == "SELL", 0), else_=1),
                Transaction.instrument_id,
                Transaction.symbol,
            )
        ).scalars()
    ]


def _normalized_nav_rows(db: Session, run_id: Any) -> list[tuple[Any, ...]]:
    return [
        (
            row.ts,
            round(float(row.cash), 10),
            round(float(row.equity), 10),
            round(float(row.drawdown or 0), 10),
            canonical_json(row.positions or {}),
        )
        for row in db.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.run_id == run_id)
            .order_by(PortfolioSnapshot.ts)
        ).scalars()
    ]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def cache_audit(db: Session, candidate: ExperimentCandidate) -> dict[str, Any]:
    """Compare pre-registered same-cost cold/hot final-holdout replays."""

    base = _oos_trial(candidate, "base")
    replay = _oos_trial(candidate, "base_cache_replay")
    if not base or not replay or not base.backtest_run_id or not replay.backtest_run_id:
        return {"status": "missing", "equivalent": False}
    base_signals = _normalized_signal_rows(db, base.backtest_run_id)
    replay_signals = _normalized_signal_rows(db, replay.backtest_run_id)
    base_events = _normalized_event_rows(db, base.backtest_run_id)
    replay_events = _normalized_event_rows(db, replay.backtest_run_id)
    base_regimes = _normalized_regime_rows(db, base.backtest_run_id)
    replay_regimes = _normalized_regime_rows(db, replay.backtest_run_id)
    base_transactions = _normalized_transaction_rows(db, base.backtest_run_id)
    replay_transactions = _normalized_transaction_rows(db, replay.backtest_run_id)
    base_nav = _normalized_nav_rows(db, base.backtest_run_id)
    replay_nav = _normalized_nav_rows(db, replay.backtest_run_id)
    base_cache_key = (base.metrics or {}).get("support_resistance_cache_key")
    replay_cache_key = (replay.metrics or {}).get("support_resistance_cache_key")
    equivalent = (
        base_cache_key is not None
        and base_cache_key == replay_cache_key
        and base_signals == replay_signals
        and base_events == replay_events
        and base_regimes == replay_regimes
        and base_transactions == replay_transactions
        and base_nav == replay_nav
    )
    return {
        "status": "completed",
        "equivalent": equivalent,
        "cacheKey": base_cache_key,
        "signalCount": len(base_signals),
        "eventCount": len(base_events),
        "regimeVersionCount": len(base_regimes),
        "transactionCount": len(base_transactions),
        "navCount": len(base_nav),
        "signalDigestBase": hashlib.sha256(canonical_json(base_signals).encode()).hexdigest(),
        "signalDigestReplay": hashlib.sha256(canonical_json(replay_signals).encode()).hexdigest(),
        "eventDigestBase": hashlib.sha256(canonical_json(base_events).encode()).hexdigest(),
        "eventDigestReplay": hashlib.sha256(canonical_json(replay_events).encode()).hexdigest(),
        "regimeDigestBase": hashlib.sha256(canonical_json(base_regimes).encode()).hexdigest(),
        "regimeDigestReplay": hashlib.sha256(canonical_json(replay_regimes).encode()).hexdigest(),
        "transactionDigestBase": hashlib.sha256(canonical_json(base_transactions).encode()).hexdigest(),
        "transactionDigestReplay": hashlib.sha256(canonical_json(replay_transactions).encode()).hexdigest(),
        "navDigestBase": hashlib.sha256(canonical_json(base_nav).encode()).hexdigest(),
        "navDigestReplay": hashlib.sha256(canonical_json(replay_nav).encode()).hexdigest(),
        "scope": "same-cost final-holdout zones, regimes, events, signals, transactions, positions, and NAV",
    }


def regime_audit(db: Session, run_id: Any) -> dict[str, Any]:
    transitions = _normalized_regime_rows(db, run_id)
    events = list(
        db.execute(
            select(SupportResistanceRunEvent)
            .where(SupportResistanceRunEvent.run_id == run_id)
            .where(
                SupportResistanceRunEvent.event_type.in_(
                    {"candidate", "selection", "regime_rejection", "regime_transition"}
                )
            )
        ).scalars()
    )
    state_transition_counts: dict[str, int] = defaultdict(int)
    candidate_counts: dict[str, int] = defaultdict(int)
    admitted_counts: dict[str, int] = defaultdict(int)
    rejection_counts: dict[str, int] = defaultdict(int)
    for event in events:
        payload = event.payload or {}
        if event.event_type == "regime_transition":
            state_transition_counts[str(payload.get("to_regime") or payload.get("regime") or "unknown")] += 1
        elif event.event_type == "candidate":
            key = f"{payload.get('regime') or 'unknown'}/{payload.get('setup') or 'unknown'}"
            candidate_counts[key] += 1
        elif event.event_type == "selection":
            key = f"{payload.get('regime') or 'unknown'}/{payload.get('setup') or 'unknown'}"
            admitted_counts[key] += 1
        elif event.event_type == "regime_rejection":
            key = f"{payload.get('regime') or 'unknown'}/{payload.get('setup') or 'unknown'}"
            rejection_counts[key] += 1
    downtrend_exit_count = db.scalar(
        select(func.count())
        .select_from(Signal)
        .where(Signal.run_id == run_id)
        .where(Signal.signal == "SELL")
        .where(Signal.reason == "confirmed downtrend regime")
    ) or 0
    coverage: dict[str, int] = {}
    duration_sessions: dict[str, dict[str, float | int]] = {}
    integrity = {
        "overlapCount": 0,
        "gapCount": 0,
        "duplicateDateCount": 0,
        "adjacentSameCount": 0,
        "unalignedTransitionCount": 0,
        "exactCoverage": True,
    }
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        rows = db.execute(
            text(
                """
                WITH timeline AS (
                  SELECT regime.materialization_id,
                         regime.instrument_id,
                         regime.regime,
                         regime.effective_from,
                         lead(regime.effective_from) OVER (
                           PARTITION BY regime.materialization_id, regime.instrument_id
                           ORDER BY regime.effective_from, regime.version
                         ) AS next_start,
                         materialization.coverage_end
                  FROM support_resistance_regime_versions regime
                  JOIN support_resistance_run_materializations link
                    ON link.materialization_id = regime.materialization_id
                  JOIN support_resistance_materializations materialization
                    ON materialization.id = regime.materialization_id
                  WHERE link.run_id = :run_id
                )
                SELECT timeline.regime, count(*)
                FROM timeline
                JOIN eod_bars bar
                  ON bar.instrument_id = timeline.instrument_id
                 AND bar.dt_ny >= timeline.effective_from
                 AND bar.dt_ny < COALESCE(timeline.next_start, timeline.coverage_end + 1)
                GROUP BY timeline.regime
                ORDER BY timeline.regime
                """
            ),
            {"run_id": run_id},
        ).all()
        coverage = {str(regime): int(count) for regime, count in rows}
        duration_rows = db.execute(
            text(
                """
                WITH timeline AS (
                  SELECT regime.materialization_id,
                         regime.instrument_id,
                         regime.regime,
                         regime.effective_from,
                         lead(regime.effective_from) OVER (
                           PARTITION BY regime.materialization_id, regime.instrument_id
                           ORDER BY regime.effective_from, regime.version
                         ) AS next_start,
                         materialization.coverage_end
                  FROM support_resistance_regime_versions regime
                  JOIN support_resistance_run_materializations link
                    ON link.materialization_id = regime.materialization_id
                  JOIN support_resistance_materializations materialization
                    ON materialization.id = regime.materialization_id
                  WHERE link.run_id = :run_id
                ), intervals AS (
                  SELECT timeline.regime, count(bar.dt_ny)::integer AS sessions
                  FROM timeline
                  JOIN eod_bars bar
                    ON bar.instrument_id = timeline.instrument_id
                   AND bar.dt_ny >= timeline.effective_from
                   AND bar.dt_ny < COALESCE(timeline.next_start, timeline.coverage_end + 1)
                  GROUP BY timeline.materialization_id, timeline.instrument_id,
                           timeline.regime, timeline.effective_from
                )
                SELECT regime, count(*)::integer, min(sessions)::integer,
                       max(sessions)::integer, avg(sessions)::double precision
                FROM intervals
                GROUP BY regime
                ORDER BY regime
                """
            ),
            {"run_id": run_id},
        ).all()
        duration_sessions = {
            str(regime): {
                "intervalCount": int(interval_count),
                "min": int(minimum),
                "max": int(maximum),
                "mean": round(float(average), 6),
            }
            for regime, interval_count, minimum, maximum, average in duration_rows
        }
        duplicate_count, adjacent_same_count, unaligned_count, missing_first_count = db.execute(
            text(
                """
                WITH scoped AS (
                  SELECT regime.*, materialization.coverage_start, materialization.coverage_end
                  FROM support_resistance_regime_versions regime
                  JOIN support_resistance_run_materializations link
                    ON link.materialization_id = regime.materialization_id
                  JOIN support_resistance_materializations materialization
                    ON materialization.id = regime.materialization_id
                  WHERE link.run_id = :run_id
                ), duplicate_starts AS (
                  SELECT count(*) AS value FROM (
                    SELECT materialization_id, instrument_id, effective_from
                    FROM scoped GROUP BY materialization_id, instrument_id, effective_from
                    HAVING count(*) > 1
                  ) rows
                ), adjacent_same AS (
                  SELECT count(*) AS value FROM (
                    SELECT regime,
                           lag(regime) OVER (
                             PARTITION BY materialization_id, instrument_id
                             ORDER BY effective_from, version
                           ) AS previous_regime
                    FROM scoped
                  ) rows WHERE regime = previous_regime
                ), unaligned AS (
                  SELECT count(*) AS value
                  FROM scoped
                  LEFT JOIN eod_bars bar
                    ON bar.instrument_id = scoped.instrument_id
                   AND bar.dt_ny = scoped.effective_from
                  WHERE scoped.instrument_id IS NOT NULL AND bar.instrument_id IS NULL
                ), first_sessions AS (
                  SELECT scoped.materialization_id, scoped.instrument_id,
                         min(scoped.effective_from) AS first_regime,
                         min(bar.dt_ny) AS first_session
                  FROM scoped
                  JOIN eod_bars bar
                    ON bar.instrument_id = scoped.instrument_id
                   AND bar.dt_ny BETWEEN scoped.coverage_start AND scoped.coverage_end
                  GROUP BY scoped.materialization_id, scoped.instrument_id
                ), missing_first AS (
                  SELECT count(*) AS value FROM first_sessions WHERE first_regime <> first_session
                )
                SELECT duplicate_starts.value, adjacent_same.value,
                       unaligned.value, missing_first.value
                FROM duplicate_starts, adjacent_same, unaligned, missing_first
                """
            ),
            {"run_id": run_id},
        ).one()
        integrity = {
            "overlapCount": 0,
            "gapCount": int(missing_first_count) + int(unaligned_count) + (0 if transitions else 1),
            "duplicateDateCount": int(duplicate_count),
            "adjacentSameCount": int(adjacent_same_count),
            "unalignedTransitionCount": int(unaligned_count),
            "exactCoverage": not any(
                (
                    duplicate_count,
                    adjacent_same_count,
                    unaligned_count,
                    missing_first_count,
                    0 if transitions else 1,
                )
            ),
        }
    trade_audit = _trade_results_by_regime(db, run_id)
    return {
        "regimeVersionCount": len(transitions),
        "coverageSessions": coverage,
        "durationSessions": duration_sessions,
        "transitionCounts": dict(sorted(state_transition_counts.items())),
        "candidateCounts": dict(sorted(candidate_counts.items())),
        "admittedCounts": dict(sorted(admitted_counts.items())),
        "rejectionCounts": dict(sorted(rejection_counts.items())),
        "filledCounts": trade_audit["filledCounts"],
        "realizedReturns": trade_audit["realizedReturns"],
        "downtrendExitCount": int(downtrend_exit_count),
        "downtrendExitPerformance": trade_audit["downtrendExitPerformance"],
        "timelineIntegrity": integrity,
    }


def _trade_results_by_regime(db: Session, run_id: Any) -> dict[str, Any]:
    transactions = list(
        db.execute(
            select(Transaction)
            .where(Transaction.run_id == run_id)
            .order_by(
                Transaction.ts,
                case((Transaction.side == "SELL", 0), else_=1),
                Transaction.instrument_id,
                Transaction.symbol,
            )
        ).scalars()
    )
    lots: dict[tuple[int | None, str], list[dict[str, Any]]] = defaultdict(list)
    filled_counts: dict[str, int] = defaultdict(int)
    realized: dict[str, list[tuple[float, float]]] = defaultdict(list)
    downtrend_exits: list[dict[str, Any]] = []
    for transaction in transactions:
        key = (transaction.instrument_id, transaction.symbol)
        qty = float(transaction.qty)
        price = float(transaction.price)
        fee = float(transaction.fee or 0)
        meta = transaction.meta or {}
        if transaction.side == "BUY":
            entry_features = meta.get("entry_signal_features") or {}
            support_resistance = entry_features.get("support_resistance") or {}
            regime = str(support_resistance.get("regime") or "unknown")
            setup = str(support_resistance.get("selected_setup") or "unknown")
            audit_key = f"{regime}/{setup}"
            filled_counts[audit_key] += 1
            lots[key].append(
                {
                    "qty": qty,
                    "unit_cost": price + fee / qty,
                    "audit_key": audit_key,
                }
            )
            continue
        remaining = qty
        sell_unit_proceeds = price - fee / qty
        exit_reason = str(meta.get("reason") or "")
        exit_returns: list[float] = []
        while remaining > 1e-10 and lots[key]:
            lot = lots[key][0]
            consumed = min(remaining, float(lot["qty"]))
            unit_cost = float(lot["unit_cost"])
            result = (sell_unit_proceeds - unit_cost) / unit_cost if unit_cost > 0 else 0.0
            realized[str(lot["audit_key"])].append((result, consumed))
            exit_returns.append(result)
            lot["qty"] = float(lot["qty"]) - consumed
            remaining -= consumed
            if lot["qty"] <= 1e-10:
                lots[key].pop(0)
        if exit_reason == "confirmed downtrend regime":
            post_exit_return = None
            post_exit_worst_drawdown = None
            if transaction.instrument_id is not None and db.bind is not None and db.bind.dialect.name == "postgresql":
                future_rows = db.execute(
                    text(
                        """
                        SELECT COALESCE(close_fa, close_u) AS close,
                               COALESCE(low_fa, low_u) AS low
                        FROM eod_bars
                        WHERE instrument_id = :instrument_id
                          AND dt_ny > :exit_date
                        ORDER BY dt_ny
                        LIMIT 20
                        """
                    ),
                    {"instrument_id": transaction.instrument_id, "exit_date": transaction.ts.date()},
                ).mappings().all()
                if future_rows and price > 0:
                    post_exit_return = float(future_rows[-1]["close"]) / price - 1
                    post_exit_worst_drawdown = min(float(row["low"]) / price - 1 for row in future_rows)
            downtrend_exits.append(
                {
                    "symbol": transaction.symbol,
                    "tradeDate": transaction.ts.date().isoformat(),
                    "realizedReturn": mean(exit_returns) if exit_returns else None,
                    "postExitReturn20": post_exit_return,
                    "postExitWorstDrawdown20": post_exit_worst_drawdown,
                    "avoidedDrawdownProxy20": (
                        max(0.0, -post_exit_worst_drawdown)
                        if post_exit_worst_drawdown is not None
                        else None
                    ),
                }
            )
    realized_exit_returns = [
        item["realizedReturn"] for item in downtrend_exits if item["realizedReturn"] is not None
    ]
    post_exit_returns = [
        item["postExitReturn20"] for item in downtrend_exits if item["postExitReturn20"] is not None
    ]
    avoided_drawdowns = [
        item["avoidedDrawdownProxy20"]
        for item in downtrend_exits
        if item["avoidedDrawdownProxy20"] is not None
    ]
    return {
        "filledCounts": dict(sorted(filled_counts.items())),
        "realizedReturns": {
            key: {
                "tradeCount": len(values),
                "mean": sum(value * weight for value, weight in values)
                / sum(weight for _, weight in values),
                "winRate": sum(weight for value, weight in values if value > 0)
                / sum(weight for _, weight in values),
            }
            for key, values in sorted(realized.items())
            if values
        },
        "downtrendExitPerformance": {
            "exits": downtrend_exits,
            "meanRealizedReturn": mean(realized_exit_returns) if realized_exit_returns else None,
            "meanPostExitReturn20": mean(post_exit_returns) if post_exit_returns else None,
            "meanAvoidedDrawdownProxy20": mean(avoided_drawdowns) if avoided_drawdowns else None,
        },
    }


BAR_SQL = text(
    """
    SELECT
        bars.instrument_id,
        bars.dt_ny,
        COALESCE(bars.open_fa, bars.open_u) AS open,
        COALESCE(bars.high_fa, bars.high_u) AS high,
        COALESCE(bars.low_fa, bars.low_u) AS low,
        COALESCE(bars.close_fa, bars.close_u) AS close
    FROM eod_bars bars
    WHERE bars.instrument_id IN :instrument_ids
      AND bars.dt_ny BETWEEN :start_date AND :end_date
    ORDER BY bars.instrument_id, bars.dt_ny
    """
).bindparams(bindparam("instrument_ids", expanding=True))


def _benchmark_instrument_id(db: Session, symbol: str = "SPY") -> int | None:
    return db.execute(
        text(
            """
            SELECT i.id
            FROM instruments i
            WHERE i.ticker_canonical = :symbol
               OR EXISTS (
                   SELECT 1 FROM symbol_history sh
                   WHERE sh.instrument_id = i.id AND sh.symbol = :symbol
               )
            ORDER BY i.is_active DESC, i.id
            LIMIT 1
            """
        ),
        {"symbol": symbol},
    ).scalar_one_or_none()


def _load_bars(
    db: Session,
    instrument_ids: Iterable[int],
    start_date: date,
    end_date: date,
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(value) for value in instrument_ids})
    if not ids:
        return {}
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in db.execute(
        BAR_SQL,
        {"instrument_ids": ids, "start_date": start_date, "end_date": end_date},
    ).mappings():
        result[int(row["instrument_id"])].append(dict(row))
    return dict(result)


def _daily_returns(rows: list[dict[str, Any]]) -> dict[date, float]:
    result: dict[date, float] = {}
    previous: float | None = None
    for row in rows:
        close = _finite(row.get("close"))
        if close is not None and previous and previous > 0:
            result[row["dt_ny"]] = (close / previous) - 1
        if close is not None:
            previous = close
    return result


def _trailing_beta(
    stock_rows: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
    event_date: date,
) -> float | None:
    stock_returns = _daily_returns([row for row in stock_rows if row["dt_ny"] < event_date])
    benchmark_returns = _daily_returns([row for row in benchmark_rows if row["dt_ny"] < event_date])
    dates = sorted(set(stock_returns) & set(benchmark_returns))[-252:]
    if len(dates) < 126:
        return None
    stock = [stock_returns[item] for item in dates]
    benchmark = [benchmark_returns[item] for item in dates]
    benchmark_mean = mean(benchmark)
    stock_mean = mean(stock)
    variance = sum((value - benchmark_mean) ** 2 for value in benchmark)
    if variance <= 0:
        return None
    covariance = sum(
        (benchmark[index] - benchmark_mean) * (stock[index] - stock_mean)
        for index in range(len(dates))
    )
    return covariance / variance


def _setup_from_signal(signal: Signal) -> str:
    features = signal.features if isinstance(signal.features, dict) else {}
    frozen = features.get("support_resistance") if isinstance(features, dict) else None
    return str((frozen or {}).get("selected_setup") or "unknown")


def _support_resistance_features(signal: Signal) -> dict[str, Any]:
    features = signal.features if isinstance(signal.features, dict) else {}
    frozen = features.get("support_resistance")
    return frozen if isinstance(frozen, dict) else {}


def _event_observations(
    db: Session,
    run_ids: list[Any],
    *,
    commission_bps: float = 1.0,
    slippage_bps: float = 2.0,
    dedupe_sessions: int = 40,
) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    signals = list(
        db.execute(
            select(Signal)
            .where(Signal.run_id.in_(run_ids), Signal.signal == "BUY", Signal.instrument_id.is_not(None))
            .order_by(Signal.instrument_id, Signal.ts, Signal.id)
        ).scalars()
    )
    if not signals:
        return []
    benchmark_id = _benchmark_instrument_id(db)
    instrument_ids = {int(item.instrument_id) for item in signals if item.instrument_id is not None}
    if benchmark_id is not None:
        instrument_ids.add(int(benchmark_id))
    earliest = min(item.ts.date() for item in signals) - timedelta(days=500)
    latest = max(item.ts.date() for item in signals) + timedelta(days=90)
    bars = _load_bars(db, instrument_ids, earliest, latest)
    benchmark_rows = bars.get(int(benchmark_id), []) if benchmark_id is not None else []
    benchmark_by_date = {row["dt_ny"]: row for row in benchmark_rows}
    round_trip_cost = 2.0 * (float(commission_bps) + float(slippage_bps)) / 10_000.0
    last_origin_index: dict[tuple[int, str], int] = {}
    observations: list[dict[str, Any]] = []
    for signal in signals:
        instrument_id = int(signal.instrument_id)
        stock_rows = bars.get(instrument_id, [])
        dates = [row["dt_ny"] for row in stock_rows]
        origin = signal.ts.date()
        origin_index = next((index for index, value in enumerate(dates) if value >= origin), None)
        if origin_index is None or origin_index + 1 >= len(stock_rows):
            continue
        setup = _setup_from_signal(signal)
        dedupe_key = (instrument_id, setup)
        previous_origin = last_origin_index.get(dedupe_key)
        if previous_origin is not None and origin_index - previous_origin < dedupe_sessions:
            continue
        last_origin_index[dedupe_key] = origin_index
        entry_index = origin_index + 1
        entry = _finite(stock_rows[entry_index].get("open"))
        if entry is None or entry <= 0:
            continue
        benchmark_entry_row = benchmark_by_date.get(stock_rows[entry_index]["dt_ny"])
        benchmark_entry = _finite((benchmark_entry_row or {}).get("open"))
        beta = _trailing_beta(stock_rows, benchmark_rows, origin)
        entry_atr = _finite(_support_resistance_features(signal).get("entry_atr"))
        first_hit: str | None = None
        first_hit_session: int | None = None
        if entry_atr is not None and entry_atr > 0:
            upper = entry + (3.0 * entry_atr)
            lower = entry - (1.5 * entry_atr)
            for session_offset, row in enumerate(stock_rows[entry_index : entry_index + 40], start=1):
                high = _finite(row.get("high"))
                low = _finite(row.get("low"))
                if low is not None and low <= lower:
                    first_hit, first_hit_session = "minus_1_5_atr", session_offset
                    break
                if high is not None and high >= upper:
                    first_hit, first_hit_session = "plus_3_atr", session_offset
                    break
        horizons: dict[str, Any] = {}
        for horizon in (1, 5, 10, 20, 40):
            exit_index = entry_index + horizon - 1
            if exit_index >= len(stock_rows):
                horizons[str(horizon)] = None
                continue
            exit_row = stock_rows[exit_index]
            exit_close = _finite(exit_row.get("close"))
            if exit_close is None:
                horizons[str(horizon)] = None
                continue
            stock_return = (exit_close / entry) - 1 - round_trip_cost
            benchmark_exit_row = benchmark_by_date.get(exit_row["dt_ny"])
            benchmark_exit = _finite((benchmark_exit_row or {}).get("close"))
            benchmark_return = (
                (benchmark_exit / benchmark_entry) - 1
                if benchmark_entry and benchmark_entry > 0 and benchmark_exit is not None
                else None
            )
            simple_excess = stock_return - benchmark_return if benchmark_return is not None else None
            beta_alpha = (
                stock_return - beta * benchmark_return
                if benchmark_return is not None and beta is not None
                else None
            )
            window = stock_rows[entry_index : exit_index + 1]
            highs = [_finite(row.get("high")) for row in window]
            lows = [_finite(row.get("low")) for row in window]
            highs = [value for value in highs if value is not None]
            lows = [value for value in lows if value is not None]
            horizons[str(horizon)] = {
                "return": stock_return,
                "benchmarkReturn": benchmark_return,
                "simpleExcess": simple_excess,
                "betaAdjustedAlpha": beta_alpha,
                "mfe": (max(highs) / entry) - 1 if highs else None,
                "mae": (min(lows) / entry) - 1 if lows else None,
            }
        observations.append(
            {
                "instrumentId": instrument_id,
                "symbol": signal.symbol,
                "setup": setup,
                "originDate": origin.isoformat(),
                "originYear": origin.year,
                "originMonth": origin.strftime("%Y-%m"),
                "beta": beta,
                "firstHit": first_hit,
                "firstHitSession": first_hit_session,
                "horizons": horizons,
            }
        )
    return observations


def _bootstrap_interval(
    observations: list[dict[str, Any]],
    *,
    horizon: int,
    seed: int = 20260828,
    replicates: int = 10_000,
) -> dict[str, Any]:
    metric_rows = [
        item
        for item in observations
        if isinstance((item.get("horizons") or {}).get(str(horizon)), dict)
        and _finite(item["horizons"][str(horizon)].get("betaAdjustedAlpha")) is not None
    ]
    if not metric_rows:
        return {"count": 0, "mean": None, "lower95": None, "upper95": None}
    blocks: dict[tuple[str, int], list[float]] = defaultdict(list)
    for item in metric_rows:
        blocks[(str(item["originMonth"]), int(item["instrumentId"]))].append(
            float(item["horizons"][str(horizon)]["betaAdjustedAlpha"])
        )
    block_values = [mean(values) for _key, values in sorted(blocks.items())]
    observed_mean = mean(
        float(item["horizons"][str(horizon)]["betaAdjustedAlpha"])
        for item in metric_rows
    )
    rng = random.Random(seed)
    samples = sorted(
        mean(rng.choices(block_values, k=len(block_values)))
        for _ in range(replicates)
    )
    lower_index = max(0, int(replicates * 0.025) - 1)
    upper_index = min(replicates - 1, int(replicates * 0.975))
    return {
        "count": len(metric_rows),
        "blockCount": len(block_values),
        "mean": observed_mean,
        "lower95": samples[lower_index],
        "upper95": samples[upper_index],
        "pValue": min(
            1.0,
            2.0 * min(
                sum(value <= 0 for value in samples) / replicates,
                sum(value >= 0 for value in samples) / replicates,
            ),
        ),
        "seed": seed,
        "replicates": replicates,
    }


def _benjamini_hochberg(p_values: dict[str, float], q: float = 0.10) -> dict[str, Any]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    count = len(ordered)
    largest_rank = 0
    for rank, (_name, value) in enumerate(ordered, start=1):
        if value <= q * rank / max(1, count):
            largest_rank = rank
    rejected = {name for name, _value in ordered[:largest_rank]}
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank, (name, value) in reversed(list(enumerate(ordered, start=1))):
        running = min(running, value * count / rank)
        adjusted[name] = min(1.0, running)
    return {
        "q": q,
        "tests": {
            name: {"pValue": value, "adjustedPValue": adjusted[name], "rejected": name in rejected}
            for name, value in sorted(p_values.items())
        },
    }


def event_study(
    db: Session,
    run_ids: list[Any],
    *,
    seed: int = 20260828,
    replicates: int = 10_000,
) -> dict[str, Any]:
    observations = _event_observations(db, run_ids)
    summaries: dict[str, Any] = {}
    for horizon in (1, 5, 10, 20, 40):
        interval = _bootstrap_interval(
            observations,
            horizon=horizon,
            seed=seed + horizon,
            replicates=replicates,
        )
        horizon_rows = [
            item["horizons"][str(horizon)]
            for item in observations
            if isinstance((item.get("horizons") or {}).get(str(horizon)), dict)
        ]
        interval["mfeMean"] = mean(
            value
            for row in horizon_rows
            if (value := _finite(row.get("mfe"))) is not None
        ) if any(_finite(row.get("mfe")) is not None for row in horizon_rows) else None
        interval["maeMean"] = mean(
            value
            for row in horizon_rows
            if (value := _finite(row.get("mae"))) is not None
        ) if any(_finite(row.get("mae")) is not None for row in horizon_rows) else None
        summaries[str(horizon)] = interval
    multiplicity = _benjamini_hochberg(
        {
            horizon: float(item["pValue"])
            for horizon, item in summaries.items()
            if _finite(item.get("pValue")) is not None
        },
        q=0.10,
    )
    annual_counts: dict[str, int] = defaultdict(int)
    setup_counts: dict[str, int] = defaultdict(int)
    first_hit_counts: dict[str, int] = defaultdict(int)
    for item in observations:
        annual_counts[str(item["originYear"])] += 1
        setup_counts[str(item["setup"])] += 1
        first_hit_counts[str(item.get("firstHit") or "neither")] += 1
    return {
        "dedupeSessions": 40,
        "eventCount": len(observations),
        "annualCounts": dict(sorted(annual_counts.items())),
        "setupCounts": dict(sorted(setup_counts.items())),
        "firstHitCounts": dict(sorted(first_hit_counts.items())),
        "horizons": summaries,
        "benjaminiHochberg": multiplicity,
    }


def _candidate_run_ids_across_validation(
    parent: ResearchExperiment,
    params_hash: str,
) -> list[Any]:
    run_ids: list[Any] = []
    for child in parent.child_experiments:
        phase = str((child.spec or {}).get("validationPhase") or "")
        if not (phase.startswith("annual_") or phase == "final_holdout"):
            continue
        candidate = next((item for item in child.candidates if item.params_hash == params_hash), None)
        if candidate is None:
            continue
        trial = _oos_trial(candidate, "base")
        if trial and trial.backtest_run_id:
            run_ids.append(trial.backtest_run_id)
    return run_ids


def _annual_fold_evidence(parent: ResearchExperiment, params_hash: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for child in parent.child_experiments:
        phase = str((child.spec or {}).get("validationPhase") or "")
        if not phase.startswith("annual_"):
            continue
        candidate = next((item for item in child.candidates if item.params_hash == params_hash), None)
        if candidate is None:
            continue
        result.append({"phase": phase, **_candidate_payload(candidate)})
    return sorted(result, key=lambda item: item["phase"])


def _equity_drawdown_series(db: Session, run_id: Any) -> list[dict[str, Any]]:
    snapshots = list(
        db.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.run_id == run_id)
            .order_by(PortfolioSnapshot.ts)
        ).scalars()
    )
    if not snapshots:
        return []
    stride = max(1, len(snapshots) // 300)
    selected = snapshots[::stride]
    if selected[-1].id != snapshots[-1].id:
        selected.append(snapshots[-1])
    return [
        {
            "date": item.ts.date().isoformat(),
            "equity": _finite(item.equity),
            "drawdown": _finite(item.drawdown),
            "benchmarkEquity": _finite((item.metrics or {}).get("benchmark_equity")),
        }
        for item in selected
    ]


def _candidate_acceptance(
    db: Session,
    parent: ResearchExperiment,
    candidate: ExperimentCandidate,
    *,
    seed: int,
    replicates: int,
) -> dict[str, Any]:
    payload = _candidate_payload(candidate)
    base = payload.get("base") or {}
    stress = payload.get("stress") or {}
    annual = _annual_fold_evidence(parent, candidate.params_hash)
    event = event_study(
        db,
        _candidate_run_ids_across_validation(parent, candidate.params_hash),
        seed=seed,
        replicates=replicates,
    )
    audit = cache_audit(db, candidate)
    regimes = (
        regime_audit(db, payload["baseRunId"])
        if payload.get("baseRunId")
        else {
            "regimeVersionCount": 0,
            "coverageSessions": {},
            "durationSessions": {},
            "transitionCounts": {},
            "candidateCounts": {},
            "admittedCounts": {},
            "rejectionCounts": {},
            "filledCounts": {},
            "realizedReturns": {},
            "downtrendExitCount": 0,
            "downtrendExitPerformance": {
                "exits": [],
                "meanRealizedReturn": None,
                "meanPostExitReturn20": None,
                "meanAvoidedDrawdownProxy20": None,
            },
            "timelineIntegrity": {
                "overlapCount": 0,
                "gapCount": 0,
                "duplicateDateCount": 0,
                "adjacentSameCount": 0,
                "unalignedTransitionCount": 0,
                "exactCoverage": False,
            },
        }
    )
    equity_curve = (
        _equity_drawdown_series(db, payload["baseRunId"])
        if payload.get("baseRunId")
        else []
    )
    base_excess = _finite(base.get("excess_return"))
    stress_excess = _finite(stress.get("excess_return"))
    drawdown = _finite(base.get("max_drawdown"))
    benchmark_drawdown = _finite(base.get("benchmark_max_drawdown"))
    concentration = _finite(base.get("pnl_concentration"))
    alpha20 = ((event.get("horizons") or {}).get("20") or {}).get("lower95")
    annual_counts = event.get("annualCounts") or {}
    gates = {
        "finalBaseExcessPositive": base_excess is not None and base_excess > 0,
        "finalStressExcessPositive": stress_excess is not None and stress_excess > 0,
        "drawdownWithinFivePoints": (
            drawdown is not None
            and benchmark_drawdown is not None
            and drawdown <= benchmark_drawdown + 0.05
        ),
        "eventAlphaLowerBoundNonNegative": _finite(alpha20) is not None and float(alpha20) >= 0,
        "eventCountAtLeast200": int(event.get("eventCount") or 0) >= 200,
        "annualEventCountsAtLeast50": all(int(annual_counts.get(str(year), 0)) >= 50 for year in (2021, 2022, 2023)),
        "pnlConcentrationAtMost20Pct": concentration is not None and concentration <= 0.20,
        "annualFoldExcessPositive": len(annual) == 3 and all(
            _finite((row.get("base") or {}).get("excess_return")) is not None
            and float(row["base"]["excess_return"]) > 0
            for row in annual
        ),
        "cacheAuditEquivalent": bool(audit.get("equivalent")),
        "regimeTimelineIntegrity": bool(
            (regimes.get("timelineIntegrity") or {}).get("exactCoverage")
        ),
    }
    payload.update(
        {
            "annualFolds": annual,
            "eventStudy": event,
            "cacheAudit": audit,
            "regimeAudit": regimes,
            "equityDrawdown": equity_curve,
            "acceptanceGates": gates,
            "passed": all(gates.values()),
        }
    )
    return payload


def build_validation_report(db: Session, parent: ResearchExperiment) -> dict[str, Any]:
    protocol = (parent.spec or {}).get("validationProtocol") or {}
    final_child = next(
        (
            item
            for item in parent.child_experiments
            if (item.spec or {}).get("validationPhase") == "final_holdout"
        ),
        None,
    )
    children = [
        {
            "id": str(item.id),
            "phase": (item.spec or {}).get("validationPhase"),
            "studyKind": item.study_kind,
            "status": item.status,
            "progress": item.progress or {},
            "report": item.report or {},
        }
        for item in parent.child_experiments
    ]
    candidates: list[dict[str, Any]] = []
    if final_child is not None:
        candidates = [
            _candidate_acceptance(
                db,
                parent,
                candidate,
                seed=int(protocol.get("bootstrapSeed") or 20260828),
                replicates=int(protocol.get("bootstrapReplicates") or 10_000),
            )
            for candidate in final_child.candidates
        ]
    default = next(
        (item for item in candidates if "all-mode" in str(item.get("rationale") or "")),
        candidates[0] if candidates else None,
    )
    calibrated = next((item for item in candidates if item is not default), None)
    if default and default.get("passed"):
        decision = "validated"
        validated_candidate_source = "default"
    elif calibrated and calibrated.get("passed"):
        decision = "validated"
        validated_candidate_source = "calibrated"
    elif final_child is None or final_child.status == "cancelled" or not candidates:
        decision = "inconclusive"
        validated_candidate_source = None
    else:
        decision = "not_validated"
        validated_candidate_source = None
    chart_data = {
        "equityAndDrawdown": [
            {"paramsHash": item.get("paramsHash"), "series": item.get("equityDrawdown") or []}
            for item in candidates
        ],
        "annualExcessReturn": [
            {
                "paramsHash": item.get("paramsHash"),
                "values": [
                    {
                        "phase": fold.get("phase"),
                        "base": (fold.get("base") or {}).get("excess_return"),
                        "stress": (fold.get("stress") or {}).get("excess_return"),
                    }
                    for fold in item.get("annualFolds") or []
                ],
            }
            for item in candidates
        ],
        "costDecay": [
            {
                "paramsHash": item.get("paramsHash"),
                "base": (item.get("base") or {}).get("excess_return"),
                "stress": (item.get("stress") or {}).get("excess_return"),
            }
            for item in candidates
        ],
        "eventAlphaMfeMae": [
            {"paramsHash": item.get("paramsHash"), "horizons": (item.get("eventStudy") or {}).get("horizons") or {}}
            for item in candidates
        ],
        "regimeCoverage": [
            {"paramsHash": item.get("paramsHash"), "audit": item.get("regimeAudit") or {}}
            for item in candidates
        ],
        "parameterMatrix": [
            {"paramsHash": item.get("paramsHash"), "overrides": item.get("overrides") or {}, "passed": item.get("passed")}
            for item in candidates
        ],
        "pnlConcentration": [
            {"paramsHash": item.get("paramsHash"), "value": (item.get("base") or {}).get("pnl_concentration")}
            for item in candidates
        ],
        "universeMembership": [
            {
                "paramsHash": item.get("paramsHash"),
                "annual": (((item.get("base") or {}).get("universe_membership") or {}).get("annual") or {}),
            }
            for item in candidates
        ],
    }
    return {
        "schemaVersion": 1,
        "studyId": str(parent.id),
        "studyKind": parent.study_kind,
        "status": parent.status,
        "decision": decision,
        "validatedCandidateSource": validated_candidate_source,
        "disclaimer": REPORT_DISCLAIMER,
        "hypothesis": (parent.spec or {}).get("hypothesis"),
        "protocol": protocol,
        "protocolHash": (parent.run_manifest or {}).get("protocolHash"),
        "universe": {
            "policy": (parent.spec or {}).get("universePolicy"),
            "membershipSemantics": "point_in_time_liquid",
        },
        "backtestBudget": (parent.run_manifest or {}).get("backtestBudget"),
        "sealedHoldout": (parent.run_manifest or {}).get("sealedHoldout"),
        "modeChampions": (parent.run_manifest or {}).get("modeChampions") or {},
        "frozenChampion": (parent.run_manifest or {}).get("frozenChampion"),
        "children": children,
        "finalCandidates": candidates,
        "charts": chart_data,
        "limitations": [
            "The dynamic universe is based only on fields available at the signal close.",
            "Delistings without modeled consideration use zero recovery; last close is reported only as an upper-bound sensitivity.",
            "A separate same-cost final-holdout replay must match events, signals, transactions, positions, and NAV exactly.",
            "No result authorizes portfolio activation, scheduling, or order submission.",
        ],
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _number(value: Any, *, percent: bool = False) -> str:
    finite = _finite(value)
    if finite is None:
        return "N/A"
    return f"{finite * 100:.2f}%" if percent else f"{finite:.4f}"


def render_markdown(report: dict[str, Any], language: str) -> str:
    zh = language == "zh-CN"
    title = "支撑/压力区策略有效性验证报告" if zh else "Support/Resistance Strategy Effectiveness Report"
    lines = [f"# {title}", "", f"> {_localized(report['disclaimer'], zh)}", ""]
    sections = [
        ("1. 执行摘要与最终判定", "1. Executive summary and final decision", f"`{report['decision']}`\n\n{report.get('hypothesis') or 'N/A'}"),
        ("2. 预注册假设、通过线和协议哈希", "2. Pre-registered hypothesis, gates, and protocol hash", f"Protocol hash: `{report.get('protocolHash') or 'N/A'}`\n\n```json\n{json.dumps(report.get('protocol') or {}, ensure_ascii=False, indent=2, sort_keys=True)}\n```"),
        ("3. 数据覆盖、质量检查和动态股票池", "3. Data coverage, quality, and dynamic universe", f"Membership: `point_in_time_liquid`\n\n```json\n{json.dumps(report.get('universe') or {}, ensure_ascii=False, indent=2, sort_keys=True)}\n```"),
        ("4. 偏差控制与统计方法", "4. Bias controls and statistical methods", "T-close membership; T+1-open fills; 40-session de-duplication; month/instrument block bootstrap; sealed final holdout."),
        ("5. 三种 setup 与四类区间研究", "5. Three-setup and four-regime study", f"```json\n{json.dumps([{'paramsHash': item.get('paramsHash'), 'eventStudy': item.get('eventStudy'), 'regimeAudit': item.get('regimeAudit')} for item in report.get('finalCandidates') or []], ensure_ascii=False, indent=2, sort_keys=True)}\n```"),
        ("6. 参数搜索、Pareto 与参数敏感性", "6. Parameter search, Pareto, and sensitivity", f"```json\n{json.dumps({'modeChampions': report.get('modeChampions'), 'frozenChampion': report.get('frozenChampion'), 'parameterMatrix': (report.get('charts') or {}).get('parameterMatrix')}, ensure_ascii=False, indent=2, sort_keys=True)}\n```"),
        ("7. 2021–2023 年度样本外结果", "7. 2021–2023 annual out-of-sample results", "\n".join(f"- `{item.get('phase')}`: `{item.get('status')}`" for item in report.get('children') or [] if str(item.get('phase') or '').startswith('annual_')) or "N/A"),
    ]
    for zh_heading, en_heading, body in sections:
        lines.extend([f"## {zh_heading if zh else en_heading}", "", str(body), ""])
    lines.extend(["## " + ("8. 2024–2026 最终留出结果" if zh else "8. 2024–2026 final holdout results"), ""])
    for item in report.get("finalCandidates") or []:
        lines.extend(
            [
                f"### {_localized(item.get('rationale') or item.get('paramsHash'), zh)}",
                "",
                f"- {'通过' if zh else 'Passed'}: `{bool(item.get('passed'))}`",
                f"- {'基础成本超额收益' if zh else 'Base excess return'}: {_number((item.get('base') or {}).get('excess_return'), percent=True)}",
                f"- {'压力成本超额收益' if zh else 'Stress excess return'}: {_number((item.get('stress') or {}).get('excess_return'), percent=True)}",
                f"- {'最大回撤' if zh else 'Maximum drawdown'}: {_number((item.get('base') or {}).get('max_drawdown'), percent=True)}",
                f"- {'去重事件数' if zh else 'Deduplicated events'}: {(item.get('eventStudy') or {}).get('eventCount', 0)}",
                "",
                "```json",
                json.dumps(item.get("acceptanceGates") or {}, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.extend(["## " + ("9. 成本、回撤、集中度与退市敏感性" if zh else "9. Costs, drawdown, concentration, and delisting sensitivity"), "", "```json", json.dumps((report.get("charts") or {}).get("costDecay") or [], ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    lines.extend(["## " + ("10. 冷/热缓存一致性" if zh else "10. Cold/hot cache consistency"), "", "```json", json.dumps([item.get("cacheAudit") for item in report.get("finalCandidates") or []], ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    lines.extend(["## " + ("11. 失败 trial、限制条件和不可外推事项" if zh else "11. Failed trials, limitations, and non-extrapolation"), ""])
    lines.extend(f"- {_localized(item, zh)}" for item in report.get("limitations") or [])
    lines.extend(["", "## " + ("12. 运行与参数附录" if zh else "12. Run and parameter appendix"), "", "```json", json.dumps({"studyId": report.get("studyId"), "protocol": report.get("protocol"), "budget": report.get("backtestBudget"), "children": [{"id": item.get("id"), "phase": item.get("phase"), "status": item.get("status")} for item in report.get("children") or []]}, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def _pdf_font() -> tuple[str, str]:
    from importlib.resources import files
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    configured = os.getenv("REPORT_FONT_PATH")
    candidates = [
        configured,
        str(Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansSC-Regular.ttf"),
        str(files("scifont").joinpath("fonts/NotoSansSC-VariableFont_wght.ttf")),
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            pdfmetrics.registerFont(TTFont("ValidationUnicode", candidate))
            return "ValidationUnicode", "embedded-truetype"
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "reportlab-cid-fallback"


def render_pdf(report: dict[str, Any], path: Path, language: str) -> dict[str, Any]:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    font_name, font_mode = _pdf_font()
    zh = language == "zh-CN"
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ValidationTitle", parent=styles["Title"], fontName=font_name, fontSize=20, leading=26, alignment=TA_CENTER, textColor=colors.HexColor("#0f172a")))
    styles.add(ParagraphStyle(name="ValidationH1", parent=styles["Heading1"], fontName=font_name, fontSize=14, leading=19, spaceBefore=10, textColor=colors.HexColor("#0f4c5c")))
    styles.add(ParagraphStyle(name="ValidationBody", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=14, textColor=colors.HexColor("#1e293b")))
    path.parent.mkdir(parents=True, exist_ok=True)

    def paragraph(value: Any, style: Any) -> Any:
        return Paragraph(escape(str(value)), style)

    def bar_chart(title_text: str, categories: list[str], series: list[list[float]]) -> Any:
        drawing = Drawing(165 * mm, 70 * mm)
        drawing.add(String(5 * mm, 64 * mm, title_text, fontName=font_name, fontSize=10))
        chart = VerticalBarChart()
        chart.x = 12 * mm
        chart.y = 12 * mm
        chart.height = 45 * mm
        chart.width = 145 * mm
        chart.data = series or [[0.0]]
        chart.categoryAxis.categoryNames = categories or ["N/A"]
        chart.categoryAxis.labels.fontName = font_name
        chart.categoryAxis.labels.fontSize = 6
        chart.valueAxis.labels.fontName = font_name
        chart.valueAxis.labels.fontSize = 6
        chart.valueAxis.valueMin = min(0.0, min((min(values) for values in chart.data if values), default=0.0))
        chart.valueAxis.valueMax = max(0.01, max((max(values) for values in chart.data if values), default=0.01))
        chart.valueAxis.valueStep = max(0.005, (chart.valueAxis.valueMax - chart.valueAxis.valueMin) / 5)
        chart.bars[0].fillColor = colors.HexColor("#0891b2")
        if len(chart.data) > 1:
            chart.bars[1].fillColor = colors.HexColor("#f97316")
        drawing.add(chart)
        return drawing

    def line_chart(title_text: str, series: list[list[tuple[float, float]]]) -> Any:
        drawing = Drawing(165 * mm, 70 * mm)
        drawing.add(String(5 * mm, 64 * mm, title_text, fontName=font_name, fontSize=10))
        chart = LinePlot()
        chart.x = 12 * mm
        chart.y = 12 * mm
        chart.height = 45 * mm
        chart.width = 145 * mm
        chart.data = series
        chart.xValueAxis.labels.fontName = font_name
        chart.yValueAxis.labels.fontName = font_name
        chart.xValueAxis.labels.fontSize = 6
        chart.yValueAxis.labels.fontSize = 6
        chart.lines[0].strokeColor = colors.HexColor("#0891b2")
        if len(series) > 1:
            chart.lines[1].strokeColor = colors.HexColor("#f97316")
        drawing.add(chart)
        return drawing

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(18 * mm, 12 * mm, str(report.get("studyId") or ""))
        canvas.drawRightString(192 * mm, 12 * mm, f"{doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=20 * mm, title="Support/Resistance Validation", author="Quant Trading System")
    story: list[Any] = []
    title = "支撑/压力区策略有效性验证报告" if zh else "Support/Resistance Strategy Effectiveness Report"
    story.extend([paragraph(title, styles["ValidationTitle"]), Spacer(1, 8 * mm)])
    decision = str(report.get("decision") or "inconclusive")
    story.append(Table([[paragraph(("最终判定" if zh else "Final decision"), styles["ValidationBody"]), paragraph(decision, styles["ValidationBody"])]], colWidths=[45 * mm, 110 * mm], style=[("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#dbeafe")), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([Spacer(1, 5 * mm), paragraph(_localized(report.get("disclaimer") or "", zh), styles["ValidationBody"])])
    headings = [
        ("执行摘要" if zh else "Executive summary", report.get("hypothesis") or "N/A"),
        ("预注册协议" if zh else "Pre-registered protocol", f"Protocol hash: {report.get('protocolHash') or 'N/A'}"),
        ("数据与动态股票池" if zh else "Data and dynamic universe", "point_in_time_liquid"),
    ]
    for heading, body in headings:
        story.extend([paragraph(heading, styles["ValidationH1"]), paragraph(body, styles["ValidationBody"])])
    story.append(PageBreak())
    story.append(paragraph("最终候选" if zh else "Final candidates", styles["ValidationH1"]))
    table_data = [["候选" if zh else "Candidate", "通过" if zh else "Pass", "基础超额" if zh else "Base excess", "压力超额" if zh else "Stress excess", "回撤" if zh else "Drawdown", "事件" if zh else "Events"]]
    for item in report.get("finalCandidates") or []:
        table_data.append([
            _localized(item.get("rationale") or item.get("paramsHash") or "", zh)[:48],
            str(bool(item.get("passed"))),
            _number((item.get("base") or {}).get("excess_return"), percent=True),
            _number((item.get("stress") or {}).get("excess_return"), percent=True),
            _number((item.get("base") or {}).get("max_drawdown"), percent=True),
            str((item.get("eventStudy") or {}).get("eventCount", 0)),
        ])
    table = Table(table_data, repeatRows=1, colWidths=[56 * mm, 15 * mm, 26 * mm, 26 * mm, 24 * mm, 17 * mm])
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f4c5c")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")])]))
    story.append(table)
    candidate_labels = [str(item.get("paramsHash") or "")[:8] for item in report.get("finalCandidates") or []]
    cost_rows = (report.get("charts") or {}).get("costDecay") or []
    story.append(bar_chart(
        "基础/压力成本超额收益" if zh else "Base/stress excess return",
        candidate_labels,
        [
            [float(_finite(item.get("base")) or 0.0) for item in cost_rows],
            [float(_finite(item.get("stress")) or 0.0) for item in cost_rows],
        ],
    ))
    concentration_rows = (report.get("charts") or {}).get("pnlConcentration") or []
    story.append(bar_chart(
        "单标的 P&L 集中度" if zh else "Single-symbol P&L concentration",
        candidate_labels,
        [[float(_finite(item.get("value")) or 0.0) for item in concentration_rows]],
    ))
    story.append(PageBreak())
    if report.get("finalCandidates"):
        first_candidate = report["finalCandidates"][0]
        horizon_rows = ((first_candidate.get("eventStudy") or {}).get("horizons") or {})
        horizon_labels = ["1", "5", "10", "20", "40"]
        story.append(bar_chart(
            "事件 alpha 及 MFE/MAE" if zh else "Event alpha and MFE/MAE",
            horizon_labels,
            [
                [float(_finite((horizon_rows.get(key) or {}).get("mean")) or 0.0) for key in horizon_labels],
                [float(_finite((horizon_rows.get(key) or {}).get("lower95")) or 0.0) for key in horizon_labels],
            ],
        ))
        annual = first_candidate.get("annualFolds") or []
        story.append(bar_chart(
            "年度超额收益" if zh else "Annual excess return",
            [str(item.get("phase") or "").replace("annual_", "") for item in annual],
            [[float(_finite((item.get("base") or {}).get("excess_return")) or 0.0) for item in annual]],
        ))
        equity = first_candidate.get("equityDrawdown") or []
        if equity:
            initial_equity = _finite(equity[0].get("equity")) or 1.0
            initial_benchmark = _finite(equity[0].get("benchmarkEquity")) or 1.0
            story.append(line_chart(
                "策略与 SPY 净值" if zh else "Strategy and SPY normalized equity",
                [
                    [(float(index), float(_finite(row.get("equity")) or initial_equity) / initial_equity) for index, row in enumerate(equity)],
                    [(float(index), float(_finite(row.get("benchmarkEquity")) or initial_benchmark) / initial_benchmark) for index, row in enumerate(equity)],
                ],
            ))
            story.append(line_chart(
                "策略回撤" if zh else "Strategy drawdown",
                [[(float(index), float(_finite(row.get("drawdown")) or 0.0)) for index, row in enumerate(equity)]],
            ))
        universe_annual = ((((first_candidate.get("base") or {}).get("universe_membership") or {}).get("annual") or {}))
        if universe_annual:
            universe_years = sorted(universe_annual)
            story.append(bar_chart(
                "动态股票池规模" if zh else "Dynamic universe size",
                universe_years,
                [[float(_finite((universe_annual[year] or {}).get("eligible_average")) or 0.0) for year in universe_years]],
            ))
    story.append(PageBreak())
    for item in report.get("finalCandidates") or []:
        story.extend([Spacer(1, 4 * mm), paragraph(_localized(item.get("rationale") or item.get("paramsHash"), zh), styles["ValidationH1"])])
        gate_rows = [["门槛" if zh else "Gate", "通过" if zh else "Pass"]] + [
            [name, str(bool(passed))]
            for name, passed in sorted((item.get("acceptanceGates") or {}).items())
        ]
        gate_table = Table(gate_rows, repeatRows=1, colWidths=[125 * mm, 30 * mm])
        gate_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")])]))
        story.append(gate_table)
        parameter_rows = [["Parameter", "Value"]] + [
            [str(name), str(value)]
            for name, value in sorted((item.get("overrides") or {}).items())
        ]
        if len(parameter_rows) > 1:
            story.append(paragraph("参数矩阵与邻域稳定性" if zh else "Parameter matrix and neighborhood stability", styles["ValidationH1"]))
            parameter_table = Table(parameter_rows, repeatRows=1, colWidths=[105 * mm, 50 * mm])
            parameter_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 6), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f4c5c")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1"))]))
            story.append(parameter_table)
    story.append(PageBreak())
    story.append(paragraph("年度样本外与最终留出" if zh else "Annual out-of-sample and final holdout", styles["ValidationH1"]))
    child_data = [["阶段" if zh else "Phase", "状态" if zh else "Status", "实验 ID" if zh else "Experiment ID"]] + [[str(item.get("phase")), str(item.get("status")), str(item.get("id"))] for item in report.get("children") or []]
    child_table = Table(child_data, repeatRows=1, colWidths=[42 * mm, 28 * mm, 95 * mm])
    child_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 7), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(child_table)
    story.append(paragraph("限制与不可外推事项" if zh else "Limitations", styles["ValidationH1"]))
    for item in report.get("limitations") or []:
        story.append(paragraph(f"- {_localized(item, zh)}", styles["ValidationBody"]))
    story.append(paragraph("可复现附录" if zh else "Reproducibility appendix", styles["ValidationH1"]))
    appendix = {
        "studyId": report.get("studyId"),
        "protocol": report.get("protocol"),
        "budget": report.get("backtestBudget"),
    }
    for key, value in appendix.items():
        story.append(paragraph(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}", styles["ValidationBody"]))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return {"fontMode": font_mode}


def verify_pdf_artifact(path: Path, report: dict[str, Any], language: str) -> dict[str, Any]:
    from pypdf import PdfReader
    import pdfplumber

    reader = PdfReader(str(path), strict=True)
    if not reader.pages:
        raise ValueError(f"PDF has no pages: {path}")
    title = str((reader.metadata or {}).get("/Title") or "")
    if "Support/Resistance" not in title:
        raise ValueError(f"PDF title metadata is invalid: {path}")
    with pdfplumber.open(str(path)) as document:
        text_content = "\n".join(page.extract_text() or "" for page in document.pages)
    expected_decision = str(report.get("decision") or "inconclusive")
    if expected_decision not in text_content:
        raise ValueError(f"PDF final decision does not match report.json: {path}")
    expected_title = (
        "支撑/压力区策略有效性验证报告"
        if language == "zh-CN"
        else "Support/Resistance Strategy Effectiveness Report"
    )
    if expected_title not in text_content:
        raise ValueError(f"PDF visible title is missing: {path}")
    return {
        "pageCount": len(reader.pages),
        "metadataTitle": title,
        "textVerified": True,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_report_artifacts(report: dict[str, Any], repository_root: Path | None = None) -> dict[str, Any]:
    root = repository_root or Path(__file__).resolve().parents[3]
    study_id = str(report["studyId"])
    research_dir = root / "output" / "research" / study_id
    pdf_dir = root / "output" / "pdf"
    research_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": research_dir / "report.json",
        "markdownZh": research_dir / "report.zh-CN.md",
        "markdownEn": research_dir / "report.en-US.md",
        "pdfZh": pdf_dir / f"support-resistance-validation-{study_id}-zh-CN.pdf",
        "pdfEn": pdf_dir / f"support-resistance-validation-{study_id}-en-US.pdf",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    paths["markdownZh"].write_text(render_markdown(report, "zh-CN"), encoding="utf-8")
    paths["markdownEn"].write_text(render_markdown(report, "en-US"), encoding="utf-8")
    pdf_meta = {
        "zh": render_pdf(report, paths["pdfZh"], "zh-CN"),
        "en": render_pdf(report, paths["pdfEn"], "en-US"),
    }
    pdf_meta["zh"]["verification"] = verify_pdf_artifact(paths["pdfZh"], report, "zh-CN")
    pdf_meta["en"]["verification"] = verify_pdf_artifact(paths["pdfEn"], report, "en-US")
    return {
        "status": "generated_verified",
        "files": {
            key: {"path": str(path), "sha256": _sha256(path), "bytes": path.stat().st_size}
            for key, path in paths.items()
        },
        "pdf": pdf_meta,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def finalize_validation_report(db: Session, parent: ResearchExperiment) -> None:
    report = build_validation_report(db, parent)
    parent.report = report
    parent.status = "completed" if report["decision"] not in {"inconclusive"} else "partially_failed"
    parent.finished_at = datetime.now(UTC)
    manifest = dict(parent.run_manifest or {})
    try:
        artifacts = write_report_artifacts(report)
    except Exception as exc:
        artifacts = {
            "status": "report_generation_failed",
            "error": str(exc)[:2000],
            "failedAt": datetime.now(UTC).isoformat(),
        }
    manifest["reportArtifacts"] = artifacts
    parent.run_manifest = manifest
    db.commit()
