from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from api.security import require_api_key
from application.manual_execution_contract import (
    EXECUTION_CONTRACT_VERSION,
    validate_operational_approval_payload,
)
from safety.circuit_breaker import circuit_breaker_registry

router = APIRouter(prefix="/api/admin", tags=["admin"])
protected_router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_api_key)])


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return gateway health and initialized dependency status."""
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and settings.environment.lower() == "production":
        return {"status": "ok", "service": "omnisell-api"}
    advisor_registry = getattr(request.app.state, "advisor_registry", None)
    advisor_types = advisor_registry.workflow_types() if advisor_registry is not None else ()
    advisor_runtime_ready = bool(
        advisor_types
        and hasattr(request.app.state, "agent_execution_workflow")
        and hasattr(request.app.state, "model_gateway")
    )
    return {
        "status": "ok",
        "service": "omnisell-api",
        "advisor_runtime_ready": advisor_runtime_ready,
        "advisor_count": len(advisor_types),
        "oauth_client_ready": hasattr(request.app.state, "oauth_client"),
        "task_queue_ready": hasattr(request.app.state, "task_queue"),
        "rag_service_ready": hasattr(request.app.state, "rag_service"),
        "approval_store_ready": hasattr(request.app.state, "hitl_manager"),
        "price_recommendations": sum(
            len(items) for items in getattr(request.app.state, "price_recommendations", {}).values()
        ),
        "queue_lengths": request.app.state.task_queue.queue_lengths()
        if hasattr(request.app.state, "task_queue")
        else {},
    }


@protected_router.get("/approvals")
async def list_approvals(request: Request) -> dict[str, Any]:
    """List pending HITL approval requests."""
    tenant_id = request.state.principal.tenant_id
    pending = [item.to_dict() for item in request.app.state.hitl_manager.get_pending(tenant_id=tenant_id)]
    return {"total": len(pending), "items": pending}


@protected_router.get("/session-context")
async def session_context(request: Request) -> dict[str, Any]:
    principal = request.state.principal
    settings = request.app.state.settings
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "role": principal.role,
        "scopes": sorted(principal.scopes),
        "environment": settings.environment,
        "amazon_writes_enabled": settings.amazon_writes_enabled,
        "amazon_pilot_read_only": settings.amazon_pilot_read_only,
    }


@protected_router.post("/approvals/{request_id}/approve")
async def approve_request(request_id: str, payload: ApprovalDecisionRequest, request: Request) -> dict[str, Any]:
    """Approve a pending HITL request."""
    _require_approval_role(request)
    existing = request.app.state.hitl_manager.get_request(request_id, tenant_id=request.state.principal.tenant_id)
    if existing is None or existing.payload.get("tenant_id", "default") != request.state.principal.tenant_id:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Approval not found."})
    if existing.requester == request.state.principal.user_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "self_approval_forbidden", "message": "Requesters cannot approve their own actions."},
        )
    _require_current_approval_context(request, existing.payload)
    if existing.risk_level.value in {"high", "critical"} and not payload.reason.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "approval_reason_required",
                "message": "A review reason is required for high-risk approvals.",
            },
        )
    try:
        approval = request.app.state.hitl_manager.approve(
            request_id=request_id,
            approver=request.state.principal.user_id,
            reason=payload.reason,
            tenant_id=request.state.principal.tenant_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail={"error": "approval_failed", "message": "Approval could not be completed."}
        ) from exc
    result = approval.to_dict()
    if approval.status.value == "awaiting_second_approval":
        result.update(
            {
                "operational_issue": None,
                "recommended_action": None,
                "customer_case": None,
                "agent_run": None,
            }
        )
        return result
    result["operational_issue"] = _transition_approval_issue(
        request, approval.payload, approved=True, reason=payload.reason
    )
    result["recommended_action"] = _transition_approval_action(
        request, approval.payload, approved=True, reason=payload.reason
    )
    result["customer_case"] = _transition_approval_customer_case(
        request, approval.payload, approved=True, reason=payload.reason
    )
    run_id = approval.payload.get("agent_run_id")
    if run_id and hasattr(request.app.state, "operations_agent_service"):
        result["agent_run"] = await request.app.state.operations_agent_service.resume(
            tenant_id=request.state.principal.tenant_id,
            run_id=str(run_id),
            actor_id=request.state.principal.user_id,
            correlation_id=getattr(request.state, "request_id", "approval-resume"),
        )
    return result


@protected_router.post("/approvals/{request_id}/reject")
async def reject_request(request_id: str, payload: ApprovalDecisionRequest, request: Request) -> dict[str, Any]:
    """Reject a pending HITL request."""
    _require_approval_role(request)
    existing = request.app.state.hitl_manager.get_request(request_id, tenant_id=request.state.principal.tenant_id)
    if existing is None or existing.payload.get("tenant_id", "default") != request.state.principal.tenant_id:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Approval not found."})
    if existing.risk_level.value in {"high", "critical"} and not payload.reason.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "rejection_reason_required",
                "message": "A review reason is required for high-risk rejections.",
            },
        )
    try:
        approval = request.app.state.hitl_manager.reject(
            request_id=request_id,
            approver=request.state.principal.user_id,
            reason=payload.reason,
            tenant_id=request.state.principal.tenant_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail={"error": "rejection_failed", "message": "Rejection could not be completed."}
        ) from exc
    result = approval.to_dict()
    result["operational_issue"] = _transition_approval_issue(
        request, approval.payload, approved=False, reason=payload.reason
    )
    result["recommended_action"] = _transition_approval_action(
        request, approval.payload, approved=False, reason=payload.reason
    )
    result["customer_case"] = _transition_approval_customer_case(
        request, approval.payload, approved=False, reason=payload.reason
    )
    run_id = approval.payload.get("agent_run_id")
    if run_id and hasattr(request.app.state, "operations_agent_service"):
        result["agent_run"] = await request.app.state.operations_agent_service.resume(
            tenant_id=request.state.principal.tenant_id,
            run_id=str(run_id),
            actor_id=request.state.principal.user_id,
            correlation_id=getattr(request.state, "request_id", "rejection-resume"),
        )
    return result


@protected_router.get("/audit")
async def query_audit(
    request: Request,
    event_type: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
    action: str | None = None,
    limit: int | None = 100,
) -> dict[str, Any]:
    """Query audit JSONL records."""
    items = request.app.state.audit_logger.query(
        event_type=event_type,
        status=status,
        risk_level=risk_level,
        action=action,
        limit=limit,
        tenant_id=request.state.principal.tenant_id,
    )
    return {"total": len(items), "items": items}


@protected_router.get("/circuit-breakers")
async def list_circuit_breakers(request: Request) -> dict[str, Any]:
    """Return process-wide circuit breaker states to administrators."""
    _require_admin_role(request)
    items = [
        {
            "name": name,
            "state": breaker.state.value,
            "failure_count": breaker.failure_count,
            "half_open_call_count": breaker.half_open_call_count,
        }
        for name, breaker in circuit_breaker_registry().items()
    ]
    return {"total": len(items), "items": items}


@protected_router.post("/circuit-breakers/{name}/reset")
async def reset_circuit_breaker(name: str, request: Request) -> dict[str, Any]:
    """Reset a process-wide circuit breaker as an administrator."""
    _require_admin_role(request)
    breaker = circuit_breaker_registry().get(name)
    if breaker is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": f"Circuit breaker not found: {name}"}
        )
    breaker.reset()
    return {"name": name, "state": breaker.state.value, "failure_count": breaker.failure_count}


@protected_router.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    """Return process-wide operational metrics to administrators."""
    _require_admin_role(request)
    return await request.app.state.metrics.snapshot(queue_lengths=request.app.state.task_queue.queue_lengths())


@protected_router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def get_prometheus_metrics(request: Request) -> str:
    """Return a process-wide Prometheus snapshot to administrators."""
    _require_admin_role(request)
    metrics = await request.app.state.metrics.snapshot(queue_lengths=request.app.state.task_queue.queue_lengths())
    lines = [
        f"omnisell_requests_total {metrics['requests_total']}",
        f"omnisell_requests_failed_total {metrics['requests_failed']}",
        f"omnisell_request_avg_latency_ms {metrics['request_avg_latency_ms']}",
        f"omnisell_tasks_total {metrics['tasks_total']}",
        f"omnisell_tasks_succeeded_total {metrics['tasks_succeeded']}",
        f"omnisell_tasks_failed_total {metrics['tasks_failed']}",
        f"omnisell_agent_latency_p50_ms {metrics['agent_latency_p50_ms']}",
        f"omnisell_agent_latency_p95_ms {metrics['agent_latency_p95_ms']}",
        f"omnisell_agent_tokens_total {metrics['agent_total_tokens']}",
        f"omnisell_agent_estimated_cost_total {metrics['agent_estimated_cost']}",
        f"omnisell_agent_schema_repairs_total {metrics['agent_schema_repairs']}",
        f"omnisell_agent_first_pass_schema_successes_total {metrics['agent_first_pass_schema_successes']}",
        f"omnisell_agent_first_pass_schema_success_rate {metrics['agent_first_pass_schema_success_rate'] or 0}",
        f"omnisell_agent_model_initial_latency_p95_ms {metrics['agent_model_initial_latency_p95_ms']}",
        f"omnisell_agent_model_repair_latency_p95_ms {metrics['agent_model_repair_latency_p95_ms']}",
        f"omnisell_agent_evidence_validation_failures_total {metrics['agent_evidence_validation_failures']}",
        f"omnisell_agent_retries_total {metrics['agent_retries']}",
        f"omnisell_agent_failovers_total {metrics['agent_failovers']}",
    ]
    for status, count in metrics["agent_runs_by_status"].items():
        lines.append(f'omnisell_agent_runs_total{{status="{status}"}} {count}')
    for workflow_type, count in metrics["agent_runs_by_workflow"].items():
        lines.append(f'omnisell_agent_workflow_runs_total{{workflow_type="{workflow_type}"}} {count}')
    for category, count in metrics["agent_schema_failure_categories"].items():
        lines.append(f'omnisell_agent_schema_failures_total{{category="{category}"}} {count}')
    for queue_name, size in metrics["queue_lengths"].items():
        lines.append(f'omnisell_queue_length{{queue="{queue_name}"}} {size}')
    for action, latency in metrics["task_avg_latency_ms_by_action"].items():
        lines.append(f'omnisell_task_avg_latency_ms{{action="{action}"}} {latency}')
    return "\n".join(lines) + "\n"


def _transition_approval_issue(
    request: Request, approval_payload: dict[str, Any], *, approved: bool, reason: str
) -> dict[str, Any] | None:
    plan = approval_payload.get("plan") or {}
    arguments = plan.get("arguments") or {}
    issue_id = approval_payload.get("issue_id") or arguments.get("issue_id")
    if not issue_id:
        return None
    principal = request.state.principal
    issue = request.app.state.operational_issue_repository.get(tenant_id=principal.tenant_id, issue_id=str(issue_id))
    if issue is None:
        return None
    try:
        if approved:
            if issue.status in {"acknowledged", "in_progress"}:
                issue = request.app.state.operational_issue_repository.transition(
                    tenant_id=principal.tenant_id,
                    issue_id=issue.id,
                    target_status="awaiting_approval",
                    actor_id=principal.user_id,
                    note="HITL request submitted.",
                )
            if issue.status == "awaiting_approval":
                issue = request.app.state.operational_issue_repository.transition(
                    tenant_id=principal.tenant_id,
                    issue_id=issue.id,
                    target_status="approved_for_manual_execution",
                    actor_id=principal.user_id,
                    note=reason or "Approved for manual execution in Seller Central.",
                )
        elif issue.status == "awaiting_approval":
            issue = request.app.state.operational_issue_repository.transition(
                tenant_id=principal.tenant_id,
                issue_id=issue.id,
                target_status="in_progress",
                actor_id=principal.user_id,
                note=reason or "HITL request rejected for operator revision.",
            )
    except ValueError:
        return {"id": issue.id, "status": issue.status}
    return {"id": issue.id, "status": issue.status}


def _require_current_approval_context(request: Request, approval_payload: dict[str, Any]) -> None:
    issue_id = approval_payload.get("issue_id")
    if not issue_id or not hasattr(request.app.state, "operational_issue_repository"):
        return
    tenant_id = request.state.principal.tenant_id
    issue = request.app.state.operational_issue_repository.get(tenant_id=tenant_id, issue_id=str(issue_id))
    if issue is None or issue.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_approval_context",
                "message": "The source issue is no longer awaiting this approval.",
            },
        )
    expected_freshness = str(approval_payload.get("data_freshness_at") or "")
    if expected_freshness and issue.data_freshness_at.isoformat() != expected_freshness:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_approval_context",
                "message": "The source evidence changed after this approval was created.",
            },
        )
    action_id = approval_payload.get("recommended_action_id")
    if action_id and hasattr(request.app.state, "recommended_action_repository"):
        action = request.app.state.recommended_action_repository.get(tenant_id=tenant_id, action_id=str(action_id))
        if action is None or action.status != "proposed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "stale_approval_context",
                    "message": "The recommended action changed after this approval was created.",
                },
            )
        if approval_payload.get("execution_contract_version") == EXECUTION_CONTRACT_VERSION:
            connection_id = str(approval_payload.get("connection_id") or "")
            connection = request.app.state.tenant_repository.get_connection(
                tenant_id=tenant_id,
                connection_id=connection_id,
            )
            if connection is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "stale_approval_context",
                        "message": "The approved Amazon connection no longer exists.",
                    },
                )
            try:
                validate_operational_approval_payload(
                    payload=approval_payload,
                    issue=issue,
                    action=action,
                    connection=connection,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "stale_approval_context", "message": str(exc)},
                ) from exc


def _transition_approval_action(
    request: Request, approval_payload: dict[str, Any], *, approved: bool, reason: str
) -> dict[str, Any] | None:
    action_id = approval_payload.get("recommended_action_id")
    if not action_id or not hasattr(request.app.state, "recommended_action_repository"):
        return None
    principal = request.state.principal
    action = request.app.state.recommended_action_repository.get(
        tenant_id=principal.tenant_id,
        action_id=str(action_id),
    )
    if action is None:
        return None
    target_status = "accepted" if approved else "rejected"
    if action.status == "proposed":
        action = request.app.state.recommended_action_repository.transition(
            tenant_id=principal.tenant_id,
            action_id=action.id,
            target_status=target_status,
            reason=reason,
        )
    return {"id": action.id, "status": action.status}


def _transition_approval_customer_case(
    request: Request, approval_payload: dict[str, Any], *, approved: bool, reason: str
) -> dict[str, Any] | None:
    case_id = approval_payload.get("customer_case_id")
    if not case_id or not hasattr(request.app.state, "customer_case_repository"):
        return None
    principal = request.state.principal
    case = request.app.state.customer_case_repository.get(tenant_id=principal.tenant_id, case_id=str(case_id))
    if case is None:
        return None
    case = request.app.state.customer_case_repository.decide_approval(
        tenant_id=principal.tenant_id,
        case_id=case.id,
        approved=approved,
        actor_id=principal.user_id,
        reason=reason,
    )
    request.app.state.audit_logger.log_operation(
        action="customer_case_approval_decided",
        status="success",
        details={"tenant_id": principal.tenant_id, "customer_case_id": case.id, "approved": approved},
        actor=principal.user_id,
        risk_level=case.risk_level,
    )
    return {"id": case.id, "status": case.status}


def _require_approval_role(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "approver"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Approval role is required."})


def _require_admin_role(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin"} or "admin" not in principal.scopes:
        raise HTTPException(
            status_code=403, detail={"error": "forbidden", "message": "Administrator role is required."}
        )
