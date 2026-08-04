from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.amazon_factory import create_amazon_adapter
from api.security import require_api_key
from application.amazon_sync import AmazonReadSyncService
from infrastructure.database.session import tenant_context


router = APIRouter(prefix="/api/sync", tags=["amazon-sync"], dependencies=[Depends(require_api_key)])
data_quality_router = APIRouter(prefix="/api/data-quality", tags=["data-quality"], dependencies=[Depends(require_api_key)])


class SyncJobRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)
    resources: list[str] = Field(default_factory=lambda: ["orders", "inventory", "listings"])


@router.post("/jobs")
async def create_sync_job(payload: SyncJobRequest, background_tasks: BackgroundTasks, request: Request, response: Response) -> dict[str, Any]:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "operator"} or ("write" not in principal.scopes and "admin" not in principal.scopes):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Operator write access is required."})
    resources = tuple(dict.fromkeys(payload.resources))
    if not resources or any(item not in {"orders", "inventory", "listings"} for item in resources):
        raise HTTPException(status_code=422, detail={"error": "invalid_resources", "message": "Resources must be orders, inventory or listings."})
    connection = request.app.state.tenant_repository.get_connection(tenant_id=principal.tenant_id, connection_id=payload.connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Amazon connection not found."})
    task = request.app.state.task_repository.create(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, queue_name="sync", action="amazon.sync",
        payload={"connection_id": connection.id, "resources": list(resources)},
        idempotency_key=f"manual-sync:{connection.id}:{','.join(resources)}:{datetime.now(UTC).strftime('%Y%m%d%H%M')}",
        correlation_id=getattr(request.state, "request_id", str(uuid4())), max_attempts=1,
    )
    if request.app.state.settings.task_backend == "celery":
        from application.celery_app import amazon_sync_task
        amazon_sync_task.delay(task.id, principal.tenant_id, connection.id, list(resources))
    else:
        background_tasks.add_task(_run_sync_job, request.app, principal.tenant_id, task.id, connection.id, resources)
    response.status_code = 202
    return {"task_id": task.id, "status": task.status, "connection_id": connection.id, "resources": list(resources)}


@router.get("/status")
async def sync_status(request: Request, connection_id: str | None = Query(default=None)) -> dict[str, Any]:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    items = request.app.state.sync_cursor_repository.list_status(tenant_id=principal.tenant_id, connection_id=connection_id)
    return {"total": len(items), "items": [_cursor_dict(item) for item in items]}


@data_quality_router.get("/issues")
async def data_quality_issues(request: Request) -> dict[str, Any]:
    principal = request.state.principal
    cursors = request.app.state.sync_cursor_repository.list_status(tenant_id=principal.tenant_id)
    items = [
        {
            "id": f"sync:{item.id}", "issue_type": "amazon_sync_failure", "severity": "high" if item.failure_count >= 3 else "medium",
            "connection_id": item.channel_connection_id, "resource_type": item.resource_type,
            "status": item.status, "failure_count": item.failure_count,
            "error_code": item.error_code, "error_message": item.error_message,
            "last_failed_at": _iso(item.last_failed_at), "last_succeeded_at": _iso(item.last_succeeded_at),
        }
        for item in cursors if item.status == "failed"
    ]
    inbox_failures = request.app.state.event_repository.list_inbox_failures(tenant_id=principal.tenant_id)
    items.extend({
        "id": f"inbox:{event.id}", "issue_type": "amazon_notification_failure",
        "severity": "high" if event.status == "dead_letter" else "medium",
        "connection_id": (event.payload or {}).get("_connection_id"), "resource_type": "notification",
        "status": event.status, "failure_count": event.attempts,
        "error_code": event.error_code, "error_message": event.error_message,
        "last_failed_at": _iso(event.received_at), "last_succeeded_at": None,
    } for event in inbox_failures)
    return {"total": len(items), "items": items}


async def _run_sync_job(app, tenant_id: str, task_id: str, connection_id: str, resources: tuple[str, ...]) -> None:
    with tenant_context(tenant_id):
        app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="running")
        connection = app.state.tenant_repository.get_connection(tenant_id=tenant_id, connection_id=connection_id)
        if connection is None:
            app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter", error_code="ConnectionNotFound", error_message="Amazon connection no longer exists.")
            return
        adapter = create_amazon_adapter(
            settings=app.state.settings, connection=connection, secret_store=app.state.secret_store,
            tenant_repository=app.state.tenant_repository,
        )
        try:
            result = await AmazonReadSyncService(
                adapter=adapter, connection=connection, commerce_repository=app.state.commerce_sync_repository,
                cursor_repository=app.state.sync_cursor_repository,
            ).sync(resources)
            failed = [name for name, value in result.items() if value["status"] == "failed"]
            app.state.task_repository.update_status(
                tenant_id=tenant_id, task_id=task_id, status="failed" if failed else "succeeded", result=result,
                error_code="PartialSyncFailure" if failed else None,
                error_message=f"Failed resources: {', '.join(failed)}" if failed else None,
            )
            if not failed:
                for marketplace_id in connection.marketplace_ids:
                    refresh_task = app.state.task_repository.create(
                        tenant_id=tenant_id, actor_id="amazon-sync", queue_name="operations", action="operations.refresh",
                        payload={"connection_id": connection.id, "marketplace_id": marketplace_id, "seller_skus": None},
                        idempotency_key=f"operations-refresh:{connection.id}:{marketplace_id}:post-sync:{task_id}",
                        correlation_id=f"post-sync:{task_id}", max_attempts=5,
                    )
                    if refresh_task.status == "pending":
                        app.state.task_repository.update_status(tenant_id=tenant_id, task_id=refresh_task.id, status="running")
                        try:
                            refresh_result = app.state.operations_refresh_service.refresh(
                                tenant_id=tenant_id, connection_id=connection.id, marketplace_id=marketplace_id,
                            )
                            app.state.task_repository.update_status(tenant_id=tenant_id, task_id=refresh_task.id, status="succeeded", result=refresh_result)
                        except Exception as refresh_exc:
                            app.state.task_repository.update_status(
                                tenant_id=tenant_id, task_id=refresh_task.id, status="dead_letter",
                                error_code=type(refresh_exc).__name__, error_message=str(refresh_exc)[:1000],
                            )
        except Exception as exc:
            app.state.task_repository.update_status(tenant_id=tenant_id, task_id=task_id, status="dead_letter", error_code=type(exc).__name__, error_message=str(exc)[:1000])
        finally:
            await adapter.close()


def _cursor_dict(item) -> dict[str, Any]:
    return {
        "id": item.id, "connection_id": item.channel_connection_id, "resource_type": item.resource_type,
        "cursor": item.cursor, "status": item.status, "item_count": item.item_count,
        "failure_count": item.failure_count, "error_code": item.error_code, "error_message": item.error_message,
        "last_started_at": _iso(item.last_started_at), "last_succeeded_at": _iso(item.last_succeeded_at),
        "last_failed_at": _iso(item.last_failed_at), "updated_at": _iso(item.updated_at),
    }


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
