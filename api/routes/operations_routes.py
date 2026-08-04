from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.security import require_api_key
from application.operations import SourceSnapshotNotFound
from safety.policy_engine import canonical_action_hash


router = APIRouter(prefix="/api/operations", tags=["operations"], dependencies=[Depends(require_api_key)])


class IssueDetectionRequest(BaseModel):
    inventory_snapshot_ids: list[str] = Field(default_factory=list, max_length=500)
    profit_snapshot_ids: list[str] = Field(default_factory=list, max_length=500)
    complete_scan: bool = False
    detected_at: datetime | None = None


class IssueTransitionRequest(BaseModel):
    status: Literal["acknowledged", "in_progress", "awaiting_approval", "approved_for_manual_execution", "verifying", "resolved", "verification_failed", "dismissed", "reopened"]
    note: str = Field(default="", max_length=2000)


class OperationsRefreshRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_skus: list[str] | None = Field(default=None, max_length=500)


class ActionTransitionRequest(BaseModel):
    status: Literal["accepted", "rejected", "completed", "canceled"]
    reason: str = Field(default="", max_length=2000)


class IssueApprovalRequest(BaseModel):
    recommended_action_id: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)


@router.post("/refresh")
async def refresh_operations(payload: OperationsRefreshRequest, background_tasks: BackgroundTasks, request: Request, response: Response) -> dict:
    _require_operator(request)
    principal = request.state.principal
    connection = request.app.state.tenant_repository.get_connection(tenant_id=principal.tenant_id, connection_id=payload.connection_id)
    if connection is None or payload.marketplace_id not in connection.marketplace_ids:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Amazon connection or marketplace not found."})
    correlation_id = getattr(request.state, "request_id", str(uuid4()))
    sku_key = ",".join(sorted(payload.seller_skus or [])) or "all"
    task = request.app.state.task_repository.create(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, queue_name="operations", action="operations.refresh",
        payload={"connection_id": connection.id, "marketplace_id": payload.marketplace_id, "seller_skus": payload.seller_skus},
        idempotency_key=f"manual-operations-refresh:{connection.id}:{payload.marketplace_id}:{sku_key}:{datetime.now(UTC).strftime('%Y%m%d%H%M')}",
        correlation_id=correlation_id, max_attempts=5,
    )
    if task.status == "pending":
        if request.app.state.settings.task_backend == "celery":
            from application.celery_app import operations_refresh_task
            operations_refresh_task.delay(task.id, principal.tenant_id, connection.id, payload.marketplace_id, payload.seller_skus)
        else:
            background_tasks.add_task(_run_operations_refresh, request.app, principal.tenant_id, task.id, connection.id, payload.marketplace_id, payload.seller_skus)
    request.app.state.audit_logger.log_operation(
        action="operations_refresh_requested", status="success",
        details={"tenant_id": principal.tenant_id, "task_id": task.id, "connection_id": connection.id, "marketplace_id": payload.marketplace_id, "seller_skus": payload.seller_skus, "correlation_id": correlation_id},
        actor=principal.user_id, risk_level="low",
    )
    response.status_code = 202
    return {"task_id": task.id, "status": task.status, "correlation_id": correlation_id}


async def _run_operations_refresh(app, tenant_id: str, task_id: str, connection_id: str, marketplace_id: str, seller_skus: list[str] | None) -> None:
    app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="running")
    try:
        result = app.state.operations_refresh_service.refresh(
            tenant_id=tenant_id, connection_id=connection_id, marketplace_id=marketplace_id, seller_skus=seller_skus,
        )
        app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="succeeded", result=result)
    except Exception as exc:
        app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter", error_code=type(exc).__name__, error_message=str(exc)[:1000])


@router.post("/issues/detect")
async def detect_issues(payload: IssueDetectionRequest, request: Request) -> dict:
    _require_scope(request, "write")
    if not payload.inventory_snapshot_ids and not payload.profit_snapshot_ids:
        raise HTTPException(status_code=422, detail={"error": "missing_source_snapshots", "message": "At least one source snapshot is required."})
    principal = request.state.principal
    try:
        result = request.app.state.operational_issue_service.detect(
            tenant_id=principal.tenant_id,
            inventory_snapshot_ids=payload.inventory_snapshot_ids,
            profit_snapshot_ids=payload.profit_snapshot_ids,
            complete_scan=payload.complete_scan,
            detected_at=payload.detected_at,
        )
    except SourceSnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "source_snapshot_not_found", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="operational_issue_detection", status="success",
        details={
            "tenant_id": principal.tenant_id, "issue_count": len(result.issues), "action_count": len(result.actions),
            "inventory_snapshot_ids": payload.inventory_snapshot_ids, "profit_snapshot_ids": payload.profit_snapshot_ids,
            "complete_scan": payload.complete_scan, "resolved_issue_count": len(result.resolved_issue_ids),
        }, actor=principal.user_id, risk_level="low",
    )
    return {
        "issue_count": len(result.issues), "action_count": len(result.actions),
        "issues": [_serialize(item) for item in result.issues],
        "actions": [_serialize(item) for item in result.actions],
        "resolved_issue_ids": list(result.resolved_issue_ids),
    }


@router.get("/issues")
async def list_issues(
    request: Request,
    status: str | None = Query(default=None),
    view: Literal["active", "history", "all"] = Query(default="active"),
    severity: str | None = Query(default=None),
    issue_type: str | None = Query(default=None),
    marketplace_id: str | None = Query(default=None),
    seller_sku: str | None = Query(default=None),
) -> dict:
    _require_scope(request, "read")
    items = request.app.state.operational_issue_repository.list_issues(
        tenant_id=request.state.principal.tenant_id, status=status, severity=severity,
        issue_type=issue_type, marketplace_id=marketplace_id, seller_sku=seller_sku,
        active_only=status is None and view == "active",
    )
    if status is None and view == "history":
        items = [item for item in items if item.status in {"resolved", "dismissed"}]
    return {"items": [_serialize(item) for item in items], "total": len(items)}


@router.get("/issues/{issue_id}")
async def get_issue(issue_id: str, request: Request) -> dict:
    _require_scope(request, "read")
    issue = request.app.state.operational_issue_repository.get(
        tenant_id=request.state.principal.tenant_id, issue_id=issue_id,
    )
    if issue is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Operational issue not found."})
    return _serialize(issue)


@router.get("/issues/{issue_id}/detail")
async def get_issue_detail(issue_id: str, request: Request) -> dict:
    _require_scope(request, "read")
    tenant_id = request.state.principal.tenant_id
    issue = request.app.state.operational_issue_repository.get(tenant_id=tenant_id, issue_id=issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Operational issue not found."})
    actions = request.app.state.recommended_action_repository.list_actions(tenant_id=tenant_id, issue_id=issue.id)
    approval = _pending_issue_approval(request, issue_id=issue.id)
    return {
        "issue": _serialize(issue),
        "recommended_actions": [_serialize(item) for item in actions],
        "pending_approval": None if approval is None else approval.to_dict(),
        "amazon_write_performed": False,
        "seller_central_required": True,
    }


@router.post("/issues/{issue_id}/approval-requests")
async def submit_issue_approval(issue_id: str, payload: IssueApprovalRequest, request: Request, response: Response) -> dict:
    _require_operator(request)
    principal = request.state.principal
    issue = request.app.state.operational_issue_repository.get(tenant_id=principal.tenant_id, issue_id=issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Operational issue not found."})
    action = request.app.state.recommended_action_repository.get(
        tenant_id=principal.tenant_id, action_id=payload.recommended_action_id,
    )
    if action is None or action.issue_id != issue.id:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Recommended action not found for this issue."})
    if action.status != "proposed":
        raise HTTPException(status_code=409, detail={"error": "action_not_proposed", "message": "Only proposed actions can be submitted for approval."})
    if action.risk_level not in {"high", "critical"}:
        raise HTTPException(status_code=409, detail={"error": "approval_not_required", "message": "This action does not require HITL approval."})
    existing = _pending_issue_approval(request, issue_id=issue.id, recommended_action_id=action.id)
    if existing is not None:
        response.status_code = 200
        return {"created": False, "issue": _serialize(issue), "recommended_action": _serialize(action), "approval_request": existing.to_dict()}
    if issue.status not in {"in_progress", "awaiting_approval"}:
        raise HTTPException(status_code=409, detail={"error": "issue_not_ready", "message": "Issue must be in progress before approval can be requested."})

    before_snapshot = {
        "issue_id": issue.id,
        "seller_sku": issue.seller_sku,
        "marketplace_id": issue.marketplace_id,
        "status": issue.status,
        "severity": issue.severity,
        "confidence": str(issue.confidence),
        "data_freshness_at": issue.data_freshness_at.isoformat(),
        "evidence": _json_safe(list(issue.evidence)),
        "missing_data": list(issue.missing_data),
    }
    proposed_changes = _json_safe(action.parameters)
    decision_context = _approval_decision_context(issue=issue, action=action)
    plan = {
        "action": "manual_execute_recommended_action",
        "arguments": {
            "issue_id": issue.id,
            "recommended_action_id": action.id,
            "action_type": action.action_type,
            **proposed_changes,
        },
    }
    hash_arguments = {
        "issue_id": issue.id,
        "recommended_action_id": action.id,
        "before_snapshot": before_snapshot,
        "proposed_changes": proposed_changes,
        "evidence": _json_safe(list(action.evidence)),
        "expected_outcome": action.expected_outcome,
        "decision_context": decision_context,
    }
    action_hash = canonical_action_hash(
        action="manual_execute_recommended_action",
        tenant_id=principal.tenant_id,
        resource_id=action.id,
        arguments=hash_arguments,
    )
    approval = request.app.state.hitl_manager.submit_request(
        action="manual_execute_recommended_action",
        payload={
            "tenant_id": principal.tenant_id,
            "resource_id": action.id,
            "issue_id": issue.id,
            "recommended_action_id": action.id,
            "action_type": action.action_type,
            "plan": plan,
            "before_snapshot": before_snapshot,
            "proposed_changes": proposed_changes,
            "evidence": _json_safe(list(action.evidence)),
            "expected_outcome": action.expected_outcome,
            "decision_context": decision_context,
            "confidence": str(action.confidence),
            "data_freshness_at": issue.data_freshness_at.isoformat(),
            "note": payload.note,
            "action_hash": action_hash,
            "amazon_write_performed": False,
            "seller_central_required": True,
        },
        requester=principal.user_id,
        risk_level=action.risk_level,
        reason=payload.note,
    )
    if issue.status == "in_progress":
        issue = request.app.state.operational_issue_repository.transition(
            tenant_id=principal.tenant_id, issue_id=issue.id, target_status="awaiting_approval",
            actor_id=principal.user_id, note=payload.note,
        )
    request.app.state.audit_logger.log_operation(
        action="operational_issue_approval_requested", status="success",
        details={
            "tenant_id": principal.tenant_id, "issue_id": issue.id, "recommended_action_id": action.id,
            "approval_request_id": approval.request_id, "action_hash": action_hash,
            "correlation_id": getattr(request.state, "request_id", None),
        },
        actor=principal.user_id, risk_level=action.risk_level,
    )
    response.status_code = 201
    return {"created": True, "issue": _serialize(issue), "recommended_action": _serialize(action), "approval_request": approval.to_dict()}


def _approval_decision_context(*, issue, action) -> dict:
    evidence = [item for item in issue.evidence if isinstance(item, dict)]
    inventory = next((item for item in evidence if item.get("snapshot_type") == "inventory_health"), {})
    provenance = next((item for item in evidence if "verified_amazon_data" in item or "data_source" in item), {})
    return _json_safe({
        "inventory": {
            "sellable": inventory.get("available"),
            "reserved": inventory.get("reserved"),
            "inbound": inventory.get("inbound"),
            "in_transit": inventory.get("in_transit"),
            "unsellable": inventory.get("unsellable"),
            "inventory_position": inventory.get("inventory_position"),
            "days_of_supply": inventory.get("days_of_supply"),
            "total_lead_time_days": inventory.get("total_lead_time_days"),
            "weighted_daily_sales": inventory.get("weighted_daily_sales"),
            "forecast_confidence": inventory.get("forecast_confidence") or str(issue.confidence),
        },
        "recommendation": {
            "action_type": action.action_type,
            "recommended_reorder_quantity": action.parameters.get("recommended_reorder_quantity"),
            "recommended_shipping_mode": action.parameters.get("recommended_shipping_mode"),
            "reason": action.description,
            "expected_outcome": action.expected_outcome,
            "alternatives": ["defer_and_refresh_evidence", "manual_review_without_execution"],
        },
        "economics": {
            "currency": action.currency or issue.currency,
            "estimated_revenue_impact": action.expected_revenue_impact or issue.estimated_revenue_impact,
            "estimated_profit_impact": action.expected_profit_impact or issue.estimated_profit_impact,
            "estimated_cost_impact": action.expected_cost_impact or issue.estimated_cost_impact,
        },
        "provenance": {
            "source": provenance.get("data_source") or inventory.get("inventory_source") or "unknown",
            "verified_amazon_data": bool(provenance.get("verified_amazon_data", False)),
            "observed_at": provenance.get("observed_at") or inventory.get("inventory_collected_at") or issue.data_freshness_at,
            "freshness_at": issue.data_freshness_at,
        },
        "missing_data": list(issue.missing_data),
    })


@router.patch("/issues/{issue_id}")
async def transition_issue(issue_id: str, payload: IssueTransitionRequest, request: Request) -> dict:
    _require_operator(request)
    principal = request.state.principal
    if payload.status in {"approved_for_manual_execution", "verifying", "resolved", "verification_failed"}:
        raise HTTPException(status_code=403, detail={"error": "protected_transition", "message": "This status can only be changed by approval, execution or deterministic verification workflows."})
    if payload.status == "reopened" and principal.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Only owner or admin can reopen a resolved issue."})
    try:
        issue = request.app.state.operational_issue_repository.transition(
            tenant_id=principal.tenant_id, issue_id=issue_id, target_status=payload.status,
            actor_id=principal.user_id, note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Operational issue not found."}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_transition", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="operational_issue_transition", status="success",
        details={"tenant_id": principal.tenant_id, "issue_id": issue_id, "target_status": payload.status, "note": payload.note},
        actor=principal.user_id, risk_level="medium",
    )
    return _serialize(issue)


@router.get("/actions")
async def list_actions(
    request: Request,
    issue_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict:
    _require_scope(request, "read")
    items = request.app.state.recommended_action_repository.list_actions(
        tenant_id=request.state.principal.tenant_id, issue_id=issue_id, status=status,
    )
    return {"items": [_serialize(item) for item in items], "total": len(items)}


@router.patch("/actions/{action_id}")
async def transition_action(action_id: str, payload: ActionTransitionRequest, request: Request) -> dict:
    _require_operator(request)
    principal = request.state.principal
    current = request.app.state.recommended_action_repository.get(tenant_id=principal.tenant_id, action_id=action_id)
    if current is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Recommended action not found."})
    if current.risk_level in {"high", "critical"} and payload.status in {"accepted", "completed"}:
        raise HTTPException(status_code=403, detail={
            "error": "approval_required",
            "message": "High-risk actions can only advance through the approval and execution workflow.",
        })
    try:
        action = request.app.state.recommended_action_repository.transition(
            tenant_id=principal.tenant_id, action_id=action_id, target_status=payload.status, reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Recommended action not found."}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_transition", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="recommended_action_transition", status="success",
        details={"tenant_id": principal.tenant_id, "action_id": action_id, "target_status": payload.status},
        actor=principal.user_id, risk_level="medium",
    )
    return _serialize(action)


def _require_scope(request: Request, scope: str) -> None:
    principal = request.state.principal
    if scope not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": f"{scope} scope is required."})


def _require_operator(request: Request) -> None:
    _require_scope(request, "write")
    if request.state.principal.role not in {"owner", "admin", "operator"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Operator role is required."})


def _pending_issue_approval(request: Request, *, issue_id: str, recommended_action_id: str | None = None):
    for approval in request.app.state.hitl_manager.get_pending(tenant_id=request.state.principal.tenant_id):
        if approval.payload.get("issue_id") != issue_id:
            continue
        if recommended_action_id and approval.payload.get("recommended_action_id") != recommended_action_id:
            continue
        return approval
    return None


def _serialize(value) -> dict:
    return _json_safe(asdict(value))


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value
