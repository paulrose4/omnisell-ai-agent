from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select

from api.config import Settings
from api.main import create_app
from commerce.ports import Page
from domain.models import (
    FulfillmentMode,
    InventoryPosition,
    Listing,
    Money,
    Order,
    OrderLine,
    OrderStatus,
)
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ChannelConnectionRow, InboxEventRow

MARKETPLACE_ID = "ATVPDKIKX0DER"


def _headers(*, user_id: str, role: str, scopes: str = "read write") -> dict[str, str]:
    return {
        "X-API-Key": "test-key",
        "X-Tenant-ID": "local-demo",
        "X-User-ID": user_id,
        "X-User-Role": role,
        "X-Permission-Scopes": scopes,
    }


class GoldenStockoutAdapter:
    def __init__(self) -> None:
        self.inbound = 0
        self.observed_at = datetime.now(UTC) - timedelta(minutes=5)

    async def list_orders(self, *, marketplace_ids, updated_after, next_token=None):
        orders = [
            Order(
                tenant_id="placeholder",
                channel_connection_id="placeholder",
                external_order_id=f"MOCK-STOCKOUT-{day}",
                marketplace_id=MARKETPLACE_ID,
                status=OrderStatus.SHIPPED,
                platform_status="Shipped",
                fulfillment_mode=FulfillmentMode.FBA,
                purchased_at=self.observed_at - timedelta(days=day, hours=1),
                last_updated_at=self.observed_at - timedelta(days=day),
                lines=[
                    OrderLine(
                        seller_sku="MOCK-STOCKOUT",
                        quantity=10,
                        title="Critical Stockout Product",
                        unit_price=Money(Decimal("29.99"), "USD"),
                    )
                ],
                platform_extensions={
                    "data_source": "mock_amazon",
                    "verified_amazon_data": False,
                    "scenario_id": "critical_stockout",
                },
            )
            for day in range(30)
        ]
        return Page(items=orders, next_token=None)

    async def list_inventory(self, *, marketplace_id, next_token=None):
        return Page(
            items=[
                InventoryPosition(
                    tenant_id="placeholder",
                    channel_connection_id="placeholder",
                    seller_sku="MOCK-STOCKOUT",
                    marketplace_id=MARKETPLACE_ID,
                    fulfillment_mode=FulfillmentMode.FBA,
                    available=2,
                    reserved=1,
                    inbound=self.inbound,
                    unsellable=0,
                    synced_at=self.observed_at,
                    platform_extensions={
                        "data_source": "mock_amazon",
                        "verified_amazon_data": False,
                        "scenario_id": "critical_stockout",
                    },
                )
            ],
            next_token=None,
        )

    async def get_listing(self, *, seller_sku, marketplace_id):
        return Listing(
            tenant_id="placeholder",
            channel_connection_id="placeholder",
            variant_id="placeholder",
            marketplace_id=MARKETPLACE_ID,
            seller_sku="MOCK-STOCKOUT",
            external_product_id="ASIN-MOCK-STOCKOUT",
            title="Critical Stockout Product",
            price=Money(Decimal("29.99"), "USD"),
            status="BUYABLE",
            platform_extensions={
                "mock_metadata": {
                    "data_source": "mock_amazon",
                    "verified_amazon_data": False,
                    "scenario_id": "critical_stockout",
                }
            },
        )

    async def close(self) -> None:
        return None


class GoldenSellerCentral:
    def __init__(self, adapter: GoldenStockoutAdapter) -> None:
        self.adapter = adapter

    async def list_scenarios(self):
        return {
            "items": [{"scenario_id": "critical_stockout", "seller_sku": "MOCK-STOCKOUT"}],
            "failures": {},
        }

    async def reset(self):
        self.adapter.inbound = 0
        self.adapter.observed_at = datetime.now(UTC) - timedelta(minutes=5)
        return {"status": "reset", "items": []}

    async def execute(
        self,
        *,
        seller_sku: str,
        action_type: str,
        changes: dict,
        idempotency_key: str,
    ):
        assert seller_sku == "MOCK-STOCKOUT"
        assert action_type == "create_replenishment_plan"
        assert idempotency_key == "golden-stockout-execution"
        before = {"seller_sku": seller_sku, "inbound": self.adapter.inbound}
        self.adapter.inbound = int(changes["inbound_quantity"])
        self.adapter.observed_at = datetime.now(UTC)
        return {
            "status": "recorded",
            "before": before,
            "after": {"seller_sku": seller_sku, "inbound": self.adapter.inbound},
            "event": {
                "external_event_id": "golden-stockout-inventory-event",
                "event_type": "FBA_INVENTORY_AVAILABILITY_CHANGES",
            },
        }


def test_golden_stockout_closes_through_api_approval_execution_and_event_sync(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'golden-stockout.sqlite'}"
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
                id="golden-connection",
                tenant_id="local-demo",
                provider="amazon",
                selling_partner_id="A1LOCALMOCKSELLER",
                region="na",
                marketplace_ids=[MARKETPLACE_ID],
                secret_ref="memory://mock",
                status="active",
                version=1,
            )
        )

    adapter = GoldenStockoutAdapter()
    seller_central = GoldenSellerCentral(adapter)
    import api.routes.mock_scenario_routes as mock_routes
    import api.routes.sync_routes as sync_routes

    monkeypatch.setattr(sync_routes, "create_amazon_adapter", lambda **_: adapter)
    monkeypatch.setattr(mock_routes, "HttpMockSellerCentralAdapter", lambda _base_url: seller_central)

    async def scenario() -> None:
        for handler in app.router.on_startup:
            result = handler()
            if hasattr(result, "__await__"):
                await result
        try:
            owner = _headers(user_id="owner-1", role="owner", scopes="read write admin")
            operator = _headers(user_id="operator-1", role="operator")
            approver_one = _headers(user_id="approver-1", role="approver")
            approver_two = _headers(user_id="approver-2", role="approver")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                reset = await client.post("/api/mock-scenarios/reset", headers=owner)
                assert reset.status_code == 200, reset.text
                assert reset.json()["verified_amazon_data"] is False
                assert reset.json()["seeded"]["data_label"] == "MOCK AMAZON DATA - NOT VERIFIED AMAZON DATA"

                sync = await client.post(
                    "/api/sync/jobs",
                    headers=operator,
                    json={"connection_id": "golden-connection", "resources": ["orders", "inventory", "listings"]},
                )
                assert sync.status_code == 202, sync.text
                sync_task = app.state.task_repository.get(tenant_id="local-demo", task_id=sync.json()["task_id"])
                assert sync_task is not None and sync_task.status == "succeeded"

                issues = await client.get("/api/operations/issues?seller_sku=MOCK-STOCKOUT", headers=operator)
                assert issues.status_code == 200, issues.text
                issue = next(item for item in issues.json()["items"] if item["issue_type"] == "inventory_stockout_risk")
                assert any(
                    evidence.get("data_source") == "mock_amazon" and evidence.get("verified_amazon_data") is False
                    for evidence in issue["evidence"]
                )

                detail = await client.get(f"/api/operations/issues/{issue['id']}/detail", headers=operator)
                assert detail.status_code == 200, detail.text
                action = next(
                    item
                    for item in detail.json()["recommended_actions"]
                    if item["action_type"] == "review_replenishment"
                )

                work_items = await client.get("/api/work-items?view=unassigned&page=1&page_size=200", headers=operator)
                assert work_items.status_code == 200, work_items.text
                work_item = next(
                    item
                    for item in work_items.json()["items"]
                    if item["operational_issue_id"] == issue["id"] and item["recommended_action_id"] == action["id"]
                )
                claimed = await client.post(
                    "/api/work-items/bulk",
                    headers=operator,
                    json={
                        "work_item_ids": [work_item["id"]],
                        "operation": "assign",
                        "assignee_id": "operator-1",
                    },
                )
                assert claimed.status_code == 200, claimed.text
                assert claimed.json()["items"][0]["assignee_id"] == "operator-1"

                acknowledged = await client.patch(
                    f"/api/operations/issues/{issue['id']}",
                    headers=operator,
                    json={"status": "acknowledged", "note": "Claimed from the golden demo queue."},
                )
                assert acknowledged.status_code == 200, acknowledged.text
                started = await client.patch(
                    f"/api/operations/issues/{issue['id']}",
                    headers=operator,
                    json={"status": "in_progress", "note": "Evidence and replenishment quantity reviewed."},
                )
                assert started.status_code == 200, started.text

                submitted = await client.post(
                    f"/api/operations/issues/{issue['id']}/approval-requests",
                    headers=operator,
                    json={
                        "connection_id": "golden-connection",
                        "recommended_action_id": action["id"],
                        "note": "Request independent approval for manual Mock Seller Central execution.",
                    },
                )
                assert submitted.status_code == 201, submitted.text
                request_id = submitted.json()["approval_request"]["request_id"]
                assert (
                    submitted.json()["approval_request"]["payload"]["decision_context"]["provenance"][
                        "verified_amazon_data"
                    ]
                    is False
                )

                first = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver_one,
                    json={"reason": "Inventory evidence and deterministic quantity reviewed."},
                )
                assert first.status_code == 200, first.text
                assert first.json()["status"] == "awaiting_second_approval"
                second = await client.post(
                    f"/api/admin/approvals/{request_id}/approve",
                    headers=approver_two,
                    json={"reason": "Independent review confirms the bounded manual plan."},
                )
                assert second.status_code == 200, second.text
                assert second.json()["status"] == "approved"
                assert second.json()["operational_issue"]["status"] == "approved_for_manual_execution"

                executed = await client.post(
                    "/api/mock-seller-central/execute",
                    headers={**operator, "Idempotency-Key": "golden-stockout-execution"},
                    json={
                        "approval_request_id": request_id,
                        "notes": "Created the approved Mock replenishment plan.",
                        "execution_evidence": {"source": "mock_seller_central_ui"},
                    },
                )
                assert executed.status_code == 200, executed.text
                assert executed.json()["event_accepted"] is True
                assert executed.json()["verified_amazon_data"] is False

                final_issue = await client.get(f"/api/operations/issues/{issue['id']}", headers=operator)
                assert final_issue.status_code == 200, final_issue.text
                assert final_issue.json()["status"] == "resolved"
                assert final_issue.json()["resolution_code"] == "post_sync_verified"

                execution = await client.get(
                    f"/api/manual-executions/{executed.json()['execution_id']}", headers=operator
                )
                assert execution.status_code == 200, execution.text
                assert execution.json()["verification_status"] == "verified"
                assert execution.json()["execution_evidence"]["mock_seller_central"] is True
                assert execution.json()["execution_evidence"]["verified_amazon_data"] is False

                final_work_item = await client.get(f"/api/work-items/{work_item['id']}", headers=operator)
                assert final_work_item.status_code == 200, final_work_item.text
                assert final_work_item.json()["status"] == "completed"
                assert final_work_item.json()["completed_at"] is not None
                assert all(
                    evidence.get("verified_amazon_data") is not True
                    for evidence in final_work_item.json()["evidence_manifest"]
                )

                with app.state.session_factory() as session:
                    event = session.scalar(
                        select(InboxEventRow).where(
                            InboxEventRow.tenant_id == "local-demo",
                            InboxEventRow.external_event_id == "golden-stockout-inventory-event",
                        )
                    )
                    assert event is not None and event.status == "processed"
        finally:
            for handler in app.router.on_shutdown:
                result = handler()
                if hasattr(result, "__await__"):
                    await result

    asyncio.run(scenario())
