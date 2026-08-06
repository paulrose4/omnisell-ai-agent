from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.contracts import (
    AgentAnalysisEnvelope,
    AgentHypothesis,
    AgentProposedAction,
    CustomerCaseAnalysisDraft,
    DifferenceCluster,
    EvidenceBundle,
    EvidencePreparationManifest,
    EvidenceReference,
    InternalTaskDraft,
    ListingFieldChange,
    ListingOptimizationAnalysisDraft,
    MissingDataItem,
    OperationsBriefAnalysisDraft,
    ReadOnlyCheck,
    ReconciliationInvestigationDraft,
    analysis_draft_schema,
)
from agents.validators.evidence import EvidenceValidationError, EvidenceValidator
from model_gateway.contracts import ModelBudgetRequest, estimate_single_provider_budget

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


def test_evidence_metadata_rejects_non_json_values() -> None:
    with pytest.raises(ValidationError, match="JSON-compatible"):
        evidence(metadata={"unsafe": {"not", "json"}})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_evidence_metadata_rejects_non_finite_json_numbers(value: float) -> None:
    with pytest.raises(ValidationError, match="finite JSON number"):
        evidence(metadata={"unsafe_number": value})


def test_evidence_metadata_rejects_excessive_depth() -> None:
    nested: object = "leaf"
    for _ in range(8):
        nested = {"child": nested}

    with pytest.raises(ValidationError, match="depth"):
        evidence(metadata={"root": nested})


def test_evidence_metadata_rejects_oversized_text_and_collections() -> None:
    with pytest.raises(ValidationError, match="string"):
        evidence(metadata={"summary": "x" * 2001})

    with pytest.raises(ValidationError, match="items"):
        evidence(metadata={"values": list(range(129))})


def test_evidence_metadata_rejects_invalid_keys_and_node_bombs() -> None:
    for metadata in ({"": "empty"}, {1: "non-string"}, {"x" * 101: "too-long"}):
        with pytest.raises(ValidationError, match="keys"):
            evidence(metadata=metadata)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="total nodes"):
        evidence(metadata={"values": [{"left": index, "right": index} for index in range(100)]})


def test_evidence_preparation_manifest_counts_are_internally_consistent() -> None:
    estimate = estimate_single_provider_budget(
        request=ModelBudgetRequest(estimated_prompt_tokens=100, max_completion_tokens=50),
        provider="provider-a",
        model="model-a",
        provider_token_limit=1000,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
        timeout_seconds=30,
        pricing_version="test-v1",
    )
    common = {
        "original_evidence_count": 2,
        "selected_evidence_count": 1,
        "omitted_evidence_count": 1,
        "original_serialized_chars": 200,
        "selected_serialized_chars": 100,
        "request_bytes": 300,
        "estimated_prompt_tokens": 100,
        "max_prompt_tokens": 200,
        "max_total_evidence_chars": 1000,
        "selected_evidence_ids": ("evidence-1",),
        "omitted_evidence_ids": ("evidence-2",),
        "model_budget_estimate": estimate,
    }
    assert EvidencePreparationManifest(**common).original_evidence_count == 2

    for changes, message in (
        ({"selected_evidence_count": 2}, "selected evidence count"),
        ({"omitted_evidence_count": 2}, "omitted evidence count"),
        ({"original_evidence_count": 3}, "original evidence count"),
    ):
        with pytest.raises(ValidationError, match=message):
            EvidencePreparationManifest(**(common | changes))


def test_remaining_contract_invariants_fail_closed() -> None:
    with pytest.raises(ValidationError, match="supporting evidence"):
        AgentHypothesis(
            hypothesis_id="hyp-unsupported",
            statement="This hypothesis has no support.",
            confidence=Decimal("0.50"),
            status="hypothesis",
        )

    with pytest.raises(ValidationError, match="prohibited_reason"):
        AgentProposedAction(
            action_type="refund",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={"argument_type": "none"},
            reason="Record the permanent block.",
            evidence_ids=("issue-1",),
            expected_outcome="No side effect.",
            risk_level="critical",
            requires_approval=True,
            prohibited=True,
        )

    with pytest.raises(ValidationError, match="proposed_text is required"):
        ListingFieldChange(
            field="title",
            operation="replace",
            reason="A replacement requires exact text.",
            evidence_ids=("issue-1",),
        )
    with pytest.raises(ValidationError, match="must not include proposed_text"):
        ListingFieldChange(
            field="title",
            operation="remove",
            proposed_text="should not be present",
            reason="A removal cannot contain replacement text.",
            evidence_ids=("issue-1",),
        )

    with pytest.raises(ValueError, match="No model-facing draft schema"):
        analysis_draft_schema(type("UnknownEnvelope", (), {}))  # type: ignore[arg-type]


def test_unknown_evidence_reference_is_rejected() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    analysis = envelope(evidence=("issue-1", "hallucinated-sku"))

    with pytest.raises(EvidenceValidationError, match="hallucinated-sku"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_analysis_tenant_must_match_evidence_bundle() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))

    with pytest.raises(EvidenceValidationError, match="tenant"):
        EvidenceValidator().validate(bundle=bundle, analysis=envelope(tenant_id="tenant-b"))


def test_blocking_missing_data_caps_confidence() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    analysis = envelope(
        confidence=Decimal("0.90"),
        missing_data=(
            MissingDataItem(
                field="profit_snapshot", reason="not collected", required_for="priority explanation", blocking=True
            ),
        ),
    )

    with pytest.raises(EvidenceValidationError, match="0.65"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_high_confidence_requires_fresh_evidence() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(freshness_status="stale"),))
    analysis = envelope(confidence=Decimal("0.91"))

    with pytest.raises(EvidenceValidationError, match="fresh"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_high_confidence_requires_at_least_one_referenced_evidence_record() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    analysis = envelope(evidence=(), hypotheses=(), confidence=Decimal("0.90"))

    with pytest.raises(EvidenceValidationError, match="fresh referenced evidence"):
        EvidenceValidator().validate(bundle=bundle, analysis=analysis)


def test_structured_confidence_requires_internal_or_platform_evidence() -> None:
    bundle = EvidenceBundle(
        tenant_id="tenant-a",
        collected_at=NOW,
        evidence=(evidence(source="manual"),),
    )

    with pytest.raises(EvidenceValidationError, match="structured internal or platform evidence"):
        EvidenceValidator().validate(bundle=bundle, analysis=envelope(confidence=Decimal("0.75")))


def test_proposed_action_cannot_enable_automation() -> None:
    with pytest.raises(ValidationError, match="automation_eligible"):
        AgentProposedAction(
            action_type="create_internal_task_draft",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={"argument_type": "internal_task"},
            reason="Draft a task for operator review.",
            evidence_ids=("issue-1",),
            expected_outcome="Internal draft only.",
            risk_level="low",
            requires_approval=False,
            automation_eligible=True,
            prohibited=False,
        )


def test_agent_cannot_change_deterministic_money_value() -> None:
    with pytest.raises(ValidationError, match="price"):
        AgentProposedAction(
            action_type="create_internal_task_draft",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={"argument_type": "internal_task", "price": {"amount": "18.99", "currency": "USD"}},
            reason="Reference the persisted price.",
            evidence_ids=("issue-1",),
            expected_outcome="Internal draft only.",
            risk_level="low",
            requires_approval=False,
            automation_eligible=False,
            prohibited=False,
        )


def test_agent_cannot_introduce_deterministic_business_value_missing_from_evidence() -> None:
    with pytest.raises(ValidationError, match="recommended_price"):
        AgentProposedAction(
            action_type="create_internal_task_draft",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={
                "argument_type": "internal_task",
                "recommended_price": {"amount": "18.99", "currency": "USD"},
            },
            reason="Reference a price that was not supplied by deterministic evidence.",
            evidence_ids=("issue-1",),
            expected_outcome="Internal draft only.",
            risk_level="low",
            requires_approval=False,
            automation_eligible=False,
            prohibited=False,
        )


def test_prohibited_action_alias_cannot_bypass_boundary_with_punctuation() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    action = AgentProposedAction(
        action_type="approve-refund",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"argument_type": "none"},
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


def test_action_resource_must_be_present_in_referenced_evidence() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    action = AgentProposedAction(
        action_type="reference_evidence",
        resource_type="operational_issue",
        resource_id="issue-2",
        arguments={"argument_type": "evidence_reference", "related_evidence_ids": ("issue-1",)},
        reason="Reference evidence for a different resource.",
        evidence_ids=("issue-1",),
        expected_outcome="The resource boundary is enforced.",
        risk_level="low",
        requires_approval=False,
    )

    with pytest.raises(EvidenceValidationError, match="issue-2"):
        EvidenceValidator().validate(bundle=bundle, analysis=envelope(proposed_actions=(action,)))


def test_permanently_prohibited_action_is_allowed_only_as_an_explicit_prohibition() -> None:
    bundle = EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=(evidence(),))
    action = AgentProposedAction(
        action_type="refund",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"argument_type": "none"},
        reason="Record the permanent product boundary.",
        evidence_ids=("issue-1",),
        expected_outcome="No refund is executed.",
        risk_level="critical",
        requires_approval=True,
        prohibited=True,
        prohibited_reason="Refunds are permanently blocked.",
    )

    validated = EvidenceValidator().validate(bundle=bundle, analysis=envelope(prohibited_actions=(action,)))

    assert validated.prohibited_actions == (action,)


def test_conflicting_deterministic_evidence_is_rejected() -> None:
    validator = EvidenceValidator()
    action = AgentProposedAction(
        action_type="reference_evidence",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"argument_type": "evidence_reference"},
        reason="Reference deterministic evidence.",
        evidence_ids=("evidence-a", "evidence-b"),
        expected_outcome="The conflicting records are rejected.",
        risk_level="low",
        requires_approval=False,
    )
    evidence_by_id = {
        "evidence-a": evidence(
            "evidence-a",
            metadata={"deterministic_values": {"inventory": {"quantity": 10}}},
        ),
        "evidence-b": evidence(
            "evidence-b",
            metadata={"deterministic_values": {"inventory": {"quantity": 11}}},
        ),
    }

    with pytest.raises(EvidenceValidationError, match="conflicting deterministic values"):
        validator._validate_deterministic_values(action, evidence_by_id)


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        ({"inventory": {"quantity": 11}, "comment": "operator review"}, "changed deterministic value"),
        ({"inventory": {"quantity": 10}, "price": {"amount": "19.99"}}, "not present in deterministic evidence"),
    ],
)
def test_deterministic_action_values_must_exactly_match_evidence(
    arguments: dict[str, object], expected_error: str
) -> None:
    validator = EvidenceValidator()
    action = AgentProposedAction(
        action_type="reference_evidence",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"argument_type": "evidence_reference"},
        reason="Reference deterministic evidence.",
        evidence_ids=("issue-1",),
        expected_outcome="Deterministic values remain unchanged.",
        risk_level="low",
        requires_approval=False,
    ).model_copy(update={"arguments": arguments})
    evidence_by_id = {"issue-1": evidence(metadata={"deterministic_values": {"inventory": {"quantity": 10}}})}

    with pytest.raises(EvidenceValidationError, match=expected_error):
        validator._validate_deterministic_values(action, evidence_by_id)


def test_deterministic_action_values_accept_exact_match_and_ignore_non_business_fields() -> None:
    validator = EvidenceValidator()
    action = AgentProposedAction(
        action_type="reference_evidence",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"argument_type": "evidence_reference"},
        reason="Reference deterministic evidence.",
        evidence_ids=("issue-1",),
        expected_outcome="Deterministic values remain unchanged.",
        risk_level="low",
        requires_approval=False,
    ).model_copy(update={"arguments": {"inventory": {"quantity": 10}, "comment": "operator review"}})
    evidence_by_id = {"issue-1": evidence(metadata={"deterministic_values": {"inventory": {"quantity": 10}}})}

    validator._validate_deterministic_values(action, evidence_by_id)


def test_malformed_deterministic_metadata_is_ignored_when_action_has_no_business_values() -> None:
    validator = EvidenceValidator()
    action = AgentProposedAction(
        action_type="reference_evidence",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={"argument_type": "evidence_reference"},
        reason="Reference evidence without deterministic values.",
        evidence_ids=("issue-1",),
        expected_outcome="Malformed optional metadata does not create a business value.",
        risk_level="low",
        requires_approval=False,
    )

    validator._validate_deterministic_values(
        action,
        {"issue-1": evidence(metadata={"deterministic_values": []})},
    )


def test_flatten_handles_models_lists_and_tuples() -> None:
    action_arguments = AgentProposedAction(
        action_type="create_internal_task_draft",
        resource_type="operational_issue",
        resource_id="issue-1",
        arguments={
            "argument_type": "internal_task",
            "related_resource_ids": ("sku-1", "sku-2"),
            "task_title": "Review stockout",
        },
        reason="Draft a bounded task.",
        evidence_ids=("issue-1",),
        expected_outcome="A task draft is created.",
        risk_level="low",
        requires_approval=False,
    ).arguments

    flattened = EvidenceValidator._flatten({"arguments": action_arguments, "pair": ("a", ["b"])})

    assert flattened["arguments.related_resource_ids.0"] == "sku-1"
    assert flattened["arguments.related_resource_ids.1"] == "sku-2"
    assert flattened["pair.0"] == "a"
    assert flattened["pair.1.0"] == "b"


def test_action_arguments_reject_unknown_keys_and_numeric_target_injection() -> None:
    common = {
        "action_type": "create_internal_task_draft",
        "resource_type": "operational_issue",
        "resource_id": "issue-1",
        "reason": "Draft a bounded task for operator review.",
        "evidence_ids": ("issue-1",),
        "expected_outcome": "An operator receives a reviewable task draft.",
        "risk_level": "low",
        "requires_approval": False,
        "automation_eligible": False,
        "prohibited": False,
    }

    with pytest.raises(ValidationError, match="unexpected_key"):
        AgentProposedAction(**common, arguments={"argument_type": "internal_task", "unexpected_key": "x"})
    with pytest.raises(ValidationError, match="target_quantity"):
        AgentProposedAction(**common, arguments={"argument_type": "internal_task", "target_quantity": 600})


def test_action_type_and_argument_type_must_match() -> None:
    with pytest.raises(ValidationError, match="requires arguments.argument_type=internal_task"):
        AgentProposedAction(
            action_type="create_internal_task_draft",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={"argument_type": "evidence_reference"},
            reason="Draft a task for operator review.",
            evidence_ids=("issue-1",),
            expected_outcome="Internal draft only.",
            risk_level="low",
            requires_approval=False,
        )


def test_unknown_model_action_type_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unexpected_tool_call"):
        AgentProposedAction(
            action_type="unexpected_tool_call",
            resource_type="operational_issue",
            resource_id="issue-1",
            arguments={"argument_type": "none"},
            reason="Attempt an unsupported action.",
            evidence_ids=("issue-1",),
            expected_outcome="Unsupported.",
            risk_level="high",
            requires_approval=True,
        )


def test_model_facing_strings_and_collections_have_hard_limits() -> None:
    with pytest.raises(ValidationError, match="at most 200 characters"):
        InternalTaskDraft(
            title="x" * 201,
            objective="Review the issue.",
            resource_type="operational_issue",
            resource_id="issue-1",
            evidence_ids=("issue-1",),
            priority="medium",
            expected_outcome="A reviewed internal task.",
        )

    with pytest.raises(ValidationError, match="at most 20 items"):
        OperationsBriefAnalysisDraft(
            executive_summary="Bounded summary.",
            evidence=tuple(f"evidence-{index}" for index in range(21)),
            hypotheses=(),
            missing_data=(),
            proposed_actions=(),
            prohibited_actions=(),
            confidence=Decimal("0.50"),
        )


def test_listing_change_rejects_unsupported_field_and_serialized_json_escape() -> None:
    with pytest.raises(ValidationError, match="sale_price"):
        ListingFieldChange(
            field="sale_price",
            operation="replace",
            proposed_text="19.99",
            reason="Attempt to move a deterministic value into model output.",
            evidence_ids=("issue-1",),
        )
    with pytest.raises(ValidationError, match="serialized JSON"):
        ListingFieldChange(
            field="title",
            operation="replace",
            proposed_text='{"target_quantity": 600}',
            reason="Attempt to smuggle an untyped payload.",
            evidence_ids=("issue-1",),
        )


def test_typed_task_change_and_reconciliation_records_reject_malformed_shapes() -> None:
    with pytest.raises(ValidationError, match="objective"):
        InternalTaskDraft.model_validate(
            {
                "title": "Review stockout",
                "resource_type": "operational_issue",
                "resource_id": "issue-1",
                "evidence_ids": ["issue-1"],
                "priority": "high",
                "expected_outcome": "Operator review completed.",
            }
        )
    with pytest.raises(ValidationError, match="cluster_id"):
        DifferenceCluster.model_validate(
            {
                "difference_type": "inventory",
                "resource_ids": ["sku-1"],
                "evidence_ids": ["issue-1"],
                "summary": "Inventory signals disagree.",
                "hypothesis_status": "hypothesis",
            }
        )
    with pytest.raises(ValidationError, match="check_type"):
        ReadOnlyCheck.model_validate(
            {
                "resource_type": "inventory_snapshot",
                "resource_id": "sku-1",
                "evidence_ids": ["issue-1"],
                "reason": "Inspect the source records.",
            }
        )


def test_core_model_output_schema_contains_no_open_maps_and_has_limits() -> None:
    schemas = (
        OperationsBriefAnalysisDraft,
        CustomerCaseAnalysisDraft,
        ListingOptimizationAnalysisDraft,
        ReconciliationInvestigationDraft,
    )

    for schema in schemas:
        json_schema = schema.model_json_schema()
        serialized = str(json_schema)
        assert "dict[str, Any]" not in serialized
        assert "additionalProperties': True" not in serialized
        assert "maxLength" in serialized
        assert "maxItems" in serialized
