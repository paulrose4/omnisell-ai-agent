from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx

from api.config import Settings
from api.main import create_app
from domain.models import OperationalIssue
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ChannelConnectionRow


def test_high_fidelity_default_reference_time_is_stable_for_a_utc_day(monkeypatch) -> None:
    import api.routes.mock_scenario_routes as routes

    class MovingDatetime(datetime):
        values = iter((
            datetime(2026, 7, 22, 4, 0, 1, tzinfo=UTC),
            datetime(2026, 7, 22, 23, 59, 59, tzinfo=UTC),
        ))

        @classmethod
        def now(cls, tz=None):
            return next(cls.values)

    monkeypatch.setattr(routes, "datetime", MovingDatetime)
    first = routes.HighFidelitySeedRequest(connection_id="connection-1")
    second = routes.HighFidelitySeedRequest(connection_id="connection-1")
    expected = datetime(2026, 7, 22, tzinfo=UTC)
    assert first.reference_time == expected
    assert second.reference_time == expected


class FakeMockSellerCentral:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def list_scenarios(self):
        return {"items": [{"scenario_id": "critical_stockout", "seller_sku": "MOCK-STOCKOUT"}], "failures": {}}

    async def reset(self):
        return {"status": "reset", "items": []}

    async def trigger(self, scenario_id: str):
        return {"status": "triggered", "event": None}

    async def load_high_fidelity(self, payload: dict):
        return {"status": "loaded", "counts": {"orders": payload["order_count"]}}

    async def execute(self, *, seller_sku: str, action_type: str, changes: dict):
        return {
            "status": "recorded",
            "before": {"seller_sku": seller_sku, "inbound": 0},
            "after": {"seller_sku": seller_sku, "inbound": changes["inbound_quantity"]},
            "event": {"external_event_id": "event-1", "event_type": "FBA_INVENTORY_AVAILABILITY_CHANGES"},
        }


def test_mock_seller_central_requires_approved_issue_and_persists_execution(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'mock-scenario-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    app = create_app(Settings(
        database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key",
        mock_drill_enabled=True, amazon_writes_enabled=False, amazon_pilot_read_only=True,
        amazon_sp_api_endpoint="http://mock-cloud:9000",
    ))
    with app.state.session_factory.begin() as session:
        session.add(ChannelConnectionRow(
            id="connection-1", tenant_id="local-demo", provider="amazon", selling_partner_id="seller-1",
            region="na", marketplace_ids=["ATVPDKIKX0DER"], secret_ref="memory://mock", status="active", version=1,
        ))
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    issue = app.state.operational_issue_repository.upsert(OperationalIssue(
        tenant_id="local-demo", seller_sku="MOCK-STOCKOUT", marketplace_id="ATVPDKIKX0DER",
        issue_type="inventory_stockout_risk", severity="critical", title="Stockout risk", summary="Needs replenishment",
        status="approved_for_manual_execution", dedupe_key="mock-stockout", confidence=Decimal("1"),
        data_freshness_at=now, evidence=({"data_source": "mock_amazon", "verified_amazon_data": False},),
        missing_data=(), detected_at=now, last_detected_at=now,
    ))

    import api.routes.mock_scenario_routes as routes
    monkeypatch.setattr(routes, "HttpMockSellerCentralAdapter", FakeMockSellerCentral)

    async def accepted(*args, **kwargs):
        return True

    monkeypatch.setattr(routes, "_accept_and_schedule_event", accepted)

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            headers = {
                "X-API-Key": "test-key", "X-Tenant-ID": "local-demo", "X-User-ID": "operator-1",
                "X-User-Role": "operator", "X-Permission-Scopes": "read write",
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                listed = await client.get("/api/mock-scenarios", headers=headers)
                assert listed.status_code == 200
                assert listed.json()["verified_amazon_data"] is False
                response = await client.post("/api/mock-seller-central/execute", headers=headers, json={
                    "connection_id": "connection-1", "issue_id": issue.id,
                    "action_type": "create_replenishment_plan", "changes": {"inbound_quantity": 600},
                    "notes": "Created mock replenishment plan", "execution_evidence": {"ticket": "MOCK-1"},
                })
                assert response.status_code == 200, response.text
                assert response.json()["issue_status"] == "verifying"
                execution = await client.get(f"/api/manual-executions/{response.json()['execution_id']}", headers=headers)
                assert execution.status_code == 200
                assert execution.json()["actual_change"]["inbound"] == 600
                protected = await client.patch(f"/api/operations/issues/{issue.id}", headers=headers, json={"status": "resolved", "note": "manual bypass"})
                assert protected.status_code == 403
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_high_fidelity_seed_returns_trackable_background_task(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'high-fidelity-background.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    app = create_app(Settings(
        database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key",
        mock_drill_enabled=True, amazon_writes_enabled=False, amazon_pilot_read_only=True,
        amazon_sp_api_endpoint="http://mock-cloud:9000",
    ))
    with app.state.session_factory.begin() as session:
        session.add(ChannelConnectionRow(
            id="connection-1", tenant_id="local-demo", provider="amazon", selling_partner_id="seller-1",
            region="na", marketplace_ids=["ATVPDKIKX0DER"], secret_ref="memory://mock", status="active", version=1,
        ))

    import api.routes.mock_scenario_routes as routes
    import api.routes.sync_routes as sync_routes

    monkeypatch.setattr(routes, "HttpMockSellerCentralAdapter", FakeMockSellerCentral)
    monkeypatch.setattr(app.state.intelligence_ingestion_service, "ingest", lambda **_: {"source": "mock"})
    monkeypatch.setattr(app.state.customer_service_workflow if hasattr(app.state, "customer_service_workflow") else SimpleNamespace(), "intake", lambda **_: SimpleNamespace(id="case-1"), raising=False)

    async def fake_sync(app_arg, tenant_id, task_id, connection_id, resources):
        app_arg.state.task_repository.update_status(
            tenant_id=tenant_id, task_id=task_id, status="succeeded", result={"orders": {"status": "succeeded"}},
        )

    async def fake_background(app_arg, tenant_id, actor_id, task_id, connection_id, config, reference_time, dataset):
        app_arg.state.task_repository.update_status(
            tenant_id=tenant_id, task_id=task_id, status="succeeded",
            result={
                "dataset": dataset.manifest(), "sync_status": "succeeded", "customer_cases": 1,
                "source": "mock", "verified_amazon_data": False, "amazon_write_performed": False,
            },
        )

    monkeypatch.setattr(sync_routes, "_run_sync_job", fake_sync)
    monkeypatch.setattr(routes, "_run_high_fidelity_seed", fake_background, raising=False)

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            app.state.customer_service_workflow.intake = lambda **_: SimpleNamespace(id="case-1")
            headers = {
                "X-API-Key": "test-key", "X-Tenant-ID": "local-demo", "X-User-ID": "owner-1",
                "X-User-Role": "owner", "X-Permission-Scopes": "read write admin",
            }
            request_payload = {
                "connection_id": "connection-1", "seed": 20260722, "days": 1, "sku_count": 10,
                "order_count": 100, "campaign_count": 1, "search_term_count": 10,
                "return_count": 1, "message_count": 1, "review_count": 1,
                "customer_case_count": 1, "business_context_count": 1,
                "reference_time": "2026-07-22T04:00:00Z",
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                submitted = await client.post("/api/mock-scenarios/high-fidelity/seed", headers=headers, json=request_payload)
                assert submitted.status_code == 202, submitted.text
                task_id = submitted.json()["task_id"]
                status = await client.get(f"/api/mock-scenarios/high-fidelity/tasks/{task_id}", headers=headers)
                assert status.status_code == 200, status.text
                assert status.json()["status"] == "succeeded"
                assert status.json()["result"]["verified_amazon_data"] is False

                other_tenant_headers = {**headers, "X-Tenant-ID": "other-tenant"}
                hidden = await client.get(f"/api/mock-scenarios/high-fidelity/tasks/{task_id}", headers=other_tenant_headers)
                assert hidden.status_code == 404
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
