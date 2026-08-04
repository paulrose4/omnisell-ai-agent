from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from agents.contracts import EvidenceBundle, EvidenceReference
from agents.registry import AdvisorRegistry
from application.customer_service import PLAYBOOKS

MARKETPLACE_COMMUNICATION_POLICY = {
    "version": "marketplace-communication-v1",
    "off_platform_contact": "prohibited",
    "review_incentive": "prohibited",
    "refund_promise": "prohibited",
    "compensation_promise": "prohibited",
    "external_links": "prohibited",
    "high_risk_reply_requires_approval": True,
}


class CustomerCaseEvidenceCollector:
    def __init__(self, *, case_repository: Any, fresh_for: timedelta = timedelta(hours=24)) -> None:
        self.case_repository = case_repository
        self.fresh_for = fresh_for

    async def collect(self, *, tenant_id: str, request: dict[str, Any]) -> EvidenceBundle:
        case_id = str(request.get("case_id") or "")
        if not case_id:
            raise ValueError("case_id is required")
        case = self.case_repository.get(tenant_id=tenant_id, case_id=case_id)
        if case is None or case.tenant_id != tenant_id:
            raise LookupError("Customer case not found")
        collected_at = datetime.now(UTC)
        source = "mock" if case.source == "mock_buyer" else "internal_record"
        references: list[EvidenceReference] = []
        case_metadata = _json_safe(
            {
                "external_case_id": case.external_case_id,
                "marketplace_id": case.marketplace_id,
                "external_order_id": case.external_order_id,
                "seller_sku": case.seller_sku,
                "asin": case.asin,
                "language": case.language,
                "deterministic_intent": case.intent,
                "deterministic_sentiment": case.sentiment,
                "deterministic_urgency": case.urgency,
                "severity": case.severity,
                "risk_level": case.risk_level,
                "status": case.status,
                "draft_reply": case.draft_reply,
                "approved_reply": case.approved_reply,
                "confidence": case.confidence,
                "missing_data": case.missing_data,
                "policy_checks": case.policy_checks,
                "approval_request_id": case.approval_request_id,
                "source": case.source,
                "verified_amazon_data": case.verified_amazon_data,
                "deterministic_values": {
                    "priority_score": str(case.priority_score),
                    "intent": case.intent,
                    "severity": case.severity,
                    "risk_level": case.risk_level,
                    "status": case.status,
                },
            }
        )
        references.append(
            _reference(
                case.id,
                "customer_case",
                source,
                "customer_case",
                case.id,
                case.updated_at,
                collected_at,
                case_metadata,
                self.fresh_for,
                case.confidence,
                False,
            )
        )
        references.append(
            _reference(
                f"message:{case.id}",
                "buyer_message",
                source,
                "buyer_message",
                case.external_case_id,
                case.created_at,
                collected_at,
                _json_safe({"message": case.customer_message, "language": case.language, "untrusted_content": True}),
                self.fresh_for,
                Decimal("1"),
                False,
            )
        )
        order = (case.evidence or {}).get("order")
        if order and case.external_order_id:
            amazon_verified = bool(case.verified_amazon_data and (case.evidence or {}).get("verified_amazon_data"))
            order_source = "amazon_api" if amazon_verified else source
            references.append(
                _reference(
                    f"order:{case.external_order_id}",
                    "order_context",
                    order_source,
                    "order",
                    case.external_order_id,
                    case.updated_at,
                    collected_at,
                    _json_safe({"order": order, "deterministic_values": order}),
                    self.fresh_for,
                    Decimal("1"),
                    amazon_verified,
                )
            )
        playbook_key = case.playbook_key or case.intent
        playbook_version = case.playbook_version or str(PLAYBOOKS.get(playbook_key, {}).get("version", "unknown"))
        playbook = PLAYBOOKS.get(playbook_key, {})
        references.append(
            _reference(
                f"playbook:{playbook_key}:{playbook_version}",
                "customer_service_playbook",
                "internal_record",
                "customer_service_playbook",
                playbook_key,
                case.updated_at,
                collected_at,
                _json_safe({"playbook_key": playbook_key, "version": playbook_version, "config": playbook}),
                self.fresh_for,
                Decimal("1"),
                False,
            )
        )
        references.append(
            _reference(
                "policy:marketplace-communication-v1",
                "marketplace_communication_policy",
                "internal_record",
                "marketplace_communication_policy",
                "marketplace-communication-v1",
                collected_at,
                collected_at,
                MARKETPLACE_COMMUNICATION_POLICY,
                self.fresh_for,
                Decimal("1"),
                False,
            )
        )
        timeline = self.case_repository.timeline(tenant_id=tenant_id, case_id=case.id)
        references.append(
            _reference(
                f"timeline:{case.id}",
                "customer_case_timeline",
                "internal_record",
                "customer_case",
                case.id,
                case.updated_at,
                collected_at,
                _json_safe({"events": timeline}),
                self.fresh_for,
                Decimal("1"),
                False,
            )
        )
        for index, rag_item in enumerate((case.evidence or {}).get("rag_evidence") or []):
            rag_id = str(rag_item.get("evidence_id") or f"rag:{case.id}:{index}")
            references.append(
                _reference(
                    rag_id,
                    "rag_document",
                    "internal_record",
                    "rag_document",
                    rag_id,
                    case.updated_at,
                    collected_at,
                    _json_safe({"document": rag_item, "untrusted_content": True}),
                    self.fresh_for,
                    Decimal(str(rag_item.get("confidence") or "0.5")),
                    False,
                )
            )
        return EvidenceBundle(tenant_id=tenant_id, collected_at=collected_at, evidence=tuple(references))


class CustomerCaseAgentService:
    WORKFLOW_TYPE = "customer_case_analysis"

    def __init__(
        self,
        *,
        workflow: Any,
        registry: AdvisorRegistry,
        evidence_collector: CustomerCaseEvidenceCollector,
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
        case_id: str,
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
                "case_id": case_id,
                "locale": locale,
                "idempotency_key": idempotency_key,
                "allowed_capabilities": ["read_evidence", "draft_customer_reply"],
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
    verified_amazon_data: bool,
) -> EvidenceReference:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    freshness = "fresh" if collected_at - observed_at <= fresh_for else "stale"
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
        freshness_status=freshness,
        confidence=confidence,
        verified_amazon_data=verified_amazon_data,
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
