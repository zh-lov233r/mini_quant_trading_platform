"""Publish market completion only after the owning ingestion pipeline passes its gates."""
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
import os
import psycopg
from psycopg.types.json import Jsonb
import requests


def dsn(url):return url.replace("postgresql+psycopg://","postgresql://",1)


MIN_MARKET_COVERAGE = 0.99


def coverage_is_ready(*, expected: int, present: int, bars: int, features: int,
                      adjusted: int, valid: int, stale: int) -> bool:
    return (expected > 0 and present / expected >= MIN_MARKET_COVERAGE
            and bars > 0 and bars == features == adjusted == valid and stale == 0)


def publish_ready(url, market, start, end):
    """Revalidate previously published days as well as the ingestion window/latest day."""
    currency = {"CN": "CNY", "US": "USD"}[market]
    with psycopg.connect(dsn(url)) as conn:
        days = conn.execute("""SELECT session_date FROM signal_data_ready WHERE market=%s
            UNION SELECT DISTINCT b.dt_ny FROM eod_bars b JOIN instruments i ON i.id=b.instrument_id
              WHERE i.currency=%s AND b.dt_ny BETWEEN %s AND %s
            UNION SELECT max(b.dt_ny) FROM eod_bars b JOIN instruments i ON i.id=b.instrument_id WHERE i.currency=%s
            ORDER BY 1""", (market, currency, start, end, currency)).fetchall()
        for (day,) in days:
            if day is None:
                continue
            conn.execute("UPDATE signal_data_ready SET valid=false WHERE market=%s AND session_date=%s", (market, day))
            zone = ZoneInfo("Asia/Shanghai" if market == "CN" else "America/New_York")
            session = conn.execute("SELECT is_open,closes_at FROM signal_market_sessions WHERE market=%s AND session_date=%s", (market,day)).fetchone()
            if (session and not session[0]) or day.weekday() >= 5:
                continue
            close_at = session[1] if session else datetime.combine(day,time(15 if market=="CN" else 16),zone)
            if close_at is None or datetime.now(timezone.utc) < close_at:
                continue
            bars, features, adjusted, valid, stale, latest = conn.execute("""SELECT count(*),count(f.instrument_id),
                count(*) FILTER (WHERE b.open_fa>0 AND b.high_fa>0 AND b.low_fa>0 AND b.close_fa>0),
                count(*) FILTER (WHERE b.high_fa>=GREATEST(b.open_fa,b.close_fa,b.low_fa)
                  AND b.low_fa<=LEAST(b.open_fa,b.close_fa,b.high_fa) AND b.volume>=0),
                count(*) FILTER (WHERE f.asof < b.asof),max(b.ts_utc)
                FROM eod_bars b JOIN instruments i ON i.id=b.instrument_id
                LEFT JOIN daily_features f ON f.instrument_id=b.instrument_id AND f.dt_ny=b.dt_ny
                WHERE i.currency=%s AND b.dt_ny=%s""", (currency, day)).fetchone()
            expected, present = conn.execute("""SELECT count(*),count(b.instrument_id)
                FROM instruments i LEFT JOIN eod_bars b ON b.instrument_id=i.id AND b.dt_ny=%s
                WHERE i.currency=%s AND (i.asset_type='CS' OR i.ticker_canonical IN ('SPY','QQQ'))
                  AND (i.listed_at IS NULL OR i.listed_at<=%s)
                  AND (i.delisted_at IS NULL OR i.delisted_at>=%s)
                  AND (EXISTS (SELECT 1 FROM eod_bars h WHERE h.instrument_id=i.id AND h.dt_ny BETWEEN %s::date-30 AND %s::date-1)
                    OR i.listed_at BETWEEN %s::date-30 AND %s)
                """, (day,currency,day,day,day,day,day,day)).fetchone()
            coverage = dict(expected=expected,present=present,bars=bars,features=features,adjusted=adjusted,valid=valid,stale=stale)
            if not coverage_is_ready(**coverage) or latest is None or latest>datetime.now(timezone.utc):
                print(f"Market readiness withheld: market={market} day={day} coverage={coverage}",flush=True)
                continue
            coverage.update(scope="market",minimum_ratio=MIN_MARKET_COVERAGE,missing_unclassified=expected-present)
            conn.execute("""INSERT INTO signal_data_ready(market,session_date,version,coverage,completed_at,valid)
                VALUES (%s,%s,%s,%s,NOW(),true) ON CONFLICT(market,session_date) DO UPDATE
                SET version=EXCLUDED.version,coverage=EXCLUDED.coverage,completed_at=NOW(),valid=true""",
                (market,day,str(uuid4()),Jsonb(coverage)))


def save_calendar(url,market,days,source):
    zone=ZoneInfo("Asia/Shanghai" if market=="CN" else "America/New_York")
    with psycopg.connect(dsn(url)) as conn:
        for day,opened,open_time,close_time in days:
            conn.execute("""INSERT INTO signal_market_sessions(market,session_date,is_open,opens_at,closes_at,source,fetched_at)
              VALUES(%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT(market,session_date) DO UPDATE SET
              is_open=EXCLUDED.is_open,opens_at=EXCLUDED.opens_at,closes_at=EXCLUDED.closes_at,source=EXCLUDED.source,fetched_at=NOW()""",
              (market,day,opened,open_time,close_time,source))


def sync_cn_calendar(url,client,start,end):
    zone=ZoneInfo("Asia/Shanghai")
    raw=client.query("trade_cal",params={"exchange":"SSE","start_date":start.strftime("%Y%m%d"),"end_date":end.strftime("%Y%m%d")},fields="cal_date,is_open")
    days=[]
    for row in raw:
        day=datetime.strptime(row["cal_date"],"%Y%m%d").date();opened=str(row["is_open"])=="1"
        days.append((day,opened,datetime.combine(day,time(9,30),zone) if opened else None,
                     datetime.combine(day,time(15),zone) if opened else None))
    save_calendar(url,"CN",days,"tushare:trade_cal")


def sync_us_calendar(url):
    # Upcoming holidays is forward-looking: never infer missing historical days.
    key=os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")
    if not key:raise ValueError("Massive API key is required for calendar synchronization")
    response=requests.get("https://api.massive.com/v1/marketstatus/upcoming",headers={"Authorization":f"Bearer {key}"},timeout=30)
    response.raise_for_status();raw=response.json()
    zone=ZoneInfo("America/New_York");today=datetime.now(zone).date()
    if not isinstance(raw,list):raise ValueError("invalid market calendar response")
    holidays={}
    for row in raw:
        if not isinstance(row,dict):raise ValueError("invalid calendar record")
        if row.get("exchange") not in ("NYSE","NASDAQ"):continue
        if row.get("status") not in ("closed","early-close") or not row.get("date"):
            raise ValueError("calendar status/date missing")
        if row["status"]=="early-close" and (not row.get("open") or not row.get("close")):
            raise ValueError("early-close calendar times missing")
        previous=holidays.get(row["date"])
        if previous and any(previous.get(k)!=row.get(k) for k in ("open","close","status")):
            raise ValueError("exchange calendars disagree")
        holidays[row["date"]]=row
    if not holidays:raise ValueError("upcoming calendar returned no coverage")
    end=min(today+timedelta(days=30),date.fromisoformat(max(holidays)))
    days=[];day=today
    while day<=end:
        special=holidays.get(day.isoformat(),{});opened=day.weekday()<5 and special.get("status")!="closed"
        opening=datetime.fromisoformat(special["open"].replace("Z","+00:00")) if special.get("open") else datetime.combine(day,time(9,30),zone)
        closing=datetime.fromisoformat(special["close"].replace("Z","+00:00")) if special.get("close") else datetime.combine(day,time(16),zone)
        if opening.tzinfo is None or closing.tzinfo is None or closing<=opening:raise ValueError("invalid calendar timestamps")
        days.append((day,opened,opening if opened else None,closing if opened else None));day+=timedelta(days=1)
    save_calendar(url,"US",days,"massive:upcoming")
