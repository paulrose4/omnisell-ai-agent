from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from agents.quota import AgentQuotaBackendUnavailable, AgentQuotaExceeded
from api.config import Settings
from api.main import create_app
from infrastructure.database.bootstrap import initialize_database

ENDPOINTS = (
    ("operations_brief_agent_service", "/api/agent-runs/operations-brief-analysis", {"brief_id": "brief-1"}),
    ("customer_case_agent_service", "/api/agent-runs/customer-case-analysis", {"case_id": "case-1"}),
    (
        "listing_optimization_agent_service",
        "/api/agent-runs/listing-optimization-analysis",
        {"snapshot_id": "snapshot-1"},
    ),
    (
        "reconciliation_agent_service",
        "/api/agent-runs/reconciliation-investigation",
        {"reconciliation_run_id": "recon-1"},
    ),
)


@dataclass
class RecordingAgentService:
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False
    runs_by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    async def analyze(self, **kwargs: Any) -> dict[str, Any]:
        return self._record(kwargs)

    async def investigate(self, **kwargs: Any) -> dict[str, Any]:
        return self._record(kwargs)

    def _record(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise LookupError("not found")
        replay_key = (kwargs["tenant_id"], kwargs["idempotency_key"])
        return self.runs_by_key.setdefault(
            replay_key,
            {"run_id": f"run-{len(self.runs_by_key) + 1}", "tenant_id": kwargs["tenant_id"]},
        )


class QuotaRejectedAgentService:
    async def analyze(self, **kwargs: Any) -> dict[str, Any]:
        raise AgentQuotaExceeded(scope="minute", retry_after_seconds=37)

    async def investigate(self, **kwargs: Any) -> dict[str, Any]:
        raise AgentQuotaExceeded(scope="minute", retry_after_seconds=37)


class QuotaUnavailableAgentService:
    async def analyze(self, **kwargs: Any) -> dict[str, Any]:
        raise AgentQuotaBackendUnavailable(retry_after_seconds=25)


def _headers(*, tenant_id: str = "tenant-a", scopes: str = "read") -> dict[str, str]:
    return {
        "X-API-Key": "test-key",
        "X-Tenant-ID": tenant_id,
        "X-User-ID": "operator-1",
        "X-User-Role": "operator",
        "X-Permission-Scopes": scopes,
    }


def _run_api_scenario(tmp_path, scenario) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent-run-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(
        Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key")
    )

    async def run() -> None:
        for handler in app.router.on_startup:
            result = handler()
            if hasattr(result, "__await__"):
                await result
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                await scenario(app, client)
        finally:
            for handler in app.router.on_shutdown:
                result = handler()
                if hasattr(result, "__await__"):
                    await result

    asyncio.run(run())


@pytest.mark.parametrize(("service_name", "endpoint", "payload"), ENDPOINTS)
def test_agent_submission_endpoints_require_read_scope_and_idempotency_key(
    tmp_path, service_name: str, endpoint: str, payload: dict[str, str]
) -> None:
    async def scenario(app, client: httpx.AsyncClient) -> None:
        service = RecordingAgentService()
        setattr(app.state, service_name, service)

        forbidden = await client.post(endpoint, headers=_headers(scopes="write"), json=payload)
        assert forbidden.status_code == 403
        assert forbidden.json()["error"] == "forbidden"

        missing_key = await client.post(endpoint, headers=_headers(), json=payload)
        assert missing_key.status_code == 400
        assert missing_key.json()["error"] == "idempotency_key_required"
        assert service.calls == []

    _run_api_scenario(tmp_path, scenario)


@pytest.mark.parametrize(("service_name", "endpoint", "payload"), ENDPOINTS)
def test_agent_submission_endpoints_propagate_tenant_and_replay_duplicate_key(
    tmp_path, service_name: str, endpoint: str, payload: dict[str, str]
) -> None:
    async def scenario(app, client: httpx.AsyncClient) -> None:
        service = RecordingAgentService()
        setattr(app.state, service_name, service)
        headers = {**_headers(tenant_id="tenant-a"), "Idempotency-Key": "agent-request-1", "X-Request-ID": "corr-1"}

        first = await client.post(endpoint, headers=headers, json=payload)
        replay = await client.post(endpoint, headers=headers, json=payload)

        assert first.status_code == 202, first.text
        assert replay.status_code == 202, replay.text
        assert replay.json() == first.json()
        assert len(service.calls) == 2
        for call in service.calls:
            assert call["tenant_id"] == "tenant-a"
            assert call["actor_id"] == "operator-1"
            assert call["correlation_id"] == "corr-1"
            assert call["idempotency_key"] == "agent-request-1"

    _run_api_scenario(tmp_path, scenario)


@pytest.mark.parametrize(("service_name", "endpoint", "payload"), ENDPOINTS)
def test_agent_submission_endpoints_mask_missing_or_cross_tenant_resources_as_not_found(
    tmp_path, service_name: str, endpoint: str, payload: dict[str, str]
) -> None:
    async def scenario(app, client: httpx.AsyncClient) -> None:
        service = RecordingAgentService(fail=True)
        setattr(app.state, service_name, service)
        headers = {**_headers(tenant_id="tenant-b"), "Idempotency-Key": "agent-request-missing"}

        response = await client.post(endpoint, headers=headers, json=payload)

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
        assert service.calls[0]["tenant_id"] == "tenant-b"

    _run_api_scenario(tmp_path, scenario)


@pytest.mark.parametrize(("service_name", "endpoint", "payload"), ENDPOINTS)
def test_agent_submission_endpoints_return_explicit_tenant_quota_error(
    tmp_path, service_name: str, endpoint: str, payload: dict[str, str]
) -> None:
    async def scenario(app, client: httpx.AsyncClient) -> None:
        setattr(app.state, service_name, QuotaRejectedAgentService())
        headers = {**_headers(), "Idempotency-Key": "quota-request-1"}

        response = await client.post(endpoint, headers=headers, json=payload)

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "37"
        body = response.json()
        assert {key: body[key] for key in ("error", "message", "scope", "retry_after_seconds")} == {
            "error": "agent_quota_exceeded",
            "message": "Tenant Agent minute quota exhausted.",
            "scope": "minute",
            "retry_after_seconds": 37,
        }
        assert body["path"] == endpoint
        assert body["correlation_id"]

    _run_api_scenario(tmp_path, scenario)


def test_agent_submission_returns_503_when_quota_backend_is_unavailable(tmp_path) -> None:
    async def scenario(app, client: httpx.AsyncClient) -> None:
        app.state.operations_brief_agent_service = QuotaUnavailableAgentService()
        response = await client.post(
            "/api/agent-runs/operations-brief-analysis",
            headers={**_headers(), "Idempotency-Key": "quota-backend-1"},
            json={"brief_id": "brief-1"},
        )

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "25"
        assert response.json()["error"] == "agent_quota_unavailable"

    _run_api_scenario(tmp_path, scenario)


@dataclass
class RecordingAgentRunService:
    resume_calls: list[dict[str, Any]] = field(default_factory=list)

    def list_runs(self, *, tenant_id: str, status: str | None, limit: int) -> list[dict[str, Any]]:
        return [{"run_id": "run-1", "tenant_id": tenant_id}]

    def get_run(self, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        if tenant_id != "tenant-a" or run_id != "run-1":
            return None
        return {"run_id": run_id, "tenant_id": tenant_id}

    async def resume(self, **kwargs: Any) -> dict[str, Any]:
        self.resume_calls.append(kwargs)
        return {"run_id": "run-retry-1", "tenant_id": kwargs["tenant_id"]}


def test_agent_run_query_and_retry_endpoints_enforce_scopes_and_tenant_masking(tmp_path) -> None:
    async def scenario(app, client: httpx.AsyncClient) -> None:
        service = RecordingAgentRunService()
        app.state.agent_run_service = service

        list_forbidden = await client.get("/api/agent-runs", headers=_headers(scopes="write"))
        detail_forbidden = await client.get("/api/agent-runs/run-1", headers=_headers(scopes="write"))
        retry_forbidden = await client.post("/api/agent-runs/run-1/resume", headers=_headers(scopes="read"))
        cross_tenant = await client.get("/api/agent-runs/run-1", headers=_headers(tenant_id="tenant-b"))

        assert list_forbidden.status_code == 403
        assert detail_forbidden.status_code == 403
        assert retry_forbidden.status_code == 403
        assert service.resume_calls == []
        assert cross_tenant.status_code == 404
        assert cross_tenant.json()["error"] == "not_found"

    _run_api_scenario(tmp_path, scenario)
