from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from agents.contracts import EvidenceBundle, EvidenceReference
from agents.registry import AdvisorRegistry

MARKETPLACE_LISTING_CONSTRAINTS = {
    "version": "v1",
    "title_max_characters": 200,
    "bullet_count_max": 5,
    "search_terms_must_not_repeat_brand": True,
    "unsupported_claims_prohibited": True,
    "publication_requires_deterministic_experiment_and_approval": True,
}


class ListingOptimizationEvidenceCollector:
    def __init__(
        self, *, snapshot_repository: Any, experiment_repository: Any, fresh_for: timedelta = timedelta(hours=24)
    ) -> None:
        self.snapshot_repository = snapshot_repository
        self.experiment_repository = experiment_repository
        self.fresh_for = fresh_for

    async def collect(self, *, tenant_id: str, request: dict[str, Any]) -> EvidenceBundle:
        snapshot_id = str(request.get("snapshot_id") or "")
        if not snapshot_id:
            raise ValueError("snapshot_id is required")
        snapshots = self.snapshot_repository.list_snapshots(tenant_id=tenant_id)
        snapshot = next((item for item in snapshots if item.id == snapshot_id and item.tenant_id == tenant_id), None)
        if snapshot is None:
            raise LookupError("Listing quality snapshot not found")
        collected_at = datetime.now(UTC)
        source = "mock" if snapshot.source == "mock" or snapshot.seller_sku.startswith("GOLDEN-") else "internal_record"
        observed_at = snapshot.source_observed_at or snapshot.calculated_at
        references: list[EvidenceReference] = []
        snapshot_metadata = _json_safe(
            {
                "listing_id": snapshot.listing_id,
                "seller_sku": snapshot.seller_sku,
                "marketplace_id": snapshot.marketplace_id,
                "content_version": snapshot.content_version,
                "listing_status": snapshot.listing_status,
                "quality_score": snapshot.quality_score,
                "quality_status": snapshot.quality_status,
                "section_scores": snapshot.section_scores,
                "quality_issues": snapshot.quality_issues,
                "recommendations": snapshot.recommendations,
                "confidence": snapshot.confidence,
                "missing_data": snapshot.missing_data,
                "degraded_reason": snapshot.degraded_reason,
                "calculation_version": snapshot.calculation_version,
                "baseline_metrics": snapshot.baseline_metrics,
                "source_evidence": snapshot.evidence,
                "deterministic_values": {
                    "quality_score": str(snapshot.quality_score),
                    "section_scores": snapshot.section_scores,
                    "baseline_metrics": snapshot.baseline_metrics,
                    "content_version": snapshot.content_version,
                },
            }
        )
        references.append(
            _reference(
                snapshot.id,
                "listing_quality_snapshot",
                source,
                "listing_quality_snapshot",
                snapshot.id,
                observed_at,
                collected_at,
                snapshot_metadata,
                self.fresh_for,
                snapshot.confidence,
            )
        )
        fact_id = f"listing-facts:{snapshot.listing_id}:{snapshot.content_version}"
        references.append(
            _reference(
                fact_id,
                "listing_product_facts",
                source,
                "listing",
                snapshot.listing_id,
                observed_at,
                collected_at,
                _json_safe(
                    {
                        "title": snapshot.title,
                        "bullet_points": snapshot.bullet_points,
                        "description": snapshot.description,
                        "image_count": snapshot.image_count,
                        "listing_status": snapshot.listing_status,
                        "source_evidence": snapshot.evidence,
                        "missing_data": snapshot.missing_data,
                        "deterministic_values": {"content_version": snapshot.content_version},
                    }
                ),
                self.fresh_for,
                snapshot.confidence,
            )
        )
        constraint_id = f"constraints:{snapshot.marketplace_id}:v1"
        references.append(
            _reference(
                constraint_id,
                "marketplace_listing_constraints",
                "internal_record",
                "marketplace_listing_constraints",
                snapshot.marketplace_id,
                collected_at,
                collected_at,
                MARKETPLACE_LISTING_CONSTRAINTS,
                self.fresh_for,
                Decimal("1"),
            )
        )
        references.append(
            _reference(
                f"search-terms:{snapshot.id}",
                "search_term_evidence",
                source,
                "listing",
                snapshot.listing_id,
                observed_at,
                collected_at,
                _json_safe(
                    {
                        "search_terms": snapshot.search_terms,
                        "source_evidence": snapshot.evidence,
                        "untrusted_content": True,
                    }
                ),
                self.fresh_for,
                snapshot.confidence,
            )
        )
        for experiment in self.experiment_repository.list_experiments(tenant_id=tenant_id):
            if experiment.listing_id != snapshot.listing_id or experiment.tenant_id != tenant_id:
                continue
            references.append(
                _reference(
                    experiment.id,
                    "listing_experiment",
                    "internal_record",
                    "listing_experiment",
                    experiment.id,
                    experiment.updated_at,
                    collected_at,
                    _json_safe(
                        {
                            "before_snapshot_id": experiment.before_snapshot_id,
                            "after_snapshot_id": experiment.after_snapshot_id,
                            "proposed_changes": experiment.proposed_changes,
                            "baseline_metrics": experiment.baseline_metrics,
                            "expected_outcome": experiment.expected_outcome,
                            "status": experiment.status,
                            "effect_metrics": experiment.effect_metrics,
                            "evaluation_reasons": experiment.evaluation_reasons,
                            "rollback_recommended": experiment.rollback_recommended,
                            "deterministic_values": {
                                "effect_metrics": experiment.effect_metrics,
                                "status": experiment.status,
                            },
                        }
                    ),
                    self.fresh_for,
                    Decimal("1"),
                )
            )
        return EvidenceBundle(tenant_id=tenant_id, collected_at=collected_at, evidence=tuple(references))


class ListingOptimizationAgentService:
    WORKFLOW_TYPE = "listing_optimization_analysis"

    def __init__(
        self,
        *,
        workflow: Any,
        registry: AdvisorRegistry,
        evidence_collector: ListingOptimizationEvidenceCollector,
        budget: dict[str, Any] | None = None,
    ) -> None:
        self.workflow = workflow
        self.registry = registry
        self.evidence_collector = evidence_collector
        self.budget = dict(budget or {})

    async def analyze(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        correlation_id: str,
        snapshot_id: str,
        locale: str = "en-US",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self.workflow.run_advisory(
            tenant_id=tenant_id,
            actor_id=actor_id,
            workflow_type=self.WORKFLOW_TYPE,
            correlation_id=correlation_id,
            request={
                **self.budget,
                "snapshot_id": snapshot_id,
                "locale": locale,
                "idempotency_key": idempotency_key,
                "allowed_capabilities": ["read_evidence", "draft_listing_changes"],
            },
            evidence_collector=self.evidence_collector,
            advisor=self.registry.get(self.WORKFLOW_TYPE),
        )


def _reference(
    evidence_id: str,
    evidence_type: str,
    source: str,
    resource_type: str,
    resource_id: str,
    observed_at: datetime,
    collected_at: datetime,
    metadata: dict[str, Any],
    fresh_for: timedelta,
    confidence: Decimal,
) -> EvidenceReference:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    digest = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return EvidenceReference(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source=source,
        resource_type=resource_type,
        resource_id=resource_id,
        observed_at=observed_at,
        collected_at=collected_at,
        freshness_status="fresh" if collected_at - observed_at <= fresh_for else "stale",
        confidence=confidence,
        verified_amazon_data=False,
        content_digest=digest,
        metadata=metadata,
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)
