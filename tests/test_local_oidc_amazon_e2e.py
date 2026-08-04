from __future__ import annotations

import inspect
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from api.config import Settings
from api.main import create_app
from infrastructure.database.models import TenantRow


os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY", ""), "127.0.0.1", "localhost"]))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_mock_server() -> tuple[subprocess.Popen, str]:
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "local_e2e_mock.server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Mock HTTP server exited during startup")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.5) as response:
                if response.status == 200:
                    return process, base_url
        except OSError:
            time.sleep(0.05)
    process.terminate()
    raise RuntimeError("Mock HTTP server did not start")


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_oidc_and_amazon_adapter_http_e2e(tmp_path: Path) -> None:
    import asyncio

    async def scenario() -> None:
        process, mock_url = _start_mock_server()
        database_url = f"sqlite:///{tmp_path / 'oidc-amazon.sqlite'}"
        settings = Settings(
            environment="development", auth_mode="oidc", api_key="",
            oidc_issuer="http://mock-cloud:9000", oidc_jwks_url=f"{mock_url}/.well-known/jwks.json", oidc_audience="omnisell-api",
            database_url=database_url, auto_create_schema=True, task_backend="memory",
            amazon_lwa_client_id="local", amazon_lwa_client_secret="local",
            amazon_lwa_token_url=f"{mock_url}/auth/o2/token", amazon_lwa_revoke_url=f"{mock_url}/auth/o2/token/revoke",
            amazon_application_id="local-app", amazon_redirect_uri="http://testserver/api/channels/amazon/callback",
            amazon_sp_api_endpoint=mock_url, amazon_seller_central_url=mock_url,
            secret_encryption_key="O4YwqxP9Py9f3PAE5hycz09mXvw3nSoYP9F3zKRagXU=", amazon_notification_token="local-notification-token",
        )
        app = create_app(settings)
        try:
            for handler in app.router.on_startup:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            with app.state.session_factory.begin() as session:
                if session.get(TenantRow, "local-demo") is None:
                    session.add(TenantRow(id="local-demo", name="Local Demo", slug="local-demo"))

            token = httpx.post(f"{mock_url}/oidc/token", data={"tenant_id": "local-demo", "subject": "owner", "role": "owner", "scope": "read write admin"}).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as api_client:
                auth_response = await api_client.post("/api/channels/amazon/authorize", headers=headers, json={"selling_partner_id": "A1LOCALMOCKSELLER", "marketplace_ids": ["ATVPDKIKX0DER"]})
                assert auth_response.status_code == 200, auth_response.text
                state = parse_qs(urlparse(auth_response.json()["authorization_url"]).query)["state"][0]
                callback = await api_client.get("/api/channels/amazon/callback", params={"state": state, "spapi_oauth_code": "local-spapi-code", "selling_partner_id": "A1LOCALMOCKSELLER"})
                assert callback.status_code == 200, callback.text
                connection_id = callback.json()["connection_id"]
                orders = await api_client.get(
                    f"/api/channels/amazon/{connection_id}/orders",
                    headers=headers,
                    params={"updated_after": (datetime.now(UTC) - timedelta(days=30)).isoformat()},
                )
                inventory = await api_client.get(f"/api/channels/amazon/{connection_id}/inventory", headers=headers)
                listing = await api_client.get(f"/api/channels/amazon/{connection_id}/listings/MOCK-HEALTHY", headers=headers)
                assert orders.status_code == inventory.status_code == listing.status_code == 200
                assert len(orders.json()["items"]) == 50
                assert inventory.json()["items"][0]["available"] == 70
                assert inventory.json()["items"][0]["platform_extensions"]["data_source"] == "mock_amazon"
                assert listing.json()["external_product_id"] == "B000000001"
        finally:
            for handler in app.router.on_shutdown:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    asyncio.run(scenario())
