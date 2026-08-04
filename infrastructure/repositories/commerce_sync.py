from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from domain.models import FulfillmentMode, InventoryPosition, Listing, Money, Order
from infrastructure.database.models import (
    InventoryPositionRow,
    ListingRow,
    OrderLineRow,
    OrderRow,
    ProductRow,
    SyncCursorRow,
    VariantRow,
)


class CommerceSyncRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def upsert_order(self, order: Order) -> Order:
        with self.session_factory.begin() as session:
            row = session.scalar(select(OrderRow).where(
                OrderRow.tenant_id == order.tenant_id,
                OrderRow.channel_connection_id == order.channel_connection_id,
                OrderRow.external_order_id == order.external_order_id,
            ))
            if row is None:
                row = OrderRow(id=order.id, tenant_id=order.tenant_id, channel_connection_id=order.channel_connection_id, external_order_id=order.external_order_id)
                session.add(row)
            row.marketplace_id = order.marketplace_id
            row.status = order.status.value
            row.fulfillment_mode = order.fulfillment_mode.value
            row.platform_status = order.platform_status
            row.purchased_at = order.purchased_at
            row.last_updated_at = order.last_updated_at
            row.raw_object_key = order.raw_object_key
            row.version = max(row.version or 0, order.version)
            row.platform_extensions = order.platform_extensions
            session.flush()
            session.execute(delete(OrderLineRow).where(OrderLineRow.tenant_id == order.tenant_id, OrderLineRow.order_id == row.id))
            for line in order.lines:
                session.add(OrderLineRow(
                    id=str(uuid4()), tenant_id=order.tenant_id, order_id=row.id,
                    external_line_id=line.external_line_id, seller_sku=line.seller_sku,
                    title=line.title, quantity=line.quantity,
                    unit_price_amount=line.unit_price.amount if line.unit_price else None,
                    unit_price_currency=line.unit_price.currency if line.unit_price else None,
                ))
            order.id = row.id
        return order

    def upsert_inventory(self, position: InventoryPosition) -> InventoryPosition:
        with self.session_factory.begin() as session:
            row = session.scalar(select(InventoryPositionRow).where(
                InventoryPositionRow.tenant_id == position.tenant_id,
                InventoryPositionRow.channel_connection_id == position.channel_connection_id,
                InventoryPositionRow.marketplace_id == position.marketplace_id,
                InventoryPositionRow.seller_sku == position.seller_sku,
                InventoryPositionRow.fulfillment_mode == position.fulfillment_mode.value,
                InventoryPositionRow.warehouse_code == position.warehouse_code,
            ))
            if row is None:
                row = InventoryPositionRow(
                    id=position.id, tenant_id=position.tenant_id,
                    channel_connection_id=position.channel_connection_id,
                    marketplace_id=position.marketplace_id, seller_sku=position.seller_sku,
                    fulfillment_mode=position.fulfillment_mode.value, warehouse_code=position.warehouse_code,
                )
                session.add(row)
            row.available = position.available
            row.reserved = position.reserved
            row.inbound = position.inbound
            row.unsellable = position.unsellable
            row.synced_at = position.synced_at
            row.platform_extensions = position.platform_extensions
            session.flush()
            position.id = row.id
        return position

    def upsert_listing(self, listing: Listing) -> Listing:
        with self.session_factory.begin() as session:
            variant = session.scalar(select(VariantRow).where(VariantRow.tenant_id == listing.tenant_id, VariantRow.sku == listing.seller_sku))
            if variant is None:
                product_id = str(uuid4())
                variant_id = str(uuid4())
                session.add(ProductRow(id=product_id, tenant_id=listing.tenant_id, name=listing.title or listing.seller_sku, brand=None, version=1))
                variant = VariantRow(id=variant_id, tenant_id=listing.tenant_id, product_id=product_id, sku=listing.seller_sku, attributes={})
                session.add(variant)
                session.flush()
            row = session.scalar(select(ListingRow).where(
                ListingRow.tenant_id == listing.tenant_id,
                ListingRow.channel_connection_id == listing.channel_connection_id,
                ListingRow.marketplace_id == listing.marketplace_id,
                ListingRow.seller_sku == listing.seller_sku,
            ))
            if row is None:
                row = ListingRow(
                    id=listing.id, tenant_id=listing.tenant_id,
                    channel_connection_id=listing.channel_connection_id,
                    variant_id=variant.id, marketplace_id=listing.marketplace_id,
                    seller_sku=listing.seller_sku,
                )
                session.add(row)
            row.external_product_id = listing.external_product_id
            row.title = listing.title
            row.price_amount = listing.price.amount if listing.price else None
            row.price_currency = listing.price.currency if listing.price else None
            row.status = listing.status
            row.content_version = max(row.content_version or 0, listing.content_version)
            row.platform_extensions = listing.platform_extensions
            session.flush()
            listing.id = row.id
            listing.variant_id = variant.id
        return listing

    def order_ids(self, *, tenant_id: str, connection_id: str) -> set[str]:
        with self.session_factory() as session:
            return set(session.scalars(select(OrderRow.external_order_id).where(OrderRow.tenant_id == tenant_id, OrderRow.channel_connection_id == connection_id)))

    def get_order_context(self, *, tenant_id: str, external_order_id: str) -> dict[str, object] | None:
        with self.session_factory() as session:
            row = session.scalar(select(OrderRow).where(
                OrderRow.tenant_id == tenant_id,
                OrderRow.external_order_id == external_order_id,
            ))
            if row is None:
                return None
            lines = list(session.scalars(select(OrderLineRow).where(
                OrderLineRow.tenant_id == tenant_id,
                OrderLineRow.order_id == row.id,
            )))
            return {
                "id": row.id,
                "external_order_id": row.external_order_id,
                "marketplace_id": row.marketplace_id,
                "status": row.status,
                "platform_status": row.platform_status,
                "fulfillment_mode": row.fulfillment_mode,
                "purchased_at": row.purchased_at.isoformat() if row.purchased_at else None,
                "last_updated_at": row.last_updated_at.isoformat() if row.last_updated_at else None,
                "lines": [
                    {
                        "seller_sku": line.seller_sku,
                        "title": line.title,
                        "quantity": line.quantity,
                        "unit_price": (
                            {"amount": str(line.unit_price_amount), "currency": line.unit_price_currency}
                            if line.unit_price_amount is not None and line.unit_price_currency else None
                        ),
                    }
                    for line in lines
                ],
                "platform_metadata": row.platform_extensions or {},
            }

    def inventory_snapshot(self, *, tenant_id: str, connection_id: str) -> dict[str, dict[str, int]]:
        with self.session_factory() as session:
            rows = list(session.scalars(select(InventoryPositionRow).where(InventoryPositionRow.tenant_id == tenant_id, InventoryPositionRow.channel_connection_id == connection_id)))
        return {row.seller_sku: {"available": row.available, "reserved": row.reserved, "inbound": row.inbound, "unsellable": row.unsellable} for row in rows}

    def listing_snapshot(self, *, tenant_id: str, connection_id: str) -> dict[str, dict[str, str | None]]:
        with self.session_factory() as session:
            rows = list(session.scalars(select(ListingRow).where(ListingRow.tenant_id == tenant_id, ListingRow.channel_connection_id == connection_id)))
        return {row.seller_sku: {"status": row.status, "title": row.title, "external_product_id": row.external_product_id} for row in rows}
    def managed_skus(self, *, tenant_id: str, connection_id: str, marketplace_id: str) -> list[str]:
        with self.session_factory() as session:
            inventory = list(session.scalars(select(InventoryPositionRow.seller_sku).where(
                InventoryPositionRow.tenant_id == tenant_id,
                InventoryPositionRow.channel_connection_id == connection_id,
                InventoryPositionRow.marketplace_id == marketplace_id,
            )))
            listings = list(session.scalars(select(ListingRow.seller_sku).where(
                ListingRow.tenant_id == tenant_id,
                ListingRow.channel_connection_id == connection_id,
                ListingRow.marketplace_id == marketplace_id,
            )))
        return sorted({sku for sku in [*inventory, *listings] if sku})

    def list_inventory_positions(self, *, tenant_id: str, connection_id: str, marketplace_id: str, seller_skus: list[str] | None = None) -> list[InventoryPosition]:
        statement = select(InventoryPositionRow).where(
            InventoryPositionRow.tenant_id == tenant_id,
            InventoryPositionRow.channel_connection_id == connection_id,
            InventoryPositionRow.marketplace_id == marketplace_id,
        )
        if seller_skus:
            statement = statement.where(InventoryPositionRow.seller_sku.in_(seller_skus))
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(InventoryPositionRow.seller_sku)))
        return [InventoryPosition(
            id=row.id, tenant_id=row.tenant_id, channel_connection_id=row.channel_connection_id,
            seller_sku=row.seller_sku, marketplace_id=row.marketplace_id,
            fulfillment_mode=FulfillmentMode(row.fulfillment_mode), available=row.available,
            reserved=row.reserved, inbound=row.inbound, unsellable=row.unsellable,
            warehouse_code=row.warehouse_code, synced_at=_as_utc(row.synced_at),
            platform_extensions=row.platform_extensions or {},
        ) for row in rows]

    def list_listings(self, *, tenant_id: str, connection_id: str, marketplace_id: str, seller_skus: list[str] | None = None) -> list[Listing]:
        statement = select(ListingRow).where(
            ListingRow.tenant_id == tenant_id,
            ListingRow.channel_connection_id == connection_id,
            ListingRow.marketplace_id == marketplace_id,
        )
        if seller_skus:
            statement = statement.where(ListingRow.seller_sku.in_(seller_skus))
        with self.session_factory() as session:
            rows = list(session.scalars(statement.order_by(ListingRow.seller_sku)))
        return [Listing(
            id=row.id, tenant_id=row.tenant_id, channel_connection_id=row.channel_connection_id,
            variant_id=row.variant_id, marketplace_id=row.marketplace_id, seller_sku=row.seller_sku,
            external_product_id=row.external_product_id, title=row.title,
            price=Money(row.price_amount, row.price_currency) if row.price_amount is not None and row.price_currency else None,
            status=row.status, content_version=row.content_version, platform_extensions=row.platform_extensions or {},
        ) for row in rows]

    def sales_summary(self, *, tenant_id: str, connection_id: str, marketplace_id: str, calculated_at: datetime) -> dict[str, dict[str, object]]:
        cutoff = calculated_at - timedelta(days=30)
        with self.session_factory() as session:
            rows = list(session.execute(
                select(OrderRow, OrderLineRow).join(OrderLineRow, OrderLineRow.order_id == OrderRow.id).where(
                    OrderRow.tenant_id == tenant_id,
                    OrderRow.channel_connection_id == connection_id,
                    OrderRow.marketplace_id == marketplace_id,
                    OrderRow.purchased_at >= cutoff,
                    OrderRow.status != "canceled",
                )
            ))
        summary: dict[str, dict[str, object]] = {}
        for order, line in rows:
            purchased_at = _as_utc(order.purchased_at) if order.purchased_at else calculated_at
            age_days = (calculated_at - purchased_at).total_seconds() / 86400
            item = summary.setdefault(line.seller_sku, {"sales_7_days": 0, "sales_14_days": 0, "sales_30_days": 0, "units": 0, "item_sales": Decimal("0"), "currency": line.unit_price_currency or "USD"})
            item["sales_30_days"] = int(item["sales_30_days"]) + line.quantity
            item["units"] = int(item["units"]) + line.quantity
            if age_days <= 14:
                item["sales_14_days"] = int(item["sales_14_days"]) + line.quantity
            if age_days <= 7:
                item["sales_7_days"] = int(item["sales_7_days"]) + line.quantity
            if line.unit_price_amount is not None:
                item["item_sales"] = Decimal(str(item["item_sales"])) + Decimal(str(line.unit_price_amount)) * line.quantity
        return summary

    def unknown_orders(self, *, tenant_id: str, connection_id: str, marketplace_id: str) -> list[dict[str, str]]:
        with self.session_factory() as session:
            rows = list(session.scalars(select(OrderRow).where(
                OrderRow.tenant_id == tenant_id,
                OrderRow.channel_connection_id == connection_id,
                OrderRow.marketplace_id == marketplace_id,
                OrderRow.status == "unknown",
            )))
        return [{"external_order_id": row.external_order_id, "platform_status": row.platform_status or "Unknown"} for row in rows]


class SyncCursorRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def mark_started(self, *, tenant_id: str, connection_id: str, resource_type: str) -> SyncCursorRow:
        return self._update(tenant_id=tenant_id, connection_id=connection_id, resource_type=resource_type, status="running", started=True)

    def mark_succeeded(self, *, tenant_id: str, connection_id: str, resource_type: str, cursor: str | None, item_count: int) -> SyncCursorRow:
        return self._update(tenant_id=tenant_id, connection_id=connection_id, resource_type=resource_type, status="succeeded", cursor=cursor, item_count=item_count, succeeded=True)

    def mark_failed(self, *, tenant_id: str, connection_id: str, resource_type: str, error: Exception) -> SyncCursorRow:
        return self._update(tenant_id=tenant_id, connection_id=connection_id, resource_type=resource_type, status="failed", failed=True, error=error)

    def list_status(self, *, tenant_id: str, connection_id: str | None = None) -> list[SyncCursorRow]:
        statement = select(SyncCursorRow).where(SyncCursorRow.tenant_id == tenant_id)
        if connection_id:
            statement = statement.where(SyncCursorRow.channel_connection_id == connection_id)
        with self.session_factory() as session:
            return list(session.scalars(statement.order_by(SyncCursorRow.updated_at.desc())))

    def _update(self, *, tenant_id: str, connection_id: str, resource_type: str, status: str, cursor: str | None = None, item_count: int = 0, started: bool = False, succeeded: bool = False, failed: bool = False, error: Exception | None = None) -> SyncCursorRow:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(select(SyncCursorRow).where(
                SyncCursorRow.tenant_id == tenant_id,
                SyncCursorRow.channel_connection_id == connection_id,
                SyncCursorRow.resource_type == resource_type,
            ))
            if row is None:
                row = SyncCursorRow(id=str(uuid4()), tenant_id=tenant_id, channel_connection_id=connection_id, resource_type=resource_type)
                session.add(row)
            row.status = status
            row.updated_at = now
            if started:
                row.last_started_at = now
                row.error_code = None
                row.error_message = None
            if succeeded:
                row.cursor = cursor
                row.item_count = item_count
                row.last_succeeded_at = now
                row.failure_count = 0
            if failed:
                row.last_failed_at = now
                row.failure_count = (row.failure_count or 0) + 1
                row.error_code = type(error).__name__ if error else "SyncError"
                row.error_message = str(error)[:1000] if error else "Synchronization failed"
            session.flush()
            session.expunge(row)
        return row


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
