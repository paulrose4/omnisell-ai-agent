from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Annotated, Any

from config import AUDIT_QUEUE, DATA_LOCK, DATA_STORE, save_collection, utc_now_iso
from fastapi import APIRouter, Depends, HTTPException, Query, status
from oauth_server import require_bearer_token
from pydantic import BaseModel, Field, field_validator, model_validator

router = APIRouter(prefix="/api/v2/product", tags=["products"])

PRODUCT_CATEGORIES = {"Electronics", "Home", "Apparel", "Beauty", "Food"}
PRODUCT_STATUSES = {"LIVE", "SOLD_OUT", "REVIEWING", "DELETED"}


class ProductCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=180)
    description: str = Field(..., min_length=1, max_length=2000)
    price: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    stock: int = Field(..., ge=0)
    images: list[str] = Field(..., min_length=1, max_length=10)
    category: str = Field(..., min_length=1, max_length=80)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in PRODUCT_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(sorted(PRODUCT_CATEGORIES))}")
        return value


class ProductUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    price: float | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_one_field(self) -> ProductUpdateRequest:
        if self.title is None and self.description is None and self.price is None and self.stock is None:
            raise ValueError("At least one of title, description, price, stock must be provided.")
        return self


def _normalize_status_filter(status_value: str | None) -> str | None:
    if status_value is None:
        return None

    normalized = status_value.upper()
    if normalized not in PRODUCT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_product_status",
                "message": f"status must be one of: {', '.join(sorted(PRODUCT_STATUSES))}",
            },
        )
    return normalized


def _find_product(product_id: str, seller_id: str) -> dict[str, Any]:
    for product in DATA_STORE["products"]:
        if product["product_id"] == product_id and product.get("seller_id") == seller_id:
            return product

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "product_not_found", "message": f"Product not found: {product_id}"},
    )


@router.get("/list")
def list_products(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    offset: Annotated[int, Query(ge=0)] = 0,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    normalized_status = _normalize_status_filter(status_filter)

    with DATA_LOCK:
        products = [
            deepcopy(product)
            for product in DATA_STORE["products"]
            if product.get("seller_id") == seller_id
            and (normalized_status is None or product.get("status") == normalized_status)
        ]

    return {
        "total_count": len(products),
        "offset": offset,
        "page_size": page_size,
        "products": products[offset : offset + page_size],
    }


@router.get("/detail")
def get_product_detail(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    product_id: Annotated[str, Query(min_length=1)],
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    with DATA_LOCK:
        product = deepcopy(_find_product(product_id, seller_id))

    return {"product": product}


@router.get("/price")
def get_product_price(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    product_id: Annotated[str, Query(min_length=1)],
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    with DATA_LOCK:
        product = deepcopy(_find_product(product_id, seller_id))

    return {
        "product_id": product["product_id"],
        "sku": product["sku"],
        "title": product["title"],
        "price": product["price"],
        "pending_price": product.get("pending_price"),
        "currency": product["currency"],
        "status": product["status"],
        "updated_at": product["updated_at"],
    }


@router.put("/update")
def update_product(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    product_id: Annotated[str, Query(min_length=1)],
    body: ProductUpdateRequest,
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    now = utc_now_iso()

    with DATA_LOCK:
        product = _find_product(product_id, seller_id)
        if product.get("status") == "DELETED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "product_deleted", "message": "Deleted products cannot be updated."},
            )

        if body.title is not None:
            product["title"] = body.title
            product["title_en"] = body.title
        if body.description is not None:
            product["description"] = body.description
            product["description_en"] = body.description
        if body.stock is not None:
            product["stock"] = body.stock
            if product.get("status") in {"LIVE", "SOLD_OUT"}:
                product["status"] = "LIVE" if body.stock > 0 else "SOLD_OUT"

        price_pending = False
        audit_item: dict[str, Any] | None = None

        if body.price is not None:
            old_price = float(product["price"])
            price_delta = abs(body.price - old_price) / old_price if old_price > 0 else 1.0
            if price_delta > 0.10:
                price_pending = True
                audit_item = {
                    "audit_id": f"AUD-{uuid.uuid4().hex[:12].upper()}",
                    "type": "PRICE_CHANGE",
                    "product_id": product_id,
                    "seller_id": seller_id,
                    "old_price": old_price,
                    "requested_price": body.price,
                    "change_rate": round(price_delta, 4),
                    "status": "PENDING",
                    "created_at": now,
                }
                AUDIT_QUEUE.append(audit_item)
                product["pending_price"] = body.price
            else:
                product["price"] = body.price
                product.pop("pending_price", None)

        product["updated_at"] = now
        save_collection("products")

        response_product = deepcopy(product)

    if price_pending:
        return {
            "status": "pending_approval",
            "message": "Price change exceeds 10%; the requested price was added to the audit queue and is not active yet.",
            "product": response_product,
            "audit": audit_item,
        }

    return {"status": "success", "product": response_product}


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_product(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    body: ProductCreateRequest,
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    now = utc_now_iso()
    product = {
        "product_id": f"P{uuid.uuid4().hex[:8].upper()}",
        "seller_id": seller_id,
        "sku": f"SKU-{uuid.uuid4().hex[:8].upper()}",
        "category": body.category,
        "title": body.title,
        "title_en": body.title,
        "title_zh": body.title,
        "description": body.description,
        "description_en": body.description,
        "description_zh": body.description,
        "price": body.price,
        "currency": body.currency,
        "stock": body.stock,
        "status": "LIVE" if body.stock > 0 else "SOLD_OUT",
        "images": body.images,
        "created_at": now,
        "updated_at": now,
    }

    with DATA_LOCK:
        DATA_STORE["products"].append(product)
        save_collection("products")

    return {"status": "success", "product": product}


@router.delete("/delete")
def delete_product(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    product_id: Annotated[str, Query(min_length=1)],
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    now = utc_now_iso()

    with DATA_LOCK:
        product = _find_product(product_id, seller_id)
        product["status"] = "DELETED"
        product["updated_at"] = now
        save_collection("products")
        response_product = deepcopy(product)

    return {"status": "success", "product": response_product}
