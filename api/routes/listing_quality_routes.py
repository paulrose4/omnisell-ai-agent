from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from api.security import require_api_key
from application.listing_quality import ListingQualityNotFound
from domain.listing_quality import ListingQualityInput
from safety.hitl import ApprovalStatus, RiskLevel

router = APIRouter(
    prefix="/api/listing-quality",
    tags=["listing-quality"],
    dependencies=[Depends(require_api_key)],
)


class ListingQualityCalculationRequest(BaseModel):
    listing_id: str = Field(min_length=1, max_length=100)
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    content_version: int = Field(gt=0)
    title: str = Field(max_length=1000)
    bullet_points: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(max_length=20000)
    image_count: int = Field(ge=0, le=100)
    search_terms: list[str] = Field(default_factory=list, max_length=100)
    status: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=50)
    source_observed_at: datetime | None = None
    calculated_at: datetime
    sessions: int | None = Field(default=None, ge=0)
    units_ordered: int | None = Field(default=None, ge=0)
    conversion_rate: Decimal | None = Field(default=None, ge=0, le=1)
    return_rate: Decimal | None = Field(default=None, ge=0, le=1)
    ad_spend: Decimal | None = Field(default=None, ge=0)
    ad_traffic_share: Decimal | None = Field(default=None, ge=0, le=1)
    cpc: Decimal | None = Field(default=None, ge=0)
    price: Decimal | None = Field(default=None, ge=0)
    discount_rate: Decimal | None = Field(default=None, ge=0, le=1)
    buy_box_share: Decimal | None = Field(default=None, ge=0, le=1)
    sellable_units: int | None = Field(default=None, ge=0)
    out_of_stock_hours: Decimal | None = Field(default=None, ge=0)
    natural_rank: Decimal | None = Field(default=None, ge=0)
    promotion_active: bool | None = None
    seasonality_key: str | None = Field(default=None, max_length=100)
    competitor_change: bool | None = None

    @field_validator("source_observed_at", "calculated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


class ListingQualityFromSyncedListingRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=100)
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    calculated_at: datetime

    @field_validator("calculated_at")
    @classmethod
    def require_calculated_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


class ListingExperimentRequest(BaseModel):
    before_snapshot_id: str = Field(min_length=1, max_length=100)
    proposed_changes: dict[str, Any]
    observation_start: datetime
    observation_end: datetime
    approval_request_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("observation_start", "observation_end")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


class ListingExperimentEvaluationRequest(BaseModel):
    after_snapshot_id: str = Field(min_length=1, max_length=100)
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


class ListingExperimentExecutionRequest(BaseModel):
    action_hash: str = Field(min_length=64, max_length=64)
    actual_changes: dict[str, Any] = Field(default_factory=dict)
    execution_evidence: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)


class ListingExperimentDecisionRequest(BaseModel):
    decision: Literal["keep", "rollback"]
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/snapshots/calculate")
async def calculate_listing_quality(payload: ListingQualityCalculationRequest, request: Request) -> dict[str, Any]:
    _require_scope(request, "write")
    principal = request.state.principal
    try:
        snapshot, issue, action = request.app.state.listing_quality_service.calculate_snapshot(
            ListingQualityInput(
                tenant_id=principal.tenant_id,
                listing_id=payload.listing_id,
                seller_sku=payload.seller_sku,
                marketplace_id=payload.marketplace_id,
                content_version=payload.content_version,
                title=payload.title,
                bullet_points=tuple(payload.bullet_points),
                description=payload.description,
                image_count=payload.image_count,
                search_terms=tuple(payload.search_terms),
                status=payload.status,
                source=payload.source,
                source_observed_at=payload.source_observed_at,
                calculated_at=payload.calculated_at,
                sessions=payload.sessions,
                units_ordered=payload.units_ordered,
                conversion_rate=payload.conversion_rate,
                return_rate=payload.return_rate,
                ad_spend=payload.ad_spend,
                ad_traffic_share=payload.ad_traffic_share,
                cpc=payload.cpc,
                price=payload.price,
                discount_rate=payload.discount_rate,
                buy_box_share=payload.buy_box_share,
                sellable_units=payload.sellable_units,
                out_of_stock_hours=payload.out_of_stock_hours,
                natural_rank=payload.natural_rank,
                promotion_active=payload.promotion_active,
                seasonality_key=payload.seasonality_key,
                competitor_change=payload.competitor_change,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_listing_quality_input", "message": str(exc)}
        ) from exc
    request.app.state.audit_logger.log_operation(
        action="calculate_listing_quality",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "listing_id": snapshot.listing_id,
            "snapshot_id": snapshot.id,
            "quality_status": snapshot.quality_status,
            "quality_score": str(snapshot.quality_score),
        },
        actor=principal.user_id,
        risk_level="low",
    )
    return {
        "snapshot": _serialize(snapshot),
        "operational_issue": _serialize(issue) if issue else None,
        "recommended_action": _serialize(action) if action else None,
    }


@router.post("/snapshots/calculate-from-listing")
async def calculate_listing_quality_from_synced_listing(
    payload: ListingQualityFromSyncedListingRequest,
    request: Request,
) -> dict[str, Any]:
    """Build a quality snapshot from the tenant's latest synchronized Listing record."""
    _require_scope(request, "write")
    principal = request.state.principal
    listings = request.app.state.commerce_sync_repository.list_listings(
        tenant_id=principal.tenant_id,
        connection_id=payload.connection_id,
        marketplace_id=payload.marketplace_id,
        seller_skus=[payload.seller_sku],
    )
    listing = next((item for item in listings if item.seller_sku == payload.seller_sku), None)
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "listing_not_found",
                "message": "Synchronized Listing was not found for this tenant and marketplace.",
            },
        )
    metadata = listing.platform_extensions or {}
    source = "mock" if metadata.get("data_source") == "mock_amazon" else "amazon_api"
    observed_at = _parse_datetime(metadata.get("observed_at") or metadata.get("source_observed_at"))
    try:
        snapshot, issue, action = request.app.state.listing_quality_service.calculate_snapshot(
            ListingQualityInput(
                tenant_id=principal.tenant_id,
                listing_id=listing.id,
                seller_sku=listing.seller_sku,
                marketplace_id=listing.marketplace_id,
                content_version=listing.content_version,
                title=listing.title or "",
                bullet_points=tuple(metadata.get("bullet_points") or ()),
                description=str(metadata.get("description") or ""),
                image_count=int(metadata.get("image_count") or 0),
                search_terms=tuple(metadata.get("search_terms") or ()),
                status=listing.status,
                source=source,
                source_observed_at=observed_at,
                calculated_at=payload.calculated_at,
                sessions=_optional_int(metadata.get("sessions")),
                units_ordered=_optional_int(metadata.get("units_ordered")),
                conversion_rate=_optional_decimal(metadata.get("conversion_rate")),
                return_rate=_optional_decimal(metadata.get("return_rate")),
                ad_spend=_optional_decimal(metadata.get("ad_spend")),
                ad_traffic_share=_optional_decimal(metadata.get("ad_traffic_share")),
                cpc=_optional_decimal(metadata.get("cpc")),
                price=_optional_decimal(metadata.get("price"))
                if metadata.get("price") is not None
                else (listing.price.amount if listing.price else None),
                discount_rate=_optional_decimal(metadata.get("discount_rate")),
                buy_box_share=_optional_decimal(metadata.get("buy_box_share")),
                sellable_units=_optional_int(metadata.get("sellable_units")),
                out_of_stock_hours=_optional_decimal(metadata.get("out_of_stock_hours")),
                natural_rank=_optional_decimal(metadata.get("natural_rank")),
                promotion_active=metadata.get("promotion_active"),
                seasonality_key=metadata.get("seasonality_key"),
                competitor_change=metadata.get("competitor_change"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_synced_listing_metadata", "message": str(exc)}
        ) from exc
    request.app.state.audit_logger.log_operation(
        action="calculate_listing_quality_from_synced_listing",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "listing_id": snapshot.listing_id,
            "snapshot_id": snapshot.id,
            "source": source,
        },
        actor=principal.user_id,
        risk_level="low",
    )
    return {
        "snapshot": _serialize(snapshot),
        "operational_issue": _serialize(issue) if issue else None,
        "recommended_action": _serialize(action) if action else None,
    }


@router.get("/snapshots")
async def list_listing_quality_snapshots(
    request: Request,
    seller_sku: str | None = Query(default=None, min_length=1, max_length=100),
) -> dict[str, Any]:
    _require_scope(request, "read")
    items = request.app.state.listing_quality_snapshot_repository.list_snapshots(
        tenant_id=request.state.principal.tenant_id,
        seller_sku=seller_sku,
    )
    return {"total": len(items), "items": [_serialize(item) for item in items]}


@router.post("/experiments")
async def create_listing_experiment(
    payload: ListingExperimentRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _require_scope(request, "write")
    principal = request.state.principal
    created_at = datetime.now(UTC)
    try:
        experiment = request.app.state.listing_quality_service.create_experiment(
            tenant_id=principal.tenant_id,
            before_snapshot_id=payload.before_snapshot_id,
            proposed_changes=payload.proposed_changes,
            observation_start=payload.observation_start,
            observation_end=payload.observation_end,
            created_at=created_at,
        )
    except ListingQualityNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_listing_experiment", "message": str(exc)}
        ) from exc

    approval = (
        request.app.state.hitl_manager.get_request(payload.approval_request_id, tenant_id=principal.tenant_id)
        if payload.approval_request_id
        else None
    )
    approval_valid = (
        approval is not None
        and approval.status == ApprovalStatus.APPROVED
        and approval.payload.get("tenant_id") == principal.tenant_id
        and approval.payload.get("action_hash") == experiment.action_hash
    )
    if payload.approval_request_id and not approval_valid:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_mismatch",
                "message": "Approval is missing, unapproved or does not match this exact Listing action.",
            },
        )

    if approval_valid:
        assert approval is not None
        experiment.status = "approved"
        experiment.approval_request_id = approval.request_id
        experiment.updated_at = created_at
        saved = request.app.state.listing_quality_service.save_experiment(experiment)
        request.app.state.audit_logger.log_operation(
            action="activate_listing_optimization_experiment",
            status="success",
            details={
                "tenant_id": principal.tenant_id,
                "experiment_id": saved.id,
                "approval_request_id": approval.request_id,
                "action_hash": saved.action_hash,
                "platform_write_executed": False,
            },
            actor=principal.user_id,
            risk_level="high",
        )
        return {"status": saved.status, "experiment": _serialize(saved)}

    saved = request.app.state.listing_quality_service.save_experiment(experiment)
    approval = request.app.state.hitl_manager.submit_request(
        action="publish_listing",
        payload={
            "tenant_id": principal.tenant_id,
            "resource_id": f"{saved.marketplace_id}:{saved.seller_sku}",
            "experiment_id": saved.id,
            "action_hash": saved.action_hash,
            "before_snapshot_id": saved.before_snapshot_id,
            "proposed_changes": saved.proposed_changes,
            "observation_start": saved.observation_start.isoformat(),
            "observation_end": saved.observation_end.isoformat(),
            "platform_write_executed": False,
        },
        requester=principal.user_id,
        risk_level=RiskLevel.HIGH,
        reason="Listing content publication requires approval of the exact proposed changes.",
    )
    saved.approval_request_id = approval.request_id
    saved = request.app.state.listing_quality_service.save_experiment(saved)
    response.status_code = 202
    request.app.state.audit_logger.log_approval(
        action="publish_listing",
        status="pending",
        details={
            "tenant_id": principal.tenant_id,
            "experiment_id": saved.id,
            "approval_request_id": approval.request_id,
            "action_hash": saved.action_hash,
        },
        actor=principal.user_id,
        risk_level="high",
    )
    return {"status": "awaiting_approval", "experiment": _serialize(saved), "approval_request": approval.to_dict()}


@router.get("/experiments")
async def list_listing_experiments(
    request: Request,
    status: str | None = Query(default=None, min_length=1, max_length=50),
) -> dict[str, Any]:
    _require_scope(request, "read")
    items = request.app.state.listing_experiment_repository.list_experiments(
        tenant_id=request.state.principal.tenant_id,
        status=status,
    )
    return {"total": len(items), "items": [_serialize(item) for item in items]}


@router.post("/experiments/{experiment_id}/execution")
async def record_listing_experiment_execution(
    experiment_id: str,
    payload: ListingExperimentExecutionRequest,
    request: Request,
) -> dict[str, Any]:
    """Record an approved Listing change performed manually in Seller Central."""
    _require_scope(request, "write")
    principal = request.state.principal
    experiment = request.app.state.listing_experiment_repository.get(
        tenant_id=principal.tenant_id,
        experiment_id=experiment_id,
    )
    if experiment is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Listing optimization experiment not found."}
        )
    if payload.action_hash != experiment.action_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "action_hash_mismatch",
                "message": "Manual execution does not match the approved Listing action.",
            },
        )
    if experiment.execution_status in {"executed", "verified"}:
        return {"experiment": _serialize(experiment), "platform_write_executed": False, "replayed": True}
    if experiment.status != "approved":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": "The exact Listing experiment must be approved before manual execution.",
            },
        )
    if payload.execution_evidence.get("platform_write_executed") is True:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "platform_write_disabled",
                "message": "This workflow records manual execution only; the API never writes Listing data.",
            },
        )
    now = datetime.now(UTC)
    experiment.execution_status = "executed"
    experiment.actual_changes = payload.actual_changes
    experiment.execution_evidence = {**payload.execution_evidence, "platform_write_executed": False}
    experiment.executed_by = principal.user_id
    experiment.executed_at = now
    experiment.execution_notes = payload.notes or None
    experiment.status = "observing"
    experiment.updated_at = now
    saved = request.app.state.listing_quality_service.save_experiment(experiment)
    request.app.state.audit_logger.log_operation(
        action="record_listing_experiment_manual_execution",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "experiment_id": saved.id,
            "action_hash": saved.action_hash,
            "executed_by": principal.user_id,
            "platform_write_executed": False,
        },
        actor=principal.user_id,
        risk_level="high",
    )
    return {"experiment": _serialize(saved), "platform_write_executed": False, "replayed": False}


@router.post("/experiments/{experiment_id}/decision")
async def decide_listing_experiment(
    experiment_id: str,
    payload: ListingExperimentDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    """Persist an independent keep/rollback decision after deterministic evaluation."""
    _require_decision_role(request)
    principal = request.state.principal
    experiment = request.app.state.listing_experiment_repository.get(
        tenant_id=principal.tenant_id,
        experiment_id=experiment_id,
    )
    if experiment is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Listing optimization experiment not found."}
        )
    if experiment.execution_status != "verified":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "evaluation_required",
                "message": "A verified after-snapshot is required before deciding to keep or rollback.",
            },
        )
    if experiment.decision is not None:
        return {"experiment": _serialize(experiment), "platform_write_executed": False, "replayed": True}
    now = datetime.now(UTC)
    experiment.decision = payload.decision
    experiment.decision_by = principal.user_id
    experiment.decision_at = now
    experiment.decision_reason = payload.reason
    experiment.status = "kept" if payload.decision == "keep" else "rollback_planned"
    experiment.updated_at = now
    saved = request.app.state.listing_quality_service.save_experiment(experiment)
    request.app.state.audit_logger.log_operation(
        action="decide_listing_experiment",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "experiment_id": saved.id,
            "decision": payload.decision,
            "reason": payload.reason,
            "platform_write_executed": False,
        },
        actor=principal.user_id,
        risk_level="high" if payload.decision == "rollback" else "medium",
    )
    return {"experiment": _serialize(saved), "platform_write_executed": False, "replayed": False}


@router.post("/experiments/{experiment_id}/evaluate")
async def evaluate_listing_experiment(
    experiment_id: str,
    payload: ListingExperimentEvaluationRequest,
    request: Request,
) -> dict[str, Any]:
    _require_scope(request, "write")
    principal = request.state.principal
    existing = request.app.state.listing_experiment_repository.get(
        tenant_id=principal.tenant_id,
        experiment_id=experiment_id,
    )
    if existing is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "message": "Listing optimization experiment not found."}
        )
    if existing.execution_status != "executed":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "manual_execution_required",
                "message": "Record the approved manual Listing execution before evaluating outcomes.",
            },
        )
    try:
        experiment = request.app.state.listing_quality_service.evaluate_experiment(
            tenant_id=principal.tenant_id,
            experiment_id=experiment_id,
            after_snapshot_id=payload.after_snapshot_id,
            evaluated_at=payload.evaluated_at,
        )
    except ListingQualityNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "experiment_not_evaluable", "message": str(exc)}) from exc
    experiment.execution_status = "verified"
    experiment = request.app.state.listing_quality_service.save_experiment(experiment)
    request.app.state.audit_logger.log_operation(
        action="evaluate_listing_optimization_experiment",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "experiment_id": experiment.id,
            "after_snapshot_id": experiment.after_snapshot_id,
            "status": experiment.status,
            "rollback_recommended": experiment.rollback_recommended,
            "platform_write_executed": False,
        },
        actor=principal.user_id,
        risk_level="high" if experiment.rollback_recommended else "medium",
    )
    return _serialize(experiment)


def _require_scope(request: Request, scope: str) -> None:
    scopes = request.state.principal.scopes
    if scope not in scopes and "admin" not in scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": f"{scope} scope is required."})


def _require_decision_role(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "approver"}:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": "An owner, admin or approver must decide Listing experiment outcomes.",
            },
        )


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(item) for item in value]
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("source observed timestamp must include timezone")
    return parsed


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))
