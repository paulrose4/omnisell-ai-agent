from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest


def test_profit_calculator_uses_complete_decimal_inputs() -> None:
    from domain.profitability import ProfitCalculationInput, ProfitCalculator
    from domain.models import SKUCostProfile

    profile = SKUCostProfile(
        tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
        product_cost=Decimal("10"), domestic_shipping_cost=Decimal("1"), international_freight_cost=Decimal("2"),
        duty_cost=Decimal("0.5"), prep_cost=Decimal("0.5"), packaging_cost=Decimal("0.5"),
        estimated_referral_fee_rate=Decimal("0.15"), estimated_fulfillment_fee=Decimal("4"),
        estimated_storage_cost=Decimal("0.25"), estimated_return_loss_rate=Decimal("0.02"),
        exchange_rate_buffer_rate=Decimal("0.01"), target_margin_rate=Decimal("0.20"),
        minimum_margin_rate=Decimal("0.10"), source="csv_import", effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = ProfitCalculator().calculate(
        profile=profile,
        input_data=ProfitCalculationInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
            window_start=datetime(2026, 7, 1, tzinfo=UTC), window_end=datetime(2026, 7, 8, tzinfo=UTC),
            item_sales=Decimal("1000"), units=20, discounts=Decimal("20"), coupons=Decimal("10"), refunds=Decimal("50"),
            advertising_cost=Decimal("80"), advertising_attributed_sales=Decimal("400"), other_variable_cost=Decimal("5"),
        ),
    )
    assert result.gross_revenue == Decimal("920.00")
    assert result.landed_cost == Decimal("290.00")
    assert result.referral_fees == Decimal("138.00")
    assert result.return_loss == Decimal("18.40")
    assert result.contribution_profit == Decimal("303.60")
    assert result.contribution_margin == Decimal("0.3300")
    assert result.break_even_price == Decimal("22.87")
    assert result.break_even_acos == Decimal("0.9590")
    assert result.missing_inputs == ()
    assert result.confidence == Decimal("0.85")


def test_profit_calculator_degrades_when_cost_is_missing() -> None:
    from domain.profitability import ProfitCalculationInput, ProfitCalculator
    from domain.models import SKUCostProfile

    profile = SKUCostProfile(
        tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
        product_cost=Decimal("10"), domestic_shipping_cost=None, international_freight_cost=Decimal("2"),
        duty_cost=Decimal("0"), prep_cost=Decimal("0"), packaging_cost=Decimal("0"),
        estimated_referral_fee_rate=Decimal("0.15"), estimated_fulfillment_fee=Decimal("4"),
        estimated_storage_cost=Decimal("0"), estimated_return_loss_rate=Decimal("0"), exchange_rate_buffer_rate=Decimal("0"),
        target_margin_rate=Decimal("0.20"), minimum_margin_rate=Decimal("0.10"), source="manual",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = ProfitCalculator().calculate(
        profile=profile,
        input_data=ProfitCalculationInput(
            tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
            window_start=datetime(2026, 7, 1, tzinfo=UTC), window_end=datetime(2026, 7, 8, tzinfo=UTC),
            item_sales=Decimal("100"), units=2,
        ),
    )
    assert "domestic_shipping_cost" in result.missing_inputs
    assert result.contribution_profit is None
    assert result.break_even_price is None
    assert result.degraded_reason == "required_profit_inputs_missing"


def test_profit_calculator_blocks_currency_mismatch() -> None:
    from domain.profitability import ProfitCalculationInput, ProfitCalculator, ProfitCalculationError
    from domain.models import SKUCostProfile

    profile = SKUCostProfile(
        tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="CNY",
        product_cost=Decimal("10"), domestic_shipping_cost=Decimal("0"), international_freight_cost=Decimal("0"), duty_cost=Decimal("0"),
        prep_cost=Decimal("0"), packaging_cost=Decimal("0"), estimated_referral_fee_rate=Decimal("0.15"),
        estimated_fulfillment_fee=Decimal("4"), estimated_storage_cost=Decimal("0"), estimated_return_loss_rate=Decimal("0"),
        exchange_rate_buffer_rate=Decimal("0"), target_margin_rate=Decimal("0.2"), minimum_margin_rate=Decimal("0.1"),
        source="manual", effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ProfitCalculationError, match="currency"):
        ProfitCalculator().calculate(
            profile=profile,
            input_data=ProfitCalculationInput(
                tenant_id="tenant-a", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER", currency="USD",
                window_start=datetime(2026, 7, 1, tzinfo=UTC), window_end=datetime(2026, 7, 8, tzinfo=UTC), item_sales=Decimal("100"), units=2,
            ),
        )


def test_cost_csv_import_is_safe_idempotent_and_tenant_scoped(tmp_path) -> None:
    from infrastructure.database.bootstrap import initialize_database
    from infrastructure.database.models import TenantRow
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.profitability import SKUCostProfileRepository
    from application.cost_imports import CostCSVImporter

    database_url = f"sqlite:///{tmp_path / 'costs.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    factory = create_session_factory(database_url)
    with factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    repository = SKUCostProfileRepository(factory)
    importer = CostCSVImporter(repository)
    csv_text = """seller_sku,marketplace_id,currency,product_cost,domestic_shipping_cost,international_freight_cost,duty_cost,prep_cost,packaging_cost,estimated_referral_fee_rate,estimated_fulfillment_fee,estimated_storage_cost,estimated_return_loss_rate,exchange_rate_buffer_rate,target_margin_rate,minimum_margin_rate,effective_from,source\nSKU-1,ATVPDKIKX0DER,USD,10,1,2,0.5,0.5,0.5,0.15,4,0.25,0.02,0.01,0.20,0.10,2026-01-01T00:00:00Z,csv_import\n"""
    dry_run = importer.import_bytes(tenant_id="tenant-a", actor_id="owner", content=csv_text.encode(), idempotency_key="cost-1", dry_run=True)
    assert dry_run.valid_rows == 1 and dry_run.committed_rows == 0
    committed = importer.import_bytes(tenant_id="tenant-a", actor_id="owner", content=csv_text.encode(), idempotency_key="cost-1", dry_run=False)
    repeated = importer.import_bytes(tenant_id="tenant-a", actor_id="owner", content=csv_text.encode(), idempotency_key="cost-1", dry_run=False)
    assert committed.job_id == repeated.job_id
    assert committed.committed_rows == 1
    assert repository.get_active(tenant_id="tenant-a", marketplace_id="ATVPDKIKX0DER", seller_sku="SKU-1", effective_at=datetime(2026, 7, 1, tzinfo=UTC)) is not None
    assert repository.get_active(tenant_id="tenant-b", marketplace_id="ATVPDKIKX0DER", seller_sku="SKU-1", effective_at=datetime(2026, 7, 1, tzinfo=UTC)) is None

    malicious = csv_text.replace("SKU-1", "=HYPERLINK(\"https://evil\")")
    rejected = importer.import_bytes(tenant_id="tenant-a", actor_id="owner", content=malicious.encode(), idempotency_key="cost-2", dry_run=True)
    assert rejected.valid_rows == 0
    assert rejected.errors[0].code == "csv_injection"



def test_profitability_api_import_calculate_and_tenant_isolation(tmp_path) -> None:
    import asyncio
    import httpx
    from api.config import Settings
    from api.main import create_app
    from infrastructure.database.bootstrap import initialize_database
    from infrastructure.database.models import TenantRow

    database_url = f"sqlite:///{tmp_path / 'api-profit.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key"))
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    csv_text = """seller_sku,marketplace_id,currency,product_cost,domestic_shipping_cost,international_freight_cost,duty_cost,prep_cost,packaging_cost,estimated_referral_fee_rate,estimated_fulfillment_fee,estimated_storage_cost,estimated_return_loss_rate,exchange_rate_buffer_rate,target_margin_rate,minimum_margin_rate,effective_from,source\nSKU-1,ATVPDKIKX0DER,USD,10,1,2,0.5,0.5,0.5,0.15,4,0.25,0.02,0.01,0.20,0.10,2026-01-01T00:00:00Z,csv_import\n"""

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                headers_a = {"X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "owner", "X-User-Role": "owner", "Idempotency-Key": "api-cost-1"}
                response = await client.post("/api/profitability/cost-profiles/import?dry_run=false", headers=headers_a, files={"file": ("costs.csv", csv_text, "text/csv")})
                assert response.status_code == 200, response.text
                assert response.json()["committed_rows"] == 1
                listed_a = await client.get("/api/profitability/cost-profiles", headers=headers_a)
                headers_b = {**headers_a, "X-Tenant-ID": "tenant-b", "Idempotency-Key": "unused"}
                listed_b = await client.get("/api/profitability/cost-profiles", headers=headers_b)
                assert listed_a.json()["total"] == 1
                assert listed_b.json()["total"] == 0
                viewer_headers = {**headers_a, "X-Permission-Scopes": "read", "Idempotency-Key": "viewer-write"}
                forbidden = await client.post(
                    "/api/profitability/cost-profiles/import?dry_run=false", headers=viewer_headers,
                    files={"file": ("costs.csv", csv_text, "text/csv")},
                )
                assert forbidden.status_code == 403
                profit = await client.post(
                    "/api/profitability/snapshots/calculate", headers=headers_a,
                    json={
                        "seller_sku": "SKU-1", "marketplace_id": "ATVPDKIKX0DER", "currency": "USD",
                        "window_start": "2026-07-01T00:00:00Z", "window_end": "2026-07-08T00:00:00Z",
                        "item_sales": "1000", "units": 20, "discounts": "20", "coupons": "10", "refunds": "50",
                        "advertising_cost": "80", "advertising_attributed_sales": "400", "other_variable_cost": "5",
                    },
                )
                assert profit.status_code == 200, profit.text
                assert profit.json()["contribution_profit"] == "303.60"
                assert profit.json()["calculation_version"] == "sku-profit-v1"
                missing_for_b = await client.post(
                    "/api/profitability/snapshots/calculate", headers=headers_b,
                    json={
                        "seller_sku": "SKU-1", "marketplace_id": "ATVPDKIKX0DER", "currency": "USD",
                        "window_start": "2026-07-01T00:00:00Z", "window_end": "2026-07-08T00:00:00Z", "item_sales": "100", "units": 2,
                    },
                )
                assert missing_for_b.status_code == 422
                assert missing_for_b.json()["error"] == "missing_cost_profile"
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
