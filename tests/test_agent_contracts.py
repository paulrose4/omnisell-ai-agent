from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.contracts import (
    AgentAnalysisEnvelope,
    AgentHypothesis,
    AgentProposedAction,
    EvidenceBundle,
    EvidenceReference,
    MissingDataItem,
)
from agents.validators.evidence import EvidenceValidationError, EvidenceValidator


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def evidence(
    evidence_id: str = "issue-1",
    *,
    source: str = "internal_record",
    freshness_status: str = "fresh",
    resource_type: str = "operational_issue",
    resource_id: str = "issue-1",
    verified_amazon_data: bool = False,
    metadata: dict | None = None,
) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=evidence_id,
        evidence_type="record",
        source=source,
        resource_type=resource_type,
        resource_id=resource_id,
        observed_at=NOW,
        collected_at=NOW,
        freshness_status=freshness_status,
        confidence=Decimal("0.95"),
        verified_amazon_data=verified_amazon_data,
        content_digest="a" * 64,
        metadata=metadata or {},
    )


def envelope(**overrides: object) -> AgentAnalysisEnvelope:
    values = {
        "schema_version": "1.0",
        "agent_type": "operations_brief_analyst",
        "prompt_version": "operations-brief-v1",
        "tenant_id": "tenant-a",
        "workflow_run_id": "run-1",
        "generated_at": NOW,
        "executive_summary": "Issue requires review.",
        "evidence": ("issue-1",),
        "hypotheses": (
            AgentHypothesis(
                hypothesis_id="hyp-1",
                statement="The issue may share a stale-input cause.",
                supporting_evidence_ids=("issue-1",),
                confidence=Decimal("0.70"),
                status="hypothesis",
            ),
        ),
        "missing_data": (),
        "proposed_actions": (),
        "prohibited_actions": (),
        "confidence": Decimal("0.80"),
        "degraded_reason": None,
        "model_usage": None,
        "warnings": (),
    }
    values.update(overrides)
    return AgentAnalysisEnvelope(**values)


def test_evidence_datetime_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        evidence().model_copy(update={"collected_at": datetime(2026, 7, 16, 8, 0)}).model_validate(
            evidence().model_dump() | {"collected_at": datetime(2026, 7, 16, 8, 0)}
        )


def test_mock_evidence_cannot_claim_verified_amazon_data() -> None:
    with pytest.raises(ValidationError, match="verified Amazon"):
        evidence(source="mock", verified_amazon_data=True)


def test_unknown_evidence_reference_is_rejected() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    analysis = envelope(evidence=("issue-1", "hallucinated-sku"))

    with pytest.raises(EvidenceValidationError, match="hallucinated-sku"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_blocking_missing_data_caps_confidence() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    analysis = envelope(
        confidence=Decimal("0.90"),
        missing_data=(MissingDataItem(field="profit_snapshot", reason="not collected", required_for="priority explanation", blocking=True),),
    )

    with pytest.raises(EvidenceValidationError, match="0.65"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_high_confidence_requires_fresh_evidence() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(freshness_status="stale"),))
    analysis = envelope(confidence=Decimal("0.91"))

    with pytest.raises(EvidenceValidationError, match="fresh"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_proposed_action_cannot_enable_automation() -> None:
    with pytest.raises(ValidationError, match="automation_eligible"):
        AgentProposedAction(
            action_type="create_internal_task_draft",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={},
            reason="Draft a task for operator review.",
            evidence_ids=("issue-1",),
            expected_outcome="Internal draft only.",
            risk_level="low",
            requires_approval=False,
            automation_eligible=True,
            prohibited=False,
        )


def test_agent_cannot_change_deterministic_money_value() -> None:
    bundle = EvidenceBundle(
        tenant_id="tenant-a",
        collected_at=NOW,
        evidence=(
            evidence(
                metadata={
                    "deterministic_values": {
                        "price": {"amount": "19.99", "currency": "USD"}
                    }
                }
            ),
        ),
    )
    action = AgentProposedAction(
        action_type="create_internal_task_draft",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"price": {"amount": "18.99", "currency": "USD"}},
        reason="Reference the persisted price.",
        evidence_ids=("issue-1",),
        expected_outcome="Internal draft only.",
        risk_level="low",
        requires_approval=False,
        automation_eligible=False,
        prohibited=False,
    )

    with pytest.raises(EvidenceValidationError, match="deterministic value"):
        EvidenceValidator().validate(bundle=bundle, analysis=envelope(proposed_actions=(action,)))


def test_agent_cannot_introduce_deterministic_business_value_missing_from_evidence() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    action = AgentProposedAction(
        action_type="create_internal_task_draft",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"recommended_price": {"amount": "18.99", "currency": "USD"}},
        reason="Reference a price that was not supplied by deterministic evidence.",
        evidence_ids=("issue-1",),
        expected_outcome="Internal draft only.",
        risk_level="low",
        requires_approval=False,
        automation_eligible=False,
        prohibited=False,
    )

    with pytest.raises(EvidenceValidationError, match="not present in deterministic evidence"):
        EvidenceValidator().validate(bundle=bundle, analysis=envelope(proposed_actions=(action,)))


def test_prohibited_action_alias_cannot_bypass_boundary_with_punctuation() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    action = AgentProposedAction(
        action_type="approve-refund",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={},
        reason="Attempt to bypass the permanent refund block.",
        evidence_ids=("issue-1",),
        expected_outcome="Refund issued.",
        risk_level="critical",
        requires_approval=True,
        automation_eligible=False,
        prohibited=False,
    )

    with pytest.raises(EvidenceValidationError, match="permanently prohibited"):
        EvidenceValidator().validate(bundle=bundle, analysis=envelope(proposed_actions=(action,)))
