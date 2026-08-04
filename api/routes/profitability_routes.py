from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

from api.security import require_api_key
from application.cost_imports import CostCSVImporter
from domain.profitability import ProfitCalculationError, ProfitCalculationInput, ProfitCalculator
from infrastructure.repositories.profitability import ImportIdempotencyConflict


router = APIRouter(prefix="/api/profitability", tags=["profitability"], dependencies=[Depends(require_api_key)])


class ProfitCalculationRequest(BaseModel):
    seller_sku: str = Field(min_length=1, max_length=100)
    marketplace_id: str = Field(min_length=1, max_length=50)
    currency: str = Field(min_length=3, max_length=3)
    window_start: datetime
    window_end: datetime
    item_sales: Decimal
    units: int = Field(ge=0)
    discounts: Decimal = Decimal("0")
    coupons: Decimal = Decimal("0")
    refunds: Decimal = Decimal("0")
    promotions: Decimal = Decimal("0")
    actual_referral_fees: Decimal | None = None
    actual_fulfillment_fees: Decimal | None = None
    actual_storage_cost: Decimal | None = None
    advertising_cost: Decimal = Decimal("0")
    advertising_attributed_sales: Decimal | None = None
    actual_return_loss: Decimal | None = None
    other_variable_cost: Decimal = Decimal("0")

    @field_validator("window_start", "window_end")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


@router.post("/cost-profiles/import")
async def import_cost_profiles(
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Query(default=True),
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict:
    if file.content_type not in {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}:
        raise HTTPException(status_code=415, detail={"error": "unsupported_media_type", "message": "A UTF-8 CSV file is required."})
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail={"error": "invalid_extension", "message": "The file extension must be .csv."})
    content = await file.read()
    principal = request.state.principal
    required_scope = "read" if dry_run else "write"
    if required_scope not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": f"{required_scope} scope is required."})
    importer = CostCSVImporter(request.app.state.cost_profile_repository)
    try:
        result = importer.import_bytes(
            tenant_id=principal.tenant_id, actor_id=principal.user_id, content=content,
            idempotency_key=idempotency_key, dry_run=dry_run,
        )
    except ImportIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "idempotency_conflict", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_cost_import", "message": str(exc)}) from exc
    request.app.state.audit_logger.log_operation(
        action="sku_cost_import", status="success" if not result.errors else "validation_failed",
        details={
            "tenant_id": principal.tenant_id, "job_id": result.job_id, "dry_run": dry_run,
            "total_rows": result.total_rows, "valid_rows": result.valid_rows,
            "committed_rows": result.committed_rows, "error_count": len(result.errors),
            "content_sha256": result.content_sha256,
        }, actor=principal.user_id, risk_level="medium",
    )
    return {
        "job_id": result.job_id, "dry_run": result.dry_run, "total_rows": result.total_rows,
        "valid_rows": result.valid_rows, "committed_rows": result.committed_rows,
        "content_sha256": result.content_sha256, "repeated": result.repeated,
        "errors": [asdict(error) for error in result.errors],
    }


@router.get("/cost-profiles")
async def list_cost_profiles(
    request: Request,
    marketplace_id: str | None = Query(default=None),
    seller_sku: str | None = Query(default=None),
) -> dict:
    if "read" not in request.state.principal.scopes and "admin" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    profiles = request.app.state.cost_profile_repository.list_profiles(
        tenant_id=request.state.principal.tenant_id, marketplace_id=marketplace_id, seller_sku=seller_sku,
    )
    return {"items": [_serialize(profile) for profile in profiles], "total": len(profiles)}


@router.post("/snapshots/calculate")
async def calculate_profit_snapshot(payload: ProfitCalculationRequest, request: Request) -> dict:
    if "read" not in request.state.principal.scopes and "admin" not in request.state.principal.scopes:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "read scope is required."})
    tenant_id = request.state.principal.tenant_id
    profile = request.app.state.cost_profile_repository.get_active(
        tenant_id=tenant_id, marketplace_id=payload.marketplace_id,
        seller_sku=payload.seller_sku, effective_at=payload.window_end,
    )
    if profile is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_cost_profile", "message": "No effective cost profile exists for this tenant, marketplace and SKU.",
                "missing_data": ["sku_cost_profile"], "confidence": "0.00", "degraded_reason": "missing_cost_profile",
            },
        )
    try:
        snapshot = ProfitCalculator().calculate(
            profile=profile,
            input_data=ProfitCalculationInput(tenant_id=tenant_id, **payload.model_dump()),
        )
    except ProfitCalculationError as exc:
        raise HTTPException(status_code=422, detail={"error": "profit_calculation_error", "message": str(exc)}) from exc
    request.app.state.profit_snapshot_repository.save(snapshot)
    request.app.state.audit_logger.log_operation(
        action="sku_profit_calculated", status="degraded" if snapshot.degraded_reason else "success",
        details={
            "tenant_id": tenant_id, "snapshot_id": snapshot.id, "seller_sku": snapshot.seller_sku,
            "marketplace_id": snapshot.marketplace_id, "calculation_version": snapshot.calculation_version,
            "missing_inputs": list(snapshot.missing_inputs), "confidence": str(snapshot.confidence), "estimated": snapshot.estimated,
        }, actor=request.state.principal.user_id, risk_level="low",
    )
    return _serialize(snapshot)


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
