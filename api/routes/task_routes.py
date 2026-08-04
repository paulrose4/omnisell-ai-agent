from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.security import require_api_key
from api.task_queue import TaskStatus


class TaskCloseRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_api_key)])


def _tenant_id(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return getattr(principal, "tenant_id", None) or getattr(getattr(request.state, "caller_context", None), "tenant_id", "default")


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """Return a task only when it belongs to the authenticated tenant."""
    try:
        record = request.app.state.task_queue.get(task_id, tenant_id=_tenant_id(request))
    except TypeError:
        record = request.app.state.task_queue.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Task not found."})
    return record.to_dict()


@router.get("")
async def list_tasks(request: Request, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List recent tasks scoped to the authenticated tenant."""
    try:
        records = request.app.state.task_queue.list_tasks(tenant_id=_tenant_id(request), limit=limit, status=status)
    except TypeError:
        records = request.app.state.task_queue.list_tasks(limit=limit, status=status)
    return {"total": len(records), "items": [record.to_dict() for record in records]}


@router.post("/{task_id}/replay")
async def replay_task(task_id: str, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    if principal.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Admin role is required to replay tasks."})
    try:
        replay = request.app.state.task_repository.replay(tenant_id=principal.tenant_id, task_id=task_id, actor_id=principal.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "task_not_replayable", "message": str(exc)}) from exc
    if replay.action == "amazon.sync":
        from application.celery_app import amazon_sync_task
        amazon_sync_task.delay(replay.id, principal.tenant_id, replay.payload["connection_id"], replay.payload["resources"])
    elif replay.action == "amazon.reconciliation":
        from application.celery_app import amazon_reconciliation_task
        amazon_reconciliation_task.delay(replay.id, principal.tenant_id, replay.payload["connection_id"])
    else:
        from application.celery_app import execute_action_task
        execute_action_task.apply_async(args=[replay.id, principal.tenant_id, replay.action, replay.payload], queue=replay.queue_name, task_id=replay.id)
    request.app.state.audit_logger.log_operation(
        action="replay_dead_letter_task", status="success",
        details={"tenant_id": principal.tenant_id, "source_task_id": task_id, "replay_task_id": replay.id},
        actor=principal.user_id, risk_level="high",
    )
    return {"task_id": replay.id, "status": replay.status, "replayed_from_task_id": task_id}


@router.post("/{task_id}/close")
async def close_task(task_id: str, payload: TaskCloseRequest, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    if principal.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Admin role is required to close failed tasks."})
    try:
        row = request.app.state.task_repository.close(
            tenant_id=principal.tenant_id, task_id=task_id, actor_id=principal.user_id, reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "task_not_closable", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="close_dead_letter_task", status="success",
        details={"tenant_id": principal.tenant_id, "task_id": task_id, "reason": payload.reason},
        actor=principal.user_id, risk_level="medium",
    )
    return {"task_id": row.id, "status": row.status, "closed_by": row.closed_by, "close_reason": row.close_reason}
