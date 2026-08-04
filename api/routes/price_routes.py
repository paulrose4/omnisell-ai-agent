from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from agents.tools import platform_api
from agents.tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
from api.security import require_api_key
from application.pricing import PricingEvidenceNotFound
from domain.pricing import PriceRecommendationInput
from safety.hitl import ApprovalStatus, RiskLevel
from safety.policy_engine import canonical_action_hash


PRICING_SERVICE_NAME = "ProfitAndPricingService"


router = APIRouter(prefix="/api/price", tags=["price"], dependencies=[Depends(require_api_key)])


class PriceMonitorRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    current_price: Decimal | None = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    market: str = Field(default="SG", min_length=2, max_length=8)
    auto_apply: bool = False


class PriceMonitorResponse(BaseModel):
    agent: str
    recommendation: dict[str, Any]
    approval_request: dict[str, Any] | None = None
    update_result: dict[str, Any] | None = None


class ProfitConstrainedPriceRequest(BaseModel):
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    currency: str = Field(min_length=3, max_length=3)
    current_price: Decimal = Field(gt=0)
    requested_price: Decimal | None = Field(default=None, gt=0)
    competitor_prices: list[Decimal] = Field(default_factory=list, max_length=100)
    profit_snapshot_id: str = Field(min_length=1, max_length=36)
    inventory_snapshot_id: str = Field(min_length=1, max_length=36)
    calculated_at: datetime
    approval_request_id: str | None = None

    @field_validator("calculated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("calculated_at must include timezone")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


@router.post("/monitor", response_model=PriceMonitorResponse)
async def monitor_price(payload: PriceMonitorRequest, request: Request) -> PriceMonitorResponse:
    """Legacy competitor monitor; no automatic write is allowed without profit and inventory evidence."""
    principal = request.state.principal
    current_price = payload.current_price
    token = None
    if current_price is None:
        token = await request.app.state.oauth_client.get_token()
        platform_price = await platform_api.get_product_price(payload.product_id, access_token=token)
        current_price = Decimal(str(platform_price["price"]))
    competitor_prices = fetch_competitor_prices(
        product_id=payload.product_id, current_price=float(current_price),
        currency=payload.currency.upper(), market=payload.market.upper(),
    )
    recommendation = generate_price_recommendation(
        product_id=payload.product_id, current_price=float(current_price), competitor_prices=competitor_prices,
        currency=payload.currency.upper(), market=payload.market.upper(),
    )
    legacy_arguments = {
        "current_price": str(recommendation["current_price"]),
        "new_price": str(recommendation["recommended_price"]),
        "data_complete": False,
    }
    recommendation["automation_eligible"] = False
    recommendation["missing_data"] = ["profit_snapshot", "inventory_health_snapshot", "cost_profile"]
    recommendation["degraded_reason"] = "legacy_price_monitor_missing_profit_constraints"
    recommendation["action_hash"] = canonical_action_hash(
        action="update_price", tenant_id=principal.tenant_id,
        resource_id=payload.product_id, arguments=legacy_arguments,
    )

    approval_request = None
    update_result = None
    if recommendation["approval_required"]:
        approval_request = request.app.state.hitl_manager.submit_request(
            action="update_product_price",
            payload={
                "tenant_id": principal.tenant_id, "resource_id": payload.product_id,
                "action_hash": recommendation["action_hash"], "current_price": recommendation["current_price"],
                "recommended_price": recommendation["recommended_price"], "change_rate": recommendation["change_rate"],
                "missing_data": recommendation["missing_data"],
            },
            requester=principal.user_id,
        ).to_dict()
    record = {
        "tenant_id": principal.tenant_id, "product_id": payload.product_id,
        "recommendation": recommendation, "approval_request": approval_request, "update_result": update_result,
    }
    request.app.state.price_recommendations.setdefault(principal.tenant_id, []).append(record)
    request.app.state.audit_logger.log_operation(
        action="price_monitor", status="degraded", details=record, actor=principal.user_id,
        risk_level="high" if recommendation["approval_required"] else "medium",
    )
    return PriceMonitorResponse(
        agent=PRICING_SERVICE_NAME,
        recommendation=recommendation, approval_request=approval_request, update_result=update_result,
    )


@router.get("/recommendations")
async def list_price_recommendations(request: Request) -> dict[str, Any]:
    """Return legacy in-memory monitoring records for compatibility."""
    items = request.app.state.price_recommendations.get(request.state.principal.tenant_id, [])
    return {"total": len(items), "items": items}


@router.post("/recommendations/calculate")
async def calculate_profit_constrained_price(
    payload: ProfitConstrainedPriceRequest, request: Request, response: Response,
) -> dict[str, Any]:
    principal = request.state.principal
    if "write" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "write scope is required."})
    try:
        recommendation = request.app.state.price_recommendation_service.calculate(
            tenant_id=principal.tenant_id,
            profit_snapshot_id=payload.profit_snapshot_id,
            inventory_snapshot_id=payload.inventory_snapshot_id,
            input_data=PriceRecommendationInput(
                tenant_id=principal.tenant_id, seller_sku=payload.seller_sku,
                marketplace_id=payload.marketplace_id, currency=payload.currency,
                current_price=payload.current_price, requested_price=payload.requested_price,
                competitor_prices=tuple(payload.competitor_prices), calculated_at=payload.calculated_at,
            ),
        )
    except PricingEvidenceNotFound as exc:
        raise HTTPException(status_code=404, detail={"error": "pricing_evidence_not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_price_recommendation", "message": str(exc)}) from exc

    approval_request = None
    if recommendation.policy_decision == "require_approval":
        approval = request.app.state.hitl_manager.get_request(payload.approval_request_id, tenant_id=principal.tenant_id) if payload.approval_request_id else None
        approval_valid = (
            approval is not None and approval.status == ApprovalStatus.APPROVED
            and approval.payload.get("tenant_id") == principal.tenant_id
            and approval.payload.get("action_hash") == recommendation.action_hash
        )
        if approval_valid:
            recommendation.status = "approved"
            recommendation.approval_request_id = approval.request_id
            approval_request = approval.to_dict()
        elif payload.approval_request_id:
            raise HTTPException(status_code=409, detail={"error": "approval_invalid", "message": "Approval is missing, not approved, or does not match this price action."})
        else:
            approval = request.app.state.hitl_manager.submit_request(
                action="update_price",
                payload={
                    "tenant_id": principal.tenant_id,
                    "resource_id": f"{payload.marketplace_id}:{payload.seller_sku}",
                    "action_hash": recommendation.action_hash,
                    "proposed_changes": {
                        "current_price": str(recommendation.current_price),
                        "recommended_price": str(recommendation.recommended_price),
                        "currency": recommendation.currency,
                        "break_even_floor": str(recommendation.break_even_floor),
                        "minimum_margin_floor": str(recommendation.minimum_margin_floor),
                        "projected_margin": str(recommendation.projected_margin),
                    },
                    "evidence": list(recommendation.evidence),
                },
                requester=principal.user_id, risk_level=RiskLevel.HIGH,
                reason="Price is outside the automatic profit-safe band and requires a different-user approval.",
            )
            recommendation.approval_request_id = approval.request_id
            approval_request = approval.to_dict()
            response.status_code = 202
    saved = request.app.state.price_recommendation_service.save(recommendation)
    request.app.state.audit_logger.log_operation(
        action="profit_constrained_price_recommendation",
        status="blocked" if saved.status == "blocked" else "success",
        details={
            "tenant_id": principal.tenant_id, "recommendation_id": saved.id,
            "seller_sku": saved.seller_sku, "marketplace_id": saved.marketplace_id,
            "current_price": str(saved.current_price), "recommended_price": str(saved.recommended_price),
            "break_even_floor": str(saved.break_even_floor), "minimum_margin_floor": str(saved.minimum_margin_floor),
            "policy_decision": saved.policy_decision, "policy_reasons": list(saved.policy_reasons),
            "action_hash": saved.action_hash, "approval_request_id": saved.approval_request_id,
        }, actor=principal.user_id, risk_level=saved.risk_level,
    )
    return {"recommendation": _serialize(saved), "approval_request": approval_request, "executed": False}


@router.get("/recommendations/persistent")
async def list_persistent_price_recommendations(
    request: Request, seller_sku: str | None = Query(default=None), status: str | None = Query(default=None),
) -> dict[str, Any]:
    principal = request.state.principal
    if "read" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    items = request.app.state.price_recommendation_repository.list_recommendations(
        tenant_id=principal.tenant_id, seller_sku=seller_sku, status=status,
    )
    return {"items": [_serialize(item) for item in items], "total": len(items)}


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
