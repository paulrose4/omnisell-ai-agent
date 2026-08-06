from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from application.high_fidelity_mock import (
    HighFidelityStoreConfig,
    HighFidelityStoreDataset,
    generate_high_fidelity_store,
)

ISSUER = os.getenv("MOCK_OIDC_ISSUER", "http://mock-cloud:9000")
INTERNAL_BASE_URL = os.getenv("MOCK_OIDC_INTERNAL_BASE_URL", ISSUER).rstrip("/")
AUDIENCE = os.getenv("MOCK_OIDC_AUDIENCE", "omnisell-api")
AMAZON_CALLBACK_URL = os.getenv("AMAZON_CALLBACK_URL", "http://localhost:8080/api/channels/amazon/callback")
OIDC_REDIRECT_ORIGIN = os.getenv("MOCK_OIDC_REDIRECT_ORIGIN", "http://localhost:3000").rstrip("/")
SELLING_PARTNER_ID = os.getenv("MOCK_SELLING_PARTNER_ID", "A1LOCALMOCKSELLER")
MARKETPLACE_ID = "ATVPDKIKX0DER"
KEY_ID = "local-e2e-key"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_NUMBERS = PRIVATE_KEY.public_key().public_numbers()
AUTHORIZATION_CODES: dict[str, dict[str, str]] = {}
PERSONAS: dict[str, dict[str, str]] = {
    "local-owner": {
        "subject": "local-owner",
        "role": "owner",
        "name": "Local Owner",
        "email": "owner@local.omnisell.test",
        "scope": "read write admin",
    },
    "local-operator": {
        "subject": "local-operator",
        "role": "operator",
        "name": "Local Operator",
        "email": "operator@local.omnisell.test",
        "scope": "read write",
    },
    "local-approver": {
        "subject": "local-approver",
        "role": "approver",
        "name": "Local Approver A",
        "email": "approver@local.omnisell.test",
        "scope": "read write",
    },
    "local-approver-2": {
        "subject": "local-approver-2",
        "role": "approver",
        "name": "Local Approver B",
        "email": "approver-b@local.omnisell.test",
        "scope": "read write",
    },
    "local-viewer": {
        "subject": "local-viewer",
        "role": "viewer",
        "name": "Local Viewer",
        "email": "viewer@local.omnisell.test",
        "scope": "read",
    },
}


def _scenario_defaults() -> dict[str, dict[str, Any]]:
    defaults = [
        ("healthy", "MOCK-HEALTHY", 70, 1, "29.99", "Healthy Mock Product"),
        ("critical_stockout", "MOCK-STOCKOUT", 35, 10, "29.99", "Critical Stockout Product"),
        ("out_of_stock", "MOCK-OOS", 20, 8, "24.99", "Out of Stock Product"),
        ("excess_inventory", "MOCK-EXCESS", 120, 1, "19.99", "Excess Inventory Product"),
        ("contribution_loss", "MOCK-LOSS", 45, 2, "12.00", "Contribution Loss Product"),
        ("missing_cost", "MOCK-MISSING-COST", 50, 1, "26.00", "Missing Cost Product"),
        ("stale_inventory", "MOCK-STALE", 40, 1, "25.00", "Stale Inventory Product"),
        ("listing_missing_fields", "MOCK-LISTING", 35, 1, "22.00", "Listing Baseline Product"),
        ("unknown_order_status", "MOCK-UNKNOWN-ORDER", 30, 1, "31.00", "Unknown Order Status Product"),
        ("inventory_mismatch", "MOCK-MISMATCH", 35, 1, "28.00", "Inventory Mismatch Product"),
    ]
    return {
        scenario_id: {
            "scenario_id": scenario_id,
            "seller_sku": sku,
            "available": available,
            "reserved": 1,
            "inbound": 0,
            "unsellable": 0,
            "daily_sales": daily_sales,
            "price": price,
            "currency": "USD",
            "title": title,
            "listing_status": "BUYABLE",
            "asin": f"B0{index:08d}",
            "last_updated_time": (
                datetime.now(UTC) - (timedelta(hours=12) if scenario_id == "stale_inventory" else timedelta(minutes=5))
            ).isoformat(),
            "data_source": "mock_amazon",
            "verified_amazon_data": False,
        }
        for index, (scenario_id, sku, available, daily_sales, price, title) in enumerate(defaults, start=1)
    }


MOCK_SCENARIOS = _scenario_defaults()
MOCK_FAILURES = {"rate_limited": False, "authorization_revoked": False}
MANUAL_ACTION_RESULTS: dict[str, tuple[str, dict[str, Any]]] = {}
HIGH_FIDELITY_DATASET: HighFidelityStoreDataset | None = None


class HighFidelityLoadRequest(BaseModel):
    seed: int = 20260722
    days: int = Field(default=30, ge=1, le=90)
    sku_count: int = Field(default=120, ge=10, le=500)
    order_count: int = Field(default=12000, ge=100, le=100000)
    campaign_count: int = Field(default=60, ge=1, le=500)
    search_term_count: int = Field(default=3500, ge=10, le=50000)
    return_count: int = Field(default=700, ge=1, le=10000)
    message_count: int = Field(default=1000, ge=1, le=10000)
    review_count: int = Field(default=400, ge=1, le=10000)
    customer_case_count: int = Field(default=350, ge=1, le=5000)
    business_context_count: int = Field(default=75, ge=1, le=5000)
    reference_time: datetime


def _scenario_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_source": "mock_amazon",
        "verified_amazon_data": False,
        "scenario_id": item["scenario_id"],
        "bullet_points": [
            "Food-grade stainless steel construction for daily use.",
            "Insulated design supports hot and cold beverages.",
            "Leak-resistant lid for commuting and travel.",
            "24 oz capacity with care instructions included.",
            "Reusable bottle designed for home, office and gym.",
        ],
        "description": "Mock Amazon product detail content used only for a repeatable Listing operations drill. " * 8,
        "image_count": 7,
        "search_terms": [
            "insulated bottle",
            "stainless steel bottle",
            "travel water bottle",
            "24 oz bottle",
            "reusable flask",
        ],
        "sessions": 500,
        "units_ordered": 50,
        "conversion_rate": "0.10",
        "return_rate": "0.04",
        "ad_spend": "100.00",
        "ad_traffic_share": "0.40",
        "cpc": "1.20",
        "price": item["price"],
        "discount_rate": "0.00",
        "buy_box_share": "0.95",
        "sellable_units": item["available"],
        "out_of_stock_hours": "0",
        "natural_rank": "12",
        "promotion_active": False,
        "seasonality_key": "regular",
        "competitor_change": False,
    }


def _mock_event(item: dict[str, Any], event_type: str) -> dict[str, Any]:
    return {
        "external_event_id": f"mock-{item['scenario_id']}-{time.time_ns()}",
        "event_type": event_type,
        "seller_sku": item["seller_sku"],
        "scenario_id": item["scenario_id"],
    }


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _now() -> datetime:
    return datetime.now(UTC)


def _access_token(tenant_id: str, subject: str, role: str, scopes: str) -> str:
    now = _now()
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": subject,
            "tenant_id": tenant_id,
            "roles": [role],
            "scope": scopes,
            "amr": ["pwd", "mfa"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def _id_token(
    *,
    client_id: str,
    nonce: str,
    subject: str,
    role: str,
    name: str,
    email: str,
    tenant_id: str = "local-demo",
) -> str:
    now = _now()
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": client_id,
        "sub": subject,
        "tenant_id": tenant_id,
        "roles": [role],
        "name": name,
        "email": email,
        "amr": ["pwd", "mfa"],
        "acr": "urn:omnisell:loa:mfa",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    if nonce:
        claims["nonce"] = nonce
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KEY_ID})


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


app = FastAPI(title="OmniSell Local Cloud and Amazon Mock", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "local-e2e-mock"}


@app.get("/.well-known/openid-configuration")
def oidc_configuration() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/oidc/authorize",
        "token_endpoint": f"{INTERNAL_BASE_URL}/oidc/token",
        "userinfo_endpoint": f"{INTERNAL_BASE_URL}/oidc/userinfo",
        "jwks_uri": f"{INTERNAL_BASE_URL}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "grant_types_supported": ["authorization_code", "client_credentials", "password"],
        "scopes_supported": ["openid", "profile", "email"],
        "claims_supported": ["sub", "name", "email", "tenant_id", "roles", "amr", "acr"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get("/.well-known/jwks.json")
def jwks() -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KEY_ID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64(PUBLIC_NUMBERS.n),
                "e": _b64(PUBLIC_NUMBERS.e),
            }
        ]
    }


@app.get("/oidc/authorize")
def oidc_authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    scope: str = "openid profile email",
    state: str = "",
    nonce: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    login_hint: str = "local-owner",
) -> RedirectResponse:
    if response_type != "code":
        raise HTTPException(status_code=400, detail={"error": "unsupported_response_type"})
    if not redirect_uri.startswith(f"{OIDC_REDIRECT_ORIGIN}/api/auth/callback/oidc"):
        raise HTTPException(status_code=400, detail={"error": "invalid_redirect_uri"})
    if code_challenge_method != "S256" or not code_challenge:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_request", "error_description": "PKCE S256 is required"}
        )
    if login_hint not in PERSONAS:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_request", "error_description": "Unknown local persona"}
        )
    code = secrets.token_urlsafe(32)
    AUTHORIZATION_CODES[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "login_hint": login_hint,
    }
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302)


@app.post("/oidc/token")
def oidc_token(
    grant_type: str = Form(default="client_credentials"),
    tenant_id: str = Form(default="local-demo"),
    subject: str = Form(default="local-owner"),
    role: str = Form(default="owner"),
    scope: str = Form(default="read write admin"),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
) -> dict[str, Any]:
    if grant_type == "authorization_code":
        authorization = AUTHORIZATION_CODES.pop(code or "", None)
        if authorization is None:
            raise HTTPException(status_code=400, detail={"error": "invalid_grant"})
        if redirect_uri != authorization["redirect_uri"] or (client_id and client_id != authorization["client_id"]):
            raise HTTPException(status_code=400, detail={"error": "invalid_grant"})
        if not code_verifier or not hmac.compare_digest(
            _pkce_challenge(code_verifier), authorization["code_challenge"]
        ):
            raise HTTPException(
                status_code=400, detail={"error": "invalid_grant", "error_description": "PKCE verification failed"}
            )
        persona = PERSONAS[authorization["login_hint"]]
        access_token = _access_token("local-demo", persona["subject"], persona["role"], persona["scope"])
        return {
            "access_token": access_token,
            "id_token": _id_token(
                client_id=authorization["client_id"],
                nonce=authorization["nonce"],
                subject=persona["subject"],
                role=persona["role"],
                name=persona["name"],
                email=persona["email"],
            ),
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": authorization["scope"],
        }
    if grant_type not in {"client_credentials", "password"}:
        raise HTTPException(status_code=400, detail={"error": "unsupported_grant_type"})
    token = _access_token(tenant_id, subject, role, scope)
    return {"access_token": token, "token_type": "Bearer", "expires_in": 3600, "scope": scope}


@app.get("/oidc/userinfo")
def oidc_userinfo(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    try:
        claims = jwt.decode(authorization.removeprefix("Bearer "), options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"}) from exc
    persona = PERSONAS.get(claims["sub"])
    if persona is None:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    return {
        "sub": claims["sub"],
        "name": persona["name"],
        "email": persona["email"],
        "tenant_id": claims.get("tenant_id", "local-demo"),
        "roles": claims.get("roles", ["owner"]),
    }


@app.get("/apps/authorize/consent")
def seller_consent(state: str, application_id: str = "", version: str = "beta") -> RedirectResponse:
    query = urlencode(
        {"state": state, "spapi_oauth_code": "local-spapi-code", "selling_partner_id": SELLING_PARTNER_ID}
    )
    return RedirectResponse(f"{AMAZON_CALLBACK_URL}?{query}", status_code=302)


@app.post("/auth/o2/token")
def lwa_token(
    grant_type: str = Form(...),
    code: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    redirect_uri: str | None = Form(default=None),
) -> dict[str, Any]:
    if grant_type == "authorization_code":
        if code != "local-spapi-code":
            raise HTTPException(status_code=400, detail={"error": "invalid_grant"})
        return {
            "access_token": "local-lwa-access",
            "refresh_token": "local-lwa-refresh",
            "token_type": "bearer",
            "expires_in": 3600,
        }
    if grant_type == "refresh_token":
        if refresh_token != "local-lwa-refresh":
            raise HTTPException(status_code=400, detail={"error": "invalid_grant"})
        return {"access_token": "local-lwa-access", "token_type": "bearer", "expires_in": 3600}
    raise HTTPException(status_code=400, detail={"error": "unsupported_grant_type"})


@app.post("/auth/o2/token/revoke")
def lwa_revoke(token: str = Form(...)) -> dict[str, bool]:
    return {"revoked": token == "local-lwa-refresh"}


def _authorize_spapi(request: Request) -> None:
    if MOCK_FAILURES["authorization_revoked"]:
        raise HTTPException(
            status_code=401, detail={"errors": [{"code": "Unauthorized", "message": "Mock authorization revoked"}]}
        )
    if MOCK_FAILURES["rate_limited"]:
        MOCK_FAILURES["rate_limited"] = False
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": "1"},
            detail={"errors": [{"code": "QuotaExceeded", "message": "Mock throttling scenario"}]},
        )
    if request.headers.get("x-amz-access-token") != "local-lwa-access":
        raise HTTPException(
            status_code=401, detail={"errors": [{"code": "Unauthorized", "message": "Invalid access token"}]}
        )


@app.get("/mock/scenarios")
def list_mock_scenarios() -> dict[str, Any]:
    return {
        "items": list(MOCK_SCENARIOS.values()),
        "failures": MOCK_FAILURES,
        "high_fidelity": HIGH_FIDELITY_DATASET.manifest() if HIGH_FIDELITY_DATASET else None,
    }


@app.post("/mock/high-fidelity/load")
def load_high_fidelity_store(payload: HighFidelityLoadRequest) -> dict[str, Any]:
    global HIGH_FIDELITY_DATASET
    config = HighFidelityStoreConfig(**payload.model_dump(exclude={"reference_time"}))
    HIGH_FIDELITY_DATASET = generate_high_fidelity_store(config=config, reference_time=payload.reference_time)
    return HIGH_FIDELITY_DATASET.manifest()


@app.get("/mock/high-fidelity/status")
def high_fidelity_status() -> dict[str, Any]:
    return (
        HIGH_FIDELITY_DATASET.manifest()
        if HIGH_FIDELITY_DATASET
        else {
            "loaded": False,
            "source": "mock",
            "verified_amazon_data": False,
        }
    )


@app.get("/mock/high-fidelity/data-quality-events")
def high_fidelity_data_quality_events() -> dict[str, Any]:
    if HIGH_FIDELITY_DATASET is None:
        return {"items": [], "count": 0, "source": "mock", "verified_amazon_data": False}
    return {
        "items": HIGH_FIDELITY_DATASET.data_quality_events,
        "count": len(HIGH_FIDELITY_DATASET.data_quality_events),
        "source": "mock",
        "verified_amazon_data": False,
    }


@app.get("/mock/high-fidelity/action-trials")
def high_fidelity_action_trials() -> dict[str, Any]:
    if HIGH_FIDELITY_DATASET is None:
        return {"items": [], "count": 0, "source": "mock", "verified_amazon_data": False}
    return {
        "items": HIGH_FIDELITY_DATASET.action_trials,
        "count": len(HIGH_FIDELITY_DATASET.action_trials),
        "source": "mock",
        "verified_amazon_data": False,
    }


@app.post("/mock/scenarios/reset")
def reset_mock_scenarios() -> dict[str, Any]:
    global HIGH_FIDELITY_DATASET
    MOCK_SCENARIOS.clear()
    MOCK_SCENARIOS.update(_scenario_defaults())
    MOCK_FAILURES.update({"rate_limited": False, "authorization_revoked": False})
    MANUAL_ACTION_RESULTS.clear()
    HIGH_FIDELITY_DATASET = None
    return {"status": "reset", "items": list(MOCK_SCENARIOS.values())}


@app.post("/mock/scenarios/{scenario_id}/trigger")
def trigger_mock_scenario(scenario_id: str) -> dict[str, Any]:
    item = MOCK_SCENARIOS.get(scenario_id)
    if item is None:
        if scenario_id == "amazon_429":
            MOCK_FAILURES["rate_limited"] = True
            return {"status": "triggered", "event": None}
        if scenario_id == "token_revoked":
            MOCK_FAILURES["authorization_revoked"] = True
            return {"status": "triggered", "event": None}
        raise HTTPException(status_code=404, detail={"error": "scenario_not_found"})
    if scenario_id == "critical_stockout":
        item.update({"available": 2, "inbound": 0})
    elif scenario_id == "out_of_stock":
        item.update({"available": 0, "inbound": 0})
    elif scenario_id == "excess_inventory":
        item.update({"available": 500})
    elif scenario_id == "listing_missing_fields":
        item.update({"title": ""})
    elif scenario_id == "inventory_mismatch":
        item.update({"available": item["available"] + 17})
    item["last_updated_time"] = _now().isoformat()
    if scenario_id == "listing_missing_fields":
        event_type = "LISTINGS_ITEM_STATUS_CHANGE"
    elif scenario_id == "unknown_order_status":
        event_type = "ORDER_CHANGE"
    else:
        event_type = "FBA_INVENTORY_AVAILABILITY_CHANGES"
    return {"status": "triggered", "item": item, "event": _mock_event(item, event_type)}


@app.post("/mock/seller-central/actions")
async def execute_mock_seller_central_action(
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> dict[str, Any]:
    payload = await request.json()
    request_digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    existing = MANUAL_ACTION_RESULTS.get(idempotency_key)
    if existing is not None:
        existing_digest, existing_result = existing
        if existing_digest != request_digest:
            raise HTTPException(status_code=409, detail={"error": "idempotency_conflict"})
        return json.loads(json.dumps(existing_result))
    sku = str(payload.get("seller_sku") or "")
    item = next((value for value in MOCK_SCENARIOS.values() if value["seller_sku"] == sku), None)
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "sku_not_found"})
    action = str(payload.get("action_type") or "")
    changes = dict(payload.get("changes") or {})
    before = dict(item)
    event_type = "FBA_INVENTORY_AVAILABILITY_CHANGES"
    if action == "create_replenishment_plan":
        item["inbound"] = int(changes.get("inbound_quantity") or 0)
    elif action == "update_listing_title":
        item["title"] = str(changes.get("title") or "")
        event_type = "LISTINGS_ITEM_STATUS_CHANGE"
    elif action == "adjust_fbm_inventory":
        item["available"] = int(changes.get("available") or 0)
    elif action == "update_price":
        item["price"] = str(changes.get("price") or item["price"])
        event_type = "ANY_OFFER_CHANGED"
    elif action != "mark_not_executed":
        raise HTTPException(status_code=422, detail={"error": "unsupported_mock_action"})
    item["last_updated_time"] = _now().isoformat()
    result = {"status": "recorded", "before": before, "after": dict(item), "event": _mock_event(item, event_type)}
    MANUAL_ACTION_RESULTS[idempotency_key] = (request_digest, result)
    return result


@app.get("/orders/v0/orders")
def list_orders(
    request: Request,
    MarketplaceIds: str | None = None,
    LastUpdatedAfter: str | None = None,
    NextToken: str | None = None,
) -> dict[str, Any]:
    _authorize_spapi(request)
    if HIGH_FIDELITY_DATASET is not None:
        orders = list(HIGH_FIDELITY_DATASET.orders)
    else:
        orders = []
        now = _now()
        for item in MOCK_SCENARIOS.values():
            for day in range(30):
                purchased = now - timedelta(days=day, hours=1)
                orders.append(
                    {
                        "AmazonOrderId": f"MOCK-{item['seller_sku']}-{day:02d}",
                        "MarketplaceId": MARKETPLACE_ID,
                        "OrderStatus": "UnexpectedPlatformState"
                        if item["scenario_id"] == "unknown_order_status"
                        else "Shipped",
                        "FulfillmentChannel": "AFN",
                        "PurchaseDate": purchased.isoformat(),
                        "LastUpdateDate": purchased.isoformat(),
                        "OrderItems": [
                            {
                                "SellerSKU": item["seller_sku"],
                                "QuantityOrdered": item["daily_sales"],
                                "Title": item["title"] or item["seller_sku"],
                                "OrderItemId": f"ITEM-{item['seller_sku']}-{day:02d}",
                                "ItemPrice": {
                                    "Amount": str(Decimal(item["price"]) * item["daily_sales"]),
                                    "CurrencyCode": item["currency"],
                                },
                            }
                        ],
                        "OmniSellMockMetadata": _scenario_metadata(item),
                    }
                )
    if LastUpdatedAfter:
        cutoff = datetime.fromisoformat(LastUpdatedAfter.replace("Z", "+00:00"))
        orders = [
            item for item in orders if datetime.fromisoformat(item["LastUpdateDate"].replace("Z", "+00:00")) >= cutoff
        ]
    offset = int(NextToken or 0)
    page = orders[offset : offset + 50]
    next_token = str(offset + 50) if offset + 50 < len(orders) else None
    return {"payload": {"Orders": page, **({"NextToken": next_token} if next_token else {})}}


@app.get("/orders/v0/orders/{order_id}")
def get_order(order_id: str, request: Request) -> dict[str, Any]:
    _authorize_spapi(request)
    if HIGH_FIDELITY_DATASET is not None:
        order = next((item for item in HIGH_FIDELITY_DATASET.orders if item["AmazonOrderId"] == order_id), None)
        if order is None:
            raise HTTPException(
                status_code=404, detail={"errors": [{"code": "NotFound", "message": "Mock order not found"}]}
            )
        return {"payload": order}
    return {
        "payload": {
            "AmazonOrderId": order_id,
            "MarketplaceId": MARKETPLACE_ID,
            "OrderStatus": "Shipped",
            "FulfillmentChannel": "AFN",
            "PurchaseDate": "2026-07-10T08:00:00Z",
            "LastUpdateDate": "2026-07-10T10:00:00Z",
        }
    }


@app.get("/listings/2021-08-01/items/{seller_id}/{seller_sku}")
def get_listing(
    seller_id: str, seller_sku: str, request: Request, marketplaceIds: str, includedData: str = ""
) -> dict[str, Any]:
    _authorize_spapi(request)
    if HIGH_FIDELITY_DATASET is not None:
        product = next((value for value in HIGH_FIDELITY_DATASET.catalog if value["seller_sku"] == seller_sku), None)
        if product is None:
            raise HTTPException(
                status_code=404, detail={"errors": [{"code": "NotFound", "message": "Mock SKU not found"}]}
            )
        return {
            "sku": seller_sku,
            "summaries": [
                {
                    "marketplaceId": marketplaceIds,
                    "asin": product["asin"],
                    "itemName": product["title"],
                    "status": ["BUYABLE"],
                    "lastUpdatedDate": HIGH_FIDELITY_DATASET.reference_time.isoformat(),
                }
            ],
            "offers": [
                {
                    "marketplaceId": marketplaceIds,
                    "offerType": "B2C",
                    "price": {"amount": product["price"], "currencyCode": product["currency"]},
                }
            ],
            "attributes": {"item_name": [{"value": product["title"], "marketplace_id": marketplaceIds}]},
            "issues": [],
            "OmniSellMockMetadata": _high_fidelity_metadata(product),
        }
    item = next((value for value in MOCK_SCENARIOS.values() if value["seller_sku"] == seller_sku), None)
    if item is None:
        raise HTTPException(status_code=404, detail={"errors": [{"code": "NotFound", "message": "Mock SKU not found"}]})
    return {
        "sku": seller_sku,
        "summaries": [
            {
                "marketplaceId": marketplaceIds,
                "asin": item["asin"],
                "itemName": item["title"],
                "status": [item["listing_status"]],
                "lastUpdatedDate": item["last_updated_time"],
            }
        ],
        "offers": [
            {
                "marketplaceId": marketplaceIds,
                "offerType": "B2C",
                "price": {"amount": item["price"], "currencyCode": item["currency"]},
            }
        ],
        "attributes": {
            "item_name": ([{"value": item["title"], "marketplace_id": marketplaceIds}] if item["title"] else [])
        },
        "issues": [],
        "OmniSellMockMetadata": _scenario_metadata(item),
    }


@app.patch("/listings/2021-08-01/items/{seller_id}/{seller_sku}")
def patch_listing(seller_id: str, seller_sku: str, request: Request, marketplaceIds: str) -> dict[str, Any]:
    _authorize_spapi(request)
    return {"sku": seller_sku, "status": "ACCEPTED", "submissionId": "LOCAL-SUBMISSION-1", "issues": []}


@app.get("/fba/inventory/v1/summaries")
def inventory(
    request: Request,
    granularityType: str,
    granularityId: str,
    marketplaceIds: str,
    details: str | None = None,
    nextToken: str | None = None,
) -> dict[str, Any]:
    _authorize_spapi(request)
    if HIGH_FIDELITY_DATASET is not None:
        latest: dict[str, dict[str, Any]] = {}
        for snapshot in HIGH_FIDELITY_DATASET.inventory_snapshots:
            latest[snapshot["seller_sku"]] = snapshot
        products = HIGH_FIDELITY_DATASET.catalog
        offset = int(nextToken or 0)
        page = products[offset : offset + 50]
        return {
            "payload": {
                "granularity": {"granularityType": granularityType, "granularityId": granularityId},
                "inventorySummaries": [
                    {
                        "asin": product["asin"],
                        "fnSku": product["fnsku"],
                        "sellerSku": product["seller_sku"],
                        "condition": "NewItem",
                        "totalQuantity": latest[product["seller_sku"]]["available"]
                        + latest[product["seller_sku"]]["reserved"]
                        + latest[product["seller_sku"]]["inbound"]
                        + latest[product["seller_sku"]]["unsellable"],
                        "inventoryDetails": {
                            "fulfillableQuantity": latest[product["seller_sku"]]["available"],
                            "inboundWorkingQuantity": latest[product["seller_sku"]]["inbound"],
                            "inboundShippedQuantity": 0,
                            "inboundReceivingQuantity": 0,
                            "reservedQuantity": {"totalReservedQuantity": latest[product["seller_sku"]]["reserved"]},
                            "unfulfillableQuantity": {
                                "totalUnfulfillableQuantity": latest[product["seller_sku"]]["unsellable"]
                            },
                        },
                        "lastUpdatedTime": latest[product["seller_sku"]]["observed_at"],
                        "productName": product["title"],
                        "OmniSellMockMetadata": _high_fidelity_metadata(product),
                    }
                    for product in page
                ],
                **({"pagination": {"nextToken": str(offset + 50)}} if offset + 50 < len(products) else {}),
            }
        }
    items = list(MOCK_SCENARIOS.values())
    offset = int(nextToken or 0)
    page = items[offset : offset + 5]
    return {
        "payload": {
            "granularity": {"granularityType": granularityType, "granularityId": granularityId},
            "inventorySummaries": [
                {
                    "asin": item["asin"],
                    "fnSku": f"FNSKU-{item['seller_sku']}",
                    "sellerSku": item["seller_sku"],
                    "condition": "NewItem",
                    "totalQuantity": item["available"] + item["reserved"] + item["inbound"] + item["unsellable"],
                    "inventoryDetails": {
                        "fulfillableQuantity": item["available"],
                        "inboundWorkingQuantity": item["inbound"],
                        "inboundShippedQuantity": 0,
                        "inboundReceivingQuantity": 0,
                        "reservedQuantity": {"totalReservedQuantity": item["reserved"]},
                        "unfulfillableQuantity": {"totalUnfulfillableQuantity": item["unsellable"]},
                    },
                    "lastUpdatedTime": item["last_updated_time"],
                    "productName": item["title"] or item["seller_sku"],
                    "OmniSellMockMetadata": _scenario_metadata(item),
                }
                for item in page
            ],
            **({"pagination": {"nextToken": str(offset + 5)}} if offset + 5 < len(items) else {}),
        }
    }


def _high_fidelity_metadata(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_source": "mock_amazon",
        "verified_amazon_data": False,
        "scenario_id": product["scenario_id"],
        "dataset_fingerprint": HIGH_FIDELITY_DATASET.fingerprint if HIGH_FIDELITY_DATASET else None,
        "category": product["category"],
        "lifecycle": product["lifecycle"],
        "product_cost": product["product_cost"],
    }
