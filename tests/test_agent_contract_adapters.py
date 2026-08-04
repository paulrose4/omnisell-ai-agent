from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.contract_adapters import LegacyAgentPayloadError, adapt_persisted_analysis

NOW = datetime(2026, 8, 5, 8, 0, tzinfo=UTC).isoformat()


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


def test_legacy_open_arguments_with_numeric_target_fail_closed() -> None:
    payload = legacy_operations_payload()
    payload["proposed_actions"][0]["arguments"]["target_quantity"] = 600

    with pytest.raises(LegacyAgentPayloadError, match="target_quantity"):
        adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=payload)


def test_unknown_legacy_workflow_is_not_reinterpreted() -> None:
    with pytest.raises(LegacyAgentPayloadError, match="Unsupported workflow_type"):
        adapt_persisted_analysis(workflow_type="unknown_workflow", payload=legacy_operations_payload())
