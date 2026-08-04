from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from api.security import require_api_key


router = APIRouter(prefix="/api/work-items", tags=["operational-work-items"], dependencies=[Depends(require_api_key)])


class BulkWorkItemRequest(BaseModel):
    work_item_ids: list[str] = Field(min_length=1, max_length=200)
    operation: Literal["assign", "set_due_at", "acknowledge", "start", "block", "unblock", "handoff"]
    assignee_id: str | None = Field(default=None, min_length=1, max_length=255)
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("due_at must include timezone")
        return value


@router.get("")
async def list_work_items(
    request: Request,
    view: Literal["all", "active", "today", "waiting", "verifying", "mine", "unassigned", "due_today", "awaiting_approval", "blocked", "verification_failed"] = "today",
    status: str | None = None,
    domain: str | None = None,
    team: str | None = None,
    assignee_id: str | None = None,
    timezone: str = Query(default="UTC", min_length=1, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _require_read(request)
    principal = request.state.principal
    now = datetime.now(UTC)
    statuses: tuple[str, ...] = ()
    verification_statuses: tuple[str, ...] = ()
    status_or_verification = False
    unassigned_only = False
    due_at_from: datetime | None = None
    due_at_before: datetime | None = None
    view_assignee_id: str | None = None
    if view == "today":
        statuses = ("open", "assigned", "in_progress")
    elif view == "waiting":
        statuses = ("awaiting_approval", "blocked")
    elif view == "verifying":
        statuses = ("verifying",)
        verification_statuses = ("failed", "inconclusive")
        status_or_verification = True
    elif view == "mine":
        view_assignee_id = principal.user_id
    elif view == "unassigned":
        unassigned_only = True
    elif view == "due_today":
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_timezone", "message": "Unknown IANA timezone."}) from exc
        today = now.astimezone(zone).date()
        due_at_from = datetime.combine(today, time.min, tzinfo=zone).astimezone(UTC)
        due_at_before = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    elif view == "awaiting_approval":
        statuses = ("awaiting_approval",)
    elif view == "blocked":
        statuses = ("blocked",)
    elif view == "verification_failed":
        verification_statuses = ("failed",)
    if assignee_id and view_assignee_id and assignee_id != view_assignee_id:
        return _page_payload(total=0, items=[], page=page, page_size=page_size, generated_at=now)
    total, items = request.app.state.work_item_repository.list_page(
        tenant_id=principal.tenant_id,
        page=page,
        page_size=page_size,
        status=status,
        statuses=statuses,
        domain=domain,
        team=team,
        assignee_id=assignee_id or view_assignee_id,
        active_only=view != "all" and status is None,
        unassigned_only=unassigned_only,
        verification_statuses=verification_statuses,
        status_or_verification=status_or_verification,
        due_at_from=due_at_from,
        due_at_before=due_at_before,
    )
    return _page_payload(total=total, items=items, page=page, page_size=page_size, generated_at=now)


@router.get("/workload")
async def workload(request: Request) -> dict[str, Any]:
    _require_read(request)
    return request.app.state.work_item_repository.workload(tenant_id=request.state.principal.tenant_id)


@router.get("/{work_item_id}/customer-case-context")
async def customer_case_context(work_item_id: str, request: Request) -> dict[str, Any]:
    _require_read(request)
    context = request.app.state.customer_case_operations_bridge.work_item_context(
        tenant_id=request.state.principal.tenant_id, work_item_id=work_item_id,
    )
    if context is None:
        return {"cluster": None, "case_ids": [], "related_case_count": 0}
    return context


@router.get("/handoff-packet")
async def handoff_packet(request: Request, assignee_id: str | None = None) -> dict[str, Any]:
    _require_read(request)
    principal = request.state.principal
    owner = assignee_id or principal.user_id
    items = request.app.state.work_item_repository.list(
        tenant_id=principal.tenant_id, assignee_id=owner, active_only=True,
    )
    return {
        "assignee_id": owner,
        "generated_at": datetime.now(UTC),
        "total": len(items),
        "items": [_json_safe(asdict(item)) for item in items],
    }


@router.get("/{work_item_id}")
async def get_work_item(work_item_id: str, request: Request) -> dict[str, Any]:
    _require_read(request)
    item = request.app.state.work_item_repository.get(
        tenant_id=request.state.principal.tenant_id,
        work_item_id=work_item_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Work item not found."})
    return _json_safe(asdict(item))


@router.post("/bulk")
async def bulk_update(payload: BulkWorkItemRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    if payload.operation in {"assign", "handoff"} and payload.assignee_id != principal.user_id and principal.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Only Owner or Admin may assign work to another operator."})
    try:
        items = request.app.state.work_item_repository.bulk_update(
            tenant_id=principal.tenant_id, work_item_ids=payload.work_item_ids,
            operation=payload.operation, actor_id=principal.user_id,
            assignee_id=payload.assignee_id, due_at=payload.due_at, note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_bulk_operation", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="work_items_bulk_updated", status="success",
        details={
            "tenant_id": principal.tenant_id, "operation": payload.operation,
            "work_item_count": len(items), "assignee_changed": payload.assignee_id is not None,
        }, actor=principal.user_id, risk_level="medium",
    )
    return {"total": len(items), "items": [_json_safe(asdict(item)) for item in items]}


@router.post("/escalate-due")
async def escalate_due(request: Request) -> dict[str, Any]:
    principal = request.state.principal
    if principal.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Owner or Admin role is required."})
    items = request.app.state.work_item_repository.escalate_due(tenant_id=principal.tenant_id)
    request.app.state.audit_logger.log_operation(
        action="work_items_sla_escalated", status="success",
        details={"tenant_id": principal.tenant_id, "work_item_count": len(items)},
        actor=principal.user_id, risk_level="medium",
    )
    return {"total": len(items), "items": [_json_safe(asdict(item)) for item in items]}


def _require_read(request: Request) -> None:
    if "read" not in request.state.principal.scopes and "admin" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})


def _require_operator(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "operator"} or ("write" not in principal.scopes and "admin" not in principal.scopes):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Operator role is required."})


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _page_payload(*, total: int, items: list[Any], page: int, page_size: int, generated_at: datetime) -> dict[str, Any]:
    pages = (total + page_size - 1) // page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": page < pages,
        "items": [_json_safe(asdict(item)) for item in items],
        "generated_at": generated_at,
    }
