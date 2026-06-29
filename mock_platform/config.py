from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")

DEFAULT_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "omnisell_agent")
DEFAULT_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "dev_secret_key_12345")
DEFAULT_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:3000/oauth/callback")

JWT_SECRET = os.getenv("MOCK_PLATFORM_JWT_SECRET", "omnisell_mock_platform_dev_secret")
JWT_ISSUER = os.getenv("MOCK_PLATFORM_JWT_ISSUER", "omnisell-mock-platform")

ACCESS_TOKEN_TTL_SECONDS = 6 * 60 * 60
AUTHORIZATION_CODE_TTL_SECONDS = 10 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

DEFAULT_SCOPES = [
    "product.read",
    "product.write",
    "order.read",
    "order.write",
    "price.read",
]

OAUTH_CLIENTS: dict[str, dict[str, Any]] = {
    DEFAULT_CLIENT_ID: {
        "client_secret": DEFAULT_CLIENT_SECRET,
        "redirect_uris": sorted(
            {
                DEFAULT_REDIRECT_URI,
                "http://localhost:3000/oauth/callback",
                "http://127.0.0.1:3000/oauth/callback",
                "http://localhost:8002/oauth/callback",
                "http://127.0.0.1:8002/oauth/callback",
                "http://localhost:8080/callback",
            }
        ),
        "scopes": DEFAULT_SCOPES,
        "seller_id": "seller_001",
    }
}

DATA_LOCK = RLock()
DATA_STORE: dict[str, list[dict[str, Any]]] = {
    "products": [],
    "orders": [],
    "users": [],
}
AUDIT_QUEUE: list[dict[str, Any]] = []


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json_file(filename: str) -> list[dict[str, Any]]:
    path = DATA_DIR / filename
    if not path.exists():
        raise RuntimeError(f"Mock data file not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(payload, list):
        raise RuntimeError(f"Mock data file must contain a JSON array: {path}")

    return payload


def load_data() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DATA_LOCK:
        DATA_STORE["products"] = load_json_file("products.json")
        DATA_STORE["orders"] = load_json_file("orders.json")
        DATA_STORE["users"] = load_json_file("users.json")


def save_collection(name: str) -> None:
    if name not in DATA_STORE:
        raise KeyError(f"Unknown data collection: {name}")

    with DATA_LOCK:
        path = DATA_DIR / f"{name}.json"
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(DATA_STORE[name], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)


def list_collection(name: str) -> list[dict[str, Any]]:
    if name not in DATA_STORE:
        raise KeyError(f"Unknown data collection: {name}")

    with DATA_LOCK:
        return deepcopy(DATA_STORE[name])


def get_oauth_client(client_id: str) -> dict[str, Any] | None:
    client = OAUTH_CLIENTS.get(client_id)
    return deepcopy(client) if client else None


def is_redirect_uri_allowed(client_id: str, redirect_uri: str) -> bool:
    client = OAUTH_CLIENTS.get(client_id)
    if not client:
        return False
    return redirect_uri in client["redirect_uris"]


def get_default_redirect_uri(client_id: str) -> str | None:
    client = OAUTH_CLIENTS.get(client_id)
    if not client:
        return None
    redirect_uris = client.get("redirect_uris", [])
    return redirect_uris[0] if redirect_uris else None


def get_default_seller_id(client_id: str | None = None) -> str:
    if client_id and client_id in OAUTH_CLIENTS:
        return str(OAUTH_CLIENTS[client_id].get("seller_id", "seller_001"))

    with DATA_LOCK:
        if DATA_STORE["users"]:
            return str(DATA_STORE["users"][0].get("seller_id", "seller_001"))

    return "seller_001"
