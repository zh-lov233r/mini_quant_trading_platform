from __future__ import annotations

import argparse
import math
import os
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

import psycopg
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]

BARS_SQL = """
SELECT
  instrument_id,
  dt_ny,
  COALESCE(close_fa, close_u) AS close_p,
  COALESCE(high_fa, high_u) AS high_p,
  COALESCE(low_fa, low_u) AS low_p,
  close_u,
  volume
FROM eod_bars
WHERE (%(start_date)s::date IS NULL OR dt_ny >= (%(start_date)s::date - 400))
  AND (%(end_date)s::date IS NULL OR dt_ny <= %(end_date)s::date)
  {instrument_filter}
ORDER BY instrument_id, dt_ny;
"""

CREATE_STAGE_SQL = """
CREATE TEMP TABLE daily_features_stage (
  instrument_id BIGINT NOT NULL,
  dt_ny DATE NOT NULL,
  ret_1d DOUBLE PRECISION,
  ret_5d DOUBLE PRECISION,
  ret_20d DOUBLE PRECISION,
  ret_60d DOUBLE PRECISION,
  ret_120d DOUBLE PRECISION,
  ret_252d DOUBLE PRECISION,
  sma_10 DOUBLE PRECISION,
  sma_20 DOUBLE PRECISION,
  sma_50 DOUBLE PRECISION,
  sma_100 DOUBLE PRECISION,
  sma_200 DOUBLE PRECISION,
  ema_12 DOUBLE PRECISION,
  ema_15 DOUBLE PRECISION,
  ema_20 DOUBLE PRECISION,
  ema_50 DOUBLE PRECISION,
  atr_14 DOUBLE PRECISION,
  volatility_20d DOUBLE PRECISION,
  volatility_60d DOUBLE PRECISION,
  rsi_2 DOUBLE PRECISION,
  rsi_5 DOUBLE PRECISION,
  rsi_14 DOUBLE PRECISION,
  zscore_5 DOUBLE PRECISION,
  zscore_10 DOUBLE PRECISION,
  zscore_20 DOUBLE PRECISION,
  bb_mid_20 DOUBLE PRECISION,
  bb_upper_20 DOUBLE PRECISION,
  bb_lower_20 DOUBLE PRECISION,
  bb_width_20 DOUBLE PRECISION,
  adv_20 DOUBLE PRECISION,
  adv_60 DOUBLE PRECISION,
  dollar_volume_20 DOUBLE PRECISION,
  rolling_high_20 DOUBLE PRECISION,
  rolling_high_55 DOUBLE PRECISION,
  rolling_low_20 DOUBLE PRECISION,
  PRIMARY KEY (instrument_id, dt_ny)
);
"""

COPY_STAGE_SQL = """
COPY daily_features_stage (
  instrument_id,
  dt_ny,
  ret_1d,
  ret_5d,
  ret_20d,
  ret_60d,
  ret_120d,
  ret_252d,
  sma_10,
  sma_20,
  sma_50,
  sma_100,
  sma_200,
  ema_12,
  ema_15,
  ema_20,
  ema_50,
  atr_14,
  volatility_20d,
  volatility_60d,
  rsi_2,
  rsi_5,
  rsi_14,
  zscore_5,
  zscore_10,
  zscore_20,
  bb_mid_20,
  bb_upper_20,
  bb_lower_20,
  bb_width_20,
  adv_20,
  adv_60,
  dollar_volume_20,
  rolling_high_20,
  rolling_high_55,
  rolling_low_20
) FROM STDIN
"""

UPSERT_SQL = """
INSERT INTO daily_features (
  instrument_id,
  dt_ny,
  ret_1d,
  ret_5d,
  ret_20d,
  ret_60d,
  ret_120d,
  ret_252d,
  sma_10,
  sma_20,
  sma_50,
  sma_100,
  sma_200,
  ema_12,
  ema_15,
  ema_20,
  ema_50,
  atr_14,
  volatility_20d,
  volatility_60d,
  rsi_2,
  rsi_5,
  rsi_14,
  zscore_5,
  zscore_10,
  zscore_20,
  bb_mid_20,
  bb_upper_20,
  bb_lower_20,
  bb_width_20,
  adv_20,
  adv_60,
  dollar_volume_20,
  rolling_high_20,
  rolling_high_55,
  rolling_low_20
)
SELECT
  instrument_id,
  dt_ny,
  ret_1d,
  ret_5d,
  ret_20d,
  ret_60d,
  ret_120d,
  ret_252d,
  sma_10,
  sma_20,
  sma_50,
  sma_100,
  sma_200,
  ema_12,
  ema_15,
  ema_20,
  ema_50,
  atr_14,
  volatility_20d,
  volatility_60d,
  rsi_2,
  rsi_5,
  rsi_14,
  zscore_5,
  zscore_10,
  zscore_20,
  bb_mid_20,
  bb_upper_20,
  bb_lower_20,
  bb_width_20,
  adv_20,
  adv_60,
  dollar_volume_20,
  rolling_high_20,
  rolling_high_55,
  rolling_low_20
FROM daily_features_stage
ON CONFLICT (instrument_id, dt_ny) DO UPDATE SET
  ret_1d = EXCLUDED.ret_1d,
  ret_5d = EXCLUDED.ret_5d,
  ret_20d = EXCLUDED.ret_20d,
  ret_60d = EXCLUDED.ret_60d,
  ret_120d = EXCLUDED.ret_120d,
  ret_252d = EXCLUDED.ret_252d,
  sma_10 = EXCLUDED.sma_10,
  sma_20 = EXCLUDED.sma_20,
  sma_50 = EXCLUDED.sma_50,
  sma_100 = EXCLUDED.sma_100,
  sma_200 = EXCLUDED.sma_200,
  ema_12 = EXCLUDED.ema_12,
  ema_15 = EXCLUDED.ema_15,
  ema_20 = EXCLUDED.ema_20,
  ema_50 = EXCLUDED.ema_50,
  atr_14 = EXCLUDED.atr_14,
  volatility_20d = EXCLUDED.volatility_20d,
  volatility_60d = EXCLUDED.volatility_60d,
  rsi_2 = EXCLUDED.rsi_2,
  rsi_5 = EXCLUDED.rsi_5,
  rsi_14 = EXCLUDED.rsi_14,
  zscore_5 = EXCLUDED.zscore_5,
  zscore_10 = EXCLUDED.zscore_10,
  zscore_20 = EXCLUDED.zscore_20,
  bb_mid_20 = EXCLUDED.bb_mid_20,
  bb_upper_20 = EXCLUDED.bb_upper_20,
  bb_lower_20 = EXCLUDED.bb_lower_20,
  bb_width_20 = EXCLUDED.bb_width_20,
  adv_20 = EXCLUDED.adv_20,
  adv_60 = EXCLUDED.adv_60,
  dollar_volume_20 = EXCLUDED.dollar_volume_20,
  rolling_high_20 = EXCLUDED.rolling_high_20,
  rolling_high_55 = EXCLUDED.rolling_high_55,
  rolling_low_20 = EXCLUDED.rolling_low_20,
  asof = now();
"""

TRUNCATE_STAGE_SQL = "TRUNCATE daily_features_stage;"


@dataclass(frozen=True)
class Bar:
    instrument_id: int
    dt_ny: date
    close_p: float | None
    high_p: float | None
    low_p: float | None
    close_u: float | None
    volume: int | None


class RollingStats:
    def __init__(self, window: int) -> None:
        self.window = window
        self.values: deque[float] = deque()
        self.sum = 0.0
        self.sum_sq = 0.0

    def push(self, value: float | None) -> None:
        if value is None or math.isnan(value):
            return
        self.values.append(value)
        self.sum += value
        self.sum_sq += value * value
        while len(self.values) > self.window:
            old = self.values.popleft()
            self.sum -= old
            self.sum_sq -= old * old

    def mean(self) -> float | None:
        if len(self.values) < self.window:
            return None
        return self.sum / self.window

    def std(self) -> float | None:
        if len(self.values) < self.window:
            return None
        mean = self.sum / self.window
        variance = (self.sum_sq / self.window) - mean * mean
        if variance < 0:
            variance = 0.0
        return math.sqrt(variance)

    def max(self) -> float | None:
        if len(self.values) < self.window:
            return None
        return max(self.values)

    def min(self) -> float | None:
        if len(self.values) < self.window:
            return None
        return min(self.values)


class RSIState:
    def __init__(self, period: int) -> None:
        self.period = period
        self.avg_gain: float | None = None
        self.avg_loss: float | None = None
        self.gains: list[float] = []
        self.losses: list[float] = []

    def update(self, delta: float | None) -> float | None:
        if delta is None:
            return None
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)

        if self.avg_gain is None or self.avg_loss is None:
            self.gains.append(gain)
            self.losses.append(loss)
            if len(self.gains) < self.period:
                return None
            if len(self.gains) > self.period:
                self.gains = self.gains[-self.period :]
                self.losses = self.losses[-self.period :]
            self.avg_gain = sum(self.gains) / self.period
            self.avg_loss = sum(self.losses) / self.period
        else:
            self.avg_gain = ((self.avg_gain * (self.period - 1)) + gain) / self.period
            self.avg_loss = ((self.avg_loss * (self.period - 1)) + loss) / self.period

        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill daily trend-following and mean-reversion features from eod_bars."
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Optional inclusive start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Optional inclusive end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URL"),
        help="Override DATABASE_URL / SQLALCHEMY_DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--batch-rows",
        type=int,
        default=50_000,
        help="How many feature rows to stage before upserting.",
    )
    parser.add_argument(
        "--instrument-limit",
        type=int,
        default=None,
        help="Optional number of instruments for smoke testing.",
    )
    parser.add_argument(
        "--instrument-id",
        type=int,
        action="append",
        default=None,
        help="Optional instrument_id filter. Repeat the flag to target multiple instruments.",
    )
    return parser.parse_args()


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _ret(close_now: float | None, close_then: float | None) -> float | None:
    if close_now is None or close_then is None or close_then == 0:
        return None
    return (close_now / close_then) - 1.0


def _ema(prev_ema: float | None, value: float | None, span: int) -> float | None:
    if value is None:
        return prev_ema
    if prev_ema is None:
        return value
    alpha = 2.0 / (span + 1.0)
    return (alpha * value) + ((1.0 - alpha) * prev_ema)


def _true_range(
    high_p: float | None,
    low_p: float | None,
    prev_close: float | None,
) -> float | None:
    if high_p is None or low_p is None:
        return None
    if prev_close is None:
        return high_p - low_p
    return max(
        high_p - low_p,
        abs(high_p - prev_close),
        abs(low_p - prev_close),
    )


def _compute_features_for_instrument(
    bars: list[Bar],
    start_date: date | None,
    end_date: date | None,
) -> list[tuple]:
    if not bars:
        return []

    close_hist: list[float | None] = []
    rolling_price = {n: RollingStats(n) for n in (5, 10, 20, 50, 100, 200)}
    rolling_volume = {n: RollingStats(n) for n in (20, 60)}
    rolling_dollar_volume = RollingStats(20)
    rolling_return = {n: RollingStats(n) for n in (20, 60)}
    rolling_high = {n: RollingStats(n) for n in (20, 55)}
    rolling_low = {20: RollingStats(20)}
    rolling_tr = RollingStats(14)
    rsi_state = {n: RSIState(n) for n in (2, 5, 14)}
    ema_state: dict[int, float | None] = {12: None, 15: None, 20: None, 50: None}
    rsi_value: dict[int, float | None] = {2: None, 5: None, 14: None}

    prev_close: float | None = None
    feature_rows: list[tuple] = []

    for idx, bar in enumerate(bars):
        close_hist.append(bar.close_p)

        delta = None if prev_close is None or bar.close_p is None else bar.close_p - prev_close
        ret_1d = _ret(bar.close_p, close_hist[idx - 1] if idx >= 1 else None)
        ret_5d = _ret(bar.close_p, close_hist[idx - 5] if idx >= 5 else None)
        ret_20d = _ret(bar.close_p, close_hist[idx - 20] if idx >= 20 else None)
        ret_60d = _ret(bar.close_p, close_hist[idx - 60] if idx >= 60 else None)
        ret_120d = _ret(bar.close_p, close_hist[idx - 120] if idx >= 120 else None)
        ret_252d = _ret(bar.close_p, close_hist[idx - 252] if idx >= 252 else None)

        if bar.close_p is not None:
            for stats in rolling_price.values():
                stats.push(bar.close_p)
            for span in ema_state:
                ema_state[span] = _ema(ema_state[span], bar.close_p, span)
            for span, level in rsi_state.items():
                rsi_value[span] = level.update(delta)

        high_value = bar.high_p if bar.high_p is not None else None
        low_value = bar.low_p if bar.low_p is not None else None
        if high_value is not None:
            rolling_high[20].push(high_value)
            rolling_high[55].push(high_value)
        if low_value is not None:
            rolling_low[20].push(low_value)

        if bar.volume is not None:
            rolling_volume[20].push(float(bar.volume))
            rolling_volume[60].push(float(bar.volume))
        if bar.close_u is not None and bar.volume is not None:
            rolling_dollar_volume.push(bar.close_u * float(bar.volume))

        tr = _true_range(bar.high_p, bar.low_p, prev_close)
        if tr is not None:
            rolling_tr.push(tr)

        if ret_1d is not None:
            rolling_return[20].push(ret_1d)
            rolling_return[60].push(ret_1d)

        sma_10 = rolling_price[10].mean()
        sma_20 = rolling_price[20].mean()
        sma_50 = rolling_price[50].mean()
        sma_100 = rolling_price[100].mean()
        sma_200 = rolling_price[200].mean()

        std_5 = rolling_price[5].std()
        std_10 = rolling_price[10].std()
        std_20 = rolling_price[20].std()
        mean_5 = rolling_price[5].mean()
        mean_10 = rolling_price[10].mean()
        mean_20 = rolling_price[20].mean()

        zscore_5 = None if bar.close_p is None or mean_5 is None or std_5 in (None, 0) else (bar.close_p - mean_5) / std_5
        zscore_10 = None if bar.close_p is None or mean_10 is None or std_10 in (None, 0) else (bar.close_p - mean_10) / std_10
        zscore_20 = None if bar.close_p is None or mean_20 is None or std_20 in (None, 0) else (bar.close_p - mean_20) / std_20

        bb_mid_20 = mean_20
        bb_upper_20 = None if mean_20 is None or std_20 is None else mean_20 + (2.0 * std_20)
        bb_lower_20 = None if mean_20 is None or std_20 is None else mean_20 - (2.0 * std_20)
        bb_width_20 = None if mean_20 in (None, 0) or bb_upper_20 is None or bb_lower_20 is None else (bb_upper_20 - bb_lower_20) / mean_20

        vol_20 = rolling_return[20].std()
        vol_60 = rolling_return[60].std()

        row = (
            bar.instrument_id,
            bar.dt_ny,
            ret_1d,
            ret_5d,
            ret_20d,
            ret_60d,
            ret_120d,
            ret_252d,
            sma_10,
            sma_20,
            sma_50,
            sma_100,
            sma_200,
            ema_state[12],
            ema_state[15],
            ema_state[20],
            ema_state[50],
            rolling_tr.mean(),
            vol_20,
            vol_60,
            rsi_value[2],
            rsi_value[5],
            rsi_value[14],
            zscore_5,
            zscore_10,
            zscore_20,
            bb_mid_20,
            bb_upper_20,
            bb_lower_20,
            bb_width_20,
            rolling_volume[20].mean(),
            rolling_volume[60].mean(),
            rolling_dollar_volume.mean(),
            rolling_high[20].max(),
            rolling_high[55].max(),
            rolling_low[20].min(),
        )

        if ((start_date is None or bar.dt_ny >= start_date) and
                (end_date is None or bar.dt_ny <= end_date)):
            feature_rows.append(row)

        prev_close = bar.close_p

    return feature_rows


def _iter_bars(
    conn: psycopg.Connection,
    start_date: str | None,
    end_date: str | None,
    instrument_ids: list[int] | None,
) -> Iterator[Bar]:
    with conn.cursor(name="bars_cursor") as cur:
        cur.itersize = 50_000
        instrument_filter = ""
        params: dict[str, object] = {
            "start_date": start_date,
            "end_date": end_date,
        }
        if instrument_ids:
            instrument_filter = "AND instrument_id = ANY(%(instrument_ids)s)"
            params["instrument_ids"] = instrument_ids

        cur.execute(
            BARS_SQL.format(instrument_filter=instrument_filter),
            params,
        )
        for row in cur:
            yield Bar(*row)


def _group_by_instrument(rows: Iterable[Bar]) -> Iterator[list[Bar]]:
    current_instrument: int | None = None
    current_rows: list[Bar] = []
    for row in rows:
        if current_instrument is None:
            current_instrument = row.instrument_id
        if row.instrument_id != current_instrument:
            yield current_rows
            current_rows = [row]
            current_instrument = row.instrument_id
        else:
            current_rows.append(row)
    if current_rows:
        yield current_rows


def _flush_stage(conn: psycopg.Connection, stage_rows: list[tuple]) -> None:
    if not stage_rows:
        return
    with conn.cursor() as cur:
        with cur.copy(COPY_STAGE_SQL) as copy:
            for row in stage_rows:
                copy.write_row(row)
        cur.execute(UPSERT_SQL)
        cur.execute(TRUNCATE_STAGE_SQL)
    conn.commit()


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Missing DATABASE_URL or SQLALCHEMY_DATABASE_URL")

    start_bound = date.fromisoformat(args.start_date) if args.start_date else None
    end_bound = date.fromisoformat(args.end_date) if args.end_date else None

    with psycopg.connect(_psycopg_dsn(args.database_url)) as read_conn, psycopg.connect(_psycopg_dsn(args.database_url)) as write_conn:
        with write_conn.cursor() as cur:
            cur.execute(CREATE_STAGE_SQL)
        write_conn.commit()

        staged_rows: list[tuple] = []
        processed_instruments = 0
        upserted_rows = 0

        bar_groups = _group_by_instrument(
            _iter_bars(
                read_conn,
                args.start_date,
                args.end_date,
                args.instrument_id,
            )
        )
        for bars in bar_groups:
            if args.instrument_limit is not None and processed_instruments >= args.instrument_limit:
                break
            processed_instruments += 1

            feature_rows = _compute_features_for_instrument(
                bars=bars,
                start_date=start_bound,
                end_date=end_bound,
            )
            staged_rows.extend(feature_rows)
            upserted_rows += len(feature_rows)

            if len(staged_rows) >= args.batch_rows:
                _flush_stage(write_conn, staged_rows)
                staged_rows.clear()
                print(
                    f"Processed instruments={processed_instruments} upserted_rows={upserted_rows}",
                    flush=True,
                )

        if staged_rows:
            _flush_stage(write_conn, staged_rows)
            print(
                f"Processed instruments={processed_instruments} upserted_rows={upserted_rows}",
                flush=True,
            )

    print(
        f"Daily feature backfill completed. instruments={processed_instruments} "
        f"upserted_rows={upserted_rows}",
        flush=True,
    )


if __name__ == "__main__":
    main()
