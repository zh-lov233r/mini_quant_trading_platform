from __future__ import annotations

import json
from datetime import date, datetime, timezone
from urllib import error, parse, request


MASSIVE_API_BASE = "https://api.massive.com"


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
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code == 404:
            return {}
        body = exc.read().decode("utf-8", errors="replace")
        # Do not include the request URL: paginated vendor URLs may contain
        # credentials or opaque cursors that should stay out of logs.
        raise RuntimeError(f"Massive HTTP {exc.code} {exc.reason}: {body[:500]}") from exc


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
