from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from api.security import require_api_key


router = APIRouter(prefix="/api/customer-cases", tags=["customer-cases"], dependencies=[Depends(require_api_key)])
mock_router = APIRouter(prefix="/api/mock-customer-service", tags=["mock-customer-service"], dependencies=[Depends(require_api_key)])


class TriggerScenarioRequest(BaseModel):
    external_order_id: str = Field(min_length=1, max_length=100)


class TransitionRequest(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=2000)


class SendReplyRequest(BaseModel):
    reply: str = Field(min_length=1, max_length=10000)
    note: str = Field(default="", max_length=2000)


class ResolveCaseRequest(BaseModel):
    resolution_code: str = Field(min_length=1, max_length=100)
    root_cause_code: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=2000)


@mock_router.get("/scenarios")
async def list_mock_customer_scenarios(request: Request) -> dict[str, Any]:
    _require_mock(request)
    _require_read(request)
    return {
        "items": request.app.state.mock_buyer_messaging.list_scenarios(),
        "data_source": "mock_buyer",
        "verified_amazon_data": False,
    }


@mock_router.post("/scenarios/{scenario_id}/trigger", status_code=status.HTTP_201_CREATED)
async def trigger_mock_customer_scenario(scenario_id: str, payload: TriggerScenarioRequest, request: Request) -> dict[str, Any]:
    _require_mock(request)
    _require_operator(request)
    try:
        message = request.app.state.mock_buyer_messaging.create_message(
            scenario_id=scenario_id, external_order_id=payload.external_order_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "scenario_not_found", "message": str(exc)}) from exc
    case = request.app.state.customer_service_workflow.intake(
        tenant_id=request.state.principal.tenant_id,
        actor_id=request.state.principal.user_id,
        message=message,
    )
    return _case_payload(case, request.app.state.customer_case_repository.timeline(
        tenant_id=request.state.principal.tenant_id, case_id=case.id,
    ))


@router.get("")
async def list_customer_cases(
    request: Request,
    status: str | None = None,
    intent: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _require_read(request)
    total, items = request.app.state.customer_case_repository.list_page(
        tenant_id=request.state.principal.tenant_id,
        status=status,
        intent=intent,
        page=page,
        page_size=page_size,
    )
    pages = (total + page_size - 1) // page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": page < pages,
        "items": [_case_payload(item) for item in items],
    }


@router.get("/{case_id}")
async def get_customer_case(case_id: str, request: Request) -> dict[str, Any]:
    _require_read(request)
    case = _case(request, case_id)
    timeline = request.app.state.customer_case_repository.timeline(
        tenant_id=request.state.principal.tenant_id, case_id=case.id,
    )
    operations_context = request.app.state.customer_case_operations_bridge.case_context(
        tenant_id=request.state.principal.tenant_id, case_id=case.id,
    )
    return _case_payload(case, timeline, operations_context)


@router.patch("/{case_id}")
async def transition_customer_case(case_id: str, payload: TransitionRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    if payload.status not in {"acknowledged", "in_progress", "dismissed", "reopened"}:
        raise HTTPException(status_code=403, detail={"error": "managed_transition", "message": "This customer case transition is managed by approval, send, or resolution workflow."})
    try:
        case = request.app.state.customer_case_repository.transition(
            tenant_id=request.state.principal.tenant_id, case_id=case_id,
            target_status=payload.status, actor_id=request.state.principal.user_id, note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_transition", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="customer_case_transitioned", status="success",
        details={"tenant_id": request.state.principal.tenant_id, "customer_case_id": case.id, "target_status": case.status, "note": payload.note},
        actor=request.state.principal.user_id, risk_level="low",
    )
    return _case_payload(case)


@router.post("/{case_id}/send")
async def send_customer_reply(case_id: str, payload: SendReplyRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    try:
        existing = _case(request, case_id)
        request.app.state.customer_service_workflow.validate_reply_for_send(case=existing, reply=payload.reply)
        case = request.app.state.customer_case_repository.mark_sent(
            tenant_id=request.state.principal.tenant_id, case_id=case_id,
            reply=payload.reply, actor_id=request.state.principal.user_id, note=payload.note,
        )
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "reply_not_sendable", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="customer_reply_sent_mock", status="success",
        details={"tenant_id": request.state.principal.tenant_id, "customer_case_id": case.id, "source": "mock_buyer", "verified_amazon_data": False},
        actor=request.state.principal.user_id, risk_level=case.risk_level,
    )
    return _case_payload(case)


@router.post("/{case_id}/resolve")
async def resolve_customer_case(case_id: str, payload: ResolveCaseRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    try:
        case = request.app.state.customer_case_repository.resolve(
            tenant_id=request.state.principal.tenant_id, case_id=case_id,
            resolution_code=payload.resolution_code, root_cause_code=payload.root_cause_code,
            actor_id=request.state.principal.user_id, note=payload.note,
        )
        try:
            operations_context = request.app.state.customer_case_operations_bridge.process_case(
                tenant_id=request.state.principal.tenant_id, case_id=case.id,
                actor_id=request.state.principal.user_id,
            )
        except Exception as bridge_error:
            operations_context = None
            request.app.state.audit_logger.log_operation(
                action="customer_case_operations_bridge_deferred", status="failed",
                details={
                    "tenant_id": request.state.principal.tenant_id,
                    "customer_case_id": case.id,
                    "error_code": type(bridge_error).__name__,
                    "outbox_recovery_pending": True,
                }, actor=request.state.principal.user_id, risk_level="medium",
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "case_not_resolvable", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="customer_case_resolved", status="success",
        details={"tenant_id": request.state.principal.tenant_id, "customer_case_id": case.id, "resolution_code": case.resolution_code, "root_cause_code": case.root_cause_code},
        actor=request.state.principal.user_id, risk_level="low",
    )
    return _case_payload(case, operations_context=operations_context)


def _case(request: Request, case_id: str):
    case = request.app.state.customer_case_repository.get(
        tenant_id=request.state.principal.tenant_id, case_id=case_id,
    )
    if case is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Customer case not found."})
    return case


def _case_payload(case, timeline: list[dict[str, Any]] | None = None, operations_context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _json_safe(asdict(case))
    if timeline is not None:
        payload["timeline"] = timeline
    payload["operations_context"] = operations_context
    return payload


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _require_mock(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.mock_drill_enabled:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Mock customer-service drill is disabled."})
    if settings.amazon_writes_enabled or not settings.amazon_pilot_read_only:
        raise HTTPException(status_code=409, detail={"error": "unsafe_mock_configuration", "message": "Mock customer-service drill requires Amazon writes disabled and pilot read-only enabled."})


def _require_read(request: Request) -> None:
    if "read" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Read permission is required."})


def _require_operator(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "operator"} or "write" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Operator role is required."})
