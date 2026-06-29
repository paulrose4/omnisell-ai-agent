from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from .config import get_settings


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Validate the API key for protected gateway endpoints."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    expected_api_key = settings.api_key
    if expected_api_key and x_api_key != expected_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key", "message": "Missing or invalid X-API-Key header."},
        )
