from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from agents.prompts.listing_optimization_v3 import LISTING_OPTIMIZATION_PROMPT_VERSION, LISTING_OPTIMIZATION_SYSTEM_PROMPT
from agents.registry import AdvisorRegistry
from application.listing_optimization_agent import ListingOptimizationAgentService, ListingOptimizationEvidenceCollector


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class SnapshotRepository:
    def list_snapshots(self, *, tenant_id: str, seller_sku: str | None = None):
        if tenant_id != "tenant-a":
            return []
        return [SimpleNamespace(
            id="snapshot-1", tenant_id=tenant_id, listing_id="listing-1", seller_sku="GOLDEN-SKU-1",
            marketplace_id="ATVPDKIKX0DER", content_version=3, title="Short title",
            bullet_points=("Known fact",), description="Known product description", image_count=2,
            search_terms=("known term",), listing_status="active", quality_score=Decimal("35.00"),
            quality_status="poor", section_scores={"title": "5.00"},
            quality_issues=({"section": "title", "code": "title_too_short"},),
            recommendations=({"section": "title", "recommendation": "Add verified product facts"},),
            evidence=({"source_type": "mock_listing", "brand": "KnownBrand", "material": "cotton"},),
            confidence=Decimal("0.80"), missing_data=("dimensions",), degraded_reason=None,
            automation_eligible=False, source="mock", source_observed_at=NOW, calculated_at=NOW,
            calculation_version="listing-quality-v1", baseline_metrics={"sessions": 100, "conversion_rate": "0.10"},
        )]


class ExperimentRepository:
    def list_experiments(self, *, tenant_id: str, status: str | None = None):
        return [SimpleNamespace(
            id="experiment-1", tenant_id=tenant_id, listing_id="listing-1", seller_sku="GOLDEN-SKU-1",
            marketplace_id="ATVPDKIKX0DER", before_snapshot_id="snapshot-0",
            proposed_changes={"title": "Prior draft"}, baseline_metrics={"sessions": 80},
            observation_start=NOW, observation_end=NOW, expected_outcome="Test title",
            action_hash="d" * 64, status="evaluated", created_at=NOW, updated_at=NOW,
            after_snapshot_id="snapshot-1", effect_metrics={"conversion_rate_change": "0.01"},
            evaluation_reasons=("conversion improved",), rollback_recommended=False, evaluated_at=NOW,
        )]


class Workflow:
    async def run_advisory(self, **kwargs):
        self.kwargs = kwargs
        return {"run_id": "run-1", "status": "succeeded", "analysis": {"agent_type": kwargs["advisor"].agent_type}}


def test_listing_collector_uses_snapshot_and_experiment_evidence_without_verified_amazon_claim() -> None:
    collector = ListingOptimizationEvidenceCollector(
        snapshot_repository=SnapshotRepository(), experiment_repository=ExperimentRepository()
    )

    bundle = asyncio.run(collector.collect(tenant_id="tenant-a", request={"snapshot_id": "snapshot-1"}))

    ids = {item.evidence_id for item in bundle.evidence}
    assert {"snapshot-1", "listing-facts:listing-1:3", "constraints:ATVPDKIKX0DER:v1", "search-terms:snapshot-1", "experiment-1"} <= ids
    assert all(item.source == "mock" or item.source == "internal_record" for item in bundle.evidence)
    assert not any(item.verified_amazon_data for item in bundle.evidence)
    assert bundle.by_id()["listing-facts:listing-1:3"].metadata["missing_data"] == ["dimensions"]


def test_listing_collector_hides_cross_tenant_snapshot() -> None:
    collector = ListingOptimizationEvidenceCollector(snapshot_repository=SnapshotRepository(), experiment_repository=ExperimentRepository())
    with pytest.raises(LookupError, match="not found"):
        asyncio.run(collector.collect(tenant_id="tenant-b", request={"snapshot_id": "snapshot-1"}))


def test_listing_service_never_requests_publish_capability() -> None:
    workflow = Workflow()
    advisor = SimpleNamespace(agent_type="listing_optimization_advisor", prompt_version="listing-optimization-v1")
    service = ListingOptimizationAgentService(
        workflow=workflow,
        registry=AdvisorRegistry({"listing_optimization_analysis": advisor}),
        evidence_collector=object(),
    )

    asyncio.run(service.analyze(tenant_id="tenant-a", actor_id="operator-1", correlation_id="corr-1", snapshot_id="snapshot-1"))

    assert workflow.kwargs["request"]["allowed_capabilities"] == ["read_evidence", "draft_listing_changes"]
    assert "publish_listing" not in workflow.kwargs["request"]["allowed_capabilities"]


def test_listing_prompt_forbids_unsupported_product_claims_and_publication() -> None:
    prompt = LISTING_OPTIMIZATION_SYSTEM_PROMPT.lower()
    for claim in ("material", "dimensions", "compatibility", "certification", "warranty", "medical"):
        assert claim in prompt
    assert "do not publish" in prompt
    assert "every proposed change" in prompt and "evidence_id" in prompt
    assert LISTING_OPTIMIZATION_PROMPT_VERSION == "listing-optimization-v3"
    assert "confidence must be 0.65 or lower" in prompt
    assert "each missing_data item uses field, reason, required_for, blocking, and suggested_source" in prompt
    assert "runtime adds identity" in prompt
