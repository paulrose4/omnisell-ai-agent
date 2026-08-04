from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from api.config import Settings
from api.main import create_app
from application.manual_execution_contract import APPROVAL_ACTION, build_operational_approval_contract
from domain.models import OperationalIssue, RecommendedAction
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ApprovalRequestRow, ChannelConnectionRow


def test_high_fidelity_default_reference_time_is_stable_for_a_utc_day(monkeypatch) -> None:
    import api.routes.mock_scenario_routes as routes

    class MovingDatetime(datetime):
        values = iter(
            (
                datetime(2026, 7, 22, 4, 0, 1, tzinfo=UTC),
                datetime(2026, 7, 22, 23, 59, 59, tzinfo=UTC),
            )
        )

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
    execute_calls: list[dict] = []

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

    async def execute(self, *, seller_sku: str, action_type: str, changes: dict, idempotency_key: str | None = None):
        type(self).execute_calls.append(
            {
                "seller_sku": seller_sku,
                "action_type": action_type,
                "changes": changes,
                "idempotency_key": idempotency_key,
            }
        )
        return {
            "status": "recorded",
            "before": {"seller_sku": seller_sku, "inbound": 0},
            "after": {"seller_sku": seller_sku, "inbound": changes["inbound_quantity"]},
            "event": {"external_event_id": "event-1", "event_type": "FBA_INVENTORY_AVAILABILITY_CHANGES"},
        }


class FailingMockSellerCentral(FakeMockSellerCentral):
    execute_attempts = 0

    async def execute(self, **kwargs):
        type(self).execute_attempts += 1
        raise TimeoutError("provider outcome is unknown")


def _seed_directly_approved_execution(app, *, now: datetime):
    with app.state.session_factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id="failure-connection",
                tenant_id="local-demo",
                provider="amazon",
                selling_partner_id="seller-failure",
                region="na",
                marketplace_ids=["ATVPDKIKX0DER"],
                secret_ref="memory://mock",
                status="active",
                version=1,
            )
        )
    issue = app.state.operational_issue_repository.upsert(
        OperationalIssue(
            tenant_id="local-demo",
            seller_sku="MOCK-STOCKOUT",
            marketplace_id="ATVPDKIKX0DER",
            issue_type="inventory_stockout_risk",
            severity="critical",
            title="Stockout risk",
            summary="Needs replenishment",
            status="in_progress",
            dedupe_key="failure-stockout",
            confidence=Decimal("1"),
            data_freshness_at=now,
            evidence=({"data_source": "mock_amazon", "verified_amazon_data": False},),
            missing_data=(),
            detected_at=now,
            last_detected_at=now,
        )
    )
    action = app.state.recommended_action_repository.upsert(
        RecommendedAction(
            tenant_id="local-demo",
            issue_id=issue.id,
            action_type="review_replenishment",
            title="Review replenishment plan",
            description="Review deterministic evidence.",
            execution_mode="recommendation_only",
            risk_level="critical",
            status="proposed",
            parameters={
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "recommended_reorder_quantity": 600,
                "replenishment_blocked": False,
            },
            evidence=issue.evidence,
            expected_outcome="Restore inventory coverage.",
            confidence=Decimal("1"),
            automation_eligible=False,
            dedupe_key="failure-stockout-action",
            created_at=now,
            updated_at=now,
        ),
        canonical_issue_id=issue.id,
    )
    connection = app.state.tenant_repository.get_connection(
        tenant_id="local-demo",
        connection_id="failure-connection",
    )
    assert connection is not None
    contract = build_operational_approval_contract(issue=issue, action=action, connection=connection)
    approval = app.state.hitl_manager.submit_request(
        action=APPROVAL_ACTION,
        payload={
            "tenant_id": "local-demo",
            "resource_id": action.id,
            "issue_id": issue.id,
            "recommended_action_id": action.id,
            "action_type": action.action_type,
            **contract.approval_payload(),
        },
        requester="operator-1",
        risk_level="critical",
        reason="Failure recovery test.",
    )
    app.state.operational_issue_repository.transition(
        tenant_id="local-demo",
        issue_id=issue.id,
        target_status="awaiting_approval",
        actor_id="operator-1",
        note="Approval requested.",
    )
    app.state.hitl_manager.approve(
        approval.request_id,
        approver="approver-1",
        reason="First review.",
        tenant_id="local-demo",
    )
    app.state.hitl_manager.approve(
        approval.request_id,
        approver="approver-2",
        reason="Second review.",
        tenant_id="local-demo",
    )
    app.state.recommended_action_repository.transition(
        tenant_id="local-demo",
        action_id=action.id,
        target_status="accepted",
        reason="Approved.",
    )
    app.state.operational_issue_repository.transition(
        tenant_id="local-demo",
        issue_id=issue.id,
        target_status="approved_for_manual_execution",
        actor_id="approver-2",
        note="Approved.",
    )
    return issue, approval


def test_mock_seller_central_requires_approved_issue_and_persists_execution(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'mock-scenario-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    app = create_app(
        Settings(
            database_url=database_url,
            auto_create_schema=False,
            api_key="test-key",
            auth_mode="api_key",
            mock_drill_enabled=True,
            amazon_writes_enabled=False,
            amazon_pilot_read_only=True,
            amazon_sp_api_endpoint="http://mock-cloud:9000",
        )
    )
    with app.state.session_factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id="connection-1",
                tenant_id="local-demo",
                provider="amazon",
                selling_partner_id="seller-1",
                region="na",
                marketplace_ids=["ATVPDKIKX0DER"],
                secret_ref="memory://mock",
                status="active",
                version=1,
            )
        )
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    issue = app.state.operational_issue_repository.upsert(
        OperationalIssue(
            tenant_id="local-demo",
            seller_sku="MOCK-STOCKOUT",
            marketplace_id="ATVPDKIKX0DER",
            issue_type="inventory_stockout_risk",
            severity="critical",
            title="Stockout risk",
            summary="Needs replenishment",
            status="in_progress",
            dedupe_key="mock-stockout",
            confidence=Decimal("1"),
            data_freshness_at=now,
            evidence=({"data_source": "mock_amazon", "verified_amazon_data": False},),
            missing_data=(),
            detected_at=now,
            last_detected_at=now,
        )
    )
    action = app.state.recommended_action_repository.upsert(
        RecommendedAction(
            tenant_id="local-demo",
            issue_id=issue.id,
            action_type="review_replenishment",
            title="Review replenishment plan",
            description="Review deterministic replenishment evidence.",
            execution_mode="recommendation_only",
            risk_level="critical",
            status="proposed",
            parameters={
                "seller_sku": "MOCK-STOCKOUT",
                "marketplace_id": "ATVPDKIKX0DER",
                "recommended_reorder_quantity": 600,
                "recommended_shipping_mode": "sea",
                "replenishment_blocked": False,
            },
            evidence=issue.evidence,
            expected_outcome="Restore inventory coverage.",
            confidence=Decimal("1"),
            automation_eligible=False,
            dedupe_key="mock-stockout-action",
            created_at=now,
            updated_at=now,
        ),
        canonical_issue_id=issue.id,
    )

    import api.routes.mock_scenario_routes as routes

    FakeMockSellerCentral.execute_calls.clear()
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
                "X-API-Key": "test-key",
                "X-Tenant-ID": "local-demo",
                "X-User-ID": "operator-1",
                "X-User-Role": "operator",
                "X-Permission-Scopes": "read write",
            }
            approver_one = {**headers, "X-User-ID": "approver-1", "X-User-Role": "approver"}
            approver_two = {**headers, "X-User-ID": "approver-2", "X-User-Role": "approver"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                listed = await client.get("/api/mock-scenarios", headers=headers)
                assert listed.status_code == 200
                assert listed.json()["verified_amazon_data"] is False
                submitted = await client.post(
                    f"/api/operations/issues/{issue.id}/approval-requests",
                    headers=headers,
                    json={
                        "connection_id": "connection-1",
                        "recommended_action_id": action.id,
                        "note": "Approve the exact replenishment plan.",
                    },
                )
                assert submitted.status_code == 201, submitted.text
                approval = submitted.json()["approval_request"]
                request_id = approval["request_id"]
                assert approval["payload"]["connection_id"] == "connection-1"
                assert approval["payload"]["execution_plan"] == {
                    "execution_contract_version": "manual-execution-v1",
                    "connection_id": "connection-1",
                    "issue_id": issue.id,
                    "recommended_action_id": action.id,
                    "recommended_action_type": "review_replenishment",
                    "action_type": "create_replenishment_plan",
                    "changes": {"inbound_quantity": 600},
                }
                pending_plans = await client.get(
                    "/api/mock-seller-central/approved-plans",
                    headers=headers,
                )
                assert pending_plans.status_code == 200, pending_plans.text
                assert pending_plans.json() == {"total": 0, "items": []}
                pending_execution = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "pending-execution",
                    },
                    json={"approval_request_id": request_id},
                )
                assert pending_execution.status_code == 409, pending_execution.text
                assert FakeMockSellerCentral.execute_calls == []
                first = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver_one,
                    json={"reason": "First independent review."},
                )
                assert first.status_code == 200, first.text
                awaiting_second = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "awaiting-second-execution",
                    },
                    json={"approval_request_id": request_id},
                )
                assert awaiting_second.status_code == 409, awaiting_second.text
                assert FakeMockSellerCentral.execute_calls == []
                second = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver_two,
                    json={"reason": "Second independent review."},
                )
                assert second.status_code == 200, second.text
                approved_plans = await client.get(
                    "/api/mock-seller-central/approved-plans",
                    headers=headers,
                )
                assert approved_plans.status_code == 200, approved_plans.text
                assert approved_plans.json()["total"] == 1
                executable = approved_plans.json()["items"][0]
                assert executable["approval_request_id"] == request_id
                assert executable["action_hash"] == approval["payload"]["action_hash"]
                assert executable["execution_plan"] == approval["payload"]["execution_plan"]
                assert executable["issue"]["seller_sku"] == "MOCK-STOCKOUT"
                assert executable["recommended_action"]["status"] == "accepted"
                assert executable["approvals"]["first_approver_id"] == "approver-1"
                assert executable["approvals"]["second_approver_id"] == "approver-2"

                hidden_for_other_tenant = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "X-Tenant-ID": "other-tenant",
                        "Idempotency-Key": "cross-tenant-execution",
                    },
                    json={"approval_request_id": request_id},
                )
                assert hidden_for_other_tenant.status_code == 404, hidden_for_other_tenant.text
                assert FakeMockSellerCentral.execute_calls == []

                with app.state.session_factory.begin() as session:
                    row = session.scalar(select(ApprovalRequestRow).where(ApprovalRequestRow.id == request_id))
                    assert row is not None
                    original_payload = deepcopy(row.proposed_changes)
                    tampered_payload = deepcopy(row.proposed_changes)
                    tampered_payload["execution_plan"]["changes"]["inbound_quantity"] = 601
                    row.proposed_changes = tampered_payload
                stored_plan_tamper = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "stored-plan-tamper",
                    },
                    json={"approval_request_id": request_id},
                )
                assert stored_plan_tamper.status_code == 409, stored_plan_tamper.text
                assert FakeMockSellerCentral.execute_calls == []
                with app.state.session_factory.begin() as session:
                    row = session.scalar(select(ApprovalRequestRow).where(ApprovalRequestRow.id == request_id))
                    assert row is not None
                    row.proposed_changes = original_payload

                with app.state.session_factory.begin() as session:
                    row = session.scalar(select(ApprovalRequestRow).where(ApprovalRequestRow.id == request_id))
                    assert row is not None
                    original_expiry = row.expires_at
                    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                expired = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "expired-approval",
                    },
                    json={"approval_request_id": request_id},
                )
                assert expired.status_code == 409, expired.text
                assert FakeMockSellerCentral.execute_calls == []
                with app.state.session_factory.begin() as session:
                    row = session.scalar(select(ApprovalRequestRow).where(ApprovalRequestRow.id == request_id))
                    assert row is not None
                    row.expires_at = original_expiry

                tampered = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "tampered-execution",
                    },
                    json={
                        "approval_request_id": request_id,
                        "action_type": "create_replenishment_plan",
                        "changes": {"inbound_quantity": 601},
                        "notes": "Attempt to change the approved quantity.",
                    },
                )
                assert tampered.status_code == 422, tampered.text
                assert FakeMockSellerCentral.execute_calls == []

                response = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "approved-execution",
                    },
                    json={
                        "approval_request_id": request_id,
                        "notes": "Created the approved mock replenishment plan.",
                        "execution_evidence": {"ticket": "MOCK-1"},
                    },
                )
                assert response.status_code == 200, response.text
                assert response.json()["issue_status"] == "verifying"
                assert response.json()["replayed"] is False
                assert FakeMockSellerCentral.execute_calls == [
                    {
                        "seller_sku": "MOCK-STOCKOUT",
                        "action_type": "create_replenishment_plan",
                        "changes": {"inbound_quantity": 600},
                        "idempotency_key": "approved-execution",
                    }
                ]
                execution = await client.get(
                    f"/api/manual-executions/{response.json()['execution_id']}", headers=headers
                )
                assert execution.status_code == 200
                assert execution.json()["actual_change"]["inbound"] == 600
                assert execution.json()["approval_request_id"] == request_id
                assert execution.json()["action_hash"] == approval["payload"]["action_hash"]
                consumed_plans = await client.get(
                    "/api/mock-seller-central/approved-plans",
                    headers=headers,
                )
                assert consumed_plans.status_code == 200, consumed_plans.text
                assert consumed_plans.json() == {"total": 0, "items": []}

                replay = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "approved-execution",
                    },
                    json={"approval_request_id": request_id, "notes": "Network retry."},
                )
                assert replay.status_code == 200, replay.text
                assert replay.json()["execution_id"] == response.json()["execution_id"]
                assert replay.json()["replayed"] is True
                assert len(FakeMockSellerCentral.execute_calls) == 1

                duplicate = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "different-key",
                    },
                    json={"approval_request_id": request_id, "notes": "Duplicate side effect attempt."},
                )
                assert duplicate.status_code == 409, duplicate.text
                assert len(FakeMockSellerCentral.execute_calls) == 1
                protected = await client.patch(
                    f"/api/operations/issues/{issue.id}",
                    headers=headers,
                    json={"status": "resolved", "note": "manual bypass"},
                )
                assert protected.status_code == 403
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_mock_seller_central_failure_requires_manual_recovery(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'mock-execution-failure.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    app = create_app(
        Settings(
            database_url=database_url,
            auto_create_schema=False,
            api_key="test-key",
            auth_mode="api_key",
            mock_drill_enabled=True,
            amazon_writes_enabled=False,
            amazon_pilot_read_only=True,
            amazon_sp_api_endpoint="http://mock-cloud:9000",
        )
    )
    import api.routes.mock_scenario_routes as routes

    FailingMockSellerCentral.execute_attempts = 0
    monkeypatch.setattr(routes, "HttpMockSellerCentralAdapter", FailingMockSellerCentral)

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            issue, approval = _seed_directly_approved_execution(
                app,
                now=datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
            )
            headers = {
                "X-API-Key": "test-key",
                "X-Tenant-ID": "local-demo",
                "X-User-ID": "operator-2",
                "X-User-Role": "operator",
                "X-Permission-Scopes": "read write",
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                failed = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "uncertain-provider-result",
                    },
                    json={"approval_request_id": approval.request_id},
                )
                assert failed.status_code == 502, failed.text
                assert FailingMockSellerCentral.execute_attempts == 1
                execution = app.state.manual_execution_repository.get_by_approval(
                    tenant_id="local-demo",
                    approval_request_id=approval.request_id,
                )
                assert execution is not None
                assert execution.status == "failed"
                assert execution.failure_reason == "TimeoutError: provider outcome is unknown"
                assert execution.completed_at is not None and execution.completed_at.tzinfo is not None
                unchanged_issue = app.state.operational_issue_repository.get(
                    tenant_id="local-demo",
                    issue_id=issue.id,
                )
                assert unchanged_issue is not None
                assert unchanged_issue.status == "approved_for_manual_execution"

                same_key_retry = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "uncertain-provider-result",
                    },
                    json={"approval_request_id": approval.request_id},
                )
                assert same_key_retry.status_code == 409, same_key_retry.text
                assert same_key_retry.json()["error"] == "execution_recovery_required"

                new_key_retry = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={
                        **headers,
                        "Idempotency-Key": "unsafe-second-attempt",
                    },
                    json={"approval_request_id": approval.request_id},
                )
                assert new_key_retry.status_code == 409, new_key_retry.text
                assert new_key_retry.json()["error"] == "approval_already_executed"
                assert FailingMockSellerCentral.execute_attempts == 1
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_high_fidelity_seed_returns_trackable_background_task(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'high-fidelity-background.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    app = create_app(
        Settings(
            database_url=database_url,
            auto_create_schema=False,
            api_key="test-key",
            auth_mode="api_key",
            mock_drill_enabled=True,
            amazon_writes_enabled=False,
            amazon_pilot_read_only=True,
            amazon_sp_api_endpoint="http://mock-cloud:9000",
        )
    )
    with app.state.session_factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id="connection-1",
                tenant_id="local-demo",
                provider="amazon",
                selling_partner_id="seller-1",
                region="na",
                marketplace_ids=["ATVPDKIKX0DER"],
                secret_ref="memory://mock",
                status="active",
                version=1,
            )
        )

    import api.routes.mock_scenario_routes as routes
    import api.routes.sync_routes as sync_routes

    monkeypatch.setattr(routes, "HttpMockSellerCentralAdapter", FakeMockSellerCentral)
    monkeypatch.setattr(app.state.intelligence_ingestion_service, "ingest", lambda **_: {"source": "mock"})
    monkeypatch.setattr(
        app.state.customer_service_workflow if hasattr(app.state, "customer_service_workflow") else SimpleNamespace(),
        "intake",
        lambda **_: SimpleNamespace(id="case-1"),
        raising=False,
    )

    async def fake_sync(app_arg, tenant_id, task_id, connection_id, resources):
        app_arg.state.task_repository.update_status(
            tenant_id=tenant_id,
            task_id=task_id,
            status="succeeded",
            result={"orders": {"status": "succeeded"}},
        )

    async def fake_background(app_arg, tenant_id, actor_id, task_id, connection_id, config, reference_time, dataset):
        app_arg.state.task_repository.update_status(
            tenant_id=tenant_id,
            task_id=task_id,
            status="succeeded",
            result={
                "dataset": dataset.manifest(),
                "sync_status": "succeeded",
                "customer_cases": 1,
                "source": "mock",
                "verified_amazon_data": False,
                "amazon_write_performed": False,
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
                "X-API-Key": "test-key",
                "X-Tenant-ID": "local-demo",
                "X-User-ID": "owner-1",
                "X-User-Role": "owner",
                "X-Permission-Scopes": "read write admin",
            }
            request_payload = {
                "connection_id": "connection-1",
                "seed": 20260722,
                "days": 1,
                "sku_count": 10,
                "order_count": 100,
                "campaign_count": 1,
                "search_term_count": 10,
                "return_count": 1,
                "message_count": 1,
                "review_count": 1,
                "customer_case_count": 1,
                "business_context_count": 1,
                "reference_time": "2026-07-22T04:00:00Z",
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                submitted = await client.post(
                    "/api/mock-scenarios/high-fidelity/seed", headers=headers, json=request_payload
                )
                assert submitted.status_code == 202, submitted.text
                task_id = submitted.json()["task_id"]
                status = await client.get(f"/api/mock-scenarios/high-fidelity/tasks/{task_id}", headers=headers)
                assert status.status_code == 200, status.text
                assert status.json()["status"] == "succeeded"
                assert status.json()["result"]["verified_amazon_data"] is False

                other_tenant_headers = {**headers, "X-Tenant-ID": "other-tenant"}
                hidden = await client.get(
                    f"/api/mock-scenarios/high-fidelity/tasks/{task_id}", headers=other_tenant_headers
                )
                assert hidden.status_code == 404
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
