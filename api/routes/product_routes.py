from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from agents.tools import platform_api
from agents.tools.translator import translate_text
from api.security import require_api_key


router = APIRouter(prefix="/api", tags=["products"], dependencies=[Depends(require_api_key)])


class SyncProductRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    target_languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    seo_optimize: bool = True
    dry_run: bool = True
    confirm: bool = False


class SyncProductResponse(BaseModel):
    status: str
    agent: str
    product_id: str
    original: dict[str, Any]
    translations: dict[str, dict[str, str]]
    update_result: dict[str, Any] | None = None
    requires_confirmation: bool = False


def _seo_keywords(category: str) -> list[str]:
    mapping = {
        "Electronics": ["fast charging", "wireless", "portable", "compatible"],
        "Apparel": ["versatile", "breathable", "daily outfit", "commute"],
        "Home": ["storage", "durable", "space saving", "easy to clean"],
        "Beauty": ["hydrating", "gentle", "lightweight", "patch test recommended"],
        "Food": ["individually packed", "fresh", "sealed storage", "gift-ready"],
    }
    return mapping.get(category, ["quality", "daily use", "value"])


def _append_seo(text: str, category: str) -> str:
    keywords = _seo_keywords(category)
    if not keywords:
        return text
    return f"{text}\nSEO keywords: {', '.join(keywords)}"


@router.post("/sync-product", response_model=SyncProductResponse)
async def sync_product(payload: SyncProductRequest, request: Request) -> SyncProductResponse:
    """Synchronize a product by reading platform details and preparing multilingual SEO content."""
    try:
        token = await request.app.state.oauth_client.get_token()
        detail = await platform_api.get_product_detail(payload.product_id, access_token=token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "platform_fetch_failed", "message": str(exc)},
        ) from exc

    product = detail.get("product", detail)
    title = str(product.get("title") or product.get("title_en") or product.get("title_zh") or "")
    description = str(product.get("description") or product.get("description_en") or product.get("description_zh") or "")
    category = str(product.get("category", ""))

    translations: dict[str, dict[str, str]] = {}
    for language in payload.target_languages:
        translated_title = translate_text(title, target_language=language, source_language=None)
        translated_description = translate_text(description, target_language=language, source_language=None)
        if payload.seo_optimize:
            translated_description = _append_seo(translated_description, category)
        translations[language] = {
            "title": translated_title,
            "description": translated_description,
        }

    update_result = None
    requires_confirmation = payload.dry_run or not payload.confirm
    if not requires_confirmation and translations:
        first_language = payload.target_languages[0]
        update_result = await platform_api.update_product(
            product_id=payload.product_id,
            title=translations[first_language]["title"],
            description=translations[first_language]["description"],
            access_token=token,
        )

    return SyncProductResponse(
        status="prepared" if requires_confirmation else "synced",
        agent=getattr(request.app.state.team.sync_agent, "name", "SyncAgent"),
        product_id=payload.product_id,
        original=product,
        translations=translations,
        update_result=update_result,
        requires_confirmation=requires_confirmation,
    )
