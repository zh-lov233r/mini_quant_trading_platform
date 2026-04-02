from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local-dev dependency
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


DEFAULT_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_TIMEOUT_SECONDS = 20.0

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit", "trailing_stop"]
TimeInForce = Literal["day", "gtc", "opg", "cls", "ioc", "fok"]


class AlpacaClientError(RuntimeError):
    """Base exception raised by the Alpaca client."""


class AlpacaConfigError(AlpacaClientError):
    """Raised when required Alpaca configuration is missing."""


class AlpacaAPIError(AlpacaClientError):
    """Raised when the Alpaca API returns a non-success response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _first_env_value(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


@dataclass(slots=True)
class AlpacaClient:
    api_key: str | None = None
    secret_key: str | None = None
    base_url: str | None = None
    timeout_seconds: float | None = None
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or _first_env_value("ALPACA_API_KEY", "ALPACA_KEY")
        self.secret_key = self.secret_key or _first_env_value(
            "ALPACA_SECRET_KEY",
            "ALPACA_SECRET",
        )
        self.base_url = (self.base_url or _first_env_value("ALPACA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        raw_timeout = (
            self.timeout_seconds
            if self.timeout_seconds is not None
            else _first_env_value("ALPACA_TIMEOUT_SECONDS")
        )
        self.timeout_seconds = float(raw_timeout or DEFAULT_TIMEOUT_SECONDS)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    @property
    def headers(self) -> dict[str, str]:
        self._ensure_credentials()
        return {
            "APCA-API-KEY-ID": str(self.api_key),
            "APCA-API-SECRET-KEY": str(self.secret_key),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/v2/account")

    def get_clock(self) -> dict[str, Any]:
        return self._request("GET", "/v2/clock")

    def list_positions(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/v2/positions")
        return response if isinstance(response, list) else []

    def get_position(self, symbol: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/positions/{symbol.upper()}")

    def close_position(
        self,
        symbol: str,
        *,
        qty: float | None = None,
        percentage: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if qty is not None:
            payload["qty"] = self._normalize_number(qty)
        if percentage is not None:
            payload["percentage"] = self._normalize_number(percentage)
        return self._request("DELETE", f"/v2/positions/{symbol.upper()}", json=payload or None)

    def close_all_positions(self, *, cancel_orders: bool | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if cancel_orders is not None:
            params["cancel_orders"] = str(cancel_orders).lower()
        response = self._request("DELETE", "/v2/positions", params=params or None)
        return response if isinstance(response, list) else []

    def list_orders(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        after: str | None = None,
        until: str | None = None,
        direction: Literal["asc", "desc"] | None = None,
        nested: bool | None = None,
        symbols: list[str] | None = None,
        side: OrderSide | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        if direction:
            params["direction"] = direction
        if nested is not None:
            params["nested"] = str(nested).lower()
        if symbols:
            params["symbols"] = ",".join(symbol.upper() for symbol in symbols)
        if side:
            params["side"] = side

        response = self._request("GET", "/v2/orders", params=params or None)
        return response if isinstance(response, list) else []

    def get_order(self, order_id: str, *, nested: bool | None = None) -> dict[str, Any]:
        params = {"nested": str(nested).lower()} if nested is not None else None
        return self._request("GET", f"/v2/orders/{order_id}", params=params)

    def cancel_order(self, order_id: str) -> bool:
        self._request("DELETE", f"/v2/orders/{order_id}")
        return True

    def cancel_all_orders(self) -> list[dict[str, Any]]:
        response = self._request("DELETE", "/v2/orders")
        return response if isinstance(response, list) else []

    def submit_order(
        self,
        *,
        symbol: str,
        side: OrderSide,
        order_type: OrderType = "market",
        time_in_force: TimeInForce = "day",
        qty: float | None = None,
        notional: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
        extended_hours: bool | None = None,
        order_class: str | None = None,
        take_profit: dict[str, Any] | None = None,
        stop_loss: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if qty is None and notional is None:
            raise ValueError("either qty or notional is required")
        if qty is not None and notional is not None:
            raise ValueError("qty and notional cannot both be set")

        payload: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if qty is not None:
            payload["qty"] = self._normalize_number(qty)
        if notional is not None:
            payload["notional"] = self._normalize_number(notional)
        if limit_price is not None:
            payload["limit_price"] = self._normalize_number(limit_price)
        if stop_price is not None:
            payload["stop_price"] = self._normalize_number(stop_price)
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if extended_hours is not None:
            payload["extended_hours"] = extended_hours
        if order_class:
            payload["order_class"] = order_class
        if take_profit:
            payload["take_profit"] = take_profit
        if stop_loss:
            payload["stop_loss"] = stop_loss

        return self._request("POST", "/v2/orders", json=payload)

    def place_order(
        self,
        symbol: str,
        qty: int | float,
        side: OrderSide = "buy",
        type: OrderType = "market",
        tif: TimeInForce = "day",
    ) -> dict[str, Any]:
        return self.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=type,
            time_in_force=tif,
        )

    def _ensure_credentials(self) -> None:
        if self.configured:
            return
        raise AlpacaConfigError(
            "missing Alpaca credentials; set ALPACA_API_KEY/ALPACA_SECRET_KEY "
            "or the legacy ALPACA_KEY/ALPACA_SECRET environment variables"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=self.headers,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AlpacaClientError(f"request to Alpaca failed: {exc}") from exc

        if response.status_code >= 400:
            payload = _safe_json(response)
            detail = _extract_error_detail(payload) or response.text.strip() or response.reason
            raise AlpacaAPIError(
                f"Alpaca API error {response.status_code}: {detail}",
                status_code=response.status_code,
                payload=payload,
            )

        if response.status_code == 204 or not response.content:
            return None

        payload = _safe_json(response)
        return payload if payload is not None else response.text

    @staticmethod
    def _normalize_number(value: int | float) -> str:
        if isinstance(value, bool):
            raise ValueError("boolean is not a valid numeric order field")
        return format(float(value), "g")


def get_alpaca_client(
    *,
    api_key: str | None = None,
    secret_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    session: requests.Session | None = None,
) -> AlpacaClient:
    return AlpacaClient(
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        session=session or requests.Session(),
    )


def place_order(
    symbol: str,
    qty: int | float,
    side: OrderSide = "buy",
    type: OrderType = "market",
    tif: TimeInForce = "day",
) -> dict[str, Any]:
    """Backward-compatible helper for the old module-level API."""

    client = get_alpaca_client()
    return client.place_order(symbol=symbol, qty=qty, side=side, type=type, tif=tif)


def _safe_json(response: requests.Response) -> Any | None:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_error_detail(payload: Any) -> str | None:
    if isinstance(payload, dict):
        detail = payload.get("message") or payload.get("error") or payload.get("detail")
        if detail:
            return str(detail)
    return None
