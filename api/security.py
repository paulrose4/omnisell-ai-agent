from __future__ import annotations

from contextvars import Token
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException, Request, status
from jwt import PyJWKClient

from safety.tool_auth import ToolCallerContext, reset_tool_caller_context, set_tool_caller_context
from infrastructure.database.session import bind_tenant_context, reset_tenant_context

from .config import Settings, get_settings


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: str
    tenant_id: str
    role: str
    scopes: frozenset[str]
    claims: dict[str, Any]


_jwk_clients: dict[str, PyJWKClient] = {}
_ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "write", "admin"}),
    "admin": frozenset({"read", "write", "admin"}),
    "operator": frozenset({"read", "write"}),
    "approver": frozenset({"read", "write"}),
    "viewer": frozenset({"read"}),
    "service_account": frozenset({"read", "write"}),
}


def _unauthorized(message: str = "Authentication required.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": "unauthorized", "message": message})


def _decode_oidc_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.oidc_issuer or not settings.oidc_jwks_url:
        raise _unauthorized("OIDC is not configured.")
    client = _jwk_clients.setdefault(settings.oidc_jwks_url, PyJWKClient(settings.oidc_jwks_url, cache_keys=True))
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=settings.oidc_algorithm_list,
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid or expired access token.") from exc


def _principal_from_claims(claims: dict[str, Any]) -> AuthenticatedPrincipal:
    tenant_id = str(claims.get("tenant_id") or claims.get("org_id") or "")
    if not tenant_id:
        raise _unauthorized("Token is missing tenant context.")
    roles = claims.get("roles") or claims.get("role") or []
    if isinstance(roles, str):
        roles = [roles]
    role = str(roles[0] if roles else "viewer").lower()
    raw_scopes = claims.get("scope") or claims.get("scopes") or []
    if isinstance(raw_scopes, str):
        raw_scopes = raw_scopes.replace(",", " ").split()
    return AuthenticatedPrincipal(
        user_id=str(claims["sub"]), tenant_id=tenant_id, role=role,
        scopes=frozenset(str(scope).lower() for scope in raw_scopes), claims=claims,
    )


async def authenticate_request(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> AuthenticatedPrincipal:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    if authorization and authorization.lower().startswith("bearer ") and settings.auth_mode in {"oidc", "hybrid"}:
        claims = _decode_oidc_token(authorization.split(" ", 1)[1].strip(), settings)
        principal = _principal_from_claims(claims)
        if settings.environment.lower() == "production" and settings.oidc_require_mfa:
            raw_amr = claims.get("amr") or []
            amr = [raw_amr.lower()] if isinstance(raw_amr, str) else [str(item).lower() for item in raw_amr]
            acr = str(claims.get("acr") or "")
            amr_ok = any(item in amr for item in settings.oidc_mfa_amr_list)
            acr_ok = bool(settings.oidc_required_acr and acr == settings.oidc_required_acr)
            if not amr_ok and not acr_ok:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "mfa_required", "message": "Production access requires multi-factor authentication."})
        if settings.environment.lower() == "production":
            repository = getattr(request.app.state, "tenant_repository", None)
            if repository is None:
                raise _unauthorized("Tenant membership service is unavailable.")
            user = repository.find_user_by_oidc_subject(str(claims["sub"]))
            if user is None:
                raise _unauthorized("Operator account has not been provisioned.")
            membership = repository.find_membership(tenant_id=principal.tenant_id, user_id=user.id)
            if membership is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "membership_required", "message": "User is not a member of this tenant."})
            principal = AuthenticatedPrincipal(
                user_id=user.id,
                tenant_id=principal.tenant_id,
                role=membership.role.value,
                scopes=_ROLE_SCOPES.get(membership.role.value, frozenset({"read"})),
                claims=claims,
            )
    elif settings.auth_mode in {"api_key", "hybrid"} and settings.api_key and x_api_key == settings.api_key:
        scopes = frozenset(
            scope.strip().lower()
            for scope in request.headers.get("X-Permission-Scopes", "read write admin").replace(",", " ").split()
            if scope.strip()
        )
        principal = AuthenticatedPrincipal(
            user_id=request.headers.get("X-User-ID", "api"),
            role=request.headers.get("X-User-Role", "agent").lower(),
            tenant_id=request.headers.get("X-Tenant-ID", "default"),
            scopes=scopes,
            claims={"auth_mode": "api_key"},
        )
    else:
        raise _unauthorized("Missing or invalid credentials.")
    request.state.principal = principal
    caller_context = ToolCallerContext(
        user_id=principal.user_id,
        role=principal.role,
        tenant_id=principal.tenant_id,
        scopes=principal.scopes,
    )
    request.state.caller_context = caller_context
    if not hasattr(request.state, "auth_tool_context_token"):
        request.state.auth_tool_context_token = set_tool_caller_context(caller_context)
    if not hasattr(request.state, "database_tenant_token"):
        request.state.database_tenant_token = bind_tenant_context(principal.tenant_id)
    return principal


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AsyncIterator[None]:
    """Authenticate and release request-scoped security contexts deterministically."""
    await authenticate_request(request, authorization=authorization, x_api_key=x_api_key)
    try:
        yield
    finally:
        unbind_database_tenant_context(request)
        token = getattr(request.state, "auth_tool_context_token", None)
        if token is not None:
            reset_tool_caller_context(token)
            delattr(request.state, "auth_tool_context_token")


def build_tool_caller_context(request: Request) -> ToolCallerContext:
    """Build tool context from verified identity, with legacy headers only in API-key mode."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        settings = getattr(request.app.state, "settings", None) or get_settings()
        if settings.auth_mode == "oidc":
            return ToolCallerContext(user_id="anonymous", role="viewer", tenant_id="unbound", scopes=frozenset())
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
    return ToolCallerContext(user_id=principal.user_id, role=principal.role, tenant_id=principal.tenant_id, scopes=principal.scopes)


def bind_tool_caller_context(request: Request) -> Token[ToolCallerContext | None]:
    caller_context = build_tool_caller_context(request)
    request.state.caller_context = caller_context
    return set_tool_caller_context(caller_context)


def unbind_tool_caller_context(token: Token[ToolCallerContext | None]) -> None:
    reset_tool_caller_context(token)


def unbind_database_tenant_context(request: Request) -> None:
    token = getattr(request.state, "database_tenant_token", None)
    if token is None:
        return
    reset_tenant_context(token)
    delattr(request.state, "database_tenant_token")
