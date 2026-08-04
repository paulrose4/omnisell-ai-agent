from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import event

from api.config import Settings
from api.main import create_app
from domain.models import FulfillmentMode, Money, Order, OrderLine, OrderStatus
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ChannelConnectionRow


MARKETPLACE_ID = "ATVPDKIKX0DER"


def _headers(user_id: str, role: str, scopes: str = "read write") -> dict[str, str]:
    return {
        "X-API-Key": "test-key",
        "X-Tenant-ID": "local-demo",
        "X-User-ID": user_id,
        "X-User-Role": role,
        "X-Permission-Scopes": scopes,
    }


def test_mock_customer_service_case360_policy_and_hitl(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'mock-customer-service.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    app = create_app(Settings(
        database_url=database_url,
        auto_create_schema=False,
        api_key="test-key",
        auth_mode="api_key",
        mock_drill_enabled=True,
        amazon_writes_enabled=False,
        amazon_pilot_read_only=True,
    ))
    engine = app.state.session_factory.kw["bind"]
    event.listen(engine, "connect", lambda dbapi_connection, _: dbapi_connection.execute("PRAGMA foreign_keys=ON"))
    engine.dispose()
    with app.state.session_factory.begin() as session:
        session.add(ChannelConnectionRow(
            id="mock-connection", tenant_id="local-demo", provider="amazon",
            selling_partner_id="mock-seller", region="na", marketplace_ids=[MARKETPLACE_ID],
            secret_ref="memory://mock", status="active", version=1,
        ))
    app.state.commerce_sync_repository.upsert_order(Order(
        tenant_id="local-demo",
        channel_connection_id="mock-connection",
        external_order_id="MOCK-CS-ORDER-1",
        marketplace_id=MARKETPLACE_ID,
        status=OrderStatus.SHIPPED,
        fulfillment_mode=FulfillmentMode.FBA,
        purchased_at=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
        platform_status="Shipped",
        lines=[OrderLine(
            seller_sku="MOCK-STOCKOUT",
            quantity=1,
            title="Mock Travel Bottle",
            unit_price=Money(Decimal("29.99"), "USD"),
        )],
        platform_extensions={"data_source": "mock_amazon", "verified_amazon_data": False},
    ))

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            operator = _headers("operator-1", "operator")
            approver = _headers("approver-1", "approver")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                scenarios = await client.get("/api/mock-customer-service/scenarios", headers=operator)
                assert scenarios.status_code == 200
                assert {item["scenario_id"] for item in scenarios.json()["items"]} >= {
                    "delivery_delay", "refund_demand", "cancel_request", "damaged_item",
                }

                created = await client.post(
                    "/api/mock-customer-service/scenarios/delivery_delay/trigger",
                    headers=operator,
                    json={"external_order_id": "MOCK-CS-ORDER-1"},
                )
                assert created.status_code == 201, created.text
                delivery_case = created.json()
                assert delivery_case["intent"] == "delivery_delay"
                assert delivery_case["source"] == "mock_buyer"
                assert delivery_case["verified_amazon_data"] is False
                assert delivery_case["evidence"]["order"]["external_order_id"] == "MOCK-CS-ORDER-1"
                assert delivery_case["draft_reply"]
                assert delivery_case["status"] == "ready_to_send"

                claimed = await client.patch(
                    f"/api/customer-cases/{delivery_case['id']}",
                    headers=operator,
                    json={"status": "acknowledged", "note": "Claimed for this shift"},
                )
                assert claimed.status_code == 200
                assert claimed.json()["assignee_id"] == "operator-1"

                unsafe_send = await client.post(
                    f"/api/customer-cases/{delivery_case['id']}/send",
                    headers=operator,
                    json={"reply": "Contact us on WhatsApp for faster help.", "note": "must be blocked"},
                )
                assert unsafe_send.status_code == 409

                sent = await client.post(
                    f"/api/customer-cases/{delivery_case['id']}/send",
                    headers=operator,
                    json={"reply": delivery_case["draft_reply"], "note": "Sent in Mock Buyer-Seller Messaging"},
                )
                assert sent.status_code == 200
                assert sent.json()["status"] == "sent"

                resolved = await client.post(
                    f"/api/customer-cases/{delivery_case['id']}/resolve",
                    headers=operator,
                    json={"resolution_code": "information_provided", "root_cause_code": "amazon_fulfillment_issue", "note": "Buyer informed"},
                )
                assert resolved.status_code == 200
                assert resolved.json()["status"] == "resolved"

                refund = await client.post(
                    "/api/mock-customer-service/scenarios/refund_demand/trigger",
                    headers=operator,
                    json={"external_order_id": "MOCK-CS-ORDER-1"},
                )
                assert refund.status_code == 201
                assert refund.json()["status"] == "policy_blocked"
                assert refund.json()["policy_checks"]["decision"] == "block"

                cancellation = await client.post(
                    "/api/mock-customer-service/scenarios/cancel_request/trigger",
                    headers=operator,
                    json={"external_order_id": "MOCK-CS-ORDER-1"},
                )
                assert cancellation.status_code == 201
                assert cancellation.json()["status"] == "policy_blocked"
                assert cancellation.json()["policy_checks"]["decision"] == "block"
                assert cancellation.json()["approval_request_id"] is None

                damaged = await client.post(
                    "/api/mock-customer-service/scenarios/damaged_item/trigger",
                    headers=operator,
                    json={"external_order_id": "MOCK-CS-ORDER-1"},
                )
                assert damaged.status_code == 201
                damaged_case = damaged.json()
                assert damaged_case["status"] == "awaiting_approval"
                assert damaged_case["approval_request_id"]

                case_page = await client.get("/api/customer-cases?page=2&page_size=2", headers=operator)
                assert case_page.status_code == 200, case_page.text
                assert case_page.json()["total"] == 4
                assert case_page.json()["page"] == 2
                assert case_page.json()["page_size"] == 2
                assert case_page.json()["pages"] == 2
                assert case_page.json()["has_next"] is False
                assert len(case_page.json()["items"]) == 2

                self_approval = await client.post(
                    f"/api/admin/approvals/{damaged_case['approval_request_id']}/approve",
                    headers=operator,
                    json={"reason": "self approval should fail"},
                )
                assert self_approval.status_code == 403

                approved = await client.post(
                    f"/api/admin/approvals/{damaged_case['approval_request_id']}/approve",
                    headers=approver,
                    json={"reason": "Reply is factual and does not promise compensation"},
                )
                assert approved.status_code == 200, approved.text
                detail = await client.get(f"/api/customer-cases/{damaged_case['id']}", headers=operator)
                assert detail.status_code == 200
                assert detail.json()["status"] == "ready_to_send"
                changed_after_approval = await client.post(
                    f"/api/customer-cases/{damaged_case['id']}/send",
                    headers=operator,
                    json={"reply": f"{detail.json()['draft_reply']} We guarantee compensation.", "note": "approval hash changed"},
                )
                assert changed_after_approval.status_code == 409

                audit = await client.get("/api/admin/audit?limit=100", headers=_headers("owner-1", "owner", "read write admin"))
                actions = {item["action"] for item in audit.json()["items"]}
                assert "customer_case_created" in actions
                assert "customer_reply_sent_mock" in actions
                assert "customer_policy_blocked" in actions
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
