from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from agents.contracts import EvidenceBundle, EvidenceReference
from agents.registry import AdvisorRegistry


MAX_MODEL_RISK_PACKETS = 12
MAX_MODEL_EVIDENCE_ITEMS = 14


class OperationsBriefEvidenceCollector:
    def __init__(
        self,
        *,
        brief_repository: Any,
        issue_repository: Any,
        action_repository: Any,
        fresh_for: timedelta = timedelta(hours=24),
    ) -> None:
        self.brief_repository = brief_repository
        self.issue_repository = issue_repository
        self.action_repository = action_repository
        self.fresh_for = fresh_for

    async def collect(self, *, tenant_id: str, request: dict[str, Any]) -> EvidenceBundle:
        brief = self.brief_repository.get_latest(tenant_id=tenant_id)
        if brief is None:
            raise LookupError("Daily operations brief not found for tenant")
        requested_brief_id = request.get("brief_id")
        if requested_brief_id and brief.id != requested_brief_id:
            raise LookupError("Requested daily operations brief is not the tenant's latest persisted brief")
        if brief.tenant_id != tenant_id:
            raise PermissionError("Daily operations brief tenant mismatch")

        issues = {
            item.id: item
            for item in self.issue_repository.list_issues(tenant_id=tenant_id)
            if item.id in set(brief.priority_issue_ids)
        }
        actions = {
            item.id: item
            for item in self.action_repository.list_actions(tenant_id=tenant_id)
            if item.id in set(brief.priority_action_ids)
        }
        is_mock = (
            str((brief.source_manifest or {}).get("mode", "")).lower() == "mock"
            or any(item.seller_sku.startswith("GOLDEN-") for item in brief.sku_health)
        )
        source = "mock" if is_mock else "internal_record"
        collected_at = datetime.now(UTC)
        references: list[EvidenceReference] = []

        brief_metadata = _json_safe({
            "brief_date": brief.brief_date,
            "timezone": brief.timezone,
            "issue_counts": brief.issue_counts,
            "domain_counts": brief.domain_counts,
            "total_open_issues": brief.total_open_issues,
            "data_quality_issue_count": brief.data_quality_issue_count,
            "impact_by_currency": brief.impact_by_currency,
            "unallocated_impact_issue_count": brief.unallocated_impact_issue_count,
            "priority_issue_ids": brief.priority_issue_ids,
            "priority_action_ids": brief.priority_action_ids,
            "source_manifest": brief.source_manifest,
            "deterministic_values": {
                "issue_counts": brief.issue_counts,
                "impact_by_currency": brief.impact_by_currency,
                "priority_issue_ids": list(brief.priority_issue_ids),
                "priority_action_ids": list(brief.priority_action_ids),
            },
        })
        references.append(_reference(
            evidence_id=brief.id,
            evidence_type="daily_operations_brief",
            source=source,
            resource_type="daily_operations_brief",
            resource_id=brief.id,
            observed_at=brief.generated_at,
            collected_at=collected_at,
            metadata=brief_metadata,
            fresh_for=self.fresh_for,
        ))

        for issue_id in brief.priority_issue_ids:
            issue = issues.get(issue_id)
            if issue is None:
                continue
            metadata = _json_safe({
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "issue_type": issue.issue_type,
                "severity": issue.severity,
                "title": issue.title,
                "summary": issue.summary,
                "status": issue.status,
                "confidence": issue.confidence,
                "missing_data": issue.missing_data,
                "degraded_reason": issue.degraded_reason,
                "currency": issue.currency,
                "evidence": issue.evidence,
                "deterministic_values": {
                    "priority_score": str(issue.priority_score),
                    "estimated_revenue_impact": _money(issue.estimated_revenue_impact, issue.currency),
                    "estimated_profit_impact": _money(issue.estimated_profit_impact, issue.currency),
                    "estimated_cost_impact": _money(issue.estimated_cost_impact, issue.currency),
                },
            })
            references.append(_reference(
                evidence_id=issue.id,
                evidence_type="operational_issue",
                source=source,
                resource_type="operational_issue",
                resource_id=issue.id,
                observed_at=issue.data_freshness_at,
                collected_at=collected_at,
                metadata=metadata,
                fresh_for=self.fresh_for,
                confidence=issue.confidence,
            ))

        for action_id in brief.priority_action_ids:
            action = actions.get(action_id)
            if action is None:
                continue
            metadata = _json_safe({
                "issue_id": action.issue_id,
                "action_type": action.action_type,
                "title": action.title,
                "description": action.description,
                "execution_mode": action.execution_mode,
                "risk_level": action.risk_level,
                "status": action.status,
                "parameters": action.parameters,
                "expected_outcome": action.expected_outcome,
                "confidence": action.confidence,
                "automation_eligible": False,
                "evidence": action.evidence,
                "deterministic_values": {
                    "expected_revenue_impact": _money(action.expected_revenue_impact, action.currency),
                    "expected_profit_impact": _money(action.expected_profit_impact, action.currency),
                    "expected_cost_impact": _money(action.expected_cost_impact, action.currency),
                },
            })
            references.append(_reference(
                evidence_id=action.id,
                evidence_type="recommended_action",
                source=source,
                resource_type="recommended_action",
                resource_id=action.id,
                observed_at=brief.generated_at,
                collected_at=collected_at,
                metadata=metadata,
                fresh_for=self.fresh_for,
                confidence=action.confidence,
            ))

        for item in brief.sku_health:
            evidence_id = f"sku-health:{item.seller_sku}:{item.marketplace_id}"
            metadata = _json_safe({
                "seller_sku": item.seller_sku,
                "marketplace_id": item.marketplace_id,
                "health_status": item.health_status,
                "highest_severity": item.highest_severity,
                "open_issue_count": item.open_issue_count,
                "issue_types": item.issue_types,
                "recommended_action_ids": item.recommended_action_ids,
                "impact_by_currency": item.impact_by_currency,
                "has_data_quality_risk": item.has_data_quality_risk,
                "deterministic_values": {"impact_by_currency": item.impact_by_currency},
            })
            references.append(_reference(
                evidence_id=evidence_id,
                evidence_type="sku_health_summary",
                source=source,
                resource_type="sku_health_summary",
                resource_id=evidence_id,
                observed_at=item.last_detected_at,
                collected_at=collected_at,
                metadata=metadata,
                fresh_for=self.fresh_for,
            ))
        if len(references) > MAX_MODEL_EVIDENCE_ITEMS:
            references = _compact_operations_brief_references(
                brief_reference=references[0],
                issue_ids=brief.priority_issue_ids,
                issues=issues,
                actions=actions,
                sku_health=brief.sku_health,
                source=source,
                collected_at=collected_at,
                fresh_for=self.fresh_for,
            )
        return EvidenceBundle(tenant_id=tenant_id, collected_at=collected_at, evidence=tuple(references))


class OperationsBriefAgentService:
    WORKFLOW_TYPE = "operations_brief_analysis"

    def __init__(self, *, workflow: Any, registry: AdvisorRegistry, evidence_collector: OperationsBriefEvidenceCollector, budget: dict[str, Any] | None = None) -> None:
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
        brief_id: str | None = None,
        locale: str = "en-US",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        advisor = self.registry.get(self.WORKFLOW_TYPE)
        return await self.workflow.run_advisory(
            tenant_id=tenant_id,
            actor_id=actor_id,
            workflow_type=self.WORKFLOW_TYPE,
            correlation_id=correlation_id,
            request={
                **self.budget,
                "brief_id": brief_id,
                "locale": locale,
                "idempotency_key": idempotency_key,
                "allowed_capabilities": ["read_evidence", "create_internal_task_draft"],
            },
            evidence_collector=self.evidence_collector,
            advisor=advisor,
        )


def _reference(
    *,
    evidence_id: str,
    evidence_type: str,
    source: str,
    resource_type: str,
    resource_id: str,
    observed_at: datetime,
    collected_at: datetime,
    metadata: dict[str, Any],
    fresh_for: timedelta,
    confidence: Decimal = Decimal("1"),
) -> EvidenceReference:
    freshness_status = "fresh" if collected_at - observed_at <= fresh_for else "stale"
    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return EvidenceReference(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source=source,
        resource_type=resource_type,
        resource_id=resource_id,
        observed_at=observed_at,
        collected_at=collected_at,
        freshness_status=freshness_status,
        confidence=confidence,
        verified_amazon_data=False,
        content_digest=digest,
        metadata=metadata,
    )


def _money(amount: Decimal | None, currency: str | None) -> dict[str, str] | None:
    if amount is None or currency is None:
        return None
    return {"amount": str(amount), "currency": currency}


def _compact_operations_brief_references(
    *,
    brief_reference: EvidenceReference,
    issue_ids: tuple[str, ...],
    issues: dict[str, Any],
    actions: dict[str, Any],
    sku_health: tuple[Any, ...],
    source: str,
    collected_at: datetime,
    fresh_for: timedelta,
) -> list[EvidenceReference]:
    actions_by_issue: dict[str, list[Any]] = {}
    for action in actions.values():
        actions_by_issue.setdefault(action.issue_id, []).append(action)
    sku_by_scope = {(item.seller_sku, item.marketplace_id): item for item in sku_health}

    ordered_issues = [issues[issue_id] for issue_id in issue_ids if issue_id in issues]
    detailed_issues = ordered_issues[:MAX_MODEL_RISK_PACKETS]
    remainder_issues = ordered_issues[MAX_MODEL_RISK_PACKETS:]
    compacted = [brief_reference]
    for index, issue in enumerate(detailed_issues):
        related_actions = sorted(actions_by_issue.get(issue.id, []), key=lambda item: item.id)
        health = sku_by_scope.get((issue.seller_sku, issue.marketplace_id))
        metadata = {
            "selection_priority": index + 1,
            "issue": _compact_issue(issue),
            "recommended_actions": [_compact_action(action) for action in related_actions],
            "sku_health": _compact_sku_health(health) if health is not None else None,
            "covered_evidence_ids": [
                issue.id,
                *(action.id for action in related_actions),
                *(
                    [f"sku-health:{health.seller_sku}:{health.marketplace_id}"]
                    if health is not None else []
                ),
            ],
        }
        compacted.append(_reference(
            evidence_id=f"risk-packet:{issue.id}",
            evidence_type="operations_risk_packet",
            source=source,
            resource_type="operational_issue",
            resource_id=issue.id,
            observed_at=issue.data_freshness_at,
            collected_at=collected_at,
            metadata=_json_safe(metadata),
            fresh_for=fresh_for,
            confidence=issue.confidence,
        ))

    if remainder_issues:
        remainder_actions = [
            action
            for issue in remainder_issues
            for action in sorted(actions_by_issue.get(issue.id, []), key=lambda item: item.id)
        ]
        metadata = {
            "selection_priority": MAX_MODEL_RISK_PACKETS + 1,
            "summary": (
                "Lower-ranked risks remain deterministic queue items. Review the referenced records before action; "
                "do not treat this compact list as resolved or low risk."
            ),
            "issues": [_compact_issue_summary(issue) for issue in remainder_issues],
            "recommended_actions": [_compact_action_summary(action) for action in remainder_actions],
            "covered_evidence_ids": [
                *(issue.id for issue in remainder_issues),
                *(action.id for action in remainder_actions),
            ],
        }
        compacted.append(_reference(
            evidence_id=f"risk-remainder:{brief_reference.evidence_id}",
            evidence_type="operations_risk_remainder",
            source=source,
            resource_type="daily_operations_brief",
            resource_id=brief_reference.resource_id,
            observed_at=brief_reference.observed_at or collected_at,
            collected_at=collected_at,
            metadata=_json_safe(metadata),
            fresh_for=fresh_for,
            confidence=min((issue.confidence for issue in remainder_issues), default=Decimal("1")),
        ))
    return compacted


def _compact_issue(issue: Any) -> dict[str, Any]:
    return {
        **_compact_issue_summary(issue),
        "title": issue.title,
        "summary": issue.summary,
        "status": issue.status,
        "confidence": str(issue.confidence),
        "missing_data": list(issue.missing_data),
        "degraded_reason": issue.degraded_reason,
        "deterministic_values": {
            "priority_score": str(issue.priority_score),
            "estimated_revenue_impact": _money(issue.estimated_revenue_impact, issue.currency),
            "estimated_profit_impact": _money(issue.estimated_profit_impact, issue.currency),
            "estimated_cost_impact": _money(issue.estimated_cost_impact, issue.currency),
        },
    }


def _compact_issue_summary(issue: Any) -> dict[str, Any]:
    return {
        "issue_id": issue.id,
        "seller_sku": issue.seller_sku,
        "marketplace_id": issue.marketplace_id,
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "priority_score": str(issue.priority_score),
        "missing_data": list(issue.missing_data),
        "degraded_reason": issue.degraded_reason,
    }


def _compact_action(action: Any) -> dict[str, Any]:
    return {
        **_compact_action_summary(action),
        "description": action.description,
        "execution_mode": action.execution_mode,
        "status": action.status,
        "parameters": action.parameters,
        "expected_outcome": action.expected_outcome,
        "confidence": str(action.confidence),
        "automation_eligible": False,
    }


def _compact_action_summary(action: Any) -> dict[str, Any]:
    return {
        "action_id": action.id,
        "issue_id": action.issue_id,
        "action_type": action.action_type,
        "title": action.title,
        "risk_level": action.risk_level,
    }


def _compact_sku_health(item: Any) -> dict[str, Any]:
    return {
        "seller_sku": item.seller_sku,
        "marketplace_id": item.marketplace_id,
        "health_status": item.health_status,
        "highest_severity": item.highest_severity,
        "open_issue_count": item.open_issue_count,
        "issue_types": list(item.issue_types),
        "has_data_quality_risk": item.has_data_quality_risk,
        "impact_by_currency": item.impact_by_currency,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    return str(value)
