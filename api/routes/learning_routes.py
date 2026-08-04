from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from api.security import require_api_key

router = APIRouter(prefix="/api/learning", tags=["operations-learning"], dependencies=[Depends(require_api_key)])


class IssueFeedbackRequest(BaseModel):
    is_correct: bool
    is_useful: bool
    reason_code: Literal[
        "actionable",
        "not_a_real_issue",
        "stale_or_missing_data",
        "threshold_not_applicable",
        "duplicate_issue",
        "planned_promotion_or_seasonality",
        "already_handled_externally",
        "impact_too_low",
        "recommendation_not_executable",
        "accepted_planned_risk",
    ]
    rule_version: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)


class MissedIssueRequest(BaseModel):
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str = Field(min_length=1, max_length=100)
    issue_type: str = Field(min_length=1, max_length=100)
    severity: Literal["low", "medium", "high", "critical"]
    description: str = Field(min_length=1, max_length=4000)
    evidence_manifest: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class ActivityRequest(BaseModel):
    work_item_id: str = Field(min_length=1, max_length=100)
    event_type: Literal["started", "paused", "resumed", "completed"]
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include timezone")
        return value


class ExecutionRequest(BaseModel):
    work_item_id: str = Field(min_length=1, max_length=100)
    action_hash: str = Field(min_length=64, max_length=64)
    exact_parameters: dict[str, Any] = Field(default_factory=dict)
    execution_evidence: dict[str, Any]
    executed_at: datetime
    authorization_id: str | None = Field(default=None, max_length=100)

    @field_validator("executed_at")
    @classmethod
    def execution_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("executed_at must include timezone")
        return value


class OutcomeRequest(BaseModel):
    work_item_id: str = Field(min_length=1, max_length=100)
    execution_id: str = Field(min_length=1, max_length=100)
    primary_metric: str = Field(min_length=1, max_length=100)
    desired_direction: Literal["increase", "decrease"]
    baseline_metrics: dict[str, Any]
    result_metrics: dict[str, Any]
    guardrail_metrics: dict[str, Any] = Field(default_factory=dict)
    observation_start: datetime
    observation_end: datetime
    confounders: list[str] = Field(default_factory=list, max_length=50)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    control_metrics: dict[str, Any] = Field(default_factory=dict)
    randomized_experiment: bool = False
    minimum_sample_satisfied: bool = False

    @field_validator("observation_start", "observation_end")
    @classmethod
    def observation_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observation timestamps must include timezone")
        return value


class RuleVersionRequest(BaseModel):
    rule_key: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: Literal["draft", "active"] = "draft"
    parent_version: str | None = Field(default=None, max_length=100)


class CalibrationProposalRequest(BaseModel):
    rule_key: str = Field(min_length=1, max_length=160)
    base_version: str = Field(min_length=1, max_length=100)
    current_parameters: dict[str, Any] = Field(default_factory=dict)
    candidate_parameters: dict[str, Any] = Field(default_factory=dict)
    replay_metrics: dict[str, Any] = Field(default_factory=dict)


class ShadowCompletionRequest(BaseModel):
    shadow_metrics: dict[str, Any] = Field(default_factory=dict)


class RuleRollbackRequest(BaseModel):
    target_rule_version_id: str = Field(min_length=1, max_length=100)


class ExecutionAuthorizationRequest(BaseModel):
    work_item_id: str = Field(min_length=1, max_length=100)
    action_hash: str = Field(min_length=64, max_length=64)
    action_type: str = Field(min_length=1, max_length=100)
    exact_parameters: dict[str, Any] = Field(default_factory=dict)
    before_snapshot: dict[str, Any] = Field(default_factory=dict)
    verified_amazon_data: bool
    data_fresh_seconds: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    account_currency: str | None = Field(default=None, min_length=3, max_length=3)
    inventory_status: str | None = Field(default=None, max_length=50)
    contribution_profit: str | None = None
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def authorization_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        return value


class AttributionPlanRequest(BaseModel):
    work_item_id: str = Field(min_length=1, max_length=100)
    action_hash: str = Field(min_length=64, max_length=64)
    primary_metric: str = Field(min_length=1, max_length=100)
    desired_direction: Literal["increase", "decrease"]
    baseline_start: datetime
    baseline_end: datetime
    observation_start: datetime
    observation_end: datetime
    currency: str = Field(min_length=3, max_length=3)
    randomized_experiment: bool = False
    has_control_group: bool = False
    minimum_sample: int = Field(gt=0)
    guardrail_metrics: list[str] = Field(default_factory=list, max_length=50)
    control_spec: dict[str, Any] = Field(default_factory=dict)
    confounders: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("baseline_start", "baseline_end", "observation_start", "observation_end")
    @classmethod
    def plan_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("attribution timestamps must include timezone")
        return value


class BusinessContextRequest(BaseModel):
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str | None = Field(default=None, max_length=100)
    context_type: Literal[
        "planned_promotion", "seasonality", "planned_stockout", "price_change", "listing_change", "advertising_change"
    ]
    starts_at: datetime
    ends_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
    source: Literal["manual", "csv_import", "amazon_api", "mock"] = "manual"
    verified_amazon_data: bool = False

    @field_validator("starts_at", "ends_at")
    @classmethod
    def context_timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("business context timestamps must include timezone")
        return value


@router.post("/issues/{issue_id}/feedback")
async def record_feedback(issue_id: str, payload: IssueFeedbackRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    try:
        result = request.app.state.operations_learning_repository.record_issue_feedback(
            tenant_id=principal.tenant_id,
            issue_id=issue_id,
            actor_id=principal.user_id,
            **payload.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="issue_feedback_recorded",
        status="success",
        details={
            "issue_id": issue_id,
            "reason_code": payload.reason_code,
            "is_correct": payload.is_correct,
            "is_useful": payload.is_useful,
        },
        actor=principal.user_id,
        risk_level="low",
    )
    return result


@router.post("/missed-issues")
async def report_missed_issue(payload: MissedIssueRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    return request.app.state.operations_learning_repository.report_missed_issue(
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        **payload.model_dump(),
    )


@router.get("/missed-issues")
async def list_missed_issues(request: Request) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.operations_learning_repository.list_missed_issues(
        tenant_id=request.state.principal.tenant_id
    )
    return {"total": len(items), "items": items}


@router.post("/activities")
async def record_activity(
    payload: ActivityRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.record_activity(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            idempotency_key=idempotency_key,
            **payload.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "invalid_activity_transition", "message": str(exc)}
        ) from exc


@router.get("/activities")
async def list_activities(
    request: Request, work_item_id: str = Query(..., min_length=1, max_length=100)
) -> dict[str, Any]:
    _require_read(request)
    principal = request.state.principal
    items = request.app.state.operations_learning_repository.list_activities(
        tenant_id=principal.tenant_id,
        work_item_id=work_item_id,
        actor_id=principal.user_id,
    )
    return {"total": len(items), "items": items}


@router.post("/executions")
async def record_execution(
    payload: ExecutionRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    try:
        result = request.app.state.operations_learning_repository.record_execution(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            idempotency_key=idempotency_key,
            **payload.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "execution_integrity_error", "message": str(exc)}
        ) from exc
    return result


@router.post("/outcomes")
async def evaluate_outcome(payload: OutcomeRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.evaluate_outcome(
            tenant_id=principal.tenant_id,
            **payload.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_outcome", "message": str(exc)}) from exc


@router.get("/outcomes")
async def list_outcomes(request: Request, work_item_id: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.operations_learning_repository.list_outcomes(
        tenant_id=request.state.principal.tenant_id,
        work_item_id=work_item_id,
    )
    return {"total": len(items), "items": items}


@router.post("/business-contexts")
async def save_business_context(payload: BusinessContextRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    try:
        return request.app.state.operations_learning_repository.save_business_context(
            tenant_id=request.state.principal.tenant_id,
            payload=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_business_context", "message": str(exc)}) from exc


@router.post("/rules")
async def create_rule_version(payload: RuleVersionRequest, request: Request) -> dict[str, Any]:
    _require_owner(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.create_rule_version(
            tenant_id=principal.tenant_id,
            created_by=principal.user_id,
            **payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_rule_version", "message": str(exc)}) from exc


@router.get("/rules")
async def list_rule_versions(request: Request, rule_key: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.operations_learning_repository.list_rule_versions(
        tenant_id=request.state.principal.tenant_id,
        rule_key=rule_key,
    )
    return {"total": len(items), "items": items}


@router.post("/calibration/proposals")
async def create_calibration_proposal(payload: CalibrationProposalRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.create_calibration_proposal(
            tenant_id=principal.tenant_id,
            proposed_by=principal.user_id,
            **payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_calibration_proposal", "message": str(exc)}
        ) from exc


@router.get("/calibration/proposals")
async def list_calibration_proposals(request: Request, status: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.operations_learning_repository.list_calibration_proposals(
        tenant_id=request.state.principal.tenant_id,
        status=status,
    )
    return {"total": len(items), "items": items}


@router.post("/calibration/proposals/{proposal_id}/approve")
async def approve_calibration_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    _require_approver(request)
    principal = request.state.principal
    try:
        result = request.app.state.operations_learning_repository.approve_calibration_proposal(
            tenant_id=principal.tenant_id,
            proposal_id=proposal_id,
            approver_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "invalid_calibration_transition", "message": str(exc)}
        ) from exc
    request.app.state.audit_logger.log_operation(
        action="calibration_proposal_approved",
        status="success",
        details={"proposal_id": proposal_id, "rule_version_id": result["rule_version"]["id"]},
        actor=principal.user_id,
        risk_level="medium",
    )
    return result


@router.post("/rules/{rule_version_id}/complete-shadow")
async def complete_rule_shadow(
    rule_version_id: str, payload: ShadowCompletionRequest, request: Request
) -> dict[str, Any]:
    _require_owner(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.complete_rule_shadow(
            tenant_id=principal.tenant_id,
            rule_version_id=rule_version_id,
            shadow_metrics=payload.shadow_metrics,
            actor_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "shadow_gate_failed", "message": str(exc)}) from exc


@router.post("/rules/{rule_version_id}/publish")
async def publish_rule_version(rule_version_id: str, request: Request) -> dict[str, Any]:
    _require_owner(request)
    principal = request.state.principal
    try:
        result = request.app.state.operations_learning_repository.publish_rule_version(
            tenant_id=principal.tenant_id,
            rule_version_id=rule_version_id,
            actor_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "rule_publish_blocked", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="rule_version_published",
        status="success",
        details={"rule_version_id": rule_version_id, "rule_key": result["rule_key"], "version": result["version"]},
        actor=principal.user_id,
        risk_level="high",
    )
    return result


@router.post("/rules/{rule_version_id}/rollback")
async def rollback_rule_version(rule_version_id: str, payload: RuleRollbackRequest, request: Request) -> dict[str, Any]:
    _require_owner(request)
    principal = request.state.principal
    try:
        result = request.app.state.operations_learning_repository.rollback_rule_version(
            tenant_id=principal.tenant_id,
            active_rule_version_id=rule_version_id,
            target_rule_version_id=payload.target_rule_version_id,
            actor_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "rule_rollback_blocked", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="rule_version_rolled_back",
        status="success",
        details={"active_rule_version_id": rule_version_id, "target_rule_version_id": payload.target_rule_version_id},
        actor=principal.user_id,
        risk_level="high",
    )
    return result


@router.post("/execution-authorizations")
async def prepare_execution_authorization(
    payload: ExecutionAuthorizationRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    values = payload.model_dump()
    policy_input = {
        key: values.pop(key)
        for key in (
            "verified_amazon_data",
            "data_fresh_seconds",
            "currency",
            "account_currency",
            "inventory_status",
            "contribution_profit",
        )
    }
    try:
        return request.app.state.operations_learning_repository.prepare_execution_authorization(
            tenant_id=principal.tenant_id,
            requested_by=principal.user_id,
            idempotency_key=idempotency_key,
            policy_input=policy_input,
            **values,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "execution_preflight_blocked", "message": str(exc)}
        ) from exc


@router.get("/execution-authorizations")
async def list_execution_authorizations(request: Request, work_item_id: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.operations_learning_repository.list_execution_authorizations(
        tenant_id=request.state.principal.tenant_id,
        work_item_id=work_item_id,
    )
    return {"total": len(items), "items": items}


@router.post("/execution-authorizations/{authorization_id}/approve")
async def approve_execution_authorization(authorization_id: str, request: Request) -> dict[str, Any]:
    _require_approver(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.approve_execution_authorization(
            tenant_id=principal.tenant_id,
            authorization_id=authorization_id,
            approver_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "authorization_approval_blocked", "message": str(exc)}
        ) from exc


@router.post("/execution-authorizations/{authorization_id}/revoke")
async def revoke_execution_authorization(authorization_id: str, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    try:
        return request.app.state.operations_learning_repository.revoke_execution_authorization(
            tenant_id=principal.tenant_id,
            authorization_id=authorization_id,
            actor_id=principal.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "authorization_revoke_blocked", "message": str(exc)}
        ) from exc


@router.post("/attribution-plans")
async def register_attribution_plan(payload: AttributionPlanRequest, request: Request) -> dict[str, Any]:
    _require_operator(request)
    principal = request.state.principal
    values = payload.model_dump()
    work_item_id = values.pop("work_item_id")
    action_hash = values.pop("action_hash")
    try:
        return request.app.state.operations_learning_repository.register_attribution_plan(
            tenant_id=principal.tenant_id,
            work_item_id=work_item_id,
            action_hash=action_hash,
            registered_by=principal.user_id,
            payload=values,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "invalid_attribution_plan", "message": str(exc)}) from exc


@router.get("/attribution-plans")
async def list_attribution_plans(request: Request, work_item_id: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.operations_learning_repository.list_attribution_plans(
        tenant_id=request.state.principal.tenant_id,
        work_item_id=work_item_id,
    )
    return {"total": len(items), "items": items}


@router.get("/metrics")
async def learning_metrics(request: Request) -> dict[str, Any]:
    _require_read(request)
    return request.app.state.operations_learning_repository.learning_metrics(
        tenant_id=request.state.principal.tenant_id
    )


def _require_read(request: Request) -> None:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})


def _require_operator(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "operator"} or (
        "write" not in principal.scopes and "admin" not in principal.scopes
    ):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Operator role is required."})


def _require_approver(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "approver"} or (
        "approve" not in principal.scopes and "admin" not in principal.scopes
    ):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Approver role is required."})


def _require_owner(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin"} or (
        "write" not in principal.scopes and "admin" not in principal.scopes
    ):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Owner role is required."})
