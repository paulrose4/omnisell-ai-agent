from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from commerce.errors import (
    AuthorizationExpired,
    Conflict,
    PlatformUnavailable,
    RateLimited,
    ResourceNotFound,
    ValidationFailed,
)
from commerce.ports import AuthorizationResult, Page, PlatformEvent
from domain.models import FulfillmentMode, InventoryPosition, Listing, Money, Order, OrderLine, OrderStatus
from observability.telemetry import amazon_api_calls, amazon_api_duration, trace_attributes, tracer
from safety.amazon_write_gate import AmazonWriteGate

from .rate_limit import OperationRateLimiter
from .secrets import SecretStore


@dataclass(frozen=True)
class AmazonSPAPIConfig:
    lwa_client_id: str
    lwa_client_secret: str
    application_id: str
    redirect_uri: str
    lwa_token_url: str = "https://api.amazon.com/auth/o2/token"
    lwa_revoke_url: str = "https://api.amazon.com/auth/o2/token/revoke"
    endpoint: str = "https://sellingpartnerapi-na.amazon.com"
    seller_central_url: str = "https://sellercentral.amazon.com"
    region: str = "na"
    timeout_seconds: float = 30.0
    max_retries: int = 4


_RATE_DEFAULTS: dict[str, tuple[float, int]] = {
    "getOrders": (0.0167, 20),
    "getOrder": (0.5, 30),
    "getListingsItem": (5.0, 10),
    "patchListingsItem": (5.0, 10),
    "getInventorySummaries": (2.0, 2),
    "createSubscription": (1.0, 5),
}


class AmazonSPAPIAdapter:
    """Amazon US SP-API adapter implementing platform-neutral commerce ports."""

    def __init__(
        self,
        *,
        config: AmazonSPAPIConfig,
        tenant_id: str,
        selling_partner_id: str,
        marketplace_ids: list[str],
        refresh_token_ref: str,
        secret_store: SecretStore,
        client: httpx.AsyncClient | None = None,
        rate_limiter: OperationRateLimiter | None = None,
        write_gate: AmazonWriteGate | None = None,
    ) -> None:
        self.config = config
        self.tenant_id = tenant_id
        self.selling_partner_id = selling_partner_id
        self.marketplace_ids = marketplace_ids
        self.refresh_token_ref = refresh_token_ref
        self.secret_store = secret_store
        self.client = client
        self.rate_limiter = rate_limiter or OperationRateLimiter()
        self.write_gate = write_gate or AmazonWriteGate()
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def authorization_url(self, *, state: str, version: str = "beta") -> str:
        query = urlencode({"application_id": self.config.application_id, "state": state, "version": version})
        return f"{self.config.seller_central_url.rstrip('/')}/apps/authorize/consent?{query}"

    async def exchange_code(self, *, authorization_code: str) -> AuthorizationResult:
        response = await self._http().post(
            self.config.lwa_token_url,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "client_id": self.config.lwa_client_id,
                "client_secret": self.config.lwa_client_secret,
                "redirect_uri": self.config.redirect_uri,
            },
        )
        data = self._parse_lwa_response(response)
        return AuthorizationResult(
            selling_partner_id=self.selling_partner_id,
            refresh_token=str(data["refresh_token"]),
            marketplace_ids=list(self.marketplace_ids),
        )

    async def refresh_access_token(self, *, refresh_token: str) -> str:
        response = await self._http().post(
            self.config.lwa_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.config.lwa_client_id,
                "client_secret": self.config.lwa_client_secret,
            },
        )
        data = self._parse_lwa_response(response)
        self._access_token = str(data["access_token"])
        self._access_token_expires_at = asyncio.get_running_loop().time() + max(
            int(data.get("expires_in", 3600)) - 60, 60
        )
        return self._access_token

    async def revoke(self, *, refresh_token: str) -> None:
        response = await self._http().post(
            self.config.lwa_revoke_url,
            data={
                "token": refresh_token,
                "client_id": self.config.lwa_client_id,
                "client_secret": self.config.lwa_client_secret,
            },
        )
        if response.status_code >= 400:
            raise AuthorizationExpired("Amazon authorization could not be revoked")
        self._access_token = None
        self._access_token_expires_at = 0.0

    async def list_orders(
        self, *, marketplace_ids: list[str], updated_after: datetime, next_token: str | None = None
    ) -> Page:
        params: dict[str, Any] = {"MarketplaceIds": ",".join(marketplace_ids)}
        if next_token:
            params = {"NextToken": next_token}
        else:
            params["LastUpdatedAfter"] = self._iso(updated_after)
        payload = await self._request("GET", "/orders/v0/orders", operation="getOrders", params=params)
        body = payload.get("payload", payload)
        orders = [self._map_order(item) for item in body.get("Orders", [])]
        return Page(items=orders, next_token=body.get("NextToken"))

    async def get_order(self, *, external_order_id: str) -> Order:
        payload = await self._request("GET", f"/orders/v0/orders/{external_order_id}", operation="getOrder")
        return self._map_order(payload.get("payload", payload))

    async def update_order_status(self, *, external_order_id: str, status: str, idempotency_key: str) -> Order:
        raise ValidationFailed(
            "Direct Amazon order-status updates are not enabled for the MVP",
            issues=[{"field": "status", "code": "unsupported_operation"}],
        )

    async def get_listing(self, *, seller_sku: str, marketplace_id: str) -> Listing:
        payload = await self._request(
            "GET",
            f"/listings/2021-08-01/items/{self.selling_partner_id}/{seller_sku}",
            operation="getListingsItem",
            params={"marketplaceIds": marketplace_id, "includedData": "summaries,attributes,issues,offers"},
        )
        return self._map_listing(payload, seller_sku=seller_sku, marketplace_id=marketplace_id)

    async def list_listings(self, *, marketplace_id: str, next_token: str | None = None) -> Page:
        raise ValidationFailed(
            "Amazon Listings Items API does not provide a generic list operation; use synced SKU mappings or reports"
        )

    async def update_listing(
        self, *, seller_sku: str, marketplace_id: str, changes: dict[str, Any], idempotency_key: str
    ) -> Listing:
        self.write_gate.require_write(action="update_listing", tenant_id=self.tenant_id, resource_id=seller_sku)
        patches = self._listing_patches(changes)
        payload = await self._request(
            "PATCH",
            f"/listings/2021-08-01/items/{self.selling_partner_id}/{seller_sku}",
            operation="patchListingsItem",
            params={"marketplaceIds": marketplace_id},
            json_body={"productType": changes.get("product_type", "PRODUCT"), "patches": patches},
            idempotency_key=idempotency_key,
        )
        issues = payload.get("issues", [])
        if any(issue.get("severity") == "ERROR" for issue in issues):
            raise ValidationFailed("Amazon rejected the listing update", issues=issues)
        return await self.get_listing(seller_sku=seller_sku, marketplace_id=marketplace_id)

    async def list_inventory(self, *, marketplace_id: str, next_token: str | None = None) -> Page:
        params: dict[str, Any] = {
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": marketplace_id,
        }
        if next_token:
            params["nextToken"] = next_token
        else:
            params["details"] = "true"
        payload = await self._request(
            "GET", "/fba/inventory/v1/summaries", operation="getInventorySummaries", params=params
        )
        body = payload.get("payload", payload)
        items = [self._map_inventory(item, marketplace_id) for item in body.get("inventorySummaries", [])]
        return Page(items=items, next_token=(body.get("pagination") or {}).get("nextToken"))

    async def adjust_inventory(
        self, *, seller_sku: str, marketplace_id: str, quantity: int, idempotency_key: str
    ) -> InventoryPosition:
        raise ValidationFailed("FBA inventory is read-only in the MVP")

    async def get_fulfillment(self, *, external_order_id: str) -> dict[str, Any]:
        order = await self.get_order(external_order_id=external_order_id)
        return {
            "external_order_id": external_order_id,
            "mode": order.fulfillment_mode.value,
            "status": order.status.value,
        }

    async def create_subscription(self, *, notification_type: str, destination_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/notifications/v1/subscriptions/{notification_type}",
            operation="createSubscription",
            json_body={"payloadVersion": "1.0", "destinationId": destination_id},
            idempotency_key=str(uuid4()),
        )

    async def normalize_event(self, payload: dict[str, Any]) -> PlatformEvent:
        notification = payload.get("notificationMetadata", {})
        event_id = str(
            notification.get("notificationId") or payload.get("notificationId") or self._stable_event_id(payload)
        )
        event_type = str(notification.get("notificationType") or payload.get("notificationType") or "UNKNOWN")
        timestamp = payload.get("eventTime") or notification.get("publishTime")
        occurred_at = self._parse_datetime(timestamp) or datetime.now(UTC)
        return PlatformEvent(
            external_event_id=event_id, event_type=event_type, occurred_at=occurred_at, payload=payload
        )

    async def create_draft(self, *, external_order_id: str, body: str, language: str) -> dict[str, Any]:
        return {"external_order_id": external_order_id, "body": body, "language": language, "status": "draft_only"}

    async def close(self) -> None:
        if self.client is None:
            return
        await self.client.aclose()

    async def _access_token_value(self) -> str:
        now = asyncio.get_running_loop().time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        refresh_token = self.secret_store.get(tenant_id=self.tenant_id, secret_ref=self.refresh_token_ref)
        return await self.refresh_access_token(refresh_token=refresh_token)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            self.write_gate.require_write(action=operation, tenant_id=self.tenant_id, resource_id=path)
        with tracer.start_as_current_span(f"amazon.sp_api.{operation}") as operation_span:
            operation_span.set_attributes(
                trace_attributes(
                    tenant_id=self.tenant_id,
                    selling_partner_id=self.selling_partner_id,
                    amazon_region=self.config.region,
                    amazon_operation=operation,
                    http_method=method,
                    http_route=path,
                    idempotency_key=idempotency_key,
                )
            )
            rate, burst = _RATE_DEFAULTS.get(operation, (1.0, 1))
            await self.rate_limiter.acquire(
                (self.selling_partner_id, self.config.application_id, operation, self.config.region),
                rate=rate,
                burst=burst,
            )
            headers = {
                "x-amz-access-token": await self._access_token_value(),
                "accept": "application/json",
                "user-agent": "OmniSell/0.2",
            }
            if idempotency_key:
                headers["x-amz-idempotency-key"] = idempotency_key
            for attempt in range(self.config.max_retries + 1):
                started = time.perf_counter()
                with tracer.start_as_current_span("amazon.sp_api.attempt") as attempt_span:
                    attempt_span.set_attributes(
                        trace_attributes(operation=operation, attempt=attempt + 1, http_method=method, http_route=path)
                    )
                    response = await self._http().request(
                        method,
                        f"{self.config.endpoint.rstrip('/')}{path}",
                        params=params,
                        json=json_body,
                        headers=headers,
                    )
                    latency_ms = (time.perf_counter() - started) * 1000
                    retry_after = self._retry_after(response) if response.status_code == 429 else None
                    attributes = trace_attributes(
                        operation=operation,
                        status_code=response.status_code,
                        attempt=attempt + 1,
                        region=self.config.region,
                    )
                    attempt_span.set_attributes(
                        {
                            **attributes,
                            "http.response.status_code": response.status_code,
                            "amazon.retry_after_seconds": retry_after or 0,
                        }
                    )
                    amazon_api_duration.record(latency_ms, attributes)
                    amazon_api_calls.add(1, attributes)
                if response.status_code < 400:
                    return response.json() if response.content else {}
                if response.status_code == 401:
                    self._access_token = None
                    if attempt == 0:
                        headers["x-amz-access-token"] = await self._access_token_value()
                        continue
                    raise AuthorizationExpired("Amazon authorization expired or was revoked")
                if response.status_code == 404:
                    raise ResourceNotFound("Amazon resource not found")
                if response.status_code == 409:
                    raise Conflict("Amazon reported a conflicting update")
                if response.status_code == 429:
                    if attempt < self.config.max_retries:
                        operation_span.add_event(
                            "amazon_rate_limited",
                            trace_attributes(attempt=attempt + 1, retry_after_seconds=retry_after or 0),
                        )
                        await asyncio.sleep(retry_after or min(2**attempt, 30))
                        continue
                    raise RateLimited("Amazon SP-API rate limit exceeded", retry_after)
                if response.status_code >= 500:
                    if attempt < self.config.max_retries:
                        operation_span.add_event(
                            "amazon_platform_retry",
                            trace_attributes(attempt=attempt + 1, status_code=response.status_code),
                        )
                        await asyncio.sleep(min(2**attempt, 30))
                        continue
                    raise PlatformUnavailable("Amazon SP-API is unavailable")
                issues = self._safe_issues(response)
                raise ValidationFailed("Amazon rejected the request", issues=issues)
            raise PlatformUnavailable("Amazon request failed after retries")

    def _http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self.client

    @staticmethod
    def _parse_lwa_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise AuthorizationExpired("Amazon Login with Amazon token exchange failed")
        data = response.json()
        if "access_token" not in data:
            raise AuthorizationExpired("Amazon token response was incomplete")
        return data

    def _map_order(self, data: dict[str, Any]) -> Order:
        fulfillment = FulfillmentMode.FBA if data.get("FulfillmentChannel") == "AFN" else FulfillmentMode.FBM
        platform_status = str(data.get("OrderStatus", "Unknown"))
        status_map = {
            "Pending": OrderStatus.PENDING,
            "Unshipped": OrderStatus.UNSHIPPED,
            "PartiallyShipped": OrderStatus.PARTIALLY_SHIPPED,
            "Shipped": OrderStatus.SHIPPED,
            "Canceled": OrderStatus.CANCELED,
        }
        lines = [
            OrderLine(
                seller_sku=str(item.get("SellerSKU", "")),
                quantity=int(item.get("QuantityOrdered", 0)),
                title=item.get("Title"),
                external_line_id=item.get("OrderItemId"),
                unit_price=self._money(item.get("ItemPrice"), divisor=max(int(item.get("QuantityOrdered", 1)), 1)),
            )
            for item in data.get("OrderItems", [])
        ]
        purchased_at = self._parse_datetime(data.get("PurchaseDate"))
        last_updated_at = self._parse_datetime(data.get("LastUpdateDate"))
        return Order(
            tenant_id=self.tenant_id,
            channel_connection_id="",
            external_order_id=str(data.get("AmazonOrderId", "")),
            marketplace_id=str(data.get("MarketplaceId", self.marketplace_ids[0] if self.marketplace_ids else "")),
            status=status_map.get(platform_status, OrderStatus.UNKNOWN),
            fulfillment_mode=fulfillment,
            lines=lines,
            purchased_at=purchased_at,
            last_updated_at=last_updated_at,
            platform_status=platform_status,
            platform_extensions=self._source_metadata(
                data,
                observed_at=last_updated_at or purchased_at,
                resource_type="order",
                resource_id=str(data.get("AmazonOrderId", "")),
            ),
        )

    def _map_listing(self, data: dict[str, Any], *, seller_sku: str, marketplace_id: str) -> Listing:
        summaries = data.get("summaries") or []
        summary = summaries[0] if summaries else {}
        offers = data.get("offers") or []
        offer = offers[0] if offers else {}
        price = self._money(offer.get("price"))
        observed_at = self._parse_datetime(
            summary.get("lastUpdatedDate")
            or summary.get("lastUpdatedTime")
            or data.get("lastUpdatedDate")
            or data.get("lastUpdatedTime")
        )
        raw_status = summary.get("status", data.get("status", "unknown"))
        if isinstance(raw_status, list):
            raw_status = next((item for item in raw_status if item), "unknown")
        return Listing(
            tenant_id=self.tenant_id,
            channel_connection_id="",
            variant_id="",
            marketplace_id=marketplace_id,
            seller_sku=seller_sku,
            external_product_id=summary.get("asin"),
            title=summary.get("itemName"),
            price=price,
            status=str(raw_status).lower(),
            platform_extensions={
                "issues": data.get("issues", []),
                "attributes": data.get("attributes", {}),
                **self._source_metadata(data, observed_at=observed_at, resource_type="listing", resource_id=seller_sku),
            },
        )

    def _map_inventory(self, data: dict[str, Any], marketplace_id: str) -> InventoryPosition:
        details = data.get("inventoryDetails") or {}
        observed_at = self._parse_datetime(data.get("lastUpdatedTime"))
        synced_at = observed_at or datetime.now(UTC)
        return InventoryPosition(
            tenant_id=self.tenant_id,
            channel_connection_id="",
            seller_sku=str(data.get("sellerSku", "")),
            marketplace_id=marketplace_id,
            fulfillment_mode=FulfillmentMode.FBA,
            available=int(details.get("fulfillableQuantity", data.get("totalQuantity", 0)) or 0),
            reserved=int((details.get("reservedQuantity") or {}).get("totalReservedQuantity", 0) or 0),
            inbound=int(
                (details.get("inboundWorkingQuantity", 0) or 0)
                + (details.get("inboundShippedQuantity", 0) or 0)
                + (details.get("inboundReceivingQuantity", 0) or 0)
            ),
            unsellable=int((details.get("unfulfillableQuantity") or {}).get("totalUnfulfillableQuantity", 0) or 0),
            synced_at=synced_at,
            platform_extensions=self._source_metadata(
                data,
                observed_at=observed_at,
                resource_type="inventory",
                resource_id=str(data.get("sellerSku", "")),
            ),
        )

    @staticmethod
    def _source_metadata(
        data: dict[str, Any],
        *,
        observed_at: datetime | None,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        collected_at = datetime.now(UTC)
        mock_metadata = dict(data.get("OmniSellMockMetadata") or {})
        if mock_metadata:
            return {
                **mock_metadata,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "observed_at": observed_at.isoformat() if observed_at else None,
                "collected_at": collected_at.isoformat(),
                "verified_amazon_data": bool(mock_metadata.get("verified_amazon_data", False)),
                "data_source": mock_metadata.get("data_source", "mock_amazon"),
            }
        return {
            "data_source": "amazon_api",
            "verified_amazon_data": True,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "observed_at": observed_at.isoformat() if observed_at else None,
            "collected_at": collected_at.isoformat(),
        }

    @staticmethod
    def _listing_patches(changes: dict[str, Any]) -> list[dict[str, Any]]:
        allowed = {
            "title": "/attributes/item_name",
            "price": "/attributes/purchasable_offer",
            "description": "/attributes/product_description",
        }
        patches = []
        for field, path in allowed.items():
            if field in changes:
                patches.append({"op": "replace", "path": path, "value": changes[field]})
        if not patches:
            raise ValidationFailed("No supported listing changes were supplied")
        return patches

    @staticmethod
    def _money(data: dict[str, Any] | None, divisor: int = 1) -> Money | None:
        if not data:
            return None
        amount = data.get("Amount", data.get("amount"))
        currency = data.get("CurrencyCode", data.get("currencyCode"))
        if amount is None or not currency:
            return None
        return Money(Decimal(str(amount)) / divisor, str(currency))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return max(float(value), 0)
        except ValueError:
            try:
                return max((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds(), 0)
            except ValueError:
                return None

    @staticmethod
    def _safe_issues(response: httpx.Response) -> list[dict[str, Any]]:
        try:
            data = response.json()
        except ValueError:
            return []
        errors = data.get("errors", data.get("issues", [])) if isinstance(data, dict) else []
        return [{"code": item.get("code"), "message": item.get("message")} for item in errors if isinstance(item, dict)]

    @staticmethod
    def _stable_event_id(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def verify_sns_signature(payload: bytes, signature: str, shared_secret: str) -> bool:
    expected = hmac.new(shared_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
