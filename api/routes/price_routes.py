from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from agents.tools.price_scraper import fetch_competitor_prices, generate_price_recommendation
from agents.tools import platform_api
from api.security import require_api_key


router = APIRouter(prefix="/api/price", tags=["price"], dependencies=[Depends(require_api_key)])


class PriceMonitorRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    current_price: float | None = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    market: str = Field(default="SG", min_length=2, max_length=8)
    auto_apply: bool = False


class PriceMonitorResponse(BaseModel):
    agent: str
    recommendation: dict[str, Any]
    approval_request: dict[str, Any] | None = None
    update_result: dict[str, Any] | None = None


@router.post("/monitor", response_model=PriceMonitorResponse)
async def monitor_price(payload: PriceMonitorRequest, request: Request) -> PriceMonitorResponse:
    """Run competitor price monitoring and risk-control recommendation."""
    current_price = payload.current_price
    token = None

    if current_price is None:
        token = await request.app.state.oauth_client.get_token()
        platform_price = await platform_api.get_product_price(payload.product_id, access_token=token)
        current_price = float(platform_price["price"])

    competitor_prices = fetch_competitor_prices(
        product_id=payload.product_id,
        current_price=current_price,
        currency=payload.currency.upper(),
        market=payload.market.upper(),
    )
    recommendation = generate_price_recommendation(
        product_id=payload.product_id,
        current_price=current_price,
        competitor_prices=competitor_prices,
        currency=payload.currency.upper(),
        market=payload.market.upper(),
    )

    approval_request = None
    update_result = None
    if recommendation["approval_required"]:
        approval_request = request.app.state.hitl_manager.submit_request(
            action="update_product_price",
            payload={
                "product_id": payload.product_id,
                "current_price": recommendation["current_price"],
                "recommended_price": recommendation["recommended_price"],
                "change_rate": recommendation["change_rate"],
            },
            requester="price_api",
        ).to_dict()
    elif payload.auto_apply and recommendation["action"] == "auto_update":
        token = token or await request.app.state.oauth_client.get_token()
        update_result = await platform_api.update_product(
            product_id=payload.product_id,
            price=float(recommendation["recommended_price"]),
            access_token=token,
        )

    record = {
        "product_id": payload.product_id,
        "recommendation": recommendation,
        "approval_request": approval_request,
        "update_result": update_result,
    }
    request.app.state.price_recommendations.append(record)
    request.app.state.audit_logger.log_operation(
        action="price_monitor",
        status="success",
        details=record,
        actor="price_api",
        risk_level="high" if recommendation["approval_required"] else "medium",
    )

    return PriceMonitorResponse(
        agent=getattr(request.app.state.team.price_agent, "name", "PriceRiskAgent"),
        recommendation=recommendation,
        approval_request=approval_request,
        update_result=update_result,
    )


@router.get("/recommendations")
async def list_price_recommendations(request: Request) -> dict[str, Any]:
    """Return recent price monitoring recommendations."""
    return {
        "total": len(request.app.state.price_recommendations),
        "items": request.app.state.price_recommendations,
    }
