from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from domain.models import InventoryPosition, Listing, Order


@dataclass
class Page:
    items: list[Any]
    next_token: str | None = None


@dataclass
class AuthorizationResult:
    selling_partner_id: str
    refresh_token: str
    marketplace_ids: list[str] = field(default_factory=list)


@dataclass
class PlatformEvent:
    external_event_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]


@runtime_checkable
class CatalogPort(Protocol):
    async def list_listings(self, *, marketplace_id: str, next_token: str | None = None) -> Page: ...
    async def get_listing(self, *, seller_sku: str, marketplace_id: str) -> Listing: ...
    async def update_listing(self, *, seller_sku: str, marketplace_id: str, changes: dict[str, Any], idempotency_key: str) -> Listing: ...


@runtime_checkable
class OrderPort(Protocol):
    async def list_orders(self, *, marketplace_ids: list[str], updated_after: datetime, next_token: str | None = None) -> Page: ...
    async def get_order(self, *, external_order_id: str) -> Order: ...
    async def update_order_status(self, *, external_order_id: str, status: str, idempotency_key: str) -> Order: ...


@runtime_checkable
class InventoryPort(Protocol):
    async def list_inventory(self, *, marketplace_id: str, next_token: str | None = None) -> Page: ...
    async def adjust_inventory(self, *, seller_sku: str, marketplace_id: str, quantity: int, idempotency_key: str) -> InventoryPosition: ...


@runtime_checkable
class FulfillmentPort(Protocol):
    async def get_fulfillment(self, *, external_order_id: str) -> dict[str, Any]: ...


@runtime_checkable
class NotificationPort(Protocol):
    async def create_subscription(self, *, notification_type: str, destination_id: str) -> dict[str, Any]: ...
    async def normalize_event(self, payload: dict[str, Any]) -> PlatformEvent: ...


@runtime_checkable
class AuthorizationPort(Protocol):
    def authorization_url(self, *, state: str, version: str = "beta") -> str: ...
    async def exchange_code(self, *, authorization_code: str) -> AuthorizationResult: ...
    async def refresh_access_token(self, *, refresh_token: str) -> str: ...
    async def revoke(self, *, refresh_token: str) -> None: ...


@runtime_checkable
class MessagingPort(Protocol):
    async def create_draft(self, *, external_order_id: str, body: str, language: str) -> dict[str, Any]: ...
