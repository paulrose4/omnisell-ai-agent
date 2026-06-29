from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from config import DATA_LOCK, DATA_STORE, save_collection, utc_now_iso
from oauth_server import require_bearer_token


router = APIRouter(prefix="/api/v2/order", tags=["orders"])

ORDER_STATUSES = {
    "UNPAID",
    "READY_TO_SHIP",
    "SHIPPED",
    "COMPLETED",
    "CANCELLED",
    "RETURN_REQUESTED",
    "RETURN_APPROVED",
    "REFUNDED",
}

ALLOWED_TRANSITIONS = {
    "UNPAID": {"READY_TO_SHIP", "CANCELLED"},
    "READY_TO_SHIP": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"COMPLETED", "RETURN_REQUESTED"},
    "COMPLETED": {"RETURN_REQUESTED"},
    "RETURN_REQUESTED": {"RETURN_APPROVED", "COMPLETED"},
    "RETURN_APPROVED": {"REFUNDED"},
    "REFUNDED": set(),
    "CANCELLED": set(),
}


def _normalize_order_status(status_value: str | None) -> str | None:
    if status_value is None:
        return None

    normalized = status_value.upper()
    if normalized not in ORDER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_order_status",
                "message": f"status must be one of: {', '.join(sorted(ORDER_STATUSES))}",
            },
        )
    return normalized


def _find_order(order_id: str, seller_id: str) -> dict[str, Any]:
    for order in DATA_STORE["orders"]:
        if order["order_id"] == order_id and order.get("seller_id") == seller_id:
            return order

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "order_not_found", "message": f"Order not found: {order_id}"},
    )


@router.get("/list")
def list_orders(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    offset: Annotated[int, Query(ge=0)] = 0,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    normalized_status = _normalize_order_status(status_filter)

    with DATA_LOCK:
        orders = [
            deepcopy(order)
            for order in DATA_STORE["orders"]
            if order.get("seller_id") == seller_id
            and (normalized_status is None or order.get("status") == normalized_status)
        ]

    return {
        "total_count": len(orders),
        "offset": offset,
        "page_size": page_size,
        "orders": orders[offset : offset + page_size],
    }


@router.put("/status")
def update_order_status(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    order_id: Annotated[str, Query(min_length=1)],
    status_value: Annotated[str, Query(alias="status", min_length=1)],
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    next_status = _normalize_order_status(status_value)
    now = utc_now_iso()

    with DATA_LOCK:
        order = _find_order(order_id, seller_id)
        current_status = str(order["status"])

        if next_status != current_status and next_status not in ALLOWED_TRANSITIONS[current_status]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_status_transition",
                    "message": f"Order status cannot transition from {current_status} to {next_status}.",
                    "allowed_next_statuses": sorted(ALLOWED_TRANSITIONS[current_status]),
                },
            )

        order["status"] = next_status
        order["updated_at"] = now

        if next_status == "CANCELLED":
            order["payment_status"] = "CANCELLED"
        elif next_status == "COMPLETED":
            order["payment_status"] = "PAID"
        elif next_status == "REFUNDED":
            order["payment_status"] = "REFUNDED"

        save_collection("orders")
        response_order = deepcopy(order)

    return {"status": "success", "order": response_order}


@router.post("/return/approve")
def approve_return(
    payload: Annotated[dict[str, Any], Depends(require_bearer_token)],
    return_id: Annotated[str, Query(min_length=1)],
) -> dict[str, Any]:
    seller_id = str(payload["sub"])
    now = utc_now_iso()

    with DATA_LOCK:
        matched_order: dict[str, Any] | None = None
        for order in DATA_STORE["orders"]:
            return_request = order.get("return_request")
            if (
                order.get("seller_id") == seller_id
                and isinstance(return_request, dict)
                and return_request.get("return_id") == return_id
            ):
                matched_order = order
                break

        if not matched_order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "return_not_found", "message": f"Return request not found: {return_id}"},
            )

        if matched_order["status"] not in {"RETURN_REQUESTED", "RETURN_APPROVED"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_return_status",
                    "message": f"Order {matched_order['order_id']} is not waiting for return approval.",
                },
            )

        return_request = matched_order["return_request"]
        return_request["status"] = "APPROVED"
        return_request["approved_at"] = now
        matched_order["status"] = "RETURN_APPROVED"
        matched_order["updated_at"] = now
        save_collection("orders")
        response_order = deepcopy(matched_order)

    return {"status": "success", "order": response_order}
