from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.security import require_api_key
from domain.operational_intelligence import AmazonOperationalSignal
from infrastructure.repositories.operational_intelligence import (
    AdvertisingRepository,
    AmazonOperationalSignalRepository,
    VoCRepository,
)


router = APIRouter(prefix="/api", tags=["operational-intelligence"], dependencies=[Depends(require_api_key)])


class AmazonSignalRequest(BaseModel):
    marketplace_id: str = Field(min_length=1, max_length=50)
    signal_type: str = Field(min_length=1, max_length=60)
    resource_id: str = Field(min_length=1, max_length=200)
    severity: str = Field(pattern="^(low|medium|high|critical)$")
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=4000)
    observed_at: datetime
    collected_at: datetime
    freshness_seconds: int = Field(ge=0)
    verified_amazon_data: bool = False
    source: str = Field(min_length=1, max_length=40)
    raw_status_code: str = Field(min_length=1, max_length=100)
    evidence_manifest: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class AdvertisingSnapshotRequest(BaseModel):
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(default="campaign", min_length=1, max_length=30)
    entity_id: str = Field(min_length=1, max_length=200)
    campaign_id: str | None = Field(default=None, max_length=200)
    ad_group_id: str | None = Field(default=None, max_length=200)
    period_start: datetime
    period_end: datetime
    currency: str = Field(default="USD", min_length=3, max_length=3)
    spend: Decimal = Field(ge=0)
    attributed_sales: Decimal | None = Field(default=None, ge=0)
    total_sales: Decimal | None = Field(default=None, ge=0)
    budget: Decimal | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    orders: int | None = Field(default=None, ge=0)
    elapsed_days: Decimal = Field(default=Decimal("1"), gt=0)
    period_days: Decimal = Field(default=Decimal("7"), gt=0)
    break_even_acos: Decimal | None = Field(default=None, ge=0)
    source: str = Field(default="manual", max_length=40)
    verified_amazon_data: bool = False
    observed_at: datetime | None = None
    collected_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class SearchTermRequest(BaseModel):
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str = Field(min_length=1, max_length=100)
    campaign_id: str = Field(min_length=1, max_length=200)
    ad_group_id: str | None = Field(default=None, max_length=200)
    search_term: str = Field(min_length=1, max_length=500)
    period_start: datetime
    period_end: datetime
    currency: str = Field(default="USD", min_length=3, max_length=3)
    spend: Decimal = Field(ge=0)
    attributed_sales: Decimal | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    orders: int | None = Field(default=None, ge=0)
    source: str = Field(default="manual", max_length=40)
    verified_amazon_data: bool = False
    observed_at: datetime | None = None
    collected_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class ReturnRequest(BaseModel):
    external_return_id: str = Field(min_length=1, max_length=200)
    external_order_id: str | None = Field(default=None, max_length=200)
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str = Field(min_length=1, max_length=100)
    reason_code: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=1, ge=1)
    return_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    observed_at: datetime
    source: str = Field(default="manual", max_length=40)
    verified_amazon_data: bool = False
    evidence_manifest: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class ReviewRequest(BaseModel):
    external_review_id: str = Field(min_length=1, max_length=200)
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str = Field(min_length=1, max_length=100)
    rating: int = Field(ge=1, le=5)
    review_text: str | None = Field(default=None, max_length=10000)
    reason_code: str | None = Field(default=None, max_length=80)
    observed_at: datetime
    source: str = Field(default="manual", max_length=40)
    verified_amazon_data: bool = False
    evidence_manifest: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class MessageTagRequest(BaseModel):
    external_message_id: str = Field(min_length=1, max_length=200)
    marketplace_id: str = Field(min_length=1, max_length=50)
    seller_sku: str = Field(min_length=1, max_length=100)
    tag: str = Field(min_length=1, max_length=80)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    observed_at: datetime
    source: str = Field(default="manual", max_length=40)
    verified_amazon_data: bool = False
    evidence_manifest: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class VOCSummaryRequest(BaseModel):
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    period_start: datetime
    period_end: datetime
    min_sample_size: int = Field(default=5, ge=1, le=1000)


@router.get("/amazon/signals")
async def list_amazon_signals(request: Request, signal_type: str | None = None, severity: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.amazon_signal_repository.list(tenant_id=request.state.principal.tenant_id, signal_type=signal_type, severity=severity)
    return {"total": len(items), "items": _safe(items), "generated_at": datetime.now(UTC)}


@router.post("/amazon/signals")
async def create_amazon_signal(payload: AmazonSignalRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    principal = request.state.principal
    try:
        signal = AmazonOperationalSignal(tenant_id=principal.tenant_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_amazon_signal", "message": str(exc)},
        ) from exc
    saved = request.app.state.amazon_signal_repository.upsert(signal)
    _audit(request, "amazon_operational_signal_ingested", {"tenant_id": principal.tenant_id, "signal_type": signal.signal_type, "resource_id": signal.resource_id})
    return {"signal": _safe(saved)}


@router.get("/advertising/diagnostics")
async def list_advertising_diagnostics(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _require_read(request)
    items, total = request.app.state.advertising_repository.list_diagnostics_page(
        tenant_id=request.state.principal.tenant_id,
        status=status,
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
        "items": _safe(items),
    }


@router.get("/advertising/snapshots")
async def list_advertising_snapshots(request: Request, seller_sku: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.advertising_repository.list_snapshots(tenant_id=request.state.principal.tenant_id, seller_sku=seller_sku)
    return {"total": len(items), "items": _safe(items)}


@router.post("/advertising/snapshots")
async def create_advertising_snapshot(payload: AdvertisingSnapshotRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    snapshot = request.app.state.advertising_repository.save_daily_snapshot(tenant_id=request.state.principal.tenant_id, payload=payload.model_dump())
    diagnostics = request.app.state.advertising_repository.diagnose_daily_snapshot(tenant_id=request.state.principal.tenant_id, snapshot_id=snapshot["id"])
    _audit(request, "advertising_snapshot_diagnosed", {"tenant_id": request.state.principal.tenant_id, "snapshot_id": snapshot["id"], "diagnostic_count": len(diagnostics)})
    return {"snapshot": _safe(snapshot), "diagnostics": _safe(diagnostics)}


@router.post("/advertising/snapshots/{snapshot_id}/diagnose")
async def diagnose_advertising_snapshot(snapshot_id: str, request: Request) -> dict[str, Any]:
    _require_write(request)
    diagnostics = request.app.state.advertising_repository.diagnose_daily_snapshot(tenant_id=request.state.principal.tenant_id, snapshot_id=snapshot_id)
    return {"total": len(diagnostics), "items": _safe(diagnostics)}


@router.get("/advertising/search-terms")
async def list_search_terms(
    request: Request,
    seller_sku: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _require_read(request)
    items, total = request.app.state.advertising_repository.list_search_terms_page(
        tenant_id=request.state.principal.tenant_id,
        seller_sku=seller_sku,
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
        "items": _safe(items),
    }


@router.post("/advertising/search-terms")
async def create_search_term(payload: SearchTermRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    try:
        saved = request.app.state.advertising_repository.save_search_term(
            tenant_id=request.state.principal.tenant_id,
            payload=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_search_term_snapshot", "message": str(exc)},
        ) from exc
    _audit(request, "advertising_search_term_ingested", {
        "tenant_id": request.state.principal.tenant_id,
        "search_term_snapshot_id": saved["id"],
        "seller_sku": saved["seller_sku"],
    })
    return {"search_term": _safe(saved)}


@router.post("/voc/returns")
async def ingest_return(payload: ReturnRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    return {"event": _safe(request.app.state.voc_repository.save_return(tenant_id=request.state.principal.tenant_id, payload=payload.model_dump()))}


@router.post("/voc/reviews")
async def ingest_review(payload: ReviewRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    return {"event": _safe(request.app.state.voc_repository.save_review(tenant_id=request.state.principal.tenant_id, payload=payload.model_dump()))}


@router.post("/voc/message-tags")
async def ingest_message_tag(payload: MessageTagRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    return {"event": _safe(request.app.state.voc_repository.save_message_tag(tenant_id=request.state.principal.tenant_id, payload=payload.model_dump()))}


@router.post("/voc/summaries")
async def create_voc_summary(payload: VOCSummaryRequest, request: Request) -> dict[str, Any]:
    _require_write(request)
    summary = request.app.state.voc_repository.summarize(tenant_id=request.state.principal.tenant_id, **payload.model_dump())
    _audit(request, "sku_voc_summary_calculated", {"tenant_id": request.state.principal.tenant_id, "seller_sku": payload.seller_sku, "status": summary["status"]})
    return {"summary": _safe(summary)}


@router.get("/voc/summaries")
async def list_voc_summaries(request: Request, seller_sku: str | None = None) -> dict[str, Any]:
    _require_read(request)
    items = request.app.state.voc_repository.list_summaries(tenant_id=request.state.principal.tenant_id, seller_sku=seller_sku)
    return {"total": len(items), "items": _safe(items)}


def _require_read(request: Request) -> None:
    scopes = request.state.principal.scopes
    if "read" not in scopes and "admin" not in scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})


def _require_write(request: Request) -> None:
    principal = request.state.principal
    if principal.role not in {"owner", "admin", "operator"} or ("write" not in principal.scopes and "admin" not in principal.scopes):
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "operator write scope is required."})


def _audit(request: Request, action: str, details: dict[str, Any]) -> None:
    request.app.state.audit_logger.log_operation(action=action, status="success", details=details, actor=request.state.principal.user_id, risk_level="medium")


def _safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _safe(asdict(value))
    return value
