from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api import middleware, security
from safety.tool_auth import ToolCallerContext


def _request(
    *,
    path: str = "/api/issues",
    method: str = "GET",
    headers: dict[str, str] | None = None,
    state: Any | None = None,
    client: tuple[str, int] | None = ("203.0.113.10", 443),
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": client,
        "server": ("testserver", 443),
        "app": SimpleNamespace(state=state or SimpleNamespace()),
    }
    return Request(scope)


def test_memory_rate_limiter_evicts_old_hits_and_rejects_current_window(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = middleware.RateLimiterMiddleware(lambda *_: None, requests_per_minute=2)
    timestamps = iter([1.0, 2.0, 70.0, 70.5, 71.0])
    monkeypatch.setattr(middleware.time, "monotonic", lambda: next(timestamps))

    assert limiter._allow_memory("client") is True
    assert limiter._allow_memory("client") is True
    assert limiter._allow_memory("client") is True
    assert limiter._allow_memory("client") is True
    assert limiter._allow_memory("client") is False


@pytest.mark.asyncio
async def test_rate_limiter_dispatches_allowed_request_and_returns_429() -> None:
    limiter = middleware.RateLimiterMiddleware(lambda *_: None, requests_per_minute=1)
    request = _request()

    async def call_next(_: Request) -> Response:
        return Response("ok", status_code=200)

    assert (await limiter.dispatch(request, call_next)).status_code == 200
    denied = await limiter.dispatch(request, call_next)
    assert denied.status_code == 429
    assert b"rate_limited" in denied.body


@pytest.mark.asyncio
async def test_redis_rate_limiter_is_atomic_and_fails_closed() -> None:
    class Pipeline:
        def __init__(self, *, result: list[Any] | None = None, error: Exception | None = None) -> None:
            self.result = result
            self.error = error
            self.calls: list[str] = []

        def zremrangebyscore(self, *_: Any) -> None:
            self.calls.append("trim")

        def zadd(self, *_: Any) -> None:
            self.calls.append("add")

        def zcard(self, *_: Any) -> None:
            self.calls.append("count")

        def expire(self, *_: Any) -> None:
            self.calls.append("expire")

        async def execute(self) -> list[Any]:
            if self.error:
                raise self.error
            return self.result or [0, 1, 1, True]

    class RedisClient:
        def __init__(self, pipeline: Pipeline) -> None:
            self._pipeline = pipeline

        def pipeline(self, *, transaction: bool) -> Pipeline:
            assert transaction is True
            return self._pipeline

    limiter = middleware.RateLimiterMiddleware(lambda *_: None, requests_per_minute=2)
    allowed_pipeline = Pipeline(result=[0, 1, 2, True])
    limiter.redis = RedisClient(allowed_pipeline)  # type: ignore[assignment]
    assert await limiter._allow_redis("client") is True
    assert allowed_pipeline.calls == ["trim", "add", "count", "expire"]

    limiter.redis = RedisClient(Pipeline(error=ConnectionError("redis unavailable")))  # type: ignore[assignment]
    assert await limiter._allow_redis("client") is False


@pytest.mark.asyncio
async def test_request_telemetry_records_identity_latency_and_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    metric_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Metrics:
        async def record_request(self, **kwargs: Any) -> None:
            metric_calls.append(kwargs)

    state = SimpleNamespace(
        metrics=Metrics(),
        audit_logger=SimpleNamespace(log_operation=lambda **kwargs: audit_calls.append(kwargs)),
    )
    request = _request(headers={"X-Request-ID": "request-1"}, state=state)
    request.state.caller_context = ToolCallerContext(
        user_id="operator-1",
        role="operator",
        tenant_id="tenant-1",
        scopes=frozenset({"read"}),
    )
    tokens: list[str] = []
    monkeypatch.setattr(middleware, "bind_tool_caller_context", lambda _: "token")
    monkeypatch.setattr(middleware, "unbind_tool_caller_context", lambda token: tokens.append(token))
    telemetry = middleware.RequestTelemetryMiddleware(lambda *_: None)

    async def call_next(_: Request) -> Response:
        return JSONResponse({"ok": True}, status_code=503)

    response = await telemetry.dispatch(request, call_next)

    assert response.headers["X-Request-ID"] == "request-1"
    assert float(response.headers["X-Response-Time-MS"]) >= 0
    assert metric_calls[0]["status_code"] == 503
    assert audit_calls[0]["status"] == "failure"
    assert audit_calls[0]["details"]["tenant_id"] == "tenant-1"
    assert tokens == ["token"]


@pytest.mark.asyncio
async def test_request_telemetry_records_500_and_releases_context_when_handler_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric_calls: list[dict[str, Any]] = []

    class Metrics:
        async def record_request(self, **kwargs: Any) -> None:
            metric_calls.append(kwargs)

    request = _request(state=SimpleNamespace(metrics=Metrics(), audit_logger=None), client=None)
    monkeypatch.setattr(middleware, "bind_tool_caller_context", lambda _: "token")
    released: list[str] = []
    monkeypatch.setattr(middleware, "unbind_tool_caller_context", lambda token: released.append(token))
    telemetry = middleware.RequestTelemetryMiddleware(lambda *_: None)

    async def call_next(_: Request) -> Response:
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        await telemetry.dispatch(request, call_next)
    assert metric_calls[0]["status_code"] == 500
    assert released == ["token"]


def _oidc_settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "oidc_issuer": "https://issuer.example",
        "oidc_jwks_url": "https://issuer.example/jwks",
        "oidc_algorithm_list": ["RS256"],
        "oidc_audience": "omnisell-api",
        "auth_mode": "oidc",
        "environment": "production",
        "oidc_require_mfa": True,
        "oidc_mfa_amr_list": ["mfa", "otp"],
        "oidc_required_acr": "urn:mfa",
        "api_key": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_decode_oidc_token_rejects_unconfigured_and_invalid_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as unconfigured:
        security._decode_oidc_token("token", _oidc_settings(oidc_issuer=""))
    assert unconfigured.value.status_code == 401

    class JwkClient:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
            return SimpleNamespace(key="key")

    security._jwk_clients.clear()
    monkeypatch.setattr(security, "PyJWKClient", JwkClient)
    monkeypatch.setattr(
        security.jwt, "decode", lambda *_args, **_kwargs: (_ for _ in ()).throw(jwt.InvalidTokenError())
    )
    with pytest.raises(HTTPException) as invalid:
        security._decode_oidc_token("token", _oidc_settings())
    assert invalid.value.status_code == 401


def test_principal_claims_normalize_roles_scopes_and_require_tenant() -> None:
    with pytest.raises(HTTPException, match="Token is missing tenant context"):
        security._principal_from_claims({"sub": "user-1"})

    principal = security._principal_from_claims(
        {"sub": "user-1", "org_id": "tenant-1", "role": "Operator", "scope": "Read,WRITE"}
    )
    assert principal.tenant_id == "tenant-1"
    assert principal.role == "operator"
    assert principal.scopes == frozenset({"read", "write"})


@pytest.mark.asyncio
async def test_oidc_authentication_requires_mfa_and_provisioned_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = {"sub": "subject-1", "tenant_id": "tenant-1", "roles": ["viewer"], "scope": ["read"]}
    monkeypatch.setattr(security, "_decode_oidc_token", lambda *_: claims)
    settings = _oidc_settings()
    request = _request(state=SimpleNamespace(settings=settings, tenant_repository=None))

    with pytest.raises(HTTPException) as mfa:
        await security.authenticate_request(request, authorization="Bearer token")
    assert mfa.value.status_code == 403
    assert mfa.value.detail["error"] == "mfa_required"

    claims["amr"] = ["mfa"]
    with pytest.raises(HTTPException, match="Tenant membership service is unavailable"):
        await security.authenticate_request(request, authorization="Bearer token")

    repository = SimpleNamespace(find_user_by_oidc_subject=lambda _: None)
    request = _request(state=SimpleNamespace(settings=settings, tenant_repository=repository))
    with pytest.raises(HTTPException, match="Operator account has not been provisioned"):
        await security.authenticate_request(request, authorization="Bearer token")

    user = SimpleNamespace(id="user-1")
    repository = SimpleNamespace(
        find_user_by_oidc_subject=lambda _: user,
        find_membership=lambda **_: None,
    )
    request = _request(state=SimpleNamespace(settings=settings, tenant_repository=repository))
    with pytest.raises(HTTPException) as membership:
        await security.authenticate_request(request, authorization="Bearer token")
    assert membership.value.status_code == 403


@pytest.mark.asyncio
async def test_oidc_authentication_uses_persisted_role_and_binds_context(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = {
        "sub": "subject-1",
        "tenant_id": "tenant-1",
        "roles": ["viewer"],
        "scope": ["read"],
        "acr": "urn:mfa",
    }
    membership = SimpleNamespace(role=SimpleNamespace(value="operator"))
    repository = SimpleNamespace(
        find_user_by_oidc_subject=lambda _: SimpleNamespace(id="user-1"),
        find_membership=lambda **_: membership,
    )
    request = _request(state=SimpleNamespace(settings=_oidc_settings(), tenant_repository=repository))
    monkeypatch.setattr(security, "_decode_oidc_token", lambda *_: claims)
    monkeypatch.setattr(security, "set_tool_caller_context", lambda _: "tool-token")
    monkeypatch.setattr(security, "bind_tenant_context", lambda _: "tenant-token")

    principal = await security.authenticate_request(request, authorization="Bearer token")

    assert principal.user_id == "user-1"
    assert principal.role == "operator"
    assert principal.scopes == frozenset({"read", "write"})
    assert request.state.auth_tool_context_token == "tool-token"
    assert request.state.database_tenant_token == "tenant-token"


@pytest.mark.asyncio
async def test_api_key_authentication_and_dependency_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _oidc_settings(auth_mode="api_key", environment="test", api_key="secret")
    request = _request(
        state=SimpleNamespace(settings=settings),
        headers={
            "X-Tenant-ID": "tenant-1",
            "X-User-ID": "operator-1",
            "X-User-Role": "Operator",
            "X-Permission-Scopes": "read, write",
        },
    )
    monkeypatch.setattr(security, "set_tool_caller_context", lambda _: "tool-token")
    monkeypatch.setattr(security, "bind_tenant_context", lambda _: "tenant-token")
    reset_tools: list[str] = []
    reset_tenants: list[str] = []
    monkeypatch.setattr(security, "reset_tool_caller_context", lambda token: reset_tools.append(token))
    monkeypatch.setattr(security, "reset_tenant_context", lambda token: reset_tenants.append(token))

    dependency = security.require_api_key(request, x_api_key="secret")
    await anext(dependency)
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    assert request.state.principal.tenant_id == "tenant-1"
    assert request.state.principal.scopes == frozenset({"read", "write"})
    assert reset_tools == ["tool-token"]
    assert reset_tenants == ["tenant-token"]


def test_tool_context_fails_unbound_for_oidc_and_supports_legacy_api_key_headers() -> None:
    oidc_request = _request(state=SimpleNamespace(settings=_oidc_settings()))
    assert security.build_tool_caller_context(oidc_request).tenant_id == "unbound"

    api_key_request = _request(
        state=SimpleNamespace(settings=_oidc_settings(auth_mode="api_key")),
        headers={"X-Tenant-ID": "tenant-1", "X-Permission-Scopes": "read write"},
    )
    context = security.build_tool_caller_context(api_key_request)
    assert context.tenant_id == "tenant-1"
    assert context.scopes == frozenset({"read", "write"})

    principal = security.AuthenticatedPrincipal(
        user_id="user-1",
        tenant_id="tenant-2",
        role="viewer",
        scopes=frozenset({"read"}),
        claims={},
    )
    api_key_request.state.principal = principal
    assert security.build_tool_caller_context(api_key_request).tenant_id == "tenant-2"


def test_unbind_database_context_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request()
    security.unbind_database_tenant_context(request)
    request.state.database_tenant_token = "tenant-token"
    reset: list[str] = []
    monkeypatch.setattr(security, "reset_tenant_context", lambda token: reset.append(token))

    security.unbind_database_tenant_context(request)

    assert reset == ["tenant-token"]
    assert not hasattr(request.state, "database_tenant_token")
