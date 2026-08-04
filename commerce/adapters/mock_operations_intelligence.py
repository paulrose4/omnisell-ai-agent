from __future__ import annotations

from datetime import UTC, datetime, timedelta

from commerce.intelligence_ports import OperationsIntelligenceBatch


class MockOperationsIntelligenceSource:
    """Deterministic test-data adapter for the operational learning loop."""

    def __init__(self, *, reference_time: datetime | None = None) -> None:
        self.reference_time = reference_time or datetime.now(UTC)
        if self.reference_time.tzinfo is None:
            raise ValueError("reference_time must be timezone-aware")

    def collect(self, *, tenant_id: str, marketplace_id: str) -> OperationsIntelligenceBatch:
        end = self.reference_time
        start = end - timedelta(days=1)
        common = {"source": "mock", "verified_amazon_data": False}
        evidence = [{"source": "mock", "verified_amazon_data": False, "scenario_id": "operations-learning-v1"}]
        return OperationsIntelligenceBatch(
            source="mock",
            verified_amazon_data=False,
            advertising_snapshots=[
                {
                    **common,
                    "tenant_id": tenant_id,
                    "marketplace_id": marketplace_id,
                    "seller_sku": "LEARN-LOSS",
                    "entity_type": "campaign",
                    "entity_id": "SP-LEARN-LOSS",
                    "campaign_id": "SP-LEARN-LOSS",
                    "period_start": start,
                    "period_end": end,
                    "observed_at": end,
                    "collected_at": end,
                    "currency": "USD",
                    "spend": "40",
                    "attributed_sales": "400",
                    "total_sales": "500",
                    "budget": "100",
                    "impressions": 2000,
                    "clicks": 30,
                    "orders": 6,
                    "elapsed_days": "2",
                    "period_days": "7",
                    "break_even_acos": "0.30",
                    "contribution_profit": "-12.50",
                    "inventory_status": "critical_stockout",
                    "evidence_manifest": evidence,
                }
            ],
            search_terms=[
                {
                    **common,
                    "tenant_id": tenant_id,
                    "marketplace_id": marketplace_id,
                    "seller_sku": "LEARN-WASTE",
                    "campaign_id": "SP-LEARN-WASTE",
                    "ad_group_id": "ADGROUP-1",
                    "search_term": "irrelevant replacement parts",
                    "period_start": start,
                    "period_end": end,
                    "observed_at": end,
                    "collected_at": end,
                    "currency": "USD",
                    "spend": "55",
                    "attributed_sales": "0",
                    "impressions": 3000,
                    "clicks": 32,
                    "orders": 0,
                    "evidence_manifest": evidence,
                }
            ],
            return_events=[
                {
                    **common,
                    "tenant_id": tenant_id,
                    "external_return_id": f"LEARN-RETURN-{index}",
                    "external_order_id": f"LEARN-ORDER-{index}",
                    "marketplace_id": marketplace_id,
                    "seller_sku": "LEARN-VOC",
                    "reason_code": "missing_part",
                    "quantity": 1,
                    "return_amount": "8.50",
                    "currency": "USD",
                    "observed_at": end - timedelta(hours=index),
                    "evidence_manifest": evidence,
                }
                for index in range(5)
            ],
            review_observations=[
                {
                    **common,
                    "tenant_id": tenant_id,
                    "external_review_id": "LEARN-REVIEW-1",
                    "marketplace_id": marketplace_id,
                    "seller_sku": "LEARN-VOC",
                    "rating": 2,
                    "reason_code": "missing_part",
                    "observed_at": end,
                    "evidence_manifest": evidence,
                }
            ],
            message_tags=[
                {
                    **common,
                    "tenant_id": tenant_id,
                    "external_message_id": "LEARN-MESSAGE-1",
                    "marketplace_id": marketplace_id,
                    "seller_sku": "LEARN-VOC",
                    "tag": "missing_part",
                    "confidence": "0.95",
                    "observed_at": end,
                    "evidence_manifest": evidence,
                }
            ],
            business_contexts=[
                {
                    **common,
                    "tenant_id": tenant_id,
                    "marketplace_id": marketplace_id,
                    "seller_sku": "LEARN-LOSS",
                    "context_type": "planned_promotion",
                    "starts_at": start,
                    "ends_at": end + timedelta(days=5),
                    "details": {"name": "Mock launch promotion"},
                }
            ],
        )
