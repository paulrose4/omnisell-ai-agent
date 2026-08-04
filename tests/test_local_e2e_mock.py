from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi.testclient import TestClient

from local_e2e_mock.server import app


def test_mock_cloud_container_packages_high_fidelity_generator() -> None:
    compose = Path("docker-compose.e2e.yml").read_text(encoding="utf-8")
    dockerfile = Path("local_e2e_mock/Dockerfile").read_text(encoding="utf-8")

    assert "build: {context: ., dockerfile: local_e2e_mock/Dockerfile}" in compose
    assert "application ./application" in dockerfile
    assert "local_e2e_mock ./local_e2e_mock" in dockerfile


def test_mock_oidc_discovery_and_signed_token() -> None:
    client = TestClient(app)
    discovery = client.get("/.well-known/openid-configuration")
    assert discovery.status_code == 200
    assert discovery.json()["jwks_uri"].endswith("/.well-known/jwks.json")
    token = client.post("/oidc/token", data={"tenant_id": "tenant-a", "subject": "owner-a"})
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"
    assert token.json()["access_token"].count(".") == 2
    claims = jwt.decode(token.json()["access_token"], options={"verify_signature": False})
    assert claims["amr"] == ["pwd", "mfa"]

    verifier_without_nonce = "local-pkce-verifier-without-nonce-0123456789012345"
    challenge_without_nonce = base64.urlsafe_b64encode(hashlib.sha256(verifier_without_nonce.encode()).digest()).rstrip(b"=").decode()
    authorization_without_nonce = client.get(
        "/oidc/authorize",
        params={
            "client_id": "omnisell-ops",
            "redirect_uri": "http://localhost:3000/api/auth/callback/oidc",
            "response_type": "code",
            "scope": "openid profile email",
            "code_challenge": challenge_without_nonce,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code_without_nonce = parse_qs(urlparse(authorization_without_nonce.headers["location"]).query)["code"][0]
    tokens_without_nonce = client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code_without_nonce,
            "redirect_uri": "http://localhost:3000/api/auth/callback/oidc",
            "client_id": "omnisell-ops",
            "code_verifier": verifier_without_nonce,
        },
    )
    claims_without_nonce = jwt.decode(tokens_without_nonce.json()["id_token"], options={"verify_signature": False})
    assert "nonce" not in claims_without_nonce


def test_mock_amazon_lwa_orders_inventory_and_listing() -> None:
    client = TestClient(app)
    exchange = client.post("/auth/o2/token", data={"grant_type": "authorization_code", "code": "local-spapi-code"})
    assert exchange.status_code == 200
    headers = {"x-amz-access-token": exchange.json()["access_token"]}

    orders = client.get("/orders/v0/orders", headers=headers, params={"MarketplaceIds": "ATVPDKIKX0DER", "LastUpdatedAfter": "2026-07-01T00:00:00Z"})
    assert orders.status_code == 200
    assert len(orders.json()["payload"]["Orders"]) == 50
    assert orders.json()["payload"]["Orders"][0]["OmniSellMockMetadata"]["data_source"] == "mock_amazon"

    inventory = client.get("/fba/inventory/v1/summaries", headers=headers, params={"granularityType": "Marketplace", "granularityId": "ATVPDKIKX0DER", "marketplaceIds": "ATVPDKIKX0DER"})
    assert inventory.status_code == 200
    assert inventory.json()["payload"]["inventorySummaries"][0]["sellerSku"] == "MOCK-HEALTHY"
    assert inventory.json()["payload"]["inventorySummaries"][0]["OmniSellMockMetadata"]["verified_amazon_data"] is False

    listing = client.get("/listings/2021-08-01/items/A1LOCALMOCKSELLER/MOCK-HEALTHY", headers=headers, params={"marketplaceIds": "ATVPDKIKX0DER"})
    assert listing.status_code == 200
    assert listing.json()["summaries"][0]["asin"] == "B000000001"
    assert listing.json()["summaries"][0]["lastUpdatedDate"]


def test_mock_scenario_trigger_changes_baseline_and_emits_event() -> None:
    client = TestClient(app)
    client.post("/mock/scenarios/reset")
    baseline = client.get("/mock/scenarios").json()["items"]
    stockout = next(item for item in baseline if item["scenario_id"] == "critical_stockout")
    assert stockout["available"] == 35
    triggered = client.post("/mock/scenarios/critical_stockout/trigger")
    assert triggered.status_code == 200
    assert triggered.json()["item"]["available"] == 2
    assert triggered.json()["event"]["event_type"] == "FBA_INVENTORY_AVAILABILITY_CHANGES"
    assert triggered.json()["item"]["data_source"] == "mock_amazon"
    assert triggered.json()["item"]["verified_amazon_data"] is False


def test_mock_spapi_rejects_missing_access_token() -> None:
    client = TestClient(app)
    response = client.get("/orders/v0/orders", params={"MarketplaceIds": "ATVPDKIKX0DER"})
    assert response.status_code == 401


def test_high_fidelity_store_loads_through_mock_spapi_with_pagination() -> None:
    client = TestClient(app)
    loaded = client.post("/mock/high-fidelity/load", json={
        "seed": 20260722, "days": 30, "sku_count": 20, "order_count": 240,
        "campaign_count": 10, "search_term_count": 80, "return_count": 30,
        "message_count": 40, "review_count": 20, "customer_case_count": 24,
        "business_context_count": 12, "reference_time": "2026-07-22T04:00:00Z",
    })
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["counts"]["orders"] == 240
    assert loaded.json()["verified_amazon_data"] is False

    exchange = client.post("/auth/o2/token", data={"grant_type": "authorization_code", "code": "local-spapi-code"})
    headers = {"x-amz-access-token": exchange.json()["access_token"]}
    first_page = client.get("/orders/v0/orders", headers=headers, params={"MarketplaceIds": "ATVPDKIKX0DER"})
    assert first_page.status_code == 200
    payload = first_page.json()["payload"]
    assert len(payload["Orders"]) == 50
    assert payload["NextToken"] == "50"
    assert payload["Orders"][0]["verified_amazon_data"] is False
    second_page = client.get("/orders/v0/orders", headers=headers, params={"MarketplaceIds": "ATVPDKIKX0DER", "NextToken": payload["NextToken"]})
    assert second_page.status_code == 200
    assert second_page.json()["payload"]["Orders"][0]["AmazonOrderId"] != payload["Orders"][0]["AmazonOrderId"]

    inventory = client.get("/fba/inventory/v1/summaries", headers=headers, params={
        "granularityType": "Marketplace", "granularityId": "ATVPDKIKX0DER", "marketplaceIds": "ATVPDKIKX0DER",
    })
    assert inventory.status_code == 200
    assert inventory.json()["payload"]["inventorySummaries"][0]["sellerSku"].startswith("HF-")
    assert inventory.json()["payload"]["inventorySummaries"][0]["OmniSellMockMetadata"]["verified_amazon_data"] is False

    quality_events = client.get("/mock/high-fidelity/data-quality-events")
    assert quality_events.status_code == 200
    assert quality_events.json()["count"] == 5
    assert all(item["verified_amazon_data"] is False for item in quality_events.json()["items"])
    action_trials = client.get("/mock/high-fidelity/action-trials")
    assert action_trials.status_code == 200
    assert action_trials.json()["count"] == 4
    assert all(item["amazon_write_performed"] is False for item in action_trials.json()["items"])

    client.post("/mock/scenarios/reset")


def test_mock_oidc_authorization_code_pkce_flow() -> None:
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlparse

    client = TestClient(app)
    discovery = client.get("/.well-known/openid-configuration").json()
    assert discovery["authorization_endpoint"].endswith("/oidc/authorize")
    assert "authorization_code" in discovery["grant_types_supported"]

    verifier = "local-pkce-verifier-012345678901234567890123456789"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    authorization = client.get(
        "/oidc/authorize",
        params={
            "client_id": "omnisell-ops",
            "redirect_uri": "http://localhost:3000/api/auth/callback/oidc",
            "response_type": "code",
            "scope": "openid profile email",
            "state": "state-1",
            "nonce": "nonce-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert authorization.status_code == 302
    callback = urlparse(authorization.headers["location"])
    params = parse_qs(callback.query)
    assert params["state"] == ["state-1"]

    tokens = client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": "http://localhost:3000/api/auth/callback/oidc",
            "client_id": "omnisell-ops",
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    payload = tokens.json()
    assert payload["access_token"].count(".") == 2
    claims = jwt.decode(payload["id_token"], options={"verify_signature": False})
    assert claims["aud"] == "omnisell-ops"
    assert claims["nonce"] == "nonce-1"
    assert claims["amr"] == ["pwd", "mfa"]
