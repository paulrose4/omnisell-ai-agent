from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agents import base as agent_base
from api.security import require_api_key


router = APIRouter(prefix="/api/admin", tags=["admin"])
protected_router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_api_key)])


class ApprovalDecisionRequest(BaseModel):
    approver: str
    reason: str = ""
    second_verifier: str | None = None


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return gateway health and initialized dependency status."""
    return {
        "status": "ok",
        "service": "omnisell-api",
        "team_ready": hasattr(request.app.state, "team"),
        "oauth_client_ready": hasattr(request.app.state, "oauth_client"),
        "task_queue_ready": hasattr(request.app.state, "task_queue"),
        "rag_service_ready": hasattr(request.app.state, "rag_service"),
        "pending_approvals": len(request.app.state.hitl_manager.get_pending()) if hasattr(request.app.state, "hitl_manager") else 0,
        "price_recommendations": len(getattr(request.app.state, "price_recommendations", [])),
        "queue_lengths": request.app.state.task_queue.queue_lengths() if hasattr(request.app.state, "task_queue") else {},
    }


@protected_router.get("/approvals")
async def list_approvals(request: Request) -> dict[str, Any]:
    """List pending HITL approval requests."""
    pending = [item.to_dict() for item in request.app.state.hitl_manager.get_pending()]
    return {"total": len(pending), "items": pending}


@protected_router.post("/approvals/{request_id}/approve")
async def approve_request(request_id: str, payload: ApprovalDecisionRequest, request: Request) -> dict[str, Any]:
    """Approve a pending HITL request."""
    try:
        approval = request.app.state.hitl_manager.approve(
            request_id=request_id,
            approver=payload.approver,
            reason=payload.reason,
            second_verifier=payload.second_verifier,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "approval_failed", "message": str(exc)}) from exc
    return approval.to_dict()


@protected_router.post("/approvals/{request_id}/reject")
async def reject_request(request_id: str, payload: ApprovalDecisionRequest, request: Request) -> dict[str, Any]:
    """Reject a pending HITL request."""
    try:
        approval = request.app.state.hitl_manager.reject(
            request_id=request_id,
            approver=payload.approver,
            reason=payload.reason,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "rejection_failed", "message": str(exc)}) from exc
    return approval.to_dict()


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
    )
    return {"total": len(items), "items": items}


@protected_router.get("/circuit-breakers")
async def list_circuit_breakers() -> dict[str, Any]:
    """Return circuit breaker states for registered tools."""
    items = [
        {
            "name": name,
            "state": breaker.state.value,
            "failure_count": breaker.failure_count,
            "half_open_call_count": breaker.half_open_call_count,
        }
        for name, breaker in agent_base._CIRCUIT_BREAKERS.items()
    ]
    return {"total": len(items), "items": items}


@protected_router.post("/circuit-breakers/{name}/reset")
async def reset_circuit_breaker(name: str) -> dict[str, Any]:
    """Reset a named circuit breaker."""
    breaker = agent_base._CIRCUIT_BREAKERS.get(name)
    if breaker is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"Circuit breaker not found: {name}"})
    breaker.reset()
    return {"name": name, "state": breaker.state.value, "failure_count": breaker.failure_count}


@protected_router.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    """Return JSON operational metrics for queues, requests, and tasks."""
    return await request.app.state.metrics.snapshot(queue_lengths=request.app.state.task_queue.queue_lengths())


@protected_router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def get_prometheus_metrics(request: Request) -> str:
    """Return a Prometheus-friendly text snapshot."""
    metrics = await request.app.state.metrics.snapshot(queue_lengths=request.app.state.task_queue.queue_lengths())
    lines = [
        f"omnisell_requests_total {metrics['requests_total']}",
        f"omnisell_requests_failed_total {metrics['requests_failed']}",
        f"omnisell_request_avg_latency_ms {metrics['request_avg_latency_ms']}",
        f"omnisell_tasks_total {metrics['tasks_total']}",
        f"omnisell_tasks_succeeded_total {metrics['tasks_succeeded']}",
        f"omnisell_tasks_failed_total {metrics['tasks_failed']}",
    ]
    for queue_name, size in metrics["queue_lengths"].items():
        lines.append(f'omnisell_queue_length{{queue="{queue_name}"}} {size}')
    for action, latency in metrics["task_avg_latency_ms_by_action"].items():
        lines.append(f'omnisell_task_avg_latency_ms{{action="{action}"}} {latency}')
    return "\n".join(lines) + "\n"
