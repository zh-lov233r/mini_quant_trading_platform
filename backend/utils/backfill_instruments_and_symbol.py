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

UPSERT_INSTR = """
INSERT INTO instruments (
  share_class_figi, composite_figi, cik,
  ticker_canonical, exchange, asset_type, share_class, name, currency,
  listed_at, is_active, vendor_source
) VALUES (
  %(share_class_figi)s, %(composite_figi)s, %(cik)s,
  %(ticker)s, %(exchange)s, %(type)s, %(share_class)s, %(name)s, %(currency)s,
  %(list_date)s, %(active)s, 'massive'
)
ON CONFLICT (share_class_figi) DO UPDATE SET
  composite_figi   = COALESCE(EXCLUDED.composite_figi, instruments.composite_figi),
  cik              = COALESCE(EXCLUDED.cik, instruments.cik),
  ticker_canonical = EXCLUDED.ticker_canonical,
  exchange         = EXCLUDED.exchange,
  asset_type       = EXCLUDED.asset_type,
  share_class      = COALESCE(EXCLUDED.share_class, instruments.share_class),
  name             = COALESCE(EXCLUDED.name, instruments.name),
  currency         = COALESCE(EXCLUDED.currency, instruments.currency),
  listed_at        = COALESCE(EXCLUDED.listed_at, instruments.listed_at),
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


# 开新区间（如果当前没有同样的 open 匹配）
SQL_OPEN_NEW = """
INSERT INTO symbol_history (
  instrument_id, exchange, symbol, valid_from, valid_from_precision, is_primary, source
)
SELECT
  %(iid)s,
  %(exchange)s,
  %(symbol)s,
  %(start_date)s,
  %(valid_from_precision)s,
  NOT EXISTS (
    SELECT 1 FROM symbol_history sh_primary
    WHERE sh_primary.instrument_id = %(iid)s
      AND sh_primary.valid_to IS NULL
      AND sh_primary.is_primary
  ),
  'massive'
WHERE NOT EXISTS (
  SELECT 1 FROM symbol_history
  WHERE instrument_id = %(iid)s
    AND valid_to IS NULL
    AND exchange = %(exchange)s
    AND symbol   = %(symbol)s
)
AND NOT EXISTS (
  SELECT 1 FROM symbol_history sh_conflict
  WHERE sh_conflict.valid_to IS NULL
    AND sh_conflict.exchange = %(exchange)s
    AND sh_conflict.symbol = %(symbol)s
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
        "active":           it.get("active"),
        "market":           market,
        "locale":           locale,
    }


def is_supported_common_stock(row: dict) -> bool:
    return (
        bool(row["share_class_figi"])
        and row["type"] in SUPPORTED_ASSET_TYPES
        and row["market"] == "stocks"
        and row["locale"] == "us"
    )

async def backfill():
    if not API or not URL:
        raise SystemExit("Got empty MASSIVE_API_KEY or DATABASE_URL")
    
    headers = {"Authorization": f"Bearer {API}"}
    async with aiohttp.ClientSession(headers=headers) as sess:
        with psycopg.connect(URL) as conn:
            total = 0
            kept = 0
            with tqdm(desc="Upserting instruments + symbol_history", unit="rows") as pbar:
                # Pragmatic rebuild path:
                # seed current active symbols first, then pull inactive symbols to improve
                # historical flat-file coverage. Unknown starts remain marked explicitly.
                for active_flag in ["true", "false"]:
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
                                row["is_common_stock"] = is_supported_common_stock(row)
                                cur.execute(UPSERT_SYMBOL_REFERENCE, row)
                                if not row["is_common_stock"]:
                                    continue

                                # 1. instruments UPSERT
                                cur.execute(UPSERT_INSTR, row)
                                iid = cur.fetchone()[0]
                                kept += 1

                                # 2. symbol_history 维护“当前区间”
                                start_date = row["list_date"] or UNKNOWN_VALID_FROM
                                params = {
                                    "iid": iid,
                                    "exchange": row["exchange"],
                                    "symbol": row["ticker"],
                                    "start_date": start_date,
                                    "valid_from_precision": "exact" if row["list_date"] else "unknown",
                                }
                                cur.execute(SQL_CLOSE_OLD, params)
                                cur.execute(SQL_OPEN_NEW, params)

                        conn.commit()
                        n = len(results); total += n; pbar.update(n)
                        next_url = js.get("next_url")   # 翻页用 next_url（无需再带 params）

            print(f"Done. Processed ~{total} tickers, kept {kept} common stocks.")

if __name__ == "__main__":
    asyncio.run(backfill())
