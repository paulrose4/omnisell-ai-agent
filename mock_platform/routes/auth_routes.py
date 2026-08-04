from __future__ import annotations

import time
from typing import Annotated

from config import (
    ACCESS_TOKEN_TTL_SECONDS,
    OAUTH_CLIENTS,
    REFRESH_TOKEN_TTL_SECONDS,
    get_default_redirect_uri,
    is_redirect_uri_allowed,
)
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from oauth_server import (
    consume_authorization_code,
    create_access_token,
    create_authorization_code,
    create_redirect_url,
    create_refresh_token,
    normalize_scopes,
    oauth_http_error,
    parse_form_body,
    require_bearer_token,
    rotate_refresh_token,
    token_expiry_iso,
    validate_client_secret,
)

router = APIRouter(tags=["oauth"])


def _oauth_redirect(redirect_uri: str, error: str, description: str, state: str | None) -> RedirectResponse:
    return RedirectResponse(
        create_redirect_url(
            redirect_uri,
            {
                "error": error,
                "error_description": description,
                "state": state,
            },
        )
    )


@router.get("/oauth/authorize")
def authorize(
    response_type: Annotated[str, Query(description="Must be code.")],
    client_id: Annotated[str, Query(min_length=1)],
    redirect_uri: Annotated[str, Query(min_length=1)],
    state: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if client_id not in OAUTH_CLIENTS:
        return _oauth_redirect(redirect_uri, "invalid_client", "Unknown client_id.", state)

    if response_type != "code":
        return _oauth_redirect(
            redirect_uri, "unsupported_response_type", "Only response_type=code is supported.", state
        )

    if not is_redirect_uri_allowed(client_id, redirect_uri):
        fallback_redirect_uri = get_default_redirect_uri(client_id) or redirect_uri
        return _oauth_redirect(fallback_redirect_uri, "invalid_redirect_uri", "redirect_uri is not registered.", state)

    try:
        scopes = normalize_scopes(scope, list(OAUTH_CLIENTS[client_id]["scopes"]))
    except ValueError as exc:
        return _oauth_redirect(redirect_uri, "invalid_scope", str(exc), state)

    code = create_authorization_code(client_id, redirect_uri, scopes, state)
    return RedirectResponse(create_redirect_url(redirect_uri, {"code": code, "state": state}))


@router.post("/oauth/token")
async def token(request: Request) -> dict[str, object]:
    form = await parse_form_body(request)

    grant_type = form.get("grant_type", "")
    code = form.get("code", "")
    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    redirect_uri = form.get("redirect_uri", "")

    if grant_type != "authorization_code":
        oauth_http_error(
            status.HTTP_400_BAD_REQUEST, "unsupported_grant_type", "grant_type must be authorization_code."
        )

    missing = [
        name
        for name, value in {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }.items()
        if not value
    ]
    if missing:
        oauth_http_error(
            status.HTTP_400_BAD_REQUEST, "invalid_request", f"Missing required form fields: {', '.join(missing)}."
        )

    validate_client_secret(client_id, client_secret)
    code_info = consume_authorization_code(code, client_id, redirect_uri)

    access_token, access_payload = create_access_token(
        client_id=client_id,
        seller_id=str(code_info["seller_id"]),
        scopes=list(code_info["scope"]),
    )
    refresh_token, _ = create_refresh_token(
        client_id=client_id,
        seller_id=str(code_info["seller_id"]),
        scopes=list(code_info["scope"]),
    )

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "expires_at": token_expiry_iso(access_payload),
        "refresh_token": refresh_token,
        "refresh_expires_in": REFRESH_TOKEN_TTL_SECONDS,
        "scope": access_payload["scope"],
        "seller_id": code_info["seller_id"],
    }


@router.post("/oauth/token/refresh")
async def refresh_token(request: Request) -> dict[str, object]:
    form = await parse_form_body(request)

    grant_type = form.get("grant_type", "")
    old_refresh_token = form.get("refresh_token", "")

    if grant_type != "refresh_token":
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "unsupported_grant_type", "grant_type must be refresh_token.")

    if not old_refresh_token:
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_request", "Missing required form field: refresh_token.")

    new_refresh_token, refresh_info = rotate_refresh_token(old_refresh_token)
    access_token, access_payload = create_access_token(
        client_id=str(refresh_info["client_id"]),
        seller_id=str(refresh_info["seller_id"]),
        scopes=list(refresh_info["scope"]),
    )

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
        "expires_at": token_expiry_iso(access_payload),
        "refresh_token": new_refresh_token,
        "refresh_expires_in": REFRESH_TOKEN_TTL_SECONDS,
        "scope": access_payload["scope"],
        "seller_id": refresh_info["seller_id"],
    }


@router.get("/oauth/token/info")
def token_info(
    payload: Annotated[dict[str, object], Depends(require_bearer_token)],
) -> dict[str, object]:
    expires_in = max(0, int(payload["exp"]) - int(time.time()))
    return {
        "active": True,
        "client_id": payload["client_id"],
        "seller_id": payload["sub"],
        "scope": str(payload.get("scope", "")).split(),
        "expires_at": token_expiry_iso(payload),
        "expires_in": expires_in,
    }
