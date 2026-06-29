from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Header, HTTPException, Request, status

from config import (
    ACCESS_TOKEN_TTL_SECONDS,
    AUTHORIZATION_CODE_TTL_SECONDS,
    DEFAULT_SCOPES,
    JWT_ISSUER,
    JWT_SECRET,
    OAUTH_CLIENTS,
    REFRESH_TOKEN_TTL_SECONDS,
    get_default_seller_id,
)


AUTHORIZATION_CODES: dict[str, dict[str, Any]] = {}
REFRESH_TOKENS: dict[str, dict[str, Any]] = {}


def oauth_http_error(status_code: int, error: str, description: str) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"error": error, "error_description": description},
    )


def create_redirect_url(redirect_uri: str, params: dict[str, str | None]) -> str:
    parts = urlsplit(redirect_uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: value for key, value in params.items() if value is not None})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def parse_form_body(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def normalize_scopes(scope: str | None, allowed_scopes: list[str] | None = None) -> list[str]:
    allowed = set(allowed_scopes or DEFAULT_SCOPES)
    requested = [item for item in (scope or "").split(" ") if item]

    if not requested:
        return sorted(allowed)

    unknown = [item for item in requested if item not in allowed]
    if unknown:
        raise ValueError(f"Unsupported scope: {', '.join(unknown)}")

    return requested


def validate_client_secret(client_id: str, client_secret: str) -> dict[str, Any]:
    client = OAUTH_CLIENTS.get(client_id)
    if not client or not hmac.compare_digest(str(client["client_secret"]), client_secret):
        oauth_http_error(status.HTTP_401_UNAUTHORIZED, "invalid_client", "Invalid client_id or client_secret.")
    return client


def create_authorization_code(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str | None,
) -> str:
    code = secrets.token_urlsafe(32)
    now = int(time.time())
    AUTHORIZATION_CODES[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "seller_id": get_default_seller_id(client_id),
        "created_at": now,
        "expires_at": now + AUTHORIZATION_CODE_TTL_SECONDS,
        "used": False,
    }
    return code


def consume_authorization_code(code: str, client_id: str, redirect_uri: str) -> dict[str, Any]:
    code_info = AUTHORIZATION_CODES.get(code)
    if not code_info:
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_grant", "Authorization code does not exist.")

    if code_info["used"]:
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_grant", "Authorization code has already been used.")

    if int(time.time()) > int(code_info["expires_at"]):
        AUTHORIZATION_CODES.pop(code, None)
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_grant", "Authorization code has expired.")

    if code_info["client_id"] != client_id or code_info["redirect_uri"] != redirect_uri:
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_grant", "Authorization code does not match this client.")

    code_info["used"] = True
    AUTHORIZATION_CODES.pop(code, None)
    return code_info


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)


def create_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header_part, payload_part, signature_part = token.split(".")
    except ValueError:
        raise_token_error("Malformed bearer token.")

    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    expected_signature = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()

    try:
        actual_signature = _b64url_decode(signature_part)
    except Exception:
        raise_token_error("Malformed bearer token signature.")

    if not hmac.compare_digest(expected_signature, actual_signature):
        raise_token_error("Invalid bearer token signature.")

    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except Exception:
        raise_token_error("Malformed bearer token payload.")

    if payload.get("iss") != JWT_ISSUER:
        raise_token_error("Invalid token issuer.")

    if int(time.time()) >= int(payload.get("exp", 0)):
        raise_token_error("Bearer token has expired.")

    return payload


def raise_token_error(description: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "invalid_token", "error_description": description},
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_access_token(client_id: str, seller_id: str, scopes: list[str]) -> tuple[str, dict[str, Any]]:
    now = int(time.time())
    payload = {
        "iss": JWT_ISSUER,
        "aud": "shopee-open-api-mock",
        "sub": seller_id,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
        "jti": secrets.token_urlsafe(16),
    }
    return create_jwt(payload), payload


def create_refresh_token(client_id: str, seller_id: str, scopes: list[str]) -> tuple[str, dict[str, Any]]:
    refresh_token = secrets.token_urlsafe(48)
    now = int(time.time())
    token_info = {
        "client_id": client_id,
        "seller_id": seller_id,
        "scope": scopes,
        "created_at": now,
        "expires_at": now + REFRESH_TOKEN_TTL_SECONDS,
        "revoked": False,
    }
    REFRESH_TOKENS[refresh_token] = token_info
    return refresh_token, token_info


def rotate_refresh_token(refresh_token: str) -> tuple[str, dict[str, Any]]:
    token_info = REFRESH_TOKENS.get(refresh_token)
    if not token_info or token_info.get("revoked"):
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_grant", "Refresh token is invalid.")

    if int(time.time()) > int(token_info["expires_at"]):
        REFRESH_TOKENS.pop(refresh_token, None)
        oauth_http_error(status.HTTP_400_BAD_REQUEST, "invalid_grant", "Refresh token has expired.")

    token_info["revoked"] = True
    REFRESH_TOKENS.pop(refresh_token, None)

    return create_refresh_token(
        client_id=str(token_info["client_id"]),
        seller_id=str(token_info["seller_id"]),
        scopes=list(token_info["scope"]),
    )


def require_bearer_token(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> dict[str, Any]:
    if not authorization:
        raise_token_error("Missing Authorization header.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise_token_error("Authorization header must use Bearer token.")

    return decode_access_token(token)


def token_expiry_iso(payload: dict[str, Any]) -> str:
    return datetime.fromtimestamp(int(payload["exp"]), UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
