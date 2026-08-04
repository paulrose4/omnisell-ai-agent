from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx


def _policy():
    from domain.models import InventoryPolicy

    return InventoryPolicy(
        tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
        supplier_lead_time_days=5, production_lead_time_days=10, shipping_lead_time_days=25,
        amazon_receiving_days=7, safety_stock_days=7, target_coverage_days=60,
        minimum_order_quantity=100, case_pack_quantity=20, preferred_shipping_mode="sea",
        sales_weight_7d=Decimal("0.50"), sales_weight_14d=Decimal("0.30"), sales_weight_30d=Decimal("0.20"),
        source="manual", effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_inventory_health_calculates_replenishment_with_moq_and_case_pack() -> None:
    from domain.inventory import InventoryHealthCalculator, InventoryHealthInput

    calculated_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    result = InventoryHealthCalculator().calculate(
        policy=_policy(),
        input_data=InventoryHealthInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
            available=80, reserved=10, inbound=50, in_transit=100, unsellable=5,
            sales_7_days=70, sales_14_days=126, sales_30_days=240,
            promotion_factor=Decimal("1.10"), seasonality_factor=Decimal("1.00"),
            inventory_source="amazon_api", inventory_collected_at=calculated_at - timedelta(hours=1), calculated_at=calculated_at,
            unit_revenue=Decimal("40"), unit_contribution_profit=Decimal("12"), estimated_extra_logistics_cost_per_unit=Decimal("3"),
        ),
    )
    assert result.inventory_position == 215
    assert result.weighted_daily_sales == Decimal("10.2300")
    assert result.days_of_supply == Decimal("21.02")
    assert result.total_lead_time_days == 47
    assert result.safety_stock_units == Decimal("71.61")
    assert result.reorder_point == Decimal("552.42")
    assert result.recommended_reorder_quantity == 400
    assert result.inventory_health == "critical_stockout_risk"
    assert result.expected_stockout_date == datetime(2026, 8, 4, 0, 28, 48, tzinfo=UTC)
    assert result.automation_eligible is True
    assert result.missing_data == ()
    assert result.estimated_revenue_at_risk == Decimal("10631.02")
    assert result.estimated_profit_at_risk == Decimal("3189.30")
    assert result.estimated_extra_logistics_cost == Decimal("1200.00")



def test_inventory_health_blocks_replenishment_automation_for_loss_making_sku() -> None:
    from domain.inventory import InventoryHealthCalculator, InventoryHealthInput

    calculated_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    result = InventoryHealthCalculator().calculate(
        policy=_policy(),
        input_data=InventoryHealthInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
            available=20, reserved=0, inbound=0, in_transit=0, unsellable=0,
            sales_7_days=70, sales_14_days=140, sales_30_days=300,
            inventory_source="amazon_api", inventory_collected_at=calculated_at, calculated_at=calculated_at,
            unit_revenue=Decimal("12.00"), unit_contribution_profit=Decimal("-4.00"),
        ),
    )

    assert result.stockout_risk == "critical"
    assert result.estimated_revenue_at_risk > 0
    assert result.estimated_profit_at_risk == Decimal("0.00")
    assert result.automation_eligible is False
    assert result.degraded_reason == "negative_unit_contribution_profit"
    assert result.input_manifest["unit_contribution_profit"] == "-4.00"


def test_inventory_health_does_not_double_subtract_amazon_reserved_inventory() -> None:
    from domain.inventory import InventoryHealthCalculator, InventoryHealthInput

    calculated_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    result = InventoryHealthCalculator().calculate(
        policy=_policy(),
        input_data=InventoryHealthInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
            available=80, reserved=10, inbound=0, in_transit=0, unsellable=5,
            sales_7_days=70, sales_14_days=140, sales_30_days=300,
            inventory_source="amazon_api", inventory_collected_at=calculated_at, calculated_at=calculated_at,
            available_is_net_of_reserved=True,
        ),
    )
    assert result.inventory_position == 75
    assert result.input_manifest["available_is_net_of_reserved"] is True
def test_inventory_health_degrades_for_zero_sales_and_stale_inventory() -> None:
    from domain.inventory import InventoryHealthCalculator, InventoryHealthInput

    calculated_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    result = InventoryHealthCalculator().calculate(
        policy=_policy(),
        input_data=InventoryHealthInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
            available=500, reserved=0, inbound=0, in_transit=0, unsellable=0,
            sales_7_days=0, sales_14_days=0, sales_30_days=0,
            inventory_source="amazon_api", inventory_collected_at=calculated_at - timedelta(hours=12), calculated_at=calculated_at,
        ),
    )
    assert result.weighted_daily_sales == Decimal("0.0000")
    assert result.days_of_supply is None
    assert result.expected_stockout_date is None
    assert result.recommended_reorder_quantity == 0
    assert result.inventory_health == "dormant_inventory"
    assert "zero_sales_velocity" in result.missing_data
    assert "stale_inventory" in result.missing_data
    assert result.automation_eligible is False
    assert result.degraded_reason == "inventory_inputs_not_automation_safe"



def test_inventory_health_marks_incomplete_or_distorted_demand_as_not_automatic() -> None:
    from domain.inventory import InventoryHealthCalculator, InventoryHealthInput

    calculated_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    result = InventoryHealthCalculator().calculate(
        policy=_policy(),
        input_data=InventoryHealthInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
            available=60, reserved=0, inbound=0, in_transit=0, unsellable=0,
            sales_7_days=70, sales_14_days=140, sales_30_days=None,
            inventory_source="csv_import", inventory_collected_at=calculated_at, calculated_at=calculated_at,
            sales_spike=True, stockout_distorted_sales=True,
        ),
    )
    assert result.weighted_daily_sales == Decimal("10.0000")
    assert "sales_30_days" in result.missing_data
    assert set(result.data_warnings) == {"sales_spike", "stockout_distorted_sales"}
    assert result.automation_eligible is False
    assert result.forecast_confidence < Decimal("0.70")
def test_inventory_policy_repository_is_versioned_and_tenant_scoped(tmp_path) -> None:
    from infrastructure.database.bootstrap import initialize_database
    from infrastructure.database.models import TenantRow
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.inventory import InventoryPolicyRepository

    database_url = f"sqlite:///{tmp_path / 'inventory.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    factory = create_session_factory(database_url)
    with factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    repository = InventoryPolicyRepository(factory)
    first = repository.save(_policy())
    second_policy = _policy()
    second_policy.effective_from = datetime(2026, 8, 1, tzinfo=UTC)
    second_policy.target_coverage_days = 75
    second = repository.save(second_policy)
    assert first.version == 1 and second.version == 2
    assert repository.get_active(tenant_id="tenant-a", marketplace_id="ATVPDKIKX0DER", seller_sku="SKU-1", effective_at=datetime(2026, 7, 15, tzinfo=UTC)).version == 1
    assert repository.get_active(tenant_id="tenant-a", marketplace_id="ATVPDKIKX0DER", seller_sku="SKU-1", effective_at=datetime(2026, 8, 2, tzinfo=UTC)).version == 2
    assert repository.get_active(tenant_id="tenant-b", marketplace_id="ATVPDKIKX0DER", seller_sku="SKU-1", effective_at=datetime(2026, 8, 2, tzinfo=UTC)) is None


def test_inventory_api_policy_and_health_are_tenant_scoped(tmp_path) -> None:
    from api.config import Settings
    from api.main import create_app
    from infrastructure.database.bootstrap import initialize_database
    from infrastructure.database.models import TenantRow

    database_url = f"sqlite:///{tmp_path / 'inventory-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key"))
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers_a = {"X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "owner", "X-User-Role": "owner", "X-Permission-Scopes": "read write admin"}
                policy_payload = {
                    "seller_sku": "SKU-1", "marketplace_id": "ATVPDKIKX0DER",
                    "supplier_lead_time_days": 5, "production_lead_time_days": 10, "shipping_lead_time_days": 25,
                    "amazon_receiving_days": 7, "safety_stock_days": 7, "target_coverage_days": 60,
                    "minimum_order_quantity": 100, "case_pack_quantity": 20, "preferred_shipping_mode": "sea",
                    "sales_weight_7d": "0.50", "sales_weight_14d": "0.30", "sales_weight_30d": "0.20",
                    "source": "manual", "effective_from": "2026-01-01T00:00:00Z",
                }
                saved = await client.post("/api/inventory/policies", headers=headers_a, json=policy_payload)
                assert saved.status_code == 200, saved.text
                health = await client.post("/api/inventory/health/calculate", headers=headers_a, json={
                    "seller_sku": "SKU-1", "marketplace_id": "ATVPDKIKX0DER",
                    "available": 80, "reserved": 10, "inbound": 50, "in_transit": 100, "unsellable": 5,
                    "sales_7_days": 70, "sales_14_days": 126, "sales_30_days": 240,
                    "promotion_factor": "1.10", "seasonality_factor": "1.00", "inventory_source": "amazon_api",
                    "inventory_collected_at": "2026-07-13T23:00:00Z", "calculated_at": "2026-07-14T00:00:00Z",
                })
                assert health.status_code == 200, health.text
                assert health.json()["recommended_reorder_quantity"] == 400
                updated_policy = {**policy_payload, "effective_from": "2026-08-01T00:00:00Z", "target_coverage_days": 75}
                pending = await client.post("/api/inventory/policies", headers=headers_a, json=updated_policy)
                assert pending.status_code == 202
                approval_id = pending.json()["approval_request"]["request_id"]
                viewer_headers = {**headers_a, "X-User-ID": "viewer", "X-User-Role": "viewer", "X-Permission-Scopes": "read"}
                viewer_approval = await client.post(
                    f"/api/admin/approvals/{approval_id}/approve", headers=viewer_headers,
                    json={"reason": "not authorized"},
                )
                assert viewer_approval.status_code == 403
                approver_headers = {**headers_a, "X-User-ID": "approver", "X-User-Role": "approver"}
                approved = await client.post(
                    f"/api/admin/approvals/{approval_id}/approve", headers=approver_headers,
                    json={"reason": "lead time reviewed"},
                )
                assert approved.status_code == 200, approved.text
                updated_policy["approval_request_id"] = approval_id
                committed_update = await client.post("/api/inventory/policies", headers=headers_a, json=updated_policy)
                assert committed_update.status_code == 200, committed_update.text
                assert committed_update.json()["version"] == 2
                headers_b = {**headers_a, "X-Tenant-ID": "tenant-b"}
                missing = await client.post("/api/inventory/health/calculate", headers=headers_b, json={
                    "seller_sku": "SKU-1", "marketplace_id": "ATVPDKIKX0DER", "available": 1, "reserved": 0,
                    "inbound": 0, "in_transit": 0, "unsellable": 0, "sales_7_days": 7, "sales_14_days": 14,
                    "sales_30_days": 30, "inventory_source": "manual", "inventory_collected_at": "2026-07-14T00:00:00Z",
                    "calculated_at": "2026-07-14T00:00:00Z",
                })
                assert missing.status_code == 422
                assert missing.json()["error"] == "missing_inventory_policy"
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
