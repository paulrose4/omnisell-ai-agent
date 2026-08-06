from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.contract_adapters import LegacyAgentPayloadError, adapt_persisted_analysis

NOW = datetime(2026, 8, 5, 8, 0, tzinfo=UTC).isoformat()


def legacy_action(
    action_type: str,
    *,
    arguments: object,
    resource_type: str = "operational_issue",
    resource_id: str = "issue-1",
    prohibited: bool = False,
) -> dict:
    return {
        "action_type": action_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "arguments": arguments,
        "reason": "An operator should review the supplied evidence.",
        "evidence_ids": ["issue-1"],
        "expected_outcome": "A bounded operator-reviewed outcome.",
        "risk_level": "critical" if prohibited else "low",
        "requires_approval": prohibited,
        "automation_eligible": False,
        "prohibited": prohibited,
        "prohibited_reason": "Permanently blocked." if prohibited else None,
    }


def legacy_operations_payload() -> dict:
    return {
        "schema_version": "1.0",
        "agent_type": "operations_brief_analyst",
        "prompt_version": "operations-brief-v6",
        "tenant_id": "tenant-a",
        "workflow_run_id": "run-1",
        "generated_at": NOW,
        "executive_summary": "Review the stockout evidence.",
        "evidence": ["issue-1"],
        "hypotheses": [],
        "missing_data": [],
        "proposed_actions": [
            {
                "action_type": "create_internal_task_draft",
                "resource_type": "operational_issue",
                "resource_id": "issue-1",
                "arguments": {"related_issue_ids": ["issue-1"], "title": "Review stockout"},
                "reason": "An operator should review the deterministic recommendation.",
                "evidence_ids": ["issue-1"],
                "expected_outcome": "A reviewable task draft.",
                "risk_level": "low",
                "requires_approval": False,
                "automation_eligible": False,
                "prohibited": False,
                "prohibited_reason": None,
            }
        ],
        "prohibited_actions": [],
        "confidence": "0.80",
        "degraded_reason": None,
        "warnings": [],
        "grouped_issue_ids": [["issue-1"]],
        "daily_checklist": ["Review issue-1"],
        "internal_task_drafts": [
            {
                "title": "Review stockout",
                "description": "Review the supplied evidence and deterministic recommendation.",
                "resource_type": "operational_issue",
                "resource_id": "issue-1",
                "evidence_ids": ["issue-1"],
                "priority": "high",
                "expected_outcome": "Operator records a decision.",
            }
        ],
        "model_usage": None,
        "evidence_preparation": None,
    }


def legacy_customer_case_payload() -> dict:
    payload = legacy_operations_payload()
    for field in ("grouped_issue_ids", "daily_checklist", "internal_task_drafts"):
        payload.pop(field)
    payload.update(
        {
            "agent_type": "customer_case_advisor",
            "proposed_actions": [
                legacy_action(
                    "draft_customer_reply",
                    arguments={"evidence_ids": ["issue-1"]},
                    resource_type="customer_case",
                    resource_id="case-1",
                )
            ],
            "prohibited_actions": [legacy_action("refund", arguments={}, prohibited=True)],
            "intents": ["delivery_question"],
            "extracted_entities": {"order_id": "ORDER-1", "unknown_reference": "external-1"},
            "sentiment_recommendation": "neutral",
            "urgency_recommendation": "normal",
            "reply_draft": "A human-reviewed reply draft.",
            "escalation_recommended": False,
            "rag_evidence_needed": [],
            "voc_tags": ["delivery"],
        }
    )
    return payload


def legacy_listing_payload() -> dict:
    payload = legacy_operations_payload()
    for field in ("grouped_issue_ids", "daily_checklist", "internal_task_drafts"):
        payload.pop(field)
    payload.update(
        {
            "agent_type": "listing_optimization_advisor",
            "proposed_actions": [
                legacy_action(
                    "draft_listing_change",
                    arguments={"field": "title"},
                    resource_type="listing",
                    resource_id="listing-1",
                ),
                legacy_action(
                    "reference_evidence",
                    arguments={"related_issue_ids": ["listing-1"], "evidence_ids": ["issue-1"]},
                    resource_type="listing",
                    resource_id="listing-1",
                ),
            ],
            "prohibited_actions": [],
            "proposed_changes": {"title": "Evidence-grounded title draft"},
            "experiment_hypothesis": "A controlled experiment may improve discoverability.",
            "expected_metrics": ["click_through_rate"],
            "unsupported_claims": [],
        }
    )
    return payload


def legacy_reconciliation_payload() -> dict:
    payload = legacy_operations_payload()
    for field in ("grouped_issue_ids", "daily_checklist", "internal_task_drafts"):
        payload.pop(field)
    payload.update(
        {
            "agent_type": "reconciliation_investigator",
            "proposed_actions": [
                legacy_action(
                    "run_read_only_check",
                    arguments={
                        "check_type": "compare_event_timeline",
                        "resource_ids": ["run-1"],
                        "evidence_ids": ["issue-1"],
                    },
                    resource_type="reconciliation_run",
                    resource_id="run-1",
                )
            ],
            "prohibited_actions": [],
            "difference_clusters": [],
            "read_only_checks": [],
            "internal_investigation_tasks": [
                {
                    "name": "Review reconciliation gap",
                    "reason": "Compare the supplied immutable records.",
                    "resource_type": "reconciliation_run",
                    "resource_id": "run-1",
                    "evidence_ids": ["issue-1"],
                    "expected_outcome": "Operator records a deterministic finding.",
                }
            ],
        }
    )
    return payload


def test_v1_operations_payload_is_adapted_to_typed_v2_contract() -> None:
    adapted = adapt_persisted_analysis(
        workflow_type="operations_brief_analysis",
        payload=legacy_operations_payload(),
    )

    assert adapted["schema_version"] == "2.0"
    assert adapted["proposed_actions"][0]["arguments"] == {
        "argument_type": "internal_task",
        "related_resource_ids": ["issue-1"],
        "related_evidence_ids": ["issue-1"],
        "task_title": "Review stockout",
    }
    assert adapted["internal_task_drafts"][0]["objective"].startswith("Review the supplied")


def test_all_legacy_workflows_are_migrated_through_typed_action_arguments() -> None:
    customer = adapt_persisted_analysis(
        workflow_type="customer_case_analysis",
        payload=legacy_customer_case_payload(),
    )
    listing = adapt_persisted_analysis(
        workflow_type="listing_optimization_analysis",
        payload=legacy_listing_payload(),
    )
    reconciliation = adapt_persisted_analysis(
        workflow_type="reconciliation_investigation",
        payload=legacy_reconciliation_payload(),
    )

    assert customer["proposed_actions"][0]["arguments"] == {
        "argument_type": "customer_reply_draft",
        "reply_mode": "draft_only",
        "rag_evidence_ids": ["issue-1"],
    }
    assert {item["entity_type"] for item in customer["extracted_entities"]} == {"order_id", "other"}
    assert customer["prohibited_actions"][0]["arguments"] == {"argument_type": "none"}
    assert listing["proposed_actions"][0]["arguments"] == {
        "argument_type": "listing_change_draft",
        "fields": ["title"],
        "experiment_required": True,
    }
    assert listing["proposed_actions"][1]["arguments"]["argument_type"] == "evidence_reference"
    assert listing["proposed_changes"][0]["field"] == "title"
    assert reconciliation["proposed_actions"][0]["arguments"] == {
        "argument_type": "read_only_check",
        "check_type": "compare_event_timeline",
        "related_resource_ids": ["run-1"],
        "related_evidence_ids": ["issue-1"],
    }
    assert reconciliation["internal_investigation_tasks"][0]["priority"] == "medium"


def test_typed_v2_payload_is_validated_without_legacy_reinterpretation() -> None:
    adapted = adapt_persisted_analysis(
        workflow_type="operations_brief_analysis",
        payload=legacy_operations_payload(),
    )

    assert adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=adapted) == adapted


@pytest.mark.parametrize("payload", [{}, [], None])
def test_empty_or_non_object_legacy_payloads_are_rejected(payload: object) -> None:
    with pytest.raises(LegacyAgentPayloadError, match="non-empty object"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)  # type: ignore[arg-type]


def test_unknown_schema_version_and_invalid_typed_payload_fail_closed() -> None:
    payload = legacy_operations_payload()
    payload["schema_version"] = "3.0"
    with pytest.raises(LegacyAgentPayloadError, match="Unsupported agent schema_version"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload.pop("executive_summary")
    with pytest.raises(LegacyAgentPayloadError, match="executive_summary"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


def test_legacy_action_and_task_shapes_fail_closed_before_policy_use() -> None:
    payload = legacy_operations_payload()
    payload["proposed_actions"] = ["not-an-object"]
    with pytest.raises(LegacyAgentPayloadError, match="Legacy action must be an object"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["proposed_actions"][0]["action_type"] = "unsupported_action"
    with pytest.raises(LegacyAgentPayloadError, match="Unsupported legacy action_type"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["proposed_actions"][0]["arguments"] = ["not", "an", "object"]
    with pytest.raises(LegacyAgentPayloadError, match="arguments must be an object"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["internal_task_drafts"] = ["not-an-object"]
    with pytest.raises(LegacyAgentPayloadError, match="item must be an object"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["proposed_actions"] = "not-an-array"
    with pytest.raises(LegacyAgentPayloadError, match="proposed_actions must be an array"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


def test_existing_typed_arguments_and_collection_forms_are_preserved() -> None:
    payload = legacy_operations_payload()
    payload["proposed_actions"][0]["arguments"] = {
        "argument_type": "internal_task",
        "related_resource_ids": ["issue-1"],
        "related_evidence_ids": ["issue-1"],
        "task_title": "Review stockout",
    }
    payload["proposed_actions"] = tuple(payload["proposed_actions"])

    adapted = adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    assert adapted["proposed_actions"][0]["arguments"]["argument_type"] == "internal_task"


def test_entity_and_listing_change_legacy_shapes_reject_invalid_scalars() -> None:
    payload = legacy_customer_case_payload()
    payload["extracted_entities"] = "not-an-object"
    with pytest.raises(LegacyAgentPayloadError, match="extracted_entities"):
        adapt_persisted_analysis(workflow_type="customer_case_analysis", payload=payload)

    payload = legacy_listing_payload()
    payload["proposed_changes"] = "not-an-object"
    with pytest.raises(LegacyAgentPayloadError, match="proposed_changes"):
        adapt_persisted_analysis(workflow_type="listing_optimization_analysis", payload=payload)


def test_already_list_shaped_entities_and_listing_changes_are_preserved() -> None:
    customer = legacy_customer_case_payload()
    customer["extracted_entities"] = [{"entity_type": "order_id", "value": "ORDER-1", "evidence_ids": ["issue-1"]}]
    listing = legacy_listing_payload()
    listing["proposed_changes"] = [
        {
            "field": "title",
            "operation": "replace",
            "proposed_text": "Evidence-grounded title draft",
            "reason": "Legacy v1 draft migrated for operator review.",
            "evidence_ids": ["issue-1"],
        }
    ]

    assert (
        adapt_persisted_analysis(workflow_type="customer_case_analysis", payload=customer)["extracted_entities"][0][
            "entity_type"
        ]
        == "order_id"
    )
    assert (
        adapt_persisted_analysis(workflow_type="listing_optimization_analysis", payload=listing)["proposed_changes"][0][
            "field"
        ]
        == "title"
    )


def test_legacy_open_arguments_with_numeric_target_fail_closed() -> None:
    payload = legacy_operations_payload()
    payload["proposed_actions"][0]["arguments"]["target_quantity"] = 600

    with pytest.raises(LegacyAgentPayloadError, match="target_quantity"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


def test_unknown_legacy_workflow_is_not_reinterpreted() -> None:
    with pytest.raises(LegacyAgentPayloadError, match="Unsupported workflow_type"):
        adapt_persisted_analysis(workflow_type="unknown_workflow", payload=legacy_operations_payload())


def test_legacy_payload_depth_is_bounded_before_copying() -> None:
    payload = legacy_operations_payload()
    nested: object = "leaf"
    for _ in range(14):
        nested = {"child": nested}
    payload["unexpected_nested_payload"] = nested

    with pytest.raises(LegacyAgentPayloadError, match="maximum depth"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


def test_legacy_payload_collection_and_string_sizes_are_bounded() -> None:
    payload = legacy_operations_payload()
    payload["warnings"] = ["warning"] * 501

    with pytest.raises(LegacyAgentPayloadError, match="collection size"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["executive_summary"] = "x" * 20001

    with pytest.raises(LegacyAgentPayloadError, match="string length"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


def test_legacy_payload_keys_json_values_and_total_nodes_are_bounded() -> None:
    payload = legacy_operations_payload()
    payload[1] = "invalid-key"
    with pytest.raises(LegacyAgentPayloadError, match="keys are invalid"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["warnings"] = {"not-json"}
    with pytest.raises(LegacyAgentPayloadError, match="non-JSON-compatible"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["oversized_object"] = {f"key-{index}": index for index in range(501)}
    with pytest.raises(LegacyAgentPayloadError, match="collection size"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)

    payload = legacy_operations_payload()
    payload["node_bomb"] = [{f"key-{index}": index for index in range(10)} for _ in range(500)]
    with pytest.raises(LegacyAgentPayloadError, match="total nodes"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_legacy_payload_rejects_non_finite_json_numbers_before_copying(value: float) -> None:
    payload = legacy_operations_payload()
    payload["non_finite_number"] = value

    with pytest.raises(LegacyAgentPayloadError, match="finite JSON number"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)
