from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from api.config import Settings
from api.main import create_app
from domain.models import InventoryHealthSnapshot, SKUCostProfile, SKUProfitSnapshot
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import TenantRow


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def _profile(*, tenant_id: str = "tenant-a") -> SKUCostProfile:
    return SKUCostProfile(
        id="cost-1", tenant_id=tenant_id, seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
        product_cost=Decimal("5"), domestic_shipping_cost=Decimal("0"), international_freight_cost=Decimal("0"),
        duty_cost=Decimal("0"), prep_cost=Decimal("0"), packaging_cost=Decimal("0"),
        estimated_referral_fee_rate=Decimal("0.15"), estimated_fulfillment_fee=Decimal("2"),
        estimated_storage_cost=Decimal("0"), estimated_return_loss_rate=Decimal("0.02"),
        exchange_rate_buffer_rate=Decimal("0.01"), target_margin_rate=Decimal("0.20"),
        minimum_margin_rate=Decimal("0.10"), effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        source="amazon_api", collected_at=NOW - timedelta(days=1), created_at=NOW - timedelta(days=1),
    )


def _profit(*, tenant_id: str = "tenant-a", degraded: bool = False) -> SKUProfitSnapshot:
    return SKUProfitSnapshot(
        id="profit-1", tenant_id=tenant_id, seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
        window_start=NOW - timedelta(days=7), window_end=NOW, revenue=Decimal("1500"), units=100,
        refunds=Decimal("0"), promotions=Decimal("0"), referral_fees=Decimal("225"), fulfillment_fees=Decimal("200"),
        storage_cost=Decimal("0"), advertising_cost=Decimal("100"), landed_cost=Decimal("500"),
        return_loss=Decimal("30"), other_variable_cost=Decimal("0"), contribution_profit=Decimal("445"),
        contribution_margin=Decimal("0.2967"), break_even_price=Decimal("8.54"), break_even_acos=Decimal("0.30"),
        calculated_at=NOW - timedelta(hours=30 if degraded else 1), calculation_version="sku-profit-v1",
        missing_inputs=("actual_storage_cost",) if degraded else (), confidence=Decimal("0.60" if degraded else "0.95"),
        degraded_reason="profit_snapshot_stale" if degraded else None, input_manifest={"cost_profile_id": "cost-1"},
        estimated=degraded,
    )


def _inventory(*, tenant_id: str = "tenant-a", stockout: bool = False, degraded: bool = False) -> InventoryHealthSnapshot:
    return InventoryHealthSnapshot(
        id="inventory-1", tenant_id=tenant_id, seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
        policy_id="policy-1", policy_version=1, available=20 if stockout else 500, reserved=0, inbound=0,
        in_transit=0, unsellable=0, inventory_position=20 if stockout else 500, weighted_daily_sales=Decimal("5"),
        days_of_supply=Decimal("4" if stockout else "100"), expected_stockout_date=NOW + timedelta(days=4 if stockout else 100),
        total_lead_time_days=35, safety_stock_units=Decimal("35"), reorder_point=Decimal("210"),
        recommended_reorder_quantity=400 if stockout else 0, excess_units=0, recommended_shipping_mode="air",
        inventory_health="stockout_risk" if stockout else "healthy", stockout_risk="critical" if stockout else "none",
        excess_risk="none", forecast_confidence=Decimal("0.60" if degraded else "0.95"),
        missing_data=("sales_30_days",) if degraded else (), data_warnings=("inventory_stale",) if degraded else (),
        degraded_reason="inventory_stale" if degraded else None, automation_eligible=not degraded,
        inventory_source="amazon_api", inventory_collected_at=NOW - timedelta(hours=8 if degraded else 1),
        calculated_at=NOW, calculation_version="inventory-health-v1", input_manifest={},
    )


def test_pricing_blocks_requested_price_below_break_even_floor() -> None:
    from domain.pricing import PriceRecommendationCalculator, PriceRecommendationInput

    result = PriceRecommendationCalculator().calculate(
        input_data=PriceRecommendationInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
            current_price=Decimal("15"), requested_price=Decimal("8"), competitor_prices=(Decimal("12"), Decimal("13")),
            calculated_at=NOW,
        ),
        profile=_profile(), profit_snapshot=_profit(), inventory_snapshot=_inventory(),
    )

    assert result.status == "blocked"
    assert result.policy_decision == "block"
    assert result.recommended_price == Decimal("8.00")
    assert result.break_even_floor > Decimal("8")
    assert result.action_hash
    assert "below_break_even_floor" in result.policy_reasons
    assert result.automation_eligible is False


def test_pricing_requires_approval_below_minimum_margin_and_blocks_stockout_discount() -> None:
    from domain.pricing import PriceRecommendationCalculator, PriceRecommendationInput

    calculator = PriceRecommendationCalculator()
    below_margin = calculator.calculate(
        input_data=PriceRecommendationInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
            current_price=Decimal("15"), requested_price=Decimal("10.50"), competitor_prices=(), calculated_at=NOW,
        ),
        profile=_profile(), profit_snapshot=_profit(), inventory_snapshot=_inventory(),
    )
    assert below_margin.status == "awaiting_approval"
    assert below_margin.policy_decision == "require_approval"
    assert below_margin.projected_margin < Decimal("0.10")
    assert "below_minimum_margin" in below_margin.policy_reasons

    stockout_discount = calculator.calculate(
        input_data=PriceRecommendationInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
            current_price=Decimal("15"), requested_price=Decimal("14"), competitor_prices=(), calculated_at=NOW,
        ),
        profile=_profile(), profit_snapshot=_profit(), inventory_snapshot=_inventory(stockout=True),
    )
    assert stockout_discount.status == "blocked"
    assert "price_decrease_during_critical_stockout" in stockout_discount.policy_reasons


def test_pricing_only_allows_small_change_with_complete_fresh_profit_and_inventory() -> None:
    from domain.pricing import PriceRecommendationCalculator, PriceRecommendationInput

    input_data = PriceRecommendationInput(
        tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
        current_price=Decimal("15"), requested_price=Decimal("15.20"), competitor_prices=(), calculated_at=NOW,
    )
    safe = PriceRecommendationCalculator().calculate(
        input_data=input_data, profile=_profile(), profit_snapshot=_profit(), inventory_snapshot=_inventory(),
    )
    assert safe.policy_decision == "allow"
    assert safe.status == "ready"
    assert safe.automation_eligible is True

    degraded = PriceRecommendationCalculator().calculate(
        input_data=input_data, profile=_profile(), profit_snapshot=_profit(degraded=True), inventory_snapshot=_inventory(degraded=True),
    )
    assert degraded.status == "awaiting_approval"
    assert degraded.automation_eligible is False
    assert "profit_snapshot_stale" in degraded.policy_reasons
    assert "inventory_stale" in degraded.policy_reasons


def test_price_recommendation_api_persists_hitl_and_enforces_tenant_scope(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pricing-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key"))
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    app.state.cost_profile_repository.save_profiles(tenant_id="tenant-a", profiles=[_profile()])
    app.state.profit_snapshot_repository.save(_profit())
    app.state.inventory_health_repository.save(_inventory())

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers_owner = {
                    "X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "owner-1",
                    "X-User-Role": "owner", "X-Permission-Scopes": "read write admin",
                }
                payload = {
                    "seller_sku": "SKU-1", "marketplace_id": "ATVPDKIKX0DER", "currency": "USD",
                    "current_price": "15", "requested_price": "10.50", "competitor_prices": [],
                    "profit_snapshot_id": "profit-1", "inventory_snapshot_id": "inventory-1",
                    "calculated_at": "2026-07-14T12:00:00Z",
                }
                pending = await client.post("/api/price/recommendations/calculate", headers=headers_owner, json=payload)
                assert pending.status_code == 202, pending.text
                body = pending.json()
                assert body["recommendation"]["status"] == "awaiting_approval"
                assert body["approval_request"]["payload"]["action_hash"] == body["recommendation"]["action_hash"]
                approval_id = body["approval_request"]["request_id"]

                headers_approver = {**headers_owner, "X-User-ID": "approver-1", "X-User-Role": "approver"}
                approved = await client.post(
                    f"/api/admin/approvals/{approval_id}/approve", headers=headers_approver,
                    json={"reason": "margin exception reviewed"},
                )
                assert approved.status_code == 200, approved.text
                approved_request = {**payload, "approval_request_id": approval_id}
                repeated = await client.post("/api/price/recommendations/calculate", headers=headers_owner, json=approved_request)
                assert repeated.status_code == 200, repeated.text
                assert repeated.json()["recommendation"]["status"] == "approved"

                listed = await client.get("/api/price/recommendations/persistent", headers=headers_owner)
                assert listed.status_code == 200
                assert listed.json()["total"] == 1
                assert listed.json()["items"][0]["occurrence_count"] == 2

                headers_b = {**headers_owner, "X-Tenant-ID": "tenant-b"}
                hidden = await client.get("/api/price/recommendations/persistent", headers=headers_b)
                assert hidden.status_code == 200
                assert hidden.json()["total"] == 0
                foreign = await client.post("/api/price/recommendations/calculate", headers=headers_b, json=payload)
                assert foreign.status_code == 404
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_pricing_guardrail_eval_dataset() -> None:
    import json
    from pathlib import Path
    from domain.pricing import PriceRecommendationCalculator, PriceRecommendationInput

    cases = json.loads((Path(__file__).parents[1] / "evals" / "pricing_guardrails.json").read_text(encoding="utf-8"))
    calculator = PriceRecommendationCalculator()
    results = {}
    for case in cases:
        recommendation = calculator.calculate(
            input_data=PriceRecommendationInput(
                tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
                current_price=Decimal("15"), requested_price=Decimal(case["requested_price"]),
                competitor_prices=(), calculated_at=NOW,
            ),
            profile=_profile(), profit_snapshot=_profit(degraded=case["degraded"]),
            inventory_snapshot=_inventory(stockout=case["stockout"], degraded=case["degraded"]),
        )
        results[case["id"]] = recommendation.policy_decision
        assert recommendation.policy_decision == case["expected_decision"], case["id"]
    assert len(results) == len(cases)
