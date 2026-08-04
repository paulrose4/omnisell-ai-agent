from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from application.briefing import DailyOperationsBriefService
from application.operations import OperationalIssueService
from domain.briefing import ListingBasicSignal, ListingIssueEvaluator
from domain.inventory import InventoryHealthCalculator, InventoryHealthInput
from domain.models import OperationalIssue, SKUCostProfile
from domain.profitability import ProfitCalculationInput, ProfitCalculator


class OperationsRefreshService:
    def __init__(self, *, commerce_repository: Any, cost_repository: Any, profit_repository: Any, policy_repository: Any, inventory_repository: Any, issue_repository: Any, action_repository: Any, brief_service: DailyOperationsBriefService, manual_execution_repository: Any | None = None) -> None:
        self.commerce = commerce_repository
        self.costs = cost_repository
        self.profits = profit_repository
        self.policies = policy_repository
        self.inventory = inventory_repository
        self.issues = issue_repository
        self.actions = action_repository
        self.briefs = brief_service
        self.manual_executions = manual_execution_repository

    def refresh(self, *, tenant_id: str, connection_id: str, marketplace_id: str, seller_skus: list[str] | None = None, calculated_at: datetime | None = None) -> dict[str, Any]:
        now = calculated_at or datetime.now(UTC)
        positions = self.commerce.list_inventory_positions(
            tenant_id=tenant_id, connection_id=connection_id, marketplace_id=marketplace_id, seller_skus=seller_skus,
        )
        resolved_skus = [item.seller_sku for item in positions]
        position_freshness = {item.seller_sku: item.synced_at for item in positions}
        sales = self.commerce.sales_summary(tenant_id=tenant_id, connection_id=connection_id, marketplace_id=marketplace_id, calculated_at=now)
        inventory_ids: list[str] = []
        profit_ids: list[str] = []
        direct_issues: list[OperationalIssue] = []

        for position in positions:
            sku_sales = sales.get(position.seller_sku, {})
            policy = self.policies.get_active(tenant_id=tenant_id, marketplace_id=marketplace_id, seller_sku=position.seller_sku, effective_at=now)
            if policy is None:
                direct_issues.append(self._data_issue(
                    tenant_id=tenant_id, marketplace_id=marketplace_id, seller_sku=position.seller_sku,
                    issue_type="missing_inventory_policy", title=f"{position.seller_sku} has no inventory policy",
                    missing_data=("inventory_policy",), freshness=position.synced_at, now=now,
                    evidence=self._platform_evidence(position.platform_extensions),
                ))
            profile = self.costs.get_active(tenant_id=tenant_id, marketplace_id=marketplace_id, seller_sku=position.seller_sku, effective_at=now)
            if profile is None:
                profile = self._missing_cost_profile(tenant_id, marketplace_id, position.seller_sku, now)
            profit_snapshot = ProfitCalculator().calculate(
                profile=profile,
                input_data=ProfitCalculationInput(
                    tenant_id=tenant_id, seller_sku=position.seller_sku, marketplace_id=marketplace_id,
                    currency=str(sku_sales.get("currency") or "USD"), window_start=now - timedelta(days=30), window_end=now,
                    item_sales=Decimal(str(sku_sales.get("item_sales") or 0)), units=int(sku_sales.get("units") or 0), calculated_at=now,
                ),
            )
            profit_snapshot.input_manifest["platform_metadata"] = self._platform_evidence(position.platform_extensions)
            self.profits.save(profit_snapshot)
            profit_ids.append(profit_snapshot.id)

            if policy is not None:
                unit_revenue = None if profit_snapshot.units <= 0 else profit_snapshot.revenue / profit_snapshot.units
                unit_profit = None if profit_snapshot.units <= 0 or profit_snapshot.contribution_profit is None else profit_snapshot.contribution_profit / profit_snapshot.units
                inventory_snapshot = InventoryHealthCalculator().calculate(
                    policy=policy,
                    input_data=InventoryHealthInput(
                        tenant_id=tenant_id, seller_sku=position.seller_sku, marketplace_id=marketplace_id,
                        available=position.available, reserved=position.reserved, inbound=position.inbound, in_transit=0,
                        unsellable=position.unsellable, sales_7_days=int(sku_sales.get("sales_7_days") or 0),
                        sales_14_days=int(sku_sales.get("sales_14_days") or 0), sales_30_days=int(sku_sales.get("sales_30_days") or 0),
                        inventory_source="mock" if position.platform_extensions.get("data_source") == "mock_amazon" else "amazon_api",
                        inventory_collected_at=position.synced_at, calculated_at=now,
                        unit_revenue=unit_revenue, unit_contribution_profit=unit_profit,
                        estimated_extra_logistics_cost_per_unit=Decimal("1.25"),
                    ),
                )
                inventory_snapshot.input_manifest["platform_metadata"] = self._platform_evidence(position.platform_extensions)
                self.inventory.save(inventory_snapshot)
                inventory_ids.append(inventory_snapshot.id)

        detection = OperationalIssueService(
            inventory_repository=self.inventory, profit_repository=self.profits,
            issue_repository=self.issues, action_repository=self.actions,
        ).detect(
            tenant_id=tenant_id, inventory_snapshot_ids=inventory_ids, profit_snapshot_ids=profit_ids,
            complete_scan=seller_skus is None, detected_at=now,
        ) if inventory_ids or profit_ids else None
        saved_issues = list(detection.issues if detection else ())

        for issue in direct_issues:
            saved_issues.append(self.issues.upsert(issue))
        for listing in self.commerce.list_listings(tenant_id=tenant_id, connection_id=connection_id, marketplace_id=marketplace_id, seller_skus=seller_skus):
            evaluated = ListingIssueEvaluator().evaluate(ListingBasicSignal(
                tenant_id=tenant_id, listing_id=listing.id, seller_sku=listing.seller_sku,
                marketplace_id=marketplace_id, status=listing.status, title=listing.title,
                price_amount=listing.price.amount if listing.price else None,
                price_currency=listing.price.currency if listing.price else None,
                external_product_id=listing.external_product_id, observed_at=now,
            ), assessed_at=now)
            if evaluated:
                issue, action = evaluated
                issue.evidence = (*issue.evidence, self._platform_evidence((listing.platform_extensions or {}).get("mock_metadata") or {}))
                canonical = self.issues.upsert(issue)
                self.actions.upsert(action, canonical_issue_id=canonical.id)
                saved_issues.append(canonical)
        for unknown in self.commerce.unknown_orders(tenant_id=tenant_id, connection_id=connection_id, marketplace_id=marketplace_id):
            saved_issues.append(self.issues.upsert(self._data_issue(
                tenant_id=tenant_id, marketplace_id=marketplace_id, seller_sku="ACCOUNT",
                issue_type="unknown_amazon_order_status", title="Amazon returned an unmapped order status",
                missing_data=("order_status_mapping",), freshness=now, now=now, evidence=unknown,
            )))

        active_keys = {item.dedupe_key for item in saved_issues}
        verification_eligible_issue_ids: set[str] | None = None
        if self.manual_executions is not None:
            verification_eligible_issue_ids = set()
            for pending_issue in self.issues.list_issues(tenant_id=tenant_id, marketplace_id=marketplace_id):
                if pending_issue.status not in {"verifying", "verification_failed"} or not pending_issue.execution_result_id:
                    continue
                execution = self.manual_executions.get(
                    tenant_id=tenant_id, execution_id=pending_issue.execution_result_id,
                )
                source_freshness = position_freshness.get(pending_issue.seller_sku)
                if execution is None or source_freshness is None:
                    continue
                executed_at = execution.executed_at
                if executed_at.tzinfo is None:
                    executed_at = executed_at.replace(tzinfo=UTC)
                if source_freshness.tzinfo is None:
                    source_freshness = source_freshness.replace(tzinfo=UTC)
                if source_freshness >= executed_at:
                    verification_eligible_issue_ids.add(pending_issue.id)
        resolved_issue_ids = self.issues.resolve_absent(
            tenant_id=tenant_id, marketplace_id=marketplace_id, seller_skus=resolved_skus,
            active_dedupe_keys=active_keys, verification_eligible_issue_ids=verification_eligible_issue_ids,
        )
        if self.manual_executions is not None:
            for issue_id in resolved_issue_ids:
                resolved_issue = self.issues.get(tenant_id=tenant_id, issue_id=issue_id)
                if resolved_issue and resolved_issue.execution_result_id:
                    self.manual_executions.mark_verified(
                        tenant_id=tenant_id, execution_id=resolved_issue.execution_result_id, status="verified",
                        details={"issue_id": issue_id, "resolution_code": resolved_issue.resolution_code, "verified_amazon_data": False},
                    )
            for failed_issue in self.issues.list_issues(tenant_id=tenant_id, status="verification_failed", marketplace_id=marketplace_id):
                if (
                    failed_issue.seller_sku in resolved_skus
                    and failed_issue.execution_result_id
                    and failed_issue.id in (verification_eligible_issue_ids or set())
                ):
                    self.manual_executions.mark_verified(
                        tenant_id=tenant_id, execution_id=failed_issue.execution_result_id, status="verification_failed",
                        details={"issue_id": failed_issue.id, "reason": "Issue remains active after post-execution synchronization.", "verified_amazon_data": False},
                    )
        brief = self.briefs.generate(tenant_id=tenant_id, brief_date=None, timezone="UTC", scan_listings=False, generated_at=now).brief
        position_sources = {
            str(item.platform_extensions.get("data_source") or "unknown")
            for item in positions
        }
        if not position_sources:
            data_source = "unknown"
        elif len(position_sources) == 1:
            data_source = next(iter(position_sources))
        else:
            data_source = "mixed"
        verified_amazon_data = bool(positions) and all(
            item.platform_extensions.get("verified_amazon_data") is True
            for item in positions
        )
        return {
            "status": "succeeded", "tenant_id": tenant_id, "connection_id": connection_id,
            "marketplace_id": marketplace_id, "seller_sku_count": len(resolved_skus),
            "profit_snapshot_count": len(profit_ids), "inventory_snapshot_count": len(inventory_ids),
            "issue_count": len(saved_issues), "resolved_issue_ids": resolved_issue_ids,
            "brief_id": brief.id, "data_source": data_source,
            "verified_amazon_data": verified_amazon_data,
        }

    @staticmethod
    def _missing_cost_profile(tenant_id: str, marketplace_id: str, seller_sku: str, now: datetime) -> SKUCostProfile:
        return SKUCostProfile(
            tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=marketplace_id, currency="USD",
            product_cost=None, domestic_shipping_cost=None, international_freight_cost=None, duty_cost=None,
            prep_cost=None, packaging_cost=None, estimated_referral_fee_rate=None, estimated_fulfillment_fee=None,
            estimated_storage_cost=None, estimated_return_loss_rate=None, exchange_rate_buffer_rate=None,
            target_margin_rate=Decimal("0.20"), minimum_margin_rate=Decimal("0.10"), source="mock",
            effective_from=now - timedelta(days=365),
        )

    @staticmethod
    def _platform_evidence(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "data_source": metadata.get("data_source", "unknown"),
            "verified_amazon_data": bool(metadata.get("verified_amazon_data", False)),
            "scenario_id": metadata.get("scenario_id"),
        }

    @staticmethod
    def _data_issue(*, tenant_id: str, marketplace_id: str, seller_sku: str, issue_type: str, title: str, missing_data: tuple[str, ...], freshness: datetime, now: datetime, evidence: dict[str, Any]) -> OperationalIssue:
        dedupe_key = sha256(f"{tenant_id}|{issue_type}|{marketplace_id}|{seller_sku}".encode()).hexdigest()
        return OperationalIssue(
            tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=marketplace_id,
            issue_type=issue_type, severity="high", title=title,
            summary="Deterministic processing cannot safely continue without operator review.", status="open",
            dedupe_key=dedupe_key, confidence=Decimal("1"), data_freshness_at=freshness,
            evidence=(evidence,), missing_data=missing_data, detected_at=now, last_detected_at=now,
        )
