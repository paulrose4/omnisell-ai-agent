from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from domain.models import InventoryPolicy, SKUCostProfile
from infrastructure.repositories.inventory import InventoryPolicyRepository
from infrastructure.repositories.profitability import SKUCostProfileRepository


MARKETPLACE_ID = "ATVPDKIKX0DER"
MOCK_SKUS = (
    "MOCK-HEALTHY",
    "MOCK-STOCKOUT",
    "MOCK-OOS",
    "MOCK-EXCESS",
    "MOCK-LOSS",
    "MOCK-MISSING-COST",
    "MOCK-STALE",
    "MOCK-LISTING",
    "MOCK-UNKNOWN-ORDER",
    "MOCK-MISMATCH",
)


def seed_golden_mock_tenant(*, session_factory: sessionmaker[Session], tenant_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Seed only deterministic operator configuration for the Mock Amazon drill.

    Platform observations, snapshots, issues and briefs must be produced through
    Mock Amazon -> AmazonReadSyncService -> OperationsRefreshService.
    """
    generated_at = now or datetime.now(UTC)
    if generated_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    cost_repository = SKUCostProfileRepository(session_factory)
    policy_repository = InventoryPolicyRepository(session_factory)
    existing_costs = {item.seller_sku for item in cost_repository.list_profiles(tenant_id=tenant_id, marketplace_id=MARKETPLACE_ID)}
    existing_policies = {item.seller_sku for item in policy_repository.list_policies(tenant_id=tenant_id, marketplace_id=MARKETPLACE_ID)}

    for seller_sku in MOCK_SKUS:
        if seller_sku not in existing_policies:
            policy_repository.save(InventoryPolicy(
                tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=MARKETPLACE_ID,
                supplier_lead_time_days=5, production_lead_time_days=10, shipping_lead_time_days=25,
                amazon_receiving_days=7, safety_stock_days=7, target_coverage_days=60,
                minimum_order_quantity=100, case_pack_quantity=20, preferred_shipping_mode="sea",
                sales_weight_7d=Decimal("0.50"), sales_weight_14d=Decimal("0.30"), sales_weight_30d=Decimal("0.20"),
                source="mock", effective_from=generated_at - timedelta(days=30),
                collected_at=generated_at, created_at=generated_at,
            ))
        if seller_sku == "MOCK-MISSING-COST" or seller_sku in existing_costs:
            continue
        is_loss = seller_sku == "MOCK-LOSS"
        cost_repository.save_profiles(tenant_id=tenant_id, profiles=[SKUCostProfile(
            tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=MARKETPLACE_ID, currency="USD",
            product_cost=Decimal("19.00") if is_loss else Decimal("6.00"),
            domestic_shipping_cost=Decimal("2.50") if is_loss else Decimal("0.60"),
            international_freight_cost=Decimal("1.50"), duty_cost=Decimal("0.50"),
            prep_cost=Decimal("0.40"), packaging_cost=Decimal("0.35"),
            estimated_referral_fee_rate=Decimal("0.15"), estimated_fulfillment_fee=Decimal("4.25"),
            estimated_storage_cost=Decimal("0.20"), estimated_return_loss_rate=Decimal("0.02"),
            exchange_rate_buffer_rate=Decimal("0.01"), target_margin_rate=Decimal("0.20"),
            minimum_margin_rate=Decimal("0.10"), source="mock",
            effective_from=generated_at - timedelta(days=30), collected_at=generated_at, created_at=generated_at,
        )])

    cost_count = len([item for item in cost_repository.list_profiles(tenant_id=tenant_id, marketplace_id=MARKETPLACE_ID) if item.seller_sku in MOCK_SKUS])
    policy_count = len([item for item in policy_repository.list_policies(tenant_id=tenant_id, marketplace_id=MARKETPLACE_ID) if item.seller_sku in MOCK_SKUS])
    return {
        "mock_data": True,
        "data_source": "mock_amazon",
        "verified_amazon_data": False,
        "data_label": "MOCK AMAZON DATA - NOT VERIFIED AMAZON DATA",
        "cost_profile_count": cost_count,
        "inventory_policy_count": policy_count,
        "snapshot_count": 0,
        "issue_count": 0,
        "brief_id": None,
        "next_step": "Run AmazonReadSyncService and operations.refresh.",
    }
