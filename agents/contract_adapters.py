from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from agents.contracts import (
    CustomerCaseAnalysisEnvelope,
    CustomerCaseProposedAction,
    ListingOptimizationAnalysisEnvelope,
    ListingProposedAction,
    OperationsBriefAnalysisEnvelope,
    ReconciliationInvestigationEnvelope,
    ReconciliationProposedAction,
)


class LegacyAgentPayloadError(ValueError):
    pass


_ENVELOPE_BY_WORKFLOW = {
    "operations_brief_analysis": OperationsBriefAnalysisEnvelope,
    "customer_case_analysis": CustomerCaseAnalysisEnvelope,
    "listing_optimization_analysis": ListingOptimizationAnalysisEnvelope,
    "reconciliation_investigation": ReconciliationInvestigationEnvelope,
}

_ACTION_CLASS_BY_WORKFLOW = {
    "operations_brief_analysis": None,
    "customer_case_analysis": CustomerCaseProposedAction,
    "listing_optimization_analysis": ListingProposedAction,
    "reconciliation_investigation": ReconciliationProposedAction,
}


def adapt_persisted_analysis(*, workflow_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    envelope_schema = _ENVELOPE_BY_WORKFLOW.get(workflow_type)
    if envelope_schema is None:
        raise LegacyAgentPayloadError(f"Unsupported workflow_type for agent payload: {workflow_type}")
    if not isinstance(payload, dict) or not payload:
        raise LegacyAgentPayloadError("Persisted agent analysis payload must be a non-empty object")

    source_version = str(payload.get("schema_version") or "1.0")
    candidate = deepcopy(payload)
    if source_version == "1.0":
        candidate = _adapt_v1(workflow_type=workflow_type, payload=candidate)
    elif source_version != "2.0":
        raise LegacyAgentPayloadError(f"Unsupported agent schema_version: {source_version}")

    try:
        return envelope_schema.model_validate(candidate).model_dump(mode="json")
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(item) for item in first.get("loc", ())) or "payload"
        raise LegacyAgentPayloadError(f"Invalid persisted agent payload at {location}: {first['msg']}") from exc


def _adapt_v1(*, workflow_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload["schema_version"] = "2.0"
    payload["proposed_actions"] = [
        _adapt_action(workflow_type=workflow_type, action=item)
        for item in _require_list(payload.get("proposed_actions", []), "proposed_actions")
    ]
    payload["prohibited_actions"] = [
        _adapt_action(workflow_type=workflow_type, action=item)
        for item in _require_list(payload.get("prohibited_actions", []), "prohibited_actions")
    ]

    if workflow_type == "operations_brief_analysis":
        payload["internal_task_drafts"] = [
            _adapt_task(item, path="internal_task_drafts")
            for item in _require_list(payload.get("internal_task_drafts", []), "internal_task_drafts")
        ]
    elif workflow_type == "customer_case_analysis":
        payload["extracted_entities"] = _adapt_entities(
            payload.get("extracted_entities", []),
            evidence_ids=_require_list(payload.get("evidence", []), "evidence"),
        )
    elif workflow_type == "listing_optimization_analysis":
        payload["proposed_changes"] = _adapt_listing_changes(
            payload.get("proposed_changes", []),
            evidence_ids=_require_list(payload.get("evidence", []), "evidence"),
        )
    elif workflow_type == "reconciliation_investigation":
        payload["internal_investigation_tasks"] = [
            _adapt_task(item, path="internal_investigation_tasks")
            for item in _require_list(payload.get("internal_investigation_tasks", []), "internal_investigation_tasks")
        ]
    return payload


def _adapt_action(*, workflow_type: str, action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise LegacyAgentPayloadError("Legacy action must be an object")
    candidate = dict(action)
    action_type = str(candidate.get("action_type") or "")
    action_class = _ACTION_CLASS_BY_WORKFLOW[workflow_type]
    if action_class is None:
        from agents.contracts import AgentProposedAction

        action_class = AgentProposedAction
    expected_argument_type = action_class.ARGUMENT_TYPE_BY_ACTION.get(action_type)
    if expected_argument_type is None:
        raise LegacyAgentPayloadError(f"Unsupported legacy action_type: {action_type}")

    arguments = candidate.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise LegacyAgentPayloadError(f"Legacy action {action_type} arguments must be an object")
    if "argument_type" in arguments:
        candidate["arguments"] = arguments
        return candidate

    evidence_ids = list(candidate.get("evidence_ids") or [])
    resource_id = str(candidate.get("resource_id") or "")
    if expected_argument_type == "none":
        _reject_unknown(arguments, set(), path=f"{action_type}.arguments")
        candidate["arguments"] = {"argument_type": "none"}
    elif expected_argument_type == "internal_task":
        allowed = {
            "related_resource_ids",
            "related_issue_ids",
            "issue_ids",
            "related_evidence_ids",
            "evidence_ids",
            "task_title",
            "title",
        }
        _reject_unknown(arguments, allowed, path=f"{action_type}.arguments")
        candidate["arguments"] = {
            "argument_type": "internal_task",
            "related_resource_ids": list(
                arguments.get("related_resource_ids")
                or arguments.get("related_issue_ids")
                or arguments.get("issue_ids")
                or ([resource_id] if resource_id else [])
            ),
            "related_evidence_ids": list(
                arguments.get("related_evidence_ids") or arguments.get("evidence_ids") or evidence_ids
            ),
            "task_title": arguments.get("task_title") or arguments.get("title"),
        }
    elif expected_argument_type == "evidence_reference":
        allowed = {"related_resource_ids", "related_issue_ids", "issue_ids", "related_evidence_ids", "evidence_ids"}
        _reject_unknown(arguments, allowed, path=f"{action_type}.arguments")
        candidate["arguments"] = {
            "argument_type": "evidence_reference",
            "related_resource_ids": list(
                arguments.get("related_resource_ids")
                or arguments.get("related_issue_ids")
                or arguments.get("issue_ids")
                or ([resource_id] if resource_id else [])
            ),
            "related_evidence_ids": list(
                arguments.get("related_evidence_ids") or arguments.get("evidence_ids") or evidence_ids
            ),
        }
    elif expected_argument_type == "customer_reply_draft":
        allowed = {"rag_evidence_ids", "evidence_ids", "reply_mode"}
        _reject_unknown(arguments, allowed, path=f"{action_type}.arguments")
        candidate["arguments"] = {
            "argument_type": "customer_reply_draft",
            "reply_mode": "draft_only",
            "rag_evidence_ids": list(arguments.get("rag_evidence_ids") or arguments.get("evidence_ids") or []),
        }
    elif expected_argument_type == "listing_change_draft":
        allowed = {"fields", "field", "experiment_required"}
        _reject_unknown(arguments, allowed, path=f"{action_type}.arguments")
        fields = arguments.get("fields") or ([arguments["field"]] if arguments.get("field") else [])
        candidate["arguments"] = {
            "argument_type": "listing_change_draft",
            "fields": list(fields),
            "experiment_required": True,
        }
    elif expected_argument_type == "read_only_check":
        allowed = {
            "check_type",
            "related_resource_ids",
            "resource_ids",
            "related_evidence_ids",
            "evidence_ids",
        }
        _reject_unknown(arguments, allowed, path=f"{action_type}.arguments")
        candidate["arguments"] = {
            "argument_type": "read_only_check",
            "check_type": arguments.get("check_type"),
            "related_resource_ids": list(
                arguments.get("related_resource_ids")
                or arguments.get("resource_ids")
                or ([resource_id] if resource_id else [])
            ),
            "related_evidence_ids": list(
                arguments.get("related_evidence_ids") or arguments.get("evidence_ids") or evidence_ids
            ),
        }
    return candidate


def _adapt_task(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LegacyAgentPayloadError(f"{path} item must be an object")
    allowed = {
        "title",
        "name",
        "objective",
        "description",
        "reason",
        "resource_type",
        "resource_id",
        "evidence_ids",
        "priority",
        "expected_outcome",
    }
    _reject_unknown(value, allowed, path=path)
    return {
        "title": value.get("title") or value.get("name"),
        "objective": value.get("objective") or value.get("description") or value.get("reason"),
        "resource_type": value.get("resource_type"),
        "resource_id": value.get("resource_id"),
        "evidence_ids": list(value.get("evidence_ids") or []),
        "priority": value.get("priority") or "medium",
        "expected_outcome": value.get("expected_outcome"),
    }


def _adapt_entities(value: Any, *, evidence_ids: list[Any]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise LegacyAgentPayloadError("extracted_entities must be an object or array")
    allowed_types = {
        "order_id",
        "asin",
        "seller_sku",
        "shipment_id",
        "marketplace_id",
        "tracking_id",
        "customer_reference",
        "other",
    }
    items = []
    for entity_type, entity_value in value.items():
        normalized_type = entity_type if entity_type in allowed_types else "other"
        items.append({"entity_type": normalized_type, "value": str(entity_value), "evidence_ids": evidence_ids})
    return items


def _adapt_listing_changes(value: Any, *, evidence_ids: list[Any]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise LegacyAgentPayloadError("proposed_changes must be an object or array")
    return [
        {
            "field": field,
            "operation": "replace",
            "proposed_text": text,
            "reason": "Legacy v1 draft migrated for operator review.",
            "evidence_ids": evidence_ids,
        }
        for field, text in value.items()
    ]


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise LegacyAgentPayloadError(f"{path} must be an array")
    return list(value)


def _reject_unknown(value: dict[str, Any], allowed: set[str], *, path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise LegacyAgentPayloadError(f"Unsupported legacy field at {path}: {', '.join(unknown)}")
