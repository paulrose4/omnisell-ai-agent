from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commerce.adapters.amazon import AmazonSPAPIAdapter, AmazonSPAPIConfig
from commerce.adapters.amazon.secrets import InMemorySecretStore
from safety.amazon_write_gate import AmazonWriteBlocked, AmazonWriteGate


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _hash_id(value: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:16]


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


class RecordingAsyncClient:
    def __init__(self, timeout: float) -> None:
        import httpx

        self.inner = httpx.AsyncClient(timeout=timeout)
        self.statuses: Counter[str] = Counter()
        self.routes: Counter[str] = Counter()

    async def request(self, method: str, url: str, **kwargs: Any):
        response = await self.inner.request(method, url, **kwargs)
        self.statuses[str(response.status_code)] += 1
        self.routes[url.split("?", 1)[0]] += 1
        return response

    async def post(self, url: str, **kwargs: Any):
        response = await self.inner.post(url, **kwargs)
        self.statuses[str(response.status_code)] += 1
        self.routes[url.split("?", 1)[0]] += 1
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def _require_configuration() -> dict[str, str]:
    required = {
        "AMAZON_LWA_CLIENT_ID": os.getenv("AMAZON_LWA_CLIENT_ID", ""),
        "AMAZON_LWA_CLIENT_SECRET": os.getenv("AMAZON_LWA_CLIENT_SECRET", ""),
        "AMAZON_REFRESH_TOKEN": os.getenv("AMAZON_REFRESH_TOKEN", ""),
        "AMAZON_SELLING_PARTNER_ID": os.getenv("AMAZON_SELLING_PARTNER_ID", ""),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Missing required staging variables: {', '.join(missing)}")
    if not _enabled("AMAZON_PILOT_READ_ONLY") or _enabled("AMAZON_WRITES_ENABLED"):
        raise SystemExit("Require AMAZON_PILOT_READ_ONLY=true and AMAZON_WRITES_ENABLED=false.")
    return required


def _sample(item: Any, *, resource_type: str, salt: str) -> dict[str, Any]:
    extensions = getattr(item, "platform_extensions", {}) or {}
    if resource_type == "order":
        resource_id = str(getattr(item, "external_order_id", ""))
        observed_at = getattr(item, "last_updated_at", None) or getattr(item, "purchased_at", None)
        fields = {
            "status": getattr(item, "platform_status", None),
            "marketplace_id": getattr(item, "marketplace_id", None),
        }
    elif resource_type == "inventory":
        resource_id = str(getattr(item, "seller_sku", ""))
        observed_at = extensions.get("observed_at")
        fields = {
            "available": getattr(item, "available", None),
            "reserved": getattr(item, "reserved", None),
            "inbound": getattr(item, "inbound", None),
            "unsellable": getattr(item, "unsellable", None),
        }
    else:
        resource_id = str(getattr(item, "seller_sku", ""))
        observed_at = extensions.get("observed_at")
        fields = {
            "asin_present": bool(getattr(item, "external_product_id", None)),
            "status": getattr(item, "status", None),
            "title_present": bool(getattr(item, "title", None)),
        }
    return {
        "resource_id_hash": _hash_id(resource_id, salt),
        "resource_type": resource_type,
        "observed_at": _iso(observed_at) if not isinstance(observed_at, str) else observed_at,
        "collected_at": extensions.get("collected_at"),
        "data_source": extensions.get("data_source"),
        "verified_amazon_data": extensions.get("verified_amazon_data"),
        "source_resource_id_present": bool(extensions.get("resource_id")),
        "fields": fields,
    }


async def _pages(
    adapter: AmazonSPAPIAdapter, *, resource: str, marketplace_id: str, days: int, max_pages: int, salt: str
) -> dict[str, Any]:
    page_count = 0
    item_count = 0
    samples: list[dict[str, Any]] = []
    token = None
    while page_count < max_pages:
        if resource == "orders":
            page = await adapter.list_orders(
                marketplace_ids=[marketplace_id],
                updated_after=datetime.now(UTC) - timedelta(days=days),
                next_token=token,
            )
            resource_type = "order"
        else:
            page = await adapter.list_inventory(marketplace_id=marketplace_id, next_token=token)
            resource_type = "inventory"
        page_count += 1
        item_count += len(page.items)
        for item in page.items[: 3 - len(samples)]:
            samples.append(_sample(item, resource_type=resource_type, salt=salt))
        token = page.next_token
        if not token:
            break
    return {
        "status": "succeeded",
        "page_count": page_count,
        "item_count": item_count,
        "has_next_token_after_limit": bool(token),
        "samples": samples,
    }


async def _run(args: argparse.Namespace) -> int:
    if not _enabled("AMAZON_READONLY_SMOKE"):
        raise SystemExit("Set AMAZON_READONLY_SMOKE=true to run this external validation.")
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        raise SystemExit("Amazon smoke refuses ENVIRONMENT=production.")
    values = _require_configuration()
    marketplaces = [
        item.strip() for item in os.getenv("AMAZON_MARKETPLACE_IDS", "ATVPDKIKX0DER").split(",") if item.strip()
    ]
    listing_skus = [item.strip() for item in os.getenv("AMAZON_SMOKE_LISTING_SKUS", "").split(",") if item.strip()]
    if not listing_skus:
        raise SystemExit("Set AMAZON_SMOKE_LISTING_SKUS to a bounded list of staging SKUs.")
    run_id = f"amazon-readonly-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    secret_store = InMemorySecretStore()
    refresh_ref = secret_store.put(tenant_id="tenant-amazon-staging", value=values["AMAZON_REFRESH_TOKEN"])
    client = RecordingAsyncClient(timeout=float(os.getenv("AMAZON_SMOKE_TIMEOUT_SECONDS", "30")))
    adapter = AmazonSPAPIAdapter(
        config=AmazonSPAPIConfig(
            lwa_client_id=values["AMAZON_LWA_CLIENT_ID"],
            lwa_client_secret=values["AMAZON_LWA_CLIENT_SECRET"],
            application_id=os.getenv("AMAZON_APPLICATION_ID", ""),
            redirect_uri=os.getenv("AMAZON_REDIRECT_URI", ""),
            lwa_token_url=os.getenv("AMAZON_LWA_TOKEN_URL", "https://api.amazon.com/auth/o2/token"),
            lwa_revoke_url=os.getenv("AMAZON_LWA_REVOKE_URL", "https://api.amazon.com/auth/o2/token/revoke"),
            endpoint=os.getenv("AMAZON_SP_API_ENDPOINT", "https://sellingpartnerapi-na.amazon.com"),
            region=os.getenv("AMAZON_REGION", "na"),
            max_retries=int(os.getenv("AMAZON_SMOKE_MAX_RETRIES", "4")),
        ),
        tenant_id="tenant-amazon-staging",
        selling_partner_id=values["AMAZON_SELLING_PARTNER_ID"],
        marketplace_ids=marketplaces,
        refresh_token_ref=refresh_ref,
        secret_store=secret_store,
        client=client,
        write_gate=AmazonWriteGate(global_enabled=False, tenant_enabled=False, pilot_read_only=True),
    )
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "environment": os.getenv("ENVIRONMENT", "development"),
        "tenant_alias": "tenant-amazon-staging",
        "seller_alias": _hash_id(values["AMAZON_SELLING_PARTNER_ID"], run_id),
        "marketplace_ids": marketplaces,
        "writes_enabled": False,
        "pilot_read_only": True,
        "resources": {},
        "checks": {},
    }
    try:
        for marketplace_id in marketplaces:
            manifest["resources"][f"orders:{marketplace_id}"] = await _pages(
                adapter,
                resource="orders",
                marketplace_id=marketplace_id,
                days=int(os.getenv("AMAZON_SMOKE_ORDER_DAYS", "30")),
                max_pages=int(os.getenv("AMAZON_SMOKE_MAX_PAGES", "20")),
                salt=run_id,
            )
            manifest["resources"][f"inventory:{marketplace_id}"] = await _pages(
                adapter,
                resource="inventory",
                marketplace_id=marketplace_id,
                days=0,
                max_pages=int(os.getenv("AMAZON_SMOKE_MAX_PAGES", "20")),
                salt=run_id,
            )
            listing_result = {"status": "succeeded", "item_count": 0, "samples": [], "not_found_count": 0}
            for sku in listing_skus:
                try:
                    listing = await adapter.get_listing(seller_sku=sku, marketplace_id=marketplace_id)
                    listing_result["item_count"] += 1
                    if len(listing_result["samples"]) < 3:
                        listing_result["samples"].append(_sample(listing, resource_type="listing", salt=run_id))
                except Exception as exc:
                    listing_result["not_found_count"] += 1
                    listing_result.setdefault("errors", []).append(type(exc).__name__)
            manifest["resources"][f"listings:{marketplace_id}"] = listing_result

        provenance_samples = [
            sample for resource in manifest["resources"].values() for sample in resource.get("samples", [])
        ]
        manifest["checks"]["provenance"] = {
            "status": "succeeded"
            if provenance_samples
            and all(
                item.get("data_source") == "amazon_api"
                and item.get("verified_amazon_data") is True
                and item.get("source_resource_id_present")
                for item in provenance_samples
            )
            else "failed",
            "sample_count": len(provenance_samples),
            "observed_at_present_count": sum(bool(item.get("observed_at")) for item in provenance_samples),
        }
        manifest["checks"]["pagination"] = {
            "status": "succeeded"
            if any(resource.get("page_count", 0) > 1 for resource in manifest["resources"].values())
            else "not_proven",
            "max_pages": int(os.getenv("AMAZON_SMOKE_MAX_PAGES", "20")),
        }
        try:
            await adapter.update_listing(
                seller_sku=listing_skus[0],
                marketplace_id=marketplaces[0],
                changes={"title": "blocked smoke probe"},
                idempotency_key=f"smoke-block-{run_id}",
            )
            manifest["checks"]["write_gate"] = {"status": "failed", "network_write_attempted": True}
        except AmazonWriteBlocked as exc:
            manifest["checks"]["write_gate"] = {
                "status": "succeeded",
                "blocked_code": exc.code,
                "network_write_attempted": False,
            }
        except Exception as exc:
            manifest["checks"]["write_gate"] = {"status": "failed", "error_type": type(exc).__name__}

        manifest["checks"]["rate_limits_and_retries"] = {
            "status": "observed",
            "http_status_counts": dict(client.statuses),
            "rate_limit_responses": client.statuses.get("429", 0),
            "server_error_responses": sum(client.statuses.get(str(code), 0) for code in (500, 502, 503, 504)),
        }
        manifest["checks"]["token_refresh"] = {
            "status": "succeeded"
            if client.routes.get(os.getenv("AMAZON_LWA_TOKEN_URL", "https://api.amazon.com/auth/o2/token"), 0) >= 1
            else "failed",
            "token_value_logged": False,
        }
        manifest["checks"]["token_revoke"] = {
            "status": "not_run",
            "reason": "Run separately with a disposable staging refresh token and AMAZON_SMOKE_ALLOW_REVOKE=true.",
        }
        if _enabled("AMAZON_SMOKE_ALLOW_REVOKE"):
            if os.getenv("AMAZON_SMOKE_REVOKE_CONFIRMATION") != "REVOKE_DISPOSABLE_STAGING_TOKEN":
                raise SystemExit(
                    "Token revoke requires AMAZON_SMOKE_REVOKE_CONFIRMATION=REVOKE_DISPOSABLE_STAGING_TOKEN."
                )
            await adapter.revoke(values["AMAZON_REFRESH_TOKEN"])
            try:
                await adapter.list_orders(
                    marketplace_ids=[marketplaces[0]], updated_after=datetime.now(UTC) - timedelta(days=1)
                )
            except Exception as exc:
                manifest["checks"]["token_revoke"] = {
                    "status": "succeeded",
                    "post_revoke_error_type": type(exc).__name__,
                }
            else:
                manifest["checks"]["token_revoke"] = {"status": "failed", "post_revoke_request_succeeded": True}
        manifest["checks"]["seller_central_comparison"] = {"status": "pending_operator_signoff"}
        manifest["checks"]["partial_failure_recovery"] = {
            "status": "not_run",
            "reason": "Execute the proxy fault-injection procedure in the external staging runbook.",
        }
    finally:
        await client.aclose()
    manifest["completed_at"] = datetime.now(UTC).isoformat()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        json.dumps(
            {"output": str(output_path), "resource_count": len(manifest["resources"]), "checks": manifest["checks"]},
            ensure_ascii=False,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in Amazon SP-API read-only staging smoke test.")
    parser.add_argument(
        "--output", default=str(PROJECT_ROOT / "artifacts" / "external-validation" / "amazon-readonly-smoke.json")
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
