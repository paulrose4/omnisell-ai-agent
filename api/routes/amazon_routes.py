from __future__ import annotations

from datetime import UTC, datetime, timedelta
from secrets import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.amazon_factory import create_amazon_adapter
from api.security import require_api_key
from domain.models import ChannelConnection, ChannelProvider, ConnectionStatus
from infrastructure.database.session import tenant_context

router = APIRouter(prefix="/api/channels/amazon", tags=["amazon"], dependencies=[Depends(require_api_key)])
callback_router = APIRouter(prefix="/api/channels/amazon", tags=["amazon-callback"])


class AmazonAuthorizeRequest(BaseModel):
    selling_partner_id: str = Field(..., min_length=1, max_length=100)
    marketplace_ids: list[str] = Field(default_factory=lambda: ["ATVPDKIKX0DER"])


def _principal(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "Authentication required."})
    return principal


@router.post("/authorize")
async def authorize(payload: AmazonAuthorizeRequest, request: Request) -> dict[str, Any]:
    principal = _principal(request)
    state = request.app.state.amazon_oauth_state.issue(principal.tenant_id)
    provisional = ChannelConnection(
        tenant_id=principal.tenant_id,
        provider=ChannelProvider.AMAZON,
        selling_partner_id=payload.selling_partner_id,
        region=request.app.state.settings.amazon_region,
        marketplace_ids=tuple(payload.marketplace_ids),
        secret_ref="pending://authorization",
    )
    adapter = create_amazon_adapter(
        settings=request.app.state.settings,
        connection=provisional,
        secret_store=request.app.state.secret_store,
        tenant_repository=request.app.state.tenant_repository,
    )
    return {"authorization_url": adapter.authorization_url(state=state), "state_expires_in": 600}


@callback_router.get("/callback")
async def callback(
    request: Request,
    state: str,
    spapi_oauth_code: str = Query(..., alias="spapi_oauth_code"),
    selling_partner_id: str = Query(..., alias="selling_partner_id"),
) -> dict[str, Any]:
    """Public Amazon browser callback. Tenant identity is recovered only from signed one-time state."""
    try:
        oauth_state = request.app.state.amazon_oauth_state.consume(state)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_oauth_state", "message": "Invalid, expired, or reused OAuth state."},
        ) from exc
    with tenant_context(oauth_state.tenant_id):
        provisional = ChannelConnection(
            tenant_id=oauth_state.tenant_id,
            provider=ChannelProvider.AMAZON,
            selling_partner_id=selling_partner_id,
            region=request.app.state.settings.amazon_region,
            marketplace_ids=tuple(request.app.state.settings.amazon_marketplaces),
            secret_ref="pending://authorization",
        )
        adapter = create_amazon_adapter(
            settings=request.app.state.settings,
            connection=provisional,
            secret_store=request.app.state.secret_store,
            tenant_repository=request.app.state.tenant_repository,
        )
        result = await adapter.exchange_code(authorization_code=spapi_oauth_code)
        secret_ref = request.app.state.secret_store.put(tenant_id=oauth_state.tenant_id, value=result.refresh_token)
        connection = ChannelConnection(
            tenant_id=oauth_state.tenant_id,
            provider=ChannelProvider.AMAZON,
            selling_partner_id=result.selling_partner_id or selling_partner_id,
            region=request.app.state.settings.amazon_region,
            marketplace_ids=tuple(result.marketplace_ids or request.app.state.settings.amazon_marketplaces),
            secret_ref=secret_ref,
            status=ConnectionStatus.ACTIVE,
        )
        connection = request.app.state.tenant_repository.save_connection(connection)
    return {
        "connection_id": connection.id,
        "status": connection.status.value,
        "marketplace_ids": list(connection.marketplace_ids),
    }


def _connection(request: Request, connection_id: str):
    principal = _principal(request)
    connection = request.app.state.tenant_repository.get_connection(
        tenant_id=principal.tenant_id, connection_id=connection_id
    )
    if connection is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Channel connection not found."})
    return connection


@router.get("/connections")
async def list_connections(request: Request) -> dict[str, Any]:
    principal = request.state.principal
    connections = request.app.state.tenant_repository.list_connections(tenant_id=principal.tenant_id)
    cursors = request.app.state.sync_cursor_repository.list_status(tenant_id=principal.tenant_id)
    by_connection: dict[str, list[dict[str, Any]]] = {}
    for cursor in cursors:
        by_connection.setdefault(cursor.channel_connection_id, []).append(
            {
                "resource_type": cursor.resource_type,
                "status": cursor.status,
                "last_succeeded_at": cursor.last_succeeded_at.isoformat().replace("+00:00", "Z")
                if cursor.last_succeeded_at
                else None,
                "failure_count": cursor.failure_count,
                "error_code": cursor.error_code,
            }
        )
    return {
        "total": len(connections),
        "items": [
            {
                "id": item.id,
                "provider": item.provider.value,
                "selling_partner_id": item.selling_partner_id,
                "region": item.region,
                "marketplace_ids": list(item.marketplace_ids),
                "status": item.status.value,
                "version": item.version,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
                "sync": by_connection.get(item.id, []),
            }
            for item in connections
        ],
    }


@router.get("/connections/{connection_id}/health")
async def connection_health(connection_id: str, request: Request) -> dict[str, Any]:
    connection = _connection(request, connection_id)
    cursors = request.app.state.sync_cursor_repository.list_status(
        tenant_id=connection.tenant_id, connection_id=connection.id
    )
    failed = [item for item in cursors if item.status == "failed"]
    return {
        "connection_id": connection.id,
        "authorization_status": connection.status.value,
        "healthy": connection.status.value == "active" and not failed,
        "write_mode": "blocked_read_only_pilot" if request.app.state.settings.amazon_pilot_read_only else "configured",
        "sync": [
            {
                "resource_type": item.resource_type,
                "status": item.status,
                "failure_count": item.failure_count,
                "last_succeeded_at": item.last_succeeded_at.isoformat().replace("+00:00", "Z")
                if item.last_succeeded_at
                else None,
                "error_code": item.error_code,
            }
            for item in cursors
        ],
    }


@router.post("/connections/{connection_id}/reauthorize")
async def reauthorize_connection(connection_id: str, request: Request) -> dict[str, Any]:
    connection = _connection(request, connection_id)
    state = request.app.state.amazon_oauth_state.issue(connection.tenant_id)
    adapter = create_amazon_adapter(
        settings=request.app.state.settings,
        connection=connection,
        secret_store=request.app.state.secret_store,
        tenant_repository=request.app.state.tenant_repository,
    )
    return {
        "connection_id": connection.id,
        "authorization_url": adapter.authorization_url(state=state),
        "state_expires_in_seconds": 600,
    }


@router.get("/{connection_id}/orders")
async def list_orders(
    connection_id: str, request: Request, updated_after: datetime | None = None, next_token: str | None = None
) -> dict[str, Any]:
    connection = _connection(request, connection_id)
    adapter = create_amazon_adapter(
        settings=request.app.state.settings,
        connection=connection,
        secret_store=request.app.state.secret_store,
        tenant_repository=request.app.state.tenant_repository,
    )
    page = await adapter.list_orders(
        marketplace_ids=list(connection.marketplace_ids),
        updated_after=updated_after or datetime.now(UTC) - timedelta(days=1),
        next_token=next_token,
    )
    return {"items": [order.__dict__ for order in page.items], "next_token": page.next_token}


@router.get("/{connection_id}/inventory")
async def list_inventory(
    connection_id: str, request: Request, marketplace_id: str = "ATVPDKIKX0DER", next_token: str | None = None
) -> dict[str, Any]:
    connection = _connection(request, connection_id)
    adapter = create_amazon_adapter(
        settings=request.app.state.settings,
        connection=connection,
        secret_store=request.app.state.secret_store,
        tenant_repository=request.app.state.tenant_repository,
    )
    page = await adapter.list_inventory(marketplace_id=marketplace_id, next_token=next_token)
    return {"items": [item.__dict__ for item in page.items], "next_token": page.next_token}


@router.get("/{connection_id}/listings/{seller_sku}")
async def get_listing(
    connection_id: str, seller_sku: str, request: Request, marketplace_id: str = "ATVPDKIKX0DER"
) -> dict[str, Any]:
    connection = _connection(request, connection_id)
    adapter = create_amazon_adapter(
        settings=request.app.state.settings,
        connection=connection,
        secret_store=request.app.state.secret_store,
        tenant_repository=request.app.state.tenant_repository,
    )
    return (await adapter.get_listing(seller_sku=seller_sku, marketplace_id=marketplace_id)).__dict__


@callback_router.post("/{connection_id}/notifications")
async def receive_notification(
    connection_id: str,
    payload: dict[str, Any],
    request: Request,
    tenant_id: str = Query(..., min_length=1, max_length=36),
    notification_token: str | None = Header(default=None, alias="X-OmniSell-Notification-Token"),
) -> dict[str, Any]:
    """Public notification ingress protected by a secret header and Inbox deduplication."""
    expected = request.app.state.settings.amazon_notification_token
    if not expected or not notification_token or not compare_digest(notification_token, expected):
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Endpoint not found."})
    with tenant_context(tenant_id):
        connection = request.app.state.tenant_repository.get_connection(
            tenant_id=tenant_id, connection_id=connection_id
        )
        if connection is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Endpoint not found."})
        adapter = create_amazon_adapter(
            settings=request.app.state.settings,
            connection=connection,
            secret_store=request.app.state.secret_store,
            tenant_repository=request.app.state.tenant_repository,
        )
        event = await adapter.normalize_event(payload)
        accepted = request.app.state.event_repository.accept_inbox(
            tenant_id=connection.tenant_id,
            provider="amazon",
            external_event_id=event.external_event_id,
            event_type=event.event_type,
            payload={**event.payload, "_connection_id": connection.id},
        )
    return {"accepted": accepted, "event_id": event.external_event_id, "event_type": event.event_type}
