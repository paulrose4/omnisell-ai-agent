from __future__ import annotations

from datetime import timedelta

from commerce.intelligence_ports import OperationsIntelligenceSource


class OperationsIntelligenceIngestionService:
    """Ingests normalized facts from Mock today and production adapters later."""

    def __init__(self, *, advertising_repository, voc_repository, learning_repository) -> None:
        self.advertising_repository = advertising_repository
        self.voc_repository = voc_repository
        self.learning_repository = learning_repository

    def ingest(self, *, source: OperationsIntelligenceSource, tenant_id: str, marketplace_id: str) -> dict[str, object]:
        batch = source.collect(tenant_id=tenant_id, marketplace_id=marketplace_id)
        self._validate_batch(batch, tenant_id=tenant_id, marketplace_id=marketplace_id)
        diagnostics = 0
        for payload in batch.advertising_snapshots:
            snapshot = self.advertising_repository.save_daily_snapshot(tenant_id=tenant_id, payload=payload)
            diagnostics += len(self.advertising_repository.diagnose_daily_snapshot(tenant_id=tenant_id, snapshot_id=snapshot["id"]))
        for payload in batch.search_terms:
            self.advertising_repository.save_search_term(tenant_id=tenant_id, payload=payload)
        for payload in batch.return_events:
            self.voc_repository.save_return(tenant_id=tenant_id, payload=payload)
        for payload in batch.review_observations:
            self.voc_repository.save_review(tenant_id=tenant_id, payload=payload)
        for payload in batch.message_tags:
            self.voc_repository.save_message_tag(tenant_id=tenant_id, payload=payload)
        for payload in batch.business_contexts:
            self.learning_repository.save_business_context(tenant_id=tenant_id, payload=payload)

        voc_summaries = 0
        scopes = {(item["seller_sku"], item["marketplace_id"]) for item in [*batch.return_events, *batch.review_observations, *batch.message_tags]}
        for seller_sku, scoped_marketplace in scopes:
            observations = [
                item["observed_at"] for item in [*batch.return_events, *batch.review_observations, *batch.message_tags]
                if item["seller_sku"] == seller_sku and item["marketplace_id"] == scoped_marketplace
            ]
            if observations:
                self.voc_repository.summarize(
                    tenant_id=tenant_id, seller_sku=seller_sku, marketplace_id=scoped_marketplace,
                    period_start=min(observations) - timedelta(seconds=1),
                    period_end=max(observations) + timedelta(seconds=1), min_sample_size=5,
                )
                voc_summaries += 1
        return {
            "source": batch.source,
            "verified_amazon_data": batch.verified_amazon_data,
            "advertising_snapshots": len(batch.advertising_snapshots),
            "search_terms": len(batch.search_terms),
            "advertising_diagnostics": diagnostics,
            "return_events": len(batch.return_events),
            "review_observations": len(batch.review_observations),
            "message_tags": len(batch.message_tags),
            "voc_summaries": voc_summaries,
            "business_contexts": len(batch.business_contexts),
        }

    @staticmethod
    def _validate_batch(batch, *, tenant_id: str, marketplace_id: str) -> None:
        for record in batch.all_records():
            if record.get("tenant_id") != tenant_id or record.get("marketplace_id") != marketplace_id:
                raise ValueError("intelligence source returned data outside the requested tenant or marketplace")
            if record.get("source") != batch.source or bool(record.get("verified_amazon_data")) != batch.verified_amazon_data:
                raise ValueError("intelligence source provenance does not match its batch contract")
