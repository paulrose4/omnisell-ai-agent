from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from uuid import uuid4

import httpx
import psycopg


TENANT_ID = "local-demo"


def expect(response: httpx.Response, status: int = 200) -> dict[str, Any]:
    if response.status_code != status:
        raise RuntimeError(f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text}")
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OmniSell local OIDC + Amazon SP-API E2E smoke test")
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--mock-cloud", default="http://localhost:9000")
    parser.add_argument("--database", default="postgresql://omnisell:local-postgres-password@localhost:55432/omnisell")
    parser.add_argument("--notification-token", default="local-notification-token-change-for-shared-machines")
    args = parser.parse_args()

    with httpx.Client(timeout=20.0, follow_redirects=True, trust_env=False) as client:
        health = expect(client.get(f"{args.api}/api/admin/health"))
        assert health["status"] == "ok"

        token_payload = expect(
            client.post(
                f"{args.mock_cloud}/oidc/token",
                data={"grant_type": "client_credentials", "tenant_id": TENANT_ID, "subject": "local-owner", "role": "owner", "scope": "read write admin"},
            )
        )
        headers = {"Authorization": f"Bearer {token_payload['access_token']}"}

        authorize = expect(
            client.post(
                f"{args.api}/api/channels/amazon/authorize",
                headers=headers,
                json={"selling_partner_id": "A1LOCALMOCKSELLER", "marketplace_ids": ["ATVPDKIKX0DER"]},
            )
        )
        callback = expect(client.get(authorize["authorization_url"]))
        connection_id = callback["connection_id"]
        assert callback["status"] == "active"
        connection_health = expect(client.get(f"{args.api}/api/channels/amazon/connections/{connection_id}/health", headers=headers))
        assert connection_health["write_mode"] == "blocked_read_only_pilot"

        order_items: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            params = {"next_token": next_token} if next_token else None
            order_page = expect(client.get(f"{args.api}/api/channels/amazon/{connection_id}/orders", headers=headers, params=params))
            order_items.extend(order_page["items"])
            next_token = order_page.get("next_token")
            if not next_token:
                break
        expected_order_ids = {"MOCK-MOCK-HEALTHY-00"}
        actual_order_ids = {item["external_order_id"] for item in order_items}
        assert expected_order_ids.issubset(actual_order_ids)

        inventory = expect(client.get(f"{args.api}/api/channels/amazon/{connection_id}/inventory", headers=headers))
        assert inventory["items"][0]["seller_sku"] == "MOCK-HEALTHY"
        assert inventory["items"][0]["available"] == 70

        listing = expect(client.get(f"{args.api}/api/channels/amazon/{connection_id}/listings/MOCK-HEALTHY", headers=headers))
        assert listing["external_product_id"] == "B000000001"
        assert listing["price"]["currency"] == "USD"

        event_id = f"LOCAL-EVENT-{uuid4()}"
        notification = {
            "notificationMetadata": {"notificationId": event_id, "notificationType": "ORDER_CHANGE", "publishTime": "2026-07-13T00:00:00Z"},
            "payload": {"AmazonOrderId": "LOCAL-ORDER-1002"},
        }
        notification_url = f"{args.api}/api/channels/amazon/{connection_id}/notifications?tenant_id=local-demo"
        notification_headers = {**headers, "X-OmniSell-Notification-Token": args.notification_token}
        first = expect(client.post(notification_url, headers=notification_headers, json=notification))
        second = expect(client.post(notification_url, headers=notification_headers, json=notification))
        assert first["accepted"] is True
        assert second["accepted"] is False

    with psycopg.connect(args.database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ciphertext FROM encrypted_secrets WHERE tenant_id = %s", (TENANT_ID,))
            rows = cursor.fetchall()
            assert rows, "No encrypted Amazon refresh token was stored"
            assert all(b"local-lwa-refresh" not in bytes(row[0]) for row in rows)

    print(json.dumps({
        "status": "ok",
        "tenant_id": TENANT_ID,
        "connection_id": connection_id,
        "orders": len(order_items),
        "inventory_sku": inventory["items"][0]["seller_sku"],
        "listing_asin": listing["external_product_id"],
        "notification_deduplicated": True,
        "refresh_token_encrypted": True,
        "amazon_write_mode": "blocked_read_only_pilot",
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"E2E smoke failed: {exc}", file=sys.stderr)
        raise
