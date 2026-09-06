from __future__ import annotations

import json
import time
import asyncio
import aiohttp
from datetime import date, datetime, timezone
from urllib import error, parse, request


MASSIVE_API_BASE = "https://api.massive.com"
MAX_FETCH_ATTEMPTS = 5
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


async def fetch_async_json(session, url: str, params: dict | None) -> dict:
    for attempt in range(MAX_FETCH_ATTEMPTS):
        try:
            async with session.get(url, params=params, timeout=180) as response:
                if response.status == 200:
                    return await response.json()
                if response.status not in RETRYABLE_HTTP_STATUSES:
                    raise RuntimeError(f"Massive HTTP {response.status}") from None
                failure = f"HTTP {response.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            failure = type(exc).__name__
        if attempt + 1 == MAX_FETCH_ATTEMPTS:
            raise RuntimeError(f"Massive request failed after {MAX_FETCH_ATTEMPTS} attempts ({failure})") from None
        await asyncio.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def normalize_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def parse_optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def fetch_json(
    url: str,
    *,
    api_key: str,
    params: dict[str, object] | None = None,
    timeout: int = 180,
) -> dict:
    final_url = url
    if params:
        final_url = f"{url}?{parse.urlencode(params)}"
    req = request.Request(
        final_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "quant-trading-system market-enrichment",
        },
        method="GET",
    )
    for attempt in range(MAX_FETCH_ATTEMPTS):
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code == 404:
                return {}
            if exc.code not in RETRYABLE_HTTP_STATUSES:
                raise RuntimeError(f"Massive HTTP {exc.code}") from None
            failure = f"HTTP {exc.code}"
        except (error.URLError, TimeoutError, ConnectionError) as exc:
            failure = type(exc).__name__
        if attempt + 1 == MAX_FETCH_ATTEMPTS:
            raise RuntimeError(f"Massive request failed after {MAX_FETCH_ATTEMPTS} attempts ({failure})") from None
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def iter_results(
    url: str,
    *,
    api_key: str,
    params: dict[str, object] | None = None,
):
    next_url: str | None = url
    next_params = params
    while next_url:
        payload = fetch_json(
            next_url,
            api_key=api_key,
            params=next_params,
        )
        for item in payload.get("results") or []:
            yield item
        next_url = payload.get("next_url")
        next_params = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
