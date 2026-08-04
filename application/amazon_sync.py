from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from commerce.errors import ResourceNotFound, ValidationFailed
from infrastructure.repositories.commerce_sync import CommerceSyncRepository, SyncCursorRepository


class AmazonReadSyncService:
    def __init__(
        self,
        *,
        adapter: Any,
        connection: Any,
        commerce_repository: CommerceSyncRepository,
        cursor_repository: SyncCursorRepository,
    ) -> None:
        self.adapter = adapter
        self.connection = connection
        self.commerce_repository = commerce_repository
        self.cursor_repository = cursor_repository

    async def sync(self, resources: tuple[str, ...] = ("orders", "inventory", "listings")) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for resource in resources:
            self.cursor_repository.mark_started(
                tenant_id=self.connection.tenant_id, connection_id=self.connection.id, resource_type=resource
            )
            try:
                sync_result = await getattr(self, f"_sync_{resource}")()
                details = sync_result if isinstance(sync_result, dict) else {"item_count": sync_result}
                count = int(details["item_count"])
                cursor = datetime.now(UTC).isoformat()
                self.cursor_repository.mark_succeeded(
                    tenant_id=self.connection.tenant_id,
                    connection_id=self.connection.id,
                    resource_type=resource,
                    cursor=cursor,
                    item_count=count,
                )
                results[resource] = {"status": "succeeded", **details, "cursor": cursor}
            except Exception as exc:
                self.cursor_repository.mark_failed(
                    tenant_id=self.connection.tenant_id,
                    connection_id=self.connection.id,
                    resource_type=resource,
                    error=exc,
                )
                results[resource] = {"status": "failed", "error_code": type(exc).__name__}
        return results

    async def _sync_orders(self) -> int:
        total = 0
        next_token = None
        updated_after = datetime.now(UTC) - timedelta(days=30)
        while True:
            page = await self.adapter.list_orders(
                marketplace_ids=list(self.connection.marketplace_ids),
                updated_after=updated_after,
                next_token=next_token,
            )
            for order in page.items:
                order.tenant_id = self.connection.tenant_id
                order.channel_connection_id = self.connection.id
                self.commerce_repository.upsert_order(order)
                total += 1
            next_token = page.next_token
            if not next_token:
                return total

    async def _sync_inventory(self) -> int:
        total = 0
        for marketplace_id in self.connection.marketplace_ids:
            next_token = None
            while True:
                page = await self.adapter.list_inventory(marketplace_id=marketplace_id, next_token=next_token)
                for position in page.items:
                    position.tenant_id = self.connection.tenant_id
                    position.channel_connection_id = self.connection.id
                    position.marketplace_id = marketplace_id
                    self.commerce_repository.upsert_inventory(position)
                    total += 1
                next_token = page.next_token
                if not next_token:
                    break
        return total

    async def _sync_listings(self) -> dict[str, Any]:
        total = 0
        skipped_items: list[dict[str, str]] = []
        for marketplace_id in self.connection.marketplace_ids:
            skus = self.commerce_repository.managed_skus(
                tenant_id=self.connection.tenant_id,
                connection_id=self.connection.id,
                marketplace_id=marketplace_id,
            )
            for seller_sku in skus:
                try:
                    listing = await self.adapter.get_listing(seller_sku=seller_sku, marketplace_id=marketplace_id)
                except (ResourceNotFound, ValidationFailed) as exc:
                    skipped_items.append(
                        {
                            "marketplace_id": marketplace_id,
                            "seller_sku": seller_sku,
                            "error_code": exc.code,
                        }
                    )
                    continue
                listing.tenant_id = self.connection.tenant_id
                listing.channel_connection_id = self.connection.id
                self.commerce_repository.upsert_listing(listing)
                total += 1
        return {"item_count": total, "skipped_items": skipped_items}
