from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from api.security import require_api_key
from domain.inventory import InventoryCalculationError, InventoryHealthCalculator, InventoryHealthInput
from domain.models import DataSource, InventoryPolicy
from safety.hitl import ApprovalStatus, RiskLevel
from safety.policy_engine import canonical_action_hash

router = APIRouter(prefix="/api/inventory", tags=["inventory-health"], dependencies=[Depends(require_api_key)])


class InventoryPolicyRequest(BaseModel):
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    supplier_lead_time_days: int = Field(ge=0, le=365)
    production_lead_time_days: int = Field(ge=0, le=365)
    shipping_lead_time_days: int = Field(ge=0, le=365)
    amazon_receiving_days: int = Field(ge=0, le=180)
    safety_stock_days: int = Field(ge=0, le=365)
    target_coverage_days: int = Field(gt=0, le=730)
    minimum_order_quantity: int = Field(ge=0)
    case_pack_quantity: int = Field(gt=0)
    preferred_shipping_mode: str = Field(min_length=1, max_length=30)
    sales_weight_7d: Decimal
    sales_weight_14d: Decimal
    sales_weight_30d: Decimal
    source: str
    effective_from: datetime
    approval_request_id: str | None = None

    @field_validator("effective_from")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {item.value for item in DataSource}:
            raise ValueError("unsupported inventory policy source")
        return normalized


class InventoryHealthRequest(BaseModel):
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    available: int = Field(ge=0)
    reserved: int = Field(ge=0)
    inbound: int = Field(ge=0)
    in_transit: int = Field(ge=0)
    unsellable: int = Field(ge=0)
    available_is_net_of_reserved: bool = False
    sales_7_days: int | None = Field(default=None, ge=0)
    sales_14_days: int | None = Field(default=None, ge=0)
    sales_30_days: int | None = Field(default=None, ge=0)
    inventory_source: str
    inventory_collected_at: datetime
    calculated_at: datetime
    seasonality_factor: Decimal = Field(default=Decimal("1"), gt=0, le=5)
    promotion_factor: Decimal = Field(default=Decimal("1"), gt=0, le=5)
    unit_revenue: Decimal | None = None
    unit_contribution_profit: Decimal | None = None
    estimated_extra_logistics_cost_per_unit: Decimal | None = None
    promotion_anomaly: bool = False
    sales_spike: bool = False
    stockout_distorted_sales: bool = False
    recent_price_change: bool = False
    recent_advertising_budget_change: bool = False

    @field_validator("inventory_collected_at", "calculated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value

    @field_validator("inventory_source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {item.value for item in DataSource}:
            raise ValueError("unsupported inventory source")
        return normalized


@router.post("/policies")
async def create_inventory_policy(payload: InventoryPolicyRequest, request: Request, response: Response) -> dict:
    principal = request.state.principal
    if principal.role not in {"owner", "admin"} or (
        "write" not in principal.scopes and "admin" not in principal.scopes
    ):
        raise HTTPException(
            status_code=403, detail={"error": "forbidden", "message": "Owner or admin write access is required."}
        )
    policy_arguments = payload.model_dump(exclude={"approval_request_id"})
    policy_hash_arguments = payload.model_dump(mode="json", exclude={"approval_request_id"})
    existing = request.app.state.inventory_policy_repository.list_policies(
        tenant_id=principal.tenant_id,
        marketplace_id=payload.marketplace_id,
        seller_sku=payload.seller_sku,
    )
    action_hash = canonical_action_hash(
        action="update_inventory_policy",
        tenant_id=principal.tenant_id,
        resource_id=f"{payload.marketplace_id}:{payload.seller_sku}",
        arguments=policy_hash_arguments,
    )
    if existing:
        approval = (
            request.app.state.hitl_manager.get_request(payload.approval_request_id, tenant_id=principal.tenant_id)
            if payload.approval_request_id
            else None
        )
        approval_valid = (
            approval is not None
            and approval.status == ApprovalStatus.APPROVED
            and approval.payload.get("tenant_id") == principal.tenant_id
            and approval.payload.get("action_hash") == action_hash
        )
        if not approval_valid:
            if payload.approval_request_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "approval_invalid",
                        "message": "The approval is missing, not approved, or does not match this policy payload.",
                    },
                )
            approval = request.app.state.hitl_manager.submit_request(
                action="update_inventory_policy",
                payload={
                    "tenant_id": principal.tenant_id,
                    "resource_id": f"{payload.marketplace_id}:{payload.seller_sku}",
                    "action_hash": action_hash,
                    "proposed_changes": policy_hash_arguments,
                },
                requester=principal.user_id,
                risk_level=RiskLevel.HIGH,
                reason="Inventory policy changes affect replenishment and require one-person approval.",
            )
            response.status_code = 202
            return {"status": "awaiting_approval", "approval_request": approval.to_dict(), "action_hash": action_hash}
    try:
        policy = request.app.state.inventory_policy_repository.save(
            InventoryPolicy(tenant_id=principal.tenant_id, **policy_arguments)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_inventory_policy", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="inventory_policy_created",
        status="success",
        details={
            "tenant_id": principal.tenant_id,
            "policy_id": policy.id,
            "seller_sku": policy.seller_sku,
            "marketplace_id": policy.marketplace_id,
            "version": policy.version,
            "effective_from": policy.effective_from.isoformat(),
        },
        actor=principal.user_id,
        risk_level="medium",
    )
    return _serialize(policy)


@router.get("/policies")
async def list_inventory_policies(
    request: Request,
    marketplace_id: str | None = Query(default=None),
    seller_sku: str | None = Query(default=None),
) -> dict:
    _require_read(request)
    policies = request.app.state.inventory_policy_repository.list_policies(
        tenant_id=request.state.principal.tenant_id,
        marketplace_id=marketplace_id,
        seller_sku=seller_sku,
    )
    return {"items": [_serialize(policy) for policy in policies], "total": len(policies)}


@router.post("/health/calculate")
async def calculate_inventory_health(payload: InventoryHealthRequest, request: Request) -> dict:
    _require_read(request)
    tenant_id = request.state.principal.tenant_id
    policy = request.app.state.inventory_policy_repository.get_active(
        tenant_id=tenant_id,
        marketplace_id=payload.marketplace_id,
        seller_sku=payload.seller_sku,
        effective_at=payload.calculated_at,
    )
    if policy is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_inventory_policy",
                "message": "No effective inventory policy exists for this tenant, marketplace and SKU.",
                "missing_data": ["inventory_policy"],
                "confidence": "0.00",
                "degraded_reason": "missing_inventory_policy",
            },
        )
    try:
        snapshot = InventoryHealthCalculator().calculate(
            policy=policy,
            input_data=InventoryHealthInput(tenant_id=tenant_id, **payload.model_dump()),
        )
    except InventoryCalculationError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "inventory_calculation_error", "message": str(exc)}
        ) from exc
    request.app.state.inventory_health_repository.save(snapshot)
    request.app.state.audit_logger.log_operation(
        action="inventory_health_calculated",
        status="degraded" if snapshot.degraded_reason else "success",
        details={
            "tenant_id": tenant_id,
            "snapshot_id": snapshot.id,
            "seller_sku": snapshot.seller_sku,
            "marketplace_id": snapshot.marketplace_id,
            "inventory_health": snapshot.inventory_health,
            "stockout_risk": snapshot.stockout_risk,
            "recommended_reorder_quantity": snapshot.recommended_reorder_quantity,
            "forecast_confidence": str(snapshot.forecast_confidence),
            "automation_eligible": snapshot.automation_eligible,
            "missing_data": list(snapshot.missing_data),
            "data_warnings": list(snapshot.data_warnings),
        },
        actor=request.state.principal.user_id,
        risk_level="low",
    )
    return _serialize(snapshot)


def _require_read(request: Request) -> None:
    if "read" not in request.state.principal.scopes and "admin" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})


def _serialize(value) -> dict:
    return _json_safe(asdict(value))


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value
