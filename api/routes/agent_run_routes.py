from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from agents.quota import AgentQuotaBackendUnavailable, AgentQuotaExceeded
from agents.workflow_definitions import WORKFLOW_DEFINITIONS
from api.security import require_api_key


router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"], dependencies=[Depends(require_api_key)])


def _quota_http_exception(exc: AgentQuotaExceeded | AgentQuotaBackendUnavailable) -> HTTPException:
    if isinstance(exc, AgentQuotaExceeded):
        detail = {
            "error": "agent_quota_exceeded",
            "message": str(exc),
            "scope": exc.scope,
            "retry_after_seconds": exc.retry_after_seconds,
        }
        status_code = 429
    else:
        detail = {
            "error": "agent_quota_unavailable",
            "message": str(exc),
            "retry_after_seconds": exc.retry_after_seconds,
        }
        status_code = 503
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


class ReconciliationInvestigationRequest(BaseModel):
    reconciliation_run_id: str = Field(min_length=1)
    locale: str = Field(default="en-US", min_length=2, max_length=20)


class ListingOptimizationAnalysisRequest(BaseModel):
    snapshot_id: str = Field(min_length=1)
    locale: str = Field(default="en-US", min_length=2, max_length=20)


class CustomerCaseAnalysisRequest(BaseModel):
    case_id: str = Field(min_length=1)
    locale: str = Field(default="en-US", min_length=2, max_length=20)


class OperationsBriefAnalysisRequest(BaseModel):
    brief_id: str | None = None
    locale: str = Field(default="en-US", min_length=2, max_length=20)


class OperationsRecommendationRequest(BaseModel):
    max_issues: int = Field(default=20, ge=1, le=100)


@router.post("/reconciliation-investigation")
async def investigate_reconciliation(payload: ReconciliationInvestigationRequest, request: Request, response: Response) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail={"error": "idempotency_key_required", "message": "Idempotency-Key header is required."})
    try:
        result = await request.app.state.reconciliation_agent_service.investigate(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", idempotency_key),
            reconciliation_run_id=payload.reconciliation_run_id, locale=payload.locale, idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Reconciliation run not found."}) from exc
    except (AgentQuotaExceeded, AgentQuotaBackendUnavailable) as exc:
        raise _quota_http_exception(exc) from exc
    response.status_code = 202
    return result


@router.post("/listing-optimization-analysis")
async def analyze_listing_optimization(payload: ListingOptimizationAnalysisRequest, request: Request, response: Response) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail={"error": "idempotency_key_required", "message": "Idempotency-Key header is required."})
    try:
        result = await request.app.state.listing_optimization_agent_service.analyze(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", idempotency_key),
            snapshot_id=payload.snapshot_id, locale=payload.locale, idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Listing quality snapshot not found."}) from exc
    except (AgentQuotaExceeded, AgentQuotaBackendUnavailable) as exc:
        raise _quota_http_exception(exc) from exc
    response.status_code = 202
    return result


@router.post("/customer-case-analysis")
async def analyze_customer_case(payload: CustomerCaseAnalysisRequest, request: Request, response: Response) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail={"error": "idempotency_key_required", "message": "Idempotency-Key header is required."})
    try:
        result = await request.app.state.customer_case_agent_service.analyze(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", idempotency_key),
            case_id=payload.case_id, locale=payload.locale, idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Customer case not found."}) from exc
    except (AgentQuotaExceeded, AgentQuotaBackendUnavailable) as exc:
        raise _quota_http_exception(exc) from exc
    response.status_code = 202
    return result


@router.post("/operations-brief-analysis")
async def analyze_operations_brief(payload: OperationsBriefAnalysisRequest, request: Request, response: Response) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail={"error": "idempotency_key_required", "message": "Idempotency-Key header is required."})
    try:
        result = await request.app.state.operations_brief_agent_service.analyze(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", idempotency_key),
            brief_id=payload.brief_id,
            locale=payload.locale,
            idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except (AgentQuotaExceeded, AgentQuotaBackendUnavailable) as exc:
        raise _quota_http_exception(exc) from exc
    response.status_code = 202
    return result


@router.post("/operations-recommendations")
async def generate_operations_recommendations(payload: OperationsRecommendationRequest, request: Request, response: Response) -> dict:
    principal = request.state.principal
    if "write" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "write scope is required."})
    result = await request.app.state.operations_agent_service.run_recommendations(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        correlation_id=getattr(request.state, "request_id", "agent-run"),
        max_issues=payload.max_issues,
    )
    response.status_code = 202
    return result


@router.get("/definitions")
async def list_workflow_definitions(request: Request) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    items = [definition.to_dict() for definition in WORKFLOW_DEFINITIONS.values()]
    return {"total": len(items), "items": items}


@router.get("")
async def list_agent_runs(request: Request, status: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    items = request.app.state.agent_run_service.list_runs(tenant_id=principal.tenant_id, status=status, limit=limit)
    return {"total": len(items), "items": items}


@router.get("/{run_id}")
async def get_agent_run(run_id: str, request: Request) -> dict:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    run = request.app.state.agent_run_service.get_run(tenant_id=principal.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Agent run not found."})
    return run


@router.post("/{run_id}/resume")
async def resume_agent_run(run_id: str, request: Request) -> dict:
    principal = request.state.principal
    if "write" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "write scope is required."})
    try:
        return await request.app.state.agent_run_service.resume(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            actor_id=principal.user_id,
            correlation_id=getattr(request.state, "request_id", "agent-resume"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Agent run not found."}) from exc
