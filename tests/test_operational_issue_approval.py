from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from api.config import Settings
from api.main import create_app
from domain.models import OperationalIssue, RecommendedAction
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ChannelConnectionRow


def _headers(*, user_id: str, role: str, scopes: str = "read write") -> dict[str, str]:
    return {
        "X-API-Key": "test-key",
        "X-Tenant-ID": "local-demo",
        "X-User-ID": user_id,
        "X-User-Role": role,
        "X-Permission-Scopes": scopes,
    }


def test_operational_issue_detail_and_hitl_submission(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'operational-approval.sqlite'}"
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
        )
    )
    with app.state.session_factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id="approval-connection",
                tenant_id="local-demo",
                provider="amazon",
                selling_partner_id="A1LOCALMOCKSELLER",
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
            title="MOCK-STOCKOUT has stockout risk",
            summary="Inventory coverage is below the replenishment lead-time requirement.",
            status="in_progress",
            dedupe_key="approval-stockout",
            confidence=Decimal("0.2"),
            data_freshness_at=now,
            evidence=(
                {
                    "snapshot_type": "inventory_health",
                    "snapshot_id": "snapshot-1",
                    "days_of_supply": "0.1",
                    "total_lead_time_days": 47,
                    "recommended_reorder_quantity": 600,
                    "inventory_source": "mock",
                    "inventory_collected_at": now.isoformat(),
                },
                {"data_source": "mock_amazon", "verified_amazon_data": False, "scenario_id": "critical_stockout"},
            ),
            missing_data=("supplier_confirmation",),
            detected_at=now,
            last_detected_at=now,
            currency="USD",
            estimated_revenue_impact=Decimal("14065.31"),
            estimated_profit_impact=Decimal("5202.01"),
            estimated_cost_impact=Decimal("750.00"),
        )
    )
    action = app.state.recommended_action_repository.upsert(
        RecommendedAction(
            tenant_id="local-demo",
            issue_id=issue.id,
            action_type="review_replenishment",
            title="Review replenishment plan",
            description="Validate inbound stock and supplier lead time before creating a replenishment plan.",
            execution_mode="recommendation_only",
            risk_level="critical",
            status="proposed",
            parameters={
                "seller_sku": "MOCK-STOCKOUT",
                "marketplace_id": "ATVPDKIKX0DER",
                "recommended_reorder_quantity": 600,
                "recommended_shipping_mode": "sea",
            },
            evidence=issue.evidence,
            expected_outcome="Restore safe inventory coverage without writing to Amazon.",
            confidence=Decimal("0.2"),
            automation_eligible=False,
            dedupe_key="action-stockout",
            created_at=now,
            updated_at=now,
            currency="USD",
            expected_revenue_impact=Decimal("14065.31"),
            expected_profit_impact=Decimal("5202.01"),
            expected_cost_impact=Decimal("750.00"),
        ),
        canonical_issue_id=issue.id,
    )

    async def scenario() -> None:
        for handler in app.router.on_startup:
            result = handler()
            if hasattr(result, "__await__"):
                await result
        try:
            operator = _headers(user_id="operator-1", role="operator")
            approver = _headers(user_id="approver-1", role="approver")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                detail = await client.get(f"/api/operations/issues/{issue.id}/detail", headers=operator)
                assert detail.status_code == 200, detail.text
                assert detail.json()["issue"]["evidence"][0]["days_of_supply"] == "0.1"
                assert detail.json()["recommended_actions"][0]["parameters"]["recommended_reorder_quantity"] == 600
                assert detail.json()["recommended_actions"][0]["expected_profit_impact"] == "5202.010000"

                submitted = await client.post(
                    f"/api/operations/issues/{issue.id}/approval-requests",
                    headers=operator,
                    json={
                        "connection_id": "approval-connection",
                        "recommended_action_id": action.id,
                        "note": "Approve replenishment recommendation for manual execution.",
                    },
                )
                assert submitted.status_code == 201, submitted.text
                body = submitted.json()
                request_id = body["approval_request"]["request_id"]
                assert body["issue"]["status"] == "awaiting_approval"
                assert body["approval_request"]["payload"]["action_hash"]
                assert body["approval_request"]["payload"]["recommended_action_id"] == action.id
                assert body["approval_request"]["payload"]["proposed_changes"]["recommended_reorder_quantity"] == 600
                decision_context = body["approval_request"]["payload"]["decision_context"]
                assert decision_context["inventory"]["days_of_supply"] == "0.1"
                assert decision_context["inventory"]["total_lead_time_days"] == 47
                assert decision_context["recommendation"]["recommended_reorder_quantity"] == 600
                assert decision_context["economics"]["currency"] == "USD"
                assert decision_context["provenance"]["verified_amazon_data"] is False
                assert body["created"] is True

                duplicate = await client.post(
                    f"/api/operations/issues/{issue.id}/approval-requests",
                    headers=operator,
                    json={
                        "connection_id": "approval-connection",
                        "recommended_action_id": action.id,
                        "note": "Duplicate click",
                    },
                )
                assert duplicate.status_code == 200, duplicate.text
                assert duplicate.json()["approval_request"]["request_id"] == request_id
                assert duplicate.json()["created"] is False

                self_approval = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=_headers(user_id="operator-1", role="owner", scopes="read write admin"),
                    json={"reason": "Self approve"},
                )
                assert self_approval.status_code == 403

                forged_second_approver = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver,
                    json={"reason": "One person cannot attest for another.", "second_verifier": "approver-2"},
                )
                assert forged_second_approver.status_code == 422

                first_approval = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver,
                    json={"reason": "First independent evidence review completed."},
                )
                assert first_approval.status_code == 200, first_approval.text
                assert first_approval.json()["status"] == "awaiting_second_approval"
                assert first_approval.json()["operational_issue"] is None
                assert first_approval.json()["recommended_action"] is None
                assert (
                    app.state.operational_issue_repository.get(
                        tenant_id="local-demo",
                        issue_id=issue.id,
                    ).status
                    == "awaiting_approval"
                )

                same_approver = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver,
                    json={"reason": "Duplicate approval attempt."},
                )
                assert same_approver.status_code == 400

                approved = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=_headers(user_id="approver-2", role="approver"),
                    json={"reason": "Second independent review; approved for manual Seller Central execution."},
                )
                assert approved.status_code == 200, approved.text
                assert approved.json()["status"] == "approved"
                assert approved.json()["operational_issue"]["status"] == "approved_for_manual_execution"
                saved_action = app.state.recommended_action_repository.get(tenant_id="local-demo", action_id=action.id)
                assert saved_action is not None and saved_action.status == "accepted"
        finally:
            for handler in app.router.on_shutdown:
                result = handler()
                if hasattr(result, "__await__"):
                    await result

    asyncio.run(scenario())
