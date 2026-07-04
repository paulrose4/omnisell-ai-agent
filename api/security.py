from __future__ import annotations

from contextvars import Token
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from safety.tool_auth import ToolCallerContext, reset_tool_caller_context, set_tool_caller_context

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


def build_tool_caller_context(request: Request) -> ToolCallerContext:
    """Build the tool caller context attached to this API request."""
    return ToolCallerContext(
        user_id=request.headers.get("X-User-ID", "api"),
        role=request.headers.get("X-User-Role", "agent").lower(),
        tenant_id=request.headers.get("X-Tenant-ID", "default"),
        scopes=None if "X-Permission-Scopes" not in request.headers else frozenset(
            scope.strip().lower()
            for scope in request.headers.get("X-Permission-Scopes", "").replace(",", " ").split()
            if scope.strip()
        ),
    )


def bind_tool_caller_context(request: Request) -> Token[ToolCallerContext | None]:
    """Attach request caller data to Request.state and the tool auth context var."""
    caller_context = build_tool_caller_context(request)
    request.state.caller_context = caller_context
    return set_tool_caller_context(caller_context)


def unbind_tool_caller_context(token: Token[ToolCallerContext | None]) -> None:
    reset_tool_caller_context(token)
