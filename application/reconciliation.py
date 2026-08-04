from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from commerce.errors import ResourceNotFound
from domain.models import OperationalIssue


class AmazonReconciliationService:
    def __init__(
        self, *, adapter, connection, commerce_repository, reconciliation_repository, issue_repository
    ) -> None:
        self.adapter = adapter
        self.connection = connection
        self.commerce = commerce_repository
        self.runs = reconciliation_repository
        self.issues = issue_repository

    async def run(self):
        started = datetime.now(UTC)
        differences = []
        provenance = []
        amazon_orders = set()
        token = None
        while True:
            page = await self.adapter.list_orders(
                marketplace_ids=list(self.connection.marketplace_ids),
                updated_after=started - timedelta(days=30),
                next_token=token,
            )
            amazon_orders.update(item.external_order_id for item in page.items)
            provenance.extend(self._provenance(item) for item in page.items)
            token = page.next_token
            if not token:
                break
        internal_orders = self.commerce.order_ids(tenant_id=self.connection.tenant_id, connection_id=self.connection.id)
        for order_id in sorted(amazon_orders ^ internal_orders):
            differences.append(
                {
                    "resource_type": "order",
                    "resource_id": order_id,
                    "difference_type": "missing_internal" if order_id in amazon_orders else "missing_amazon",
                    "amazon_value": {"present": order_id in amazon_orders},
                    "internal_value": {"present": order_id in internal_orders},
                }
            )
        amazon_inventory = {}
        for marketplace in self.connection.marketplace_ids:
            token = None
            while True:
                page = await self.adapter.list_inventory(marketplace_id=marketplace, next_token=token)
                for item in page.items:
                    amazon_inventory[item.seller_sku] = {
                        "available": item.available,
                        "reserved": item.reserved,
                        "inbound": item.inbound,
                        "unsellable": item.unsellable,
                    }
                    provenance.append(self._provenance(item))
                token = page.next_token
                if not token:
                    break
        internal_inventory = self.commerce.inventory_snapshot(
            tenant_id=self.connection.tenant_id, connection_id=self.connection.id
        )
        for sku in sorted(set(amazon_inventory) | set(internal_inventory)):
            if amazon_inventory.get(sku) != internal_inventory.get(sku):
                differences.append(
                    {
                        "resource_type": "inventory",
                        "resource_id": sku,
                        "difference_type": "quantity_mismatch",
                        "amazon_value": amazon_inventory.get(sku),
                        "internal_value": internal_inventory.get(sku),
                    }
                )
        internal_listings = self.commerce.listing_snapshot(
            tenant_id=self.connection.tenant_id, connection_id=self.connection.id
        )
        for marketplace in self.connection.marketplace_ids:
            for sku, internal in internal_listings.items():
                try:
                    listing = await self.adapter.get_listing(seller_sku=sku, marketplace_id=marketplace)
                except ResourceNotFound:
                    differences.append(
                        {
                            "resource_type": "listing",
                            "resource_id": sku,
                            "difference_type": "missing_amazon",
                            "amazon_value": None,
                            "internal_value": internal,
                        }
                    )
                    continue
                provenance.append(self._provenance(listing))
                amazon = {
                    "status": listing.status,
                    "title": listing.title,
                    "external_product_id": listing.external_product_id,
                }
                if amazon != internal:
                    differences.append(
                        {
                            "resource_type": "listing",
                            "resource_id": sku,
                            "difference_type": "content_mismatch",
                            "amazon_value": amazon,
                            "internal_value": internal,
                        }
                    )
        completed = datetime.now(UTC)
        status = "matched" if not differences else "requires_manual_review"
        observed_values = [item["observed_at"] for item in provenance if item["observed_at"]]
        sources = {item["data_source"] for item in provenance}
        verified = bool(provenance) and all(item["verified_amazon_data"] for item in provenance)
        source = (
            "amazon_api"
            if verified and sources == {"amazon_api"}
            else ("mock" if any("mock" in item for item in sources) else "unknown")
        )
        run = self.runs.save(
            tenant_id=self.connection.tenant_id,
            connection_id=self.connection.id,
            status=status,
            summary={
                "amazon_order_count": len(amazon_orders),
                "internal_order_count": len(internal_orders),
                "amazon_inventory_sku_count": len(amazon_inventory),
                "internal_inventory_sku_count": len(internal_inventory),
                "difference_count": len(differences),
                "data_source": source,
                "verified_amazon_data": verified,
                "amazon_observed_at": max(observed_values) if observed_values else None,
                "amazon_collected_at": completed.isoformat(),
                "freshness_status": "fresh" if provenance else "unknown",
            },
            differences=differences,
            started_at=started,
            completed_at=completed,
        )
        if differences:
            issue = OperationalIssue(
                tenant_id=self.connection.tenant_id,
                seller_sku="ACCOUNT",
                marketplace_id=self.connection.marketplace_ids[0],
                issue_type="amazon_reconciliation_mismatch",
                severity="high",
                title="Amazon reconciliation requires review",
                summary=f"Daily reconciliation found {len(differences)} differences.",
                status="open",
                dedupe_key=sha256(
                    f"{self.connection.tenant_id}|reconciliation|{self.connection.id}".encode()
                ).hexdigest(),
                confidence=__import__("decimal").Decimal("1"),
                data_freshness_at=completed,
                evidence=({"run_id": run.id, "difference_count": len(differences)},),
                missing_data=(),
                detected_at=completed,
                last_detected_at=completed,
            )
            self.issues.upsert(issue)
        return run

    @staticmethod
    def _provenance(item):
        metadata = getattr(item, "platform_extensions", {}) or {}
        return {
            "data_source": str(metadata.get("data_source") or "unknown"),
            "verified_amazon_data": bool(metadata.get("verified_amazon_data", False)),
            "observed_at": metadata.get("observed_at"),
        }
