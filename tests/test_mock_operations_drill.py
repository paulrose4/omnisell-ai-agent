from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from application.amazon_sync import AmazonReadSyncService
from application.briefing import DailyOperationsBriefService
from application.manual_execution_contract import APPROVAL_ACTION, build_operational_approval_contract
from application.operations_refresh import OperationsRefreshService
from commerce.errors import ResourceNotFound
from commerce.ports import Page
from domain.models import (
    ChannelConnection,
    ChannelProvider,
    ConnectionStatus,
    FulfillmentMode,
    InventoryPolicy,
    InventoryPosition,
    Listing,
    Money,
    Order,
    OrderLine,
    OrderStatus,
    SKUCostProfile,
)
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import ChannelConnectionRow
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.briefing import DailyOperationsBriefRepository, ListingBasicSignalRepository
from infrastructure.repositories.commerce_sync import CommerceSyncRepository, SyncCursorRepository
from infrastructure.repositories.hitl import SQLHITLManager
from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository, InventoryPolicyRepository
from infrastructure.repositories.manual_execution import ManualExecutionRepository
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.profitability import SKUCostProfileRepository, SKUProfitSnapshotRepository
from safety.audit import AuditLogger

MARKETPLACE_ID = "ATVPDKIKX0DER"
SCENARIOS = {
    "MOCK-HEALTHY": (70, 1, "29.99", "Healthy Mock Product", "healthy"),
    "MOCK-STOCKOUT": (2, 10, "29.99", "Critical Stockout Product", "critical_stockout"),
    "MOCK-OOS": (0, 8, "24.99", "Out of Stock Product", "out_of_stock"),
    "MOCK-EXCESS": (500, 1, "19.99", "Excess Inventory Product", "excess_inventory"),
    "MOCK-LOSS": (45, 2, "12.00", "Contribution Loss Product", "contribution_loss"),
    "MOCK-MISSING-COST": (50, 1, "26.00", "Missing Cost Product", "missing_cost"),
    "MOCK-STALE": (40, 1, "25.00", "Stale Inventory Product", "stale_inventory"),
    "MOCK-LISTING": (35, 1, "22.00", "", "listing_missing_fields"),
    "MOCK-UNKNOWN-ORDER": (30, 1, "31.00", "Unknown Order Status Product", "unknown_order_status"),
    "MOCK-MISMATCH": (35, 1, "28.00", "Inventory Mismatch Product", "inventory_mismatch"),
}


def _metadata(scenario_id: str) -> dict[str, object]:
    return {"data_source": "mock_amazon", "verified_amazon_data": False, "scenario_id": scenario_id}


def test_operations_refresh_never_verifies_an_empty_evidence_set() -> None:
    class EmptyCommerce:
        @staticmethod
        def list_inventory_positions(**_kwargs):
            return []

        @staticmethod
        def sales_summary(**_kwargs):
            return {}

        @staticmethod
        def list_listings(**_kwargs):
            return []

        @staticmethod
        def unknown_orders(**_kwargs):
            return []

    class EmptyIssues:
        @staticmethod
        def resolve_absent(**_kwargs):
            return []

    class Briefs:
        @staticmethod
        def generate(**_kwargs):
            return SimpleNamespace(brief=SimpleNamespace(id="brief-empty-evidence"))

    service = OperationsRefreshService(
        commerce_repository=EmptyCommerce(),
        cost_repository=object(),
        profit_repository=object(),
        policy_repository=object(),
        inventory_repository=object(),
        issue_repository=EmptyIssues(),
        action_repository=object(),
        brief_service=Briefs(),
    )

    result = service.refresh(
        tenant_id="local-demo",
        connection_id="connection-empty",
        marketplace_id=MARKETPLACE_ID,
        calculated_at=datetime.now(UTC),
    )

    assert result["seller_sku_count"] == 0
    assert result["data_source"] == "unknown"
    assert result["verified_amazon_data"] is False


class ScenarioAdapter:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.inbound_overrides: dict[str, int] = {}

    async def list_orders(self, *, marketplace_ids, updated_after, next_token=None):
        orders = []
        for sku, (_, daily_sales, price, title, scenario_id) in SCENARIOS.items():
            for day in range(30):
                status = OrderStatus.UNKNOWN if scenario_id == "unknown_order_status" else OrderStatus.SHIPPED
                orders.append(
                    Order(
                        tenant_id="placeholder",
                        channel_connection_id="placeholder",
                        external_order_id=f"MOCK-{sku}-{day}",
                        marketplace_id=MARKETPLACE_ID,
                        status=status,
                        platform_status="UnexpectedPlatformState" if status == OrderStatus.UNKNOWN else "Shipped",
                        fulfillment_mode=FulfillmentMode.FBA,
                        purchased_at=self.now - timedelta(days=day, hours=1),
                        last_updated_at=self.now - timedelta(days=day),
                        lines=[
                            OrderLine(
                                seller_sku=sku,
                                quantity=daily_sales,
                                title=title or sku,
                                unit_price=Money(Decimal(price), "USD"),
                            )
                        ],
                        platform_extensions=_metadata(scenario_id),
                    )
                )
        return Page(items=orders, next_token=None)

    async def list_inventory(self, *, marketplace_id, next_token=None):
        items = []
        for sku, (available, _, _, _, scenario_id) in SCENARIOS.items():
            collected_at = self.now - (
                timedelta(hours=12) if scenario_id == "stale_inventory" else timedelta(minutes=5)
            )
            items.append(
                InventoryPosition(
                    tenant_id="placeholder",
                    channel_connection_id="placeholder",
                    seller_sku=sku,
                    marketplace_id=MARKETPLACE_ID,
                    fulfillment_mode=FulfillmentMode.FBA,
                    available=available,
                    reserved=1,
                    inbound=self.inbound_overrides.get(sku, 0),
                    unsellable=0,
                    synced_at=collected_at,
                    platform_extensions=_metadata(scenario_id),
                )
            )
        return Page(items=items, next_token=None)

    async def get_listing(self, *, seller_sku, marketplace_id):
        _, _, price, title, scenario_id = SCENARIOS[seller_sku]
        return Listing(
            tenant_id="placeholder",
            channel_connection_id="placeholder",
            variant_id="placeholder",
            marketplace_id=MARKETPLACE_ID,
            seller_sku=seller_sku,
            external_product_id=f"ASIN-{seller_sku}",
            title=title,
            price=Money(Decimal(price), "USD"),
            status="BUYABLE",
            platform_extensions={"mock_metadata": _metadata(scenario_id)},
        )


def test_mock_amazon_sync_drives_deterministic_operations_refresh(tmp_path) -> None:
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'mock-drill.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    session_factory = create_session_factory(database_url)
    connection = ChannelConnection(
        id="mock-connection",
        tenant_id="local-demo",
        provider=ChannelProvider.AMAZON,
        selling_partner_id="A1LOCALMOCKSELLER",
        region="na",
        marketplace_ids=(MARKETPLACE_ID,),
        secret_ref="memory://mock",
        status=ConnectionStatus.ACTIVE,
    )
    with session_factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id=connection.id,
                tenant_id=connection.tenant_id,
                provider="amazon",
                selling_partner_id=connection.selling_partner_id,
                region=connection.region,
                marketplace_ids=list(connection.marketplace_ids),
                secret_ref=connection.secret_ref,
                status="active",
                version=1,
            )
        )

    policy_repository = InventoryPolicyRepository(session_factory)
    cost_repository = SKUCostProfileRepository(session_factory)
    for sku in SCENARIOS:
        policy_repository.save(
            InventoryPolicy(
                tenant_id="local-demo",
                seller_sku=sku,
                marketplace_id=MARKETPLACE_ID,
                supplier_lead_time_days=5,
                production_lead_time_days=10,
                shipping_lead_time_days=25,
                amazon_receiving_days=7,
                safety_stock_days=7,
                target_coverage_days=60,
                minimum_order_quantity=100,
                case_pack_quantity=20,
                preferred_shipping_mode="sea",
                sales_weight_7d=Decimal("0.50"),
                sales_weight_14d=Decimal("0.30"),
                sales_weight_30d=Decimal("0.20"),
                source="mock",
                effective_from=now - timedelta(days=30),
                collected_at=now,
                created_at=now,
            )
        )
        if sku != "MOCK-MISSING-COST":
            product_cost = Decimal("19.00") if sku == "MOCK-LOSS" else Decimal("6.00")
            cost_repository.save_profiles(
                tenant_id="local-demo",
                profiles=[
                    SKUCostProfile(
                        tenant_id="local-demo",
                        seller_sku=sku,
                        marketplace_id=MARKETPLACE_ID,
                        currency="USD",
                        product_cost=product_cost,
                        domestic_shipping_cost=Decimal("2.50") if sku == "MOCK-LOSS" else Decimal("0.60"),
                        international_freight_cost=Decimal("1.50"),
                        duty_cost=Decimal("0.50"),
                        prep_cost=Decimal("0.40"),
                        packaging_cost=Decimal("0.35"),
                        estimated_referral_fee_rate=Decimal("0.15"),
                        estimated_fulfillment_fee=Decimal("4.25"),
                        estimated_storage_cost=Decimal("0.20"),
                        estimated_return_loss_rate=Decimal("0.02"),
                        exchange_rate_buffer_rate=Decimal("0.01"),
                        target_margin_rate=Decimal("0.20"),
                        minimum_margin_rate=Decimal("0.10"),
                        source="mock",
                        effective_from=now - timedelta(days=30),
                        collected_at=now,
                        created_at=now,
                    )
                ],
            )

    commerce = CommerceSyncRepository(session_factory)

    class AdapterWithLegacyGap(ScenarioAdapter):
        async def get_listing(self, *, seller_sku, marketplace_id):
            if seller_sku == "LOCAL-SKU-1001":
                raise ResourceNotFound("Legacy managed SKU is not present in Mock Amazon")
            return await super().get_listing(seller_sku=seller_sku, marketplace_id=marketplace_id)

    adapter = AdapterWithLegacyGap(now)
    sync_service = AmazonReadSyncService(
        adapter=adapter,
        connection=connection,
        commerce_repository=commerce,
        cursor_repository=SyncCursorRepository(session_factory),
    )
    sync_result = asyncio.run(sync_service.sync())
    assert sync_result["orders"]["item_count"] == 300
    assert sync_result["inventory"]["item_count"] == 10
    assert sync_result["listings"]["item_count"] == 10

    issue_repository = OperationalIssueRepository(session_factory)
    action_repository = RecommendedActionRepository(session_factory)
    brief_service = DailyOperationsBriefService(
        brief_repository=DailyOperationsBriefRepository(session_factory),
        listing_repository=ListingBasicSignalRepository(session_factory),
        issue_repository=issue_repository,
        action_repository=action_repository,
    )
    manual_execution_repository = ManualExecutionRepository(session_factory)
    refresh_service = OperationsRefreshService(
        commerce_repository=commerce,
        cost_repository=cost_repository,
        profit_repository=SKUProfitSnapshotRepository(session_factory),
        policy_repository=policy_repository,
        inventory_repository=InventoryHealthSnapshotRepository(session_factory),
        issue_repository=issue_repository,
        action_repository=action_repository,
        brief_service=brief_service,
        manual_execution_repository=manual_execution_repository,
    )
    refresh = refresh_service.refresh(
        tenant_id="local-demo",
        connection_id=connection.id,
        marketplace_id=MARKETPLACE_ID,
        calculated_at=now,
    )

    issues = issue_repository.list_issues(tenant_id="local-demo")
    issue_keys = {(item.seller_sku, item.issue_type) for item in issues}
    assert ("MOCK-STOCKOUT", "inventory_stockout_risk") in issue_keys
    assert ("MOCK-OOS", "inventory_stockout_risk") in issue_keys
    assert ("MOCK-EXCESS", "inventory_excess") in issue_keys
    assert ("MOCK-LOSS", "sku_contribution_loss") in issue_keys
    assert ("MOCK-MISSING-COST", "profit_data_quality") in issue_keys
    assert ("MOCK-STALE", "inventory_data_quality") in issue_keys
    assert ("MOCK-LISTING", "listing_basic_quality") in issue_keys
    assert ("ACCOUNT", "unknown_amazon_order_status") in issue_keys
    assert not any(
        item.seller_sku == "MOCK-HEALTHY" and item.status not in {"resolved", "dismissed"} for item in issues
    )
    assert (
        sum(item.issue_type.endswith("data_quality") for item in issues if item.status not in {"resolved", "dismissed"})
        == 2
    )
    loss_stockout = next(
        item for item in issues if item.seller_sku == "MOCK-LOSS" and item.issue_type == "inventory_stockout_risk"
    )
    loss_action = next(
        item for item in action_repository.list_actions(tenant_id="local-demo", issue_id=loss_stockout.id)
    )
    assert loss_action.action_type == "review_loss_making_stockout"
    assert loss_action.parameters["replenishment_blocked"] is True
    assert refresh["data_source"] == "mock_amazon"
    assert refresh["verified_amazon_data"] is False
    assert refresh["brief_id"]
    stockout = next(
        item for item in issues if item.seller_sku == "MOCK-STOCKOUT" and item.issue_type == "inventory_stockout_risk"
    )
    stockout_action = next(
        item
        for item in action_repository.list_actions(
            tenant_id="local-demo",
            issue_id=stockout.id,
        )
        if item.action_type == "review_replenishment"
    )
    assert any(evidence.get("data_source") == "mock_amazon" for evidence in stockout.evidence)

    issue_repository.transition(
        tenant_id="local-demo", issue_id=stockout.id, target_status="acknowledged", actor_id="operator", note="Claimed"
    )
    stockout = issue_repository.transition(
        tenant_id="local-demo", issue_id=stockout.id, target_status="in_progress", actor_id="operator", note="Reviewing"
    )
    approval_contract = build_operational_approval_contract(
        issue=stockout,
        action=stockout_action,
        connection=connection,
    )
    hitl = SQLHITLManager(session_factory, AuditLogger(tmp_path / "approval-audit.jsonl"))
    approval = hitl.submit_request(
        action=APPROVAL_ACTION,
        payload={
            "tenant_id": "local-demo",
            "resource_id": stockout_action.id,
            "issue_id": stockout.id,
            "recommended_action_id": stockout_action.id,
            "action_type": stockout_action.action_type,
            **approval_contract.approval_payload(),
        },
        requester="operator",
        risk_level="critical",
        reason="Governed drill approval.",
    )
    issue_repository.transition(
        tenant_id="local-demo",
        issue_id=stockout.id,
        target_status="awaiting_approval",
        actor_id="operator",
        note="Approval requested",
    )
    hitl.approve(approval.request_id, approver="approver-1", reason="First review.", tenant_id="local-demo")
    hitl.approve(approval.request_id, approver="approver-2", reason="Second review.", tenant_id="local-demo")
    action_repository.transition(
        tenant_id="local-demo",
        action_id=stockout_action.id,
        target_status="accepted",
        reason="Approved by two independent reviewers.",
    )
    issue_repository.transition(
        tenant_id="local-demo",
        issue_id=stockout.id,
        target_status="approved_for_manual_execution",
        actor_id="approver",
        note="Approved",
    )
    execution, created = manual_execution_repository.reserve(
        tenant_id="local-demo",
        issue_id=stockout.id,
        approval_request_id=approval.request_id,
        recommended_action_id=stockout_action.id,
        action_hash=approval_contract.action_hash,
        execution_contract_version=approval_contract.execution_plan.execution_contract_version,
        idempotency_key="mock-drill-execution",
        action_type="create_replenishment_plan",
        approved_change={"inbound_quantity": 600},
        executed_by="operator",
    )
    assert created is True
    execution = manual_execution_repository.complete(
        tenant_id="local-demo",
        execution_id=execution.id,
        before_snapshot={"inbound": 0},
        actual_change={"inbound": 600},
        execution_evidence={"mock_seller_central": True},
    )
    assert execution.executed_at.tzinfo is not None
    issue_repository.attach_execution(
        tenant_id="local-demo",
        issue_id=stockout.id,
        execution_result_id=execution.id,
        actor_id="operator",
    )

    adapter.now = execution.executed_at - timedelta(seconds=1)
    asyncio.run(sync_service.sync(("inventory",)))
    stale_verification = refresh_service.refresh(
        tenant_id="local-demo",
        connection_id=connection.id,
        marketplace_id=MARKETPLACE_ID,
        seller_skus=["MOCK-STOCKOUT"],
        calculated_at=execution.executed_at + timedelta(seconds=1),
    )
    assert stockout.id not in stale_verification["resolved_issue_ids"]
    still_verifying = issue_repository.get(tenant_id="local-demo", issue_id=stockout.id)
    assert still_verifying is not None and still_verifying.status == "verifying"
    pending_execution = manual_execution_repository.get(tenant_id="local-demo", execution_id=execution.id)
    assert pending_execution is not None and pending_execution.verification_status == "pending"

    adapter.inbound_overrides["MOCK-STOCKOUT"] = 600
    adapter.now = execution.executed_at + timedelta(minutes=5, seconds=2)
    asyncio.run(sync_service.sync(("inventory",)))
    verification = refresh_service.refresh(
        tenant_id="local-demo",
        connection_id=connection.id,
        marketplace_id=MARKETPLACE_ID,
        seller_skus=["MOCK-STOCKOUT"],
        calculated_at=execution.executed_at + timedelta(minutes=10),
    )
    assert stockout.id in verification["resolved_issue_ids"]
    resolved = issue_repository.get(tenant_id="local-demo", issue_id=stockout.id)
    assert resolved is not None and resolved.status == "resolved"
    verified_execution = manual_execution_repository.get(tenant_id="local-demo", execution_id=execution.id)
    assert verified_execution is not None and verified_execution.verification_status == "verified"


def test_listing_sync_isolates_unsupported_managed_sku(tmp_path) -> None:
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    database_url = f"sqlite:///{tmp_path / 'mock-listing-isolation.sqlite'}"
    initialize_database(database_url, default_tenant_id="local-demo")
    session_factory = create_session_factory(database_url)
    connection = ChannelConnection(
        id="mock-connection",
        tenant_id="local-demo",
        provider=ChannelProvider.AMAZON,
        selling_partner_id="A1LOCALMOCKSELLER",
        region="na",
        marketplace_ids=(MARKETPLACE_ID,),
        secret_ref="memory://mock",
        status=ConnectionStatus.ACTIVE,
    )
    with session_factory.begin() as session:
        session.add(
            ChannelConnectionRow(
                id=connection.id,
                tenant_id=connection.tenant_id,
                provider="amazon",
                selling_partner_id=connection.selling_partner_id,
                region=connection.region,
                marketplace_ids=list(connection.marketplace_ids),
                secret_ref=connection.secret_ref,
                status="active",
                version=1,
            )
        )

    commerce = CommerceSyncRepository(session_factory)

    class AdapterWithLegacyGap(ScenarioAdapter):
        async def get_listing(self, *, seller_sku, marketplace_id):
            if seller_sku == "LOCAL-SKU-1001":
                raise ResourceNotFound("Legacy managed SKU is not present in Mock Amazon")
            return await super().get_listing(seller_sku=seller_sku, marketplace_id=marketplace_id)

    adapter = AdapterWithLegacyGap(now)
    sync_service = AmazonReadSyncService(
        adapter=adapter,
        connection=connection,
        commerce_repository=commerce,
        cursor_repository=SyncCursorRepository(session_factory),
    )
    asyncio.run(sync_service.sync(("inventory",)))
    commerce.upsert_inventory(
        InventoryPosition(
            tenant_id="local-demo",
            channel_connection_id=connection.id,
            seller_sku="LOCAL-SKU-1001",
            marketplace_id=MARKETPLACE_ID,
            fulfillment_mode=FulfillmentMode.FBA,
            available=5,
            synced_at=now,
            platform_extensions=_metadata("legacy_managed_sku"),
        )
    )

    result = asyncio.run(sync_service.sync(("listings",)))

    assert result["listings"]["status"] == "succeeded"
    assert result["listings"]["item_count"] == 10
    assert result["listings"]["skipped_items"] == [
        {"marketplace_id": MARKETPLACE_ID, "seller_sku": "LOCAL-SKU-1001", "error_code": "resource_not_found"}
    ]
