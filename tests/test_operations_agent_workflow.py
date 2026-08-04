from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from api.config import Settings
from api.main import create_app
from domain.models import OperationalIssue, RecommendedAction
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import TenantRow

NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)


def test_operations_agent_persists_hitl_and_resumes_without_amazon_write(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'operations-agent.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(
        Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key")
    )
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="Tenant B", slug="tenant-b"))

    issue = OperationalIssue(
        tenant_id="tenant-a",
        seller_sku="SKU-LOSS",
        marketplace_id="ATVPDKIKX0DER",
        issue_type="sku_contribution_loss",
        severity="high",
        title="SKU-LOSS is losing contribution profit",
        summary="Deterministic profit calculation found a negative contribution margin.",
        status="open",
        dedupe_key="tenant-a:loss:sku-loss",
        confidence=Decimal("0.85"),
        data_freshness_at=NOW,
        evidence=({"source": "mock", "contribution_profit": "-125.00", "currency": "USD"},),
        missing_data=(),
        detected_at=NOW,
        last_detected_at=NOW,
        currency="USD",
        estimated_profit_impact=Decimal("125.00"),
    )
    saved_issue = app.state.operational_issue_repository.upsert(issue)
    app.state.recommended_action_repository.upsert(
        RecommendedAction(
            tenant_id="tenant-a",
            issue_id=saved_issue.id,
            action_type="review_profit_levers",
            title="Prepare a profit remediation proposal",
            description="Review price, advertising and landed cost without changing Amazon data.",
            execution_mode="recommendation_only",
            risk_level="high",
            status="proposed",
            parameters={"seller_sku": "SKU-LOSS", "marketplace_id": "ATVPDKIKX0DER"},
            evidence=issue.evidence,
            expected_outcome="Produce a human-reviewed remediation plan.",
            confidence=Decimal("0.85"),
            automation_eligible=False,
            dedupe_key="tenant-a:action:sku-loss",
            created_at=NOW,
            updated_at=NOW,
            currency="USD",
            expected_profit_impact=Decimal("125.00"),
        ),
        canonical_issue_id=saved_issue.id,
    )

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                operator_headers = {
                    "X-API-Key": "test-key",
                    "X-Tenant-ID": "tenant-a",
                    "X-User-ID": "operator-1",
                    "X-User-Role": "operator",
                    "X-Permission-Scopes": "read write",
                }
                acknowledged = await client.patch(
                    f"/api/operations/issues/{saved_issue.id}",
                    headers=operator_headers,
                    json={"status": "acknowledged", "note": "Operator claimed the issue."},
                )
                assert acknowledged.status_code == 200
                in_progress = await client.patch(
                    f"/api/operations/issues/{saved_issue.id}",
                    headers=operator_headers,
                    json={"status": "in_progress", "note": "Operator started investigation."},
                )
                assert in_progress.status_code == 200
                started = await client.post(
                    "/api/agent-runs/operations-recommendations",
                    headers=operator_headers,
                    json={"max_issues": 10},
                )
                assert started.status_code == 202, started.text
                body = started.json()
                assert body["status"] == "awaiting_approval"
                assert len(body["approvals"]) == 1
                run_id = body["run_id"]
                approval_id = body["approvals"][0]["request_id"]

                runs = await client.get("/api/agent-runs", headers=operator_headers)
                assert runs.status_code == 200
                assert runs.json()["items"][0]["run_id"] == run_id
                assert runs.json()["items"][0]["status"] == "awaiting_approval"

                tenant_b_headers = {**operator_headers, "X-Tenant-ID": "tenant-b"}
                hidden = await client.get(f"/api/agent-runs/{run_id}", headers=tenant_b_headers)
                assert hidden.status_code == 404

                approver_headers = {
                    **operator_headers,
                    "X-User-ID": "approver-1",
                    "X-User-Role": "approver",
                }
                approved = await client.post(
                    f"/api/admin/approvals/{approval_id}/approve",
                    headers=approver_headers,
                    json={"reason": "Evidence reviewed; execute manually in Seller Central."},
                )
                assert approved.status_code == 200, approved.text
                assert approved.json()["agent_run"]["status"] == "succeeded"
                assert approved.json()["operational_issue"]["status"] == "approved_for_manual_execution"

                completed = await client.get(f"/api/agent-runs/{run_id}", headers=operator_headers)
                assert completed.status_code == 200
                assert completed.json()["status"] == "succeeded"
                assert completed.json()["created_at"].endswith("+00:00")
                assert completed.json()["updated_at"].endswith("+00:00")
                assert completed.json()["state"]["results"][0]["result"]["execution_mode"] == "manual_only"
                assert completed.json()["state"]["results"][0]["result"]["amazon_write_performed"] is False

                audit = await client.get(
                    "/api/admin/audit?limit=100",
                    headers={**approver_headers, "X-User-Role": "admin", "X-Permission-Scopes": "read write admin"},
                )
                assert audit.status_code == 200
                audit_actions = {item["action"] for item in audit.json()["items"]}
                assert {
                    "agent_run_started",
                    "evidence_collected",
                    "policy_evaluated",
                    "approval_requested",
                    "approval_resumed",
                    "execution_started",
                    "execution_succeeded",
                    "verification_succeeded",
                } <= audit_actions
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
