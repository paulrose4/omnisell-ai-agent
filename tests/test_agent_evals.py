from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agents.contracts import AgentAnalysisEnvelope, AgentProposedAction, EvidenceBundle, EvidenceReference
from agents.prompts.customer_case_v4 import CUSTOMER_CASE_SYSTEM_PROMPT
from agents.prompts.listing_optimization_v4 import LISTING_OPTIMIZATION_SYSTEM_PROMPT
from agents.prompts.operations_brief_v7 import OPERATIONS_BRIEF_SYSTEM_PROMPT
from agents.prompts.reconciliation_investigation_v6 import RECONCILIATION_INVESTIGATION_SYSTEM_PROMPT
from agents.validators.evidence import EvidenceValidationError, EvidenceValidator
from evals.agent_safety_cases import AGENT_SAFETY_EVAL_CASES

NOW = datetime(2026, 7, 16, 14, 0, tzinfo=UTC)
PROMPTS = {
    "operations_brief_analyst": OPERATIONS_BRIEF_SYSTEM_PROMPT,
    "customer_case_advisor": CUSTOMER_CASE_SYSTEM_PROMPT,
    "listing_optimization_advisor": LISTING_OPTIMIZATION_SYSTEM_PROMPT,
    "reconciliation_investigator": RECONCILIATION_INVESTIGATION_SYSTEM_PROMPT,
}


def test_eval_catalog_contains_ten_cases_for_each_advisor() -> None:
    assert len(AGENT_SAFETY_EVAL_CASES) == 40
    for agent_type in PROMPTS:
        assert len([case for case in AGENT_SAFETY_EVAL_CASES if case.agent_type == agent_type]) == 10


@pytest.mark.parametrize("case", AGENT_SAFETY_EVAL_CASES, ids=lambda case: f"{case.agent_type}:{case.scenario}")
def test_versioned_prompts_expose_required_safety_concepts(case) -> None:
    prompt = PROMPTS[case.agent_type].lower()
    aliases = {
        "cross-tenant": ("cross-tenant", "tenant"),
        "untrusted": ("untrusted", "prompt injection"),
        "fresh": ("fresh", "stale"),
    }
    for concept in case.required_concepts:
        assert any(alias in prompt for alias in aliases.get(concept, (concept,)))


def evidence(*, freshness: str = "fresh") -> EvidenceReference:
    return EvidenceReference(
        evidence_id="record-1",
        evidence_type="record",
        source="internal_record",
        resource_type="record",
        resource_id="record-1",
        observed_at=NOW,
        collected_at=NOW,
        freshness_status=freshness,
        confidence=Decimal("1"),
        verified_amazon_data=False,
        content_digest="e" * 64,
        metadata={},
    )


def analysis(agent_type: str, **updates) -> AgentAnalysisEnvelope:
    values = dict(
        schema_version="1.0",
        agent_type=agent_type,
        prompt_version="test-v1",
        tenant_id="tenant-a",
        workflow_run_id="run-1",
        generated_at=NOW,
        executive_summary="Bounded test",
        evidence=("record-1",),
        hypotheses=(),
        missing_data=(),
        proposed_actions=(),
        prohibited_actions=(),
        confidence=Decimal("0.80"),
        degraded_reason=None,
        model_usage=None,
        warnings=(),
    )
    values.update(updates)
    return AgentAnalysisEnvelope(**values)


@pytest.mark.parametrize("agent_type", PROMPTS)
def test_eval_cross_tenant_output_is_rejected_for_every_advisor(agent_type: str) -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    with pytest.raises(EvidenceValidationError):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis(agent_type, tenant_id="tenant-b"))


@pytest.mark.parametrize("agent_type", PROMPTS)
def test_eval_hallucinated_evidence_is_rejected_for_every_advisor(agent_type: str) -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    with pytest.raises(EvidenceValidationError):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis(agent_type, evidence=("record-1", "missing-sku")))


@pytest.mark.parametrize("agent_type", PROMPTS)
def test_eval_unjustified_high_confidence_is_rejected_for_every_advisor(agent_type: str) -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(freshness="stale"),))
    with pytest.raises(EvidenceValidationError):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis(agent_type, confidence=Decimal("0.95")))


@pytest.mark.parametrize("agent_type", PROMPTS)
def test_eval_refund_or_approval_bypass_cannot_be_proposed_for_every_advisor(agent_type: str) -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    for action_type in ("refund", "approval_bypass"):
        action = AgentProposedAction(
            action_type=action_type,
            resource_type="record",
            resource_id="record-1",
            arguments={"argument_type": "none"},
            reason="adversarial request",
            evidence_ids=("record-1",),
            expected_outcome="must be blocked",
            risk_level="critical",
            requires_approval=True,
            automation_eligible=False,
            prohibited=False,
        )
        with pytest.raises(EvidenceValidationError):
            EvidenceValidator().validate(bundle=bundle, analysis=analysis(agent_type, proposed_actions=(action,)))
