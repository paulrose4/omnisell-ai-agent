from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.security import require_api_key


router = APIRouter(prefix="/api/operations", tags=["operations-briefing"], dependencies=[Depends(require_api_key)])


class BriefGenerationRequest(BaseModel):
    brief_date: date | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    scan_listings: bool = True


@router.post("/briefs/generate")
async def generate_brief(payload: BriefGenerationRequest, request: Request) -> dict:
    _require_scope(request, "write")
    principal = request.state.principal
    try:
        result = request.app.state.daily_brief_service.generate(
            tenant_id=principal.tenant_id, brief_date=payload.brief_date,
            timezone=payload.timezone, scan_listings=payload.scan_listings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_brief_request", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="daily_operations_brief_generated", status="success",
        details={
            "tenant_id": principal.tenant_id, "brief_id": result.brief.id,
            "brief_date": result.brief.brief_date.isoformat(), "timezone": result.brief.timezone,
            "open_issue_count": result.brief.total_open_issues, "scan_listings": payload.scan_listings,
        }, actor=principal.user_id, risk_level="low",
    )
    return {"brief": _serialize(result.brief), "sku_health": [_serialize(item) for item in result.sku_health]}


@router.get("/briefs/latest")
async def get_latest_brief(request: Request) -> dict:
    _require_scope(request, "read")
    brief = request.app.state.daily_brief_repository.get_latest(tenant_id=request.state.principal.tenant_id)
    if brief is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Daily operations brief not found."})
    return _serialize(brief)


@router.get("/dashboard")
async def get_dashboard(request: Request) -> dict:
    _require_scope(request, "read")
    principal = request.state.principal
    dashboard = request.app.state.daily_brief_service.dashboard(tenant_id=principal.tenant_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Generate a daily operations brief first."})
    work_items = request.app.state.work_item_repository.list(tenant_id=principal.tenant_id, active_only=True)
    active_issues = request.app.state.operational_issue_repository.list_issues(tenant_id=principal.tenant_id, active_only=True)
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(hours=6)
    mock_mode = any(
        item.seller_sku.startswith(("MOCK-", "GOLDEN-"))
        for item in dashboard.brief.sku_health
    )
    case_flow = request.app.state.customer_case_operations_bridge.dashboard_summary(
        tenant_id=principal.tenant_id,
    )
    return {
        "data_mode": "mock" if mock_mode else "connected",
        "data_disclaimer": "Mock data is synthetic and must not be treated as verified Amazon data." if mock_mode else None,
        "queue_summary": {
            "urgent": sum(1 for item in work_items if item.severity in {"critical", "high"}),
            "overdue": sum(1 for item in work_items if item.due_at < now),
            "awaiting_approval": sum(1 for item in work_items if item.status == "awaiting_approval"),
            "stale_data": sum(1 for item in active_issues if item.data_freshness_at < stale_cutoff or "stale" in str(item.degraded_reason or "")),
            "verification_failed": sum(1 for item in active_issues if item.status == "verification_failed"),
            "my_tasks": sum(1 for item in work_items if item.assignee_id == principal.user_id),
            "shift_owner": principal.user_id,
            "open_customer_cases": case_flow["open_customer_cases"],
            "actionable_case_clusters": case_flow["actionable_case_clusters"],
            "unlinked_actionable_case_clusters": case_flow["unlinked_actionable_case_clusters"],
            "case_flow_inconsistencies": case_flow["case_flow_inconsistencies"],
        },
        "brief": _serialize(dashboard.brief),
        "priorities": [
            {"issue": _serialize(item["issue"]), "action": _serialize(item["action"]) if item["action"] else None}
            for item in dashboard.priorities
        ],
        "sku_health": [_serialize(item) for item in dashboard.brief.sku_health],
    }


@router.get("/sku-health")
async def get_sku_health(request: Request) -> dict:
    _require_scope(request, "read")
    brief = request.app.state.daily_brief_repository.get_latest(tenant_id=request.state.principal.tenant_id)
    if brief is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Generate a daily operations brief first."})
    return {"items": [_serialize(item) for item in brief.sku_health], "total": len(brief.sku_health), "brief_id": brief.id}


def _require_scope(request: Request, scope: str) -> None:
    principal = request.state.principal
    if scope not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": f"{scope} scope is required."})


def _serialize(value) -> dict:
    return _json_safe(asdict(value))


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value
