from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status


def require_agent_service(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    if os.getenv("QUANT_AGENT_INTEGRATION_ENABLED", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "agent_integration_disabled", "message": "Quant agent integration is disabled."},
        )

    expected = os.getenv("QUANT_AGENT_SERVICE_TOKEN", "")
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_service_token", "message": "A valid agent service token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
