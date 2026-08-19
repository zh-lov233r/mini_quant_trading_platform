import asyncio
import os

import aiohttp
import psycopg
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
API = os.getenv("MASSIVE_API_KEY")
URL = os.getenv("DATABASE_URL")

BASE = "https://api.massive.com/v3/reference/tickers"
UNKNOWN_VALID_FROM = "1900-01-01"
SUPPORTED_ASSET_TYPES = {"CS"}
BENCHMARK_PROXY_ASSET_TYPES = {"ETF"}
BENCHMARK_PROXY_SYMBOLS = {"SPY", "QQQ"}
ACTIVE_SYNC_ORDER = ("false", "true")

UPSERT_INSTR = """
INSERT INTO instruments (
  share_class_figi, composite_figi, cik,
  ticker_canonical, exchange, asset_type, share_class, name, currency,
  listed_at, delisted_at, is_active, vendor_source
) VALUES (
  %(share_class_figi)s, %(composite_figi)s, %(cik)s,
  %(ticker)s, %(exchange)s, %(type)s, %(share_class)s, %(name)s, %(currency)s,
  %(list_date)s, %(delisted_at)s, %(active)s, 'massive'
)
ON CONFLICT (share_class_figi) DO UPDATE SET
  composite_figi   = COALESCE(EXCLUDED.composite_figi, instruments.composite_figi),
  cik              = COALESCE(EXCLUDED.cik, instruments.cik),
  ticker_canonical = CASE
    WHEN EXCLUDED.is_active THEN EXCLUDED.ticker_canonical
    ELSE instruments.ticker_canonical
  END,
  exchange         = EXCLUDED.exchange,
  asset_type       = EXCLUDED.asset_type,
  share_class      = COALESCE(EXCLUDED.share_class, instruments.share_class),
  name             = COALESCE(EXCLUDED.name, instruments.name),
  currency         = COALESCE(EXCLUDED.currency, instruments.currency),
  listed_at        = COALESCE(EXCLUDED.listed_at, instruments.listed_at),
  delisted_at      = CASE
    WHEN EXCLUDED.is_active THEN NULL
    ELSE COALESCE(EXCLUDED.delisted_at, instruments.delisted_at)
  END,
  is_active        = EXCLUDED.is_active
RETURNING id;
"""

UPSERT_SYMBOL_REFERENCE = """
INSERT INTO symbol_reference (
  symbol, exchange, asset_type, market, locale, is_common_stock, name, source
) VALUES (
  %(ticker)s, %(exchange)s, %(type)s, %(market)s, %(locale)s, %(is_common_stock)s, %(name)s, 'massive'
)
ON CONFLICT (symbol, exchange, asset_type) DO UPDATE SET
  market          = EXCLUDED.market,
  locale          = EXCLUDED.locale,
  is_common_stock = EXCLUDED.is_common_stock,
  name            = COALESCE(EXCLUDED.name, symbol_reference.name),
  source          = EXCLUDED.source,
  asof            = now();
"""

# 关旧区间（如果当前 open 的映射与新 symbol/exchange 不同）
# 只在 start_date 晚于旧区间开始日时才关旧
SQL_CLOSE_OLD = """
UPDATE symbol_history
SET valid_to = GREATEST(valid_from, %(start_date)s::date - 1)   -- 永远不早于 valid_from
WHERE instrument_id = %(iid)s
  AND valid_to IS NULL
  AND (exchange <> %(exchange)s OR symbol <> %(symbol)s)
  AND %(start_date)s::date > valid_from;                        -- 只有真的“后来者”才关旧
"""


# When a new active identity takes over a symbol, close any still-open owner on
# other instruments first. This prevents the same raw symbol from staying open
# on multiple instrument ids and breaking downstream symbol -> instrument
# resolution for market-data imports.
SQL_CLOSE_CONFLICTING_SYMBOL_OWNERS = """
UPDATE symbol_history
SET valid_to = CASE
    WHEN %(start_date)s::date > valid_from THEN %(start_date)s::date - 1
    ELSE valid_from
END
WHERE instrument_id <> %(iid)s
  AND symbol = %(symbol)s
  AND id IN (
    SELECT DISTINCT ON (instrument_id) id
    FROM symbol_history
    WHERE instrument_id <> %(iid)s
      AND symbol = %(symbol)s
    ORDER BY instrument_id, valid_from DESC, id DESC
  )
  AND (
    valid_to IS NULL
    OR (
      valid_to < %(start_date)s::date - 1
      AND EXISTS (
        SELECT 1
        FROM eod_bars
        WHERE instrument_id = symbol_history.instrument_id
          AND dt_ny > symbol_history.valid_to
          AND dt_ny < %(start_date)s::date
      )
      AND NOT EXISTS (
        SELECT 1
        FROM symbol_history other
        WHERE other.id <> symbol_history.id
          AND other.exchange = symbol_history.exchange
          AND other.symbol = symbol_history.symbol
          AND other.valid_from < %(start_date)s::date
          AND COALESCE(other.valid_to, DATE 'infinity') >= symbol_history.valid_from
      )
    )
  );
"""


SQL_EFFECTIVE_HANDOFF_START = """
SELECT GREATEST(
  %(start_date)s::date,
  COALESCE(MAX(e.dt_ny) + 1, %(start_date)s::date)
)
FROM instruments old
LEFT JOIN eod_bars e ON e.instrument_id = old.id
WHERE old.id <> %(iid)s
  AND old.ticker_canonical = %(symbol)s;
"""


# The vendor can remove FIGIs after a security is delisted. Those rows cannot be
# inserted through the FIGI-keyed upsert, but they still need to retire the
# existing instrument that was created while the FIGI was available.
SQL_DEACTIVATE_MISSING_FIGI = """
UPDATE instruments
SET is_active = FALSE,
    delisted_at = COALESCE(%(delisted_at)s::date, delisted_at),
    updated_at = now()
WHERE ticker_canonical = %(ticker)s
  AND exchange = %(exchange)s
  AND (%(cik)s::text IS NULL OR cik = %(cik)s::text)
RETURNING id;
"""


SQL_CLOSE_INACTIVE_SYMBOL = """
UPDATE symbol_history
SET valid_to = GREATEST(
      valid_from,
      COALESCE(
        %(delisted_at)s::date - 1,
        (SELECT MAX(dt_ny) FROM eod_bars WHERE instrument_id = %(iid)s),
        valid_from
      )
    )
WHERE instrument_id = %(iid)s
  AND valid_to IS NULL
  AND exchange = %(exchange)s
  AND symbol = %(symbol)s;
"""


SQL_CLOSE_ALL_INACTIVE_OPEN_SYMBOLS = """
UPDATE symbol_history sh
SET valid_to = GREATEST(
      sh.valid_from,
      COALESCE(
        i.delisted_at - 1,
        (SELECT MAX(dt_ny) FROM eod_bars WHERE instrument_id = i.id),
        sh.valid_from
      )
    )
FROM instruments i
WHERE i.id = sh.instrument_id
  AND NOT i.is_active
  AND sh.valid_to IS NULL;
"""


# A ticker can be reused by a genuinely new security. Once the current active
# identity is known, the old owner must no longer remain active or canonical.
SQL_RETIRE_CONFLICTING_CANONICAL_OWNERS = """
UPDATE instruments
SET ticker_canonical = NULL,
    is_active = FALSE,
    delisted_at = COALESCE(
      delisted_at,
      CASE
        WHEN %(start_date)s::date > DATE '1900-01-01'
          THEN %(start_date)s::date - 1
        ELSE NULL
      END
    ),
    updated_at = now()
WHERE id <> %(iid)s
  AND ticker_canonical = %(symbol)s;
"""


# 开新区间（如果当前没有同样的 open 匹配）
SQL_OPEN_NEW = """
INSERT INTO symbol_history (
  instrument_id, exchange, symbol, valid_from, valid_from_precision, is_primary, source
)
SELECT
  %(iid)s,
  %(exchange)s,
  %(symbol)s,
  CASE
    WHEN latest_same_symbol.latest_valid_to IS NOT NULL
      AND %(start_date)s::date <= latest_same_symbol.latest_valid_to
      THEN latest_same_symbol.latest_valid_to + 1
    ELSE %(start_date)s::date
  END,
  CASE
    WHEN latest_same_symbol.latest_valid_to IS NOT NULL
      AND %(start_date)s::date <= latest_same_symbol.latest_valid_to
      AND %(valid_from_precision)s <> 'exact'
      THEN 'inferred'
    ELSE %(valid_from_precision)s
  END,
  NOT EXISTS (
    SELECT 1 FROM symbol_history sh_primary
    WHERE sh_primary.instrument_id = %(iid)s
      AND sh_primary.valid_to IS NULL
      AND sh_primary.is_primary
  ),
  'massive'
FROM (
  SELECT MAX(valid_to) AS latest_valid_to
  FROM symbol_history
  WHERE (
    (exchange = %(exchange)s AND symbol = %(symbol)s)
    OR instrument_id = %(iid)s
  )
  AND valid_to IS NOT NULL
) latest_same_symbol
WHERE NOT EXISTS (
  SELECT 1 FROM symbol_history
  WHERE instrument_id = %(iid)s
    AND valid_to IS NULL
    AND exchange = %(exchange)s
    AND symbol   = %(symbol)s
)
AND (
  %(allow_reopen)s
  OR NOT EXISTS (
    SELECT 1 FROM symbol_history
    WHERE instrument_id = %(iid)s
      AND exchange = %(exchange)s
      AND symbol = %(symbol)s
  )
)
AND NOT EXISTS (
  SELECT 1 FROM symbol_history sh_conflict
  WHERE sh_conflict.valid_to IS NULL
    AND sh_conflict.symbol = %(symbol)s
    AND sh_conflict.instrument_id <> %(iid)s
);
"""

def norm_item(it: dict) -> dict:
    # 字段名以 Massive v3 为准；做一些容错与规格化
    ticker = (it.get("ticker") or "").upper().strip()
    exch = it.get("primary_exchange") or it.get("primary_exchange_sip") or "UNK"
    # v3 里 currency 可能叫 currency_name 或 currency
    cur = it.get("currency_name") or it.get("currency")
    cur = (cur or "USD").upper()
    asset_type = (it.get("type") or "CS").upper()
    market = (it.get("market") or "stocks").lower()
    locale = (it.get("locale") or "us").lower()

    delisted_at = it.get("delisted_utc")
    if delisted_at:
        delisted_at = str(delisted_at).split("T", 1)[0]

    return {
        "share_class_figi": it.get("share_class_figi"),
        "composite_figi":   it.get("composite_figi"),
        "cik":              it.get("cik"),
        "ticker":           ticker,
        "exchange":         exch,
        "type":             asset_type,
        "share_class":      it.get("share_class"),   # 可能为空
        "name":             it.get("name"),
        "currency":         cur,
        "list_date":        it.get("list_date"),     # ISO 日期或 None
        "delisted_at":      delisted_at,
        "active":           it.get("active"),
        "market":           market,
        "locale":           locale,
    }


def is_common_stock_reference(row: dict) -> bool:
    return (
        row["type"] in SUPPORTED_ASSET_TYPES
        and row["market"] == "stocks"
        and row["locale"] == "us"
    )


def is_supported_common_stock(row: dict) -> bool:
    return bool(row["share_class_figi"]) and is_common_stock_reference(row)


def is_supported_benchmark_proxy(row: dict) -> bool:
    return (
        bool(row["share_class_figi"])
        and row["ticker"] in BENCHMARK_PROXY_SYMBOLS
        and row["type"] in BENCHMARK_PROXY_ASSET_TYPES
        and row["market"] == "stocks"
        and row["locale"] == "us"
    )


def build_symbol_history_params(row: dict, *, instrument_id: int) -> dict[str, object]:
    start_date = row["list_date"] or UNKNOWN_VALID_FROM
    return {
        "iid": instrument_id,
        "exchange": row["exchange"],
        "symbol": row["ticker"],
        "start_date": start_date,
        "valid_from_precision": "exact" if row["list_date"] else "unknown",
        "allow_reopen": bool(row.get("active")),
    }


def should_close_conflicting_symbol_owners(row: dict) -> bool:
    return bool(row.get("active"))

async def backfill():
    if not API or not URL:
        raise SystemExit("Got empty MASSIVE_API_KEY or DATABASE_URL")
    
    headers = {"Authorization": f"Bearer {API}"}
    async with aiohttp.ClientSession(headers=headers) as sess:
        with psycopg.connect(URL) as conn:
            total = 0
            kept = 0
            with tqdm(desc="Upserting instruments + symbol_history", unit="rows") as pbar:
                # Historical identities must be loaded first. Current active rows
                # come last so an old ticker record sharing the same FIGI cannot
                # overwrite the current canonical identity (for example NVRI).
                for active_flag in ACTIVE_SYNC_ORDER:
                    next_url = BASE
                    params = {
                        "market": "stocks",
                        "active": active_flag,      # 只能是 "true"/"false"
                        "limit": 1000,
                        "sort": "ticker"
                    }
                    while next_url:
                        async with sess.get(
                            next_url,
                            params=params if next_url == BASE else None,
                            timeout=180
                        ) as r:
                            # 打印更清晰的错误信息
                            if r.status != 200:
                                body = await r.text()
                                raise RuntimeError(f"{r.status} {r.reason} | {next_url} | {body}")
                            js = await r.json()

                        results = js.get("results") or []
                        if not results:
                            break

                        with conn.cursor() as cur:
                            for raw in results:
                                row = norm_item(raw)
                                reference_is_common_stock = is_common_stock_reference(row)
                                # This flag represents the project's supported
                                # FIGI-backed common-stock universe, not every
                                # vendor row whose broad type happens to be CS.
                                row["is_common_stock"] = is_supported_common_stock(row)
                                cur.execute(UPSERT_SYMBOL_REFERENCE, row)

                                if (
                                    reference_is_common_stock
                                    and not row["share_class_figi"]
                                    and not row["active"]
                                ):
                                    cur.execute(SQL_DEACTIVATE_MISSING_FIGI, row)
                                    for (iid,) in cur.fetchall():
                                        params = build_symbol_history_params(
                                            row, instrument_id=iid
                                        )
                                        params["delisted_at"] = row["delisted_at"]
                                        cur.execute(SQL_CLOSE_INACTIVE_SYMBOL, params)
                                    continue

                                if not (
                                    is_supported_common_stock(row)
                                    or is_supported_benchmark_proxy(row)
                                ):
                                    continue

                                # 1. instruments UPSERT
                                cur.execute(UPSERT_INSTR, row)
                                iid = cur.fetchone()[0]
                                kept += 1

                                # 2. symbol_history 维护“当前区间”
                                params = build_symbol_history_params(row, instrument_id=iid)
                                if should_close_conflicting_symbol_owners(row):
                                    cur.execute(SQL_EFFECTIVE_HANDOFF_START, params)
                                    params["start_date"] = cur.fetchone()[0]
                                    cur.execute(
                                        SQL_RETIRE_CONFLICTING_CANONICAL_OWNERS,
                                        params,
                                    )
                                    cur.execute(SQL_CLOSE_CONFLICTING_SYMBOL_OWNERS, params)
                                cur.execute(SQL_CLOSE_OLD, params)
                                cur.execute(SQL_OPEN_NEW, params)
                                if not row["active"]:
                                    params["delisted_at"] = row["delisted_at"]
                                    cur.execute(SQL_CLOSE_INACTIVE_SYMBOL, params)

                        conn.commit()
                        n = len(results); total += n; pbar.update(n)
                        next_url = js.get("next_url")   # 翻页用 next_url（无需再带 params）

                with conn.cursor() as cur:
                    cur.execute(SQL_CLOSE_ALL_INACTIVE_OPEN_SYMBOLS)
                    closed_inactive = cur.rowcount
                conn.commit()

            print(
                "Done. "
                f"Processed ~{total} tickers, kept {kept} supported securities "
                f"(common stocks + benchmark proxies {sorted(BENCHMARK_PROXY_SYMBOLS)}); "
                f"closed {closed_inactive} inactive symbol intervals."
            )

if __name__ == "__main__":
    asyncio.run(backfill())
