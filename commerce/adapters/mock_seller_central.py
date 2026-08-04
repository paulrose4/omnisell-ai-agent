from __future__ import annotations

from typing import Any, Protocol

import httpx


class MockSellerCentralPort(Protocol):
    async def list_scenarios(self) -> dict[str, Any]: ...
    async def reset(self) -> dict[str, Any]: ...
    async def trigger(self, scenario_id: str) -> dict[str, Any]: ...
    async def load_high_fidelity(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def execute(self, *, seller_sku: str, action_type: str, changes: dict[str, Any]) -> dict[str, Any]: ...


class HttpMockSellerCentralAdapter:
    def __init__(self, base_url: str, *, timeout_seconds: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def list_scenarios(self) -> dict[str, Any]:
        return await self._request("GET", "/mock/scenarios")

    async def reset(self) -> dict[str, Any]:
        return await self._request("POST", "/mock/scenarios/reset")

    async def trigger(self, scenario_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/mock/scenarios/{scenario_id}/trigger")

    async def load_high_fidelity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/mock/high-fidelity/load", json=payload)

    async def execute(self, *, seller_sku: str, action_type: str, changes: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/mock/seller-central/actions",
            json={"seller_sku": seller_sku, "action_type": action_type, "changes": changes},
        )

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
            response = await client.request(method, f"{self.base_url}{path}", json=json)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Mock Seller Central returned a non-object response")
        return payload
