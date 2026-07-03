from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.security import require_api_key
from api.task_queue import TaskStatus


router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_api_key)])


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """Return the current status and result of an asynchronous task."""
    record = request.app.state.task_queue.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"Task not found: {task_id}"})
    return record.to_dict()


@router.get("")
async def list_tasks(request: Request, status: TaskStatus | None = None, limit: int = 50) -> dict[str, Any]:
    """List recent asynchronous tasks."""
    records = request.app.state.task_queue.list_tasks(limit=limit, status=status)
    return {"total": len(records), "items": [record.to_dict() for record in records]}
