from __future__ import annotations

import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

DEFAULT_MOCK_PLATFORM_URL = "http://localhost:8002"
DEFAULT_REDIRECT_URI = "http://localhost:3000/oauth/callback"


class PlatformAPIError(RuntimeError):
    """Raised when the Mock Platform API returns an error or invalid response."""


def get_platform_base_url() -> str:
    """Return the configured Mock Platform base URL."""
    return os.getenv("MOCK_PLATFORM_URL", DEFAULT_MOCK_PLATFORM_URL).rstrip("/")


def get_oauth_credentials() -> tuple[str, str, str]:
    """Return OAuth client_id, client_secret, and redirect_uri for the Mock Platform."""
    return (
        os.getenv("OAUTH_CLIENT_ID", "omnisell_agent"),
        os.getenv("OAUTH_CLIENT_SECRET", "dev_secret_key_12345"),
        os.getenv("OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI),
    )


async def get_access_token(base_url: str | None = None) -> str:
    """Obtain a Bearer access token from the Mock Platform using OAuth2 authorization code flow."""
    resolved_base_url = (base_url or get_platform_base_url()).rstrip("/")
    client_id, client_secret, redirect_uri = get_oauth_credentials()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        authorize_response = await client.get(
            f"{resolved_base_url}/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": "autogen-tool",
                "scope": "product.read product.write order.read order.write price.read",
            },
        )
        if authorize_response.status_code not in {302, 303, 307, 308}:
            raise PlatformAPIError(
                f"OAuth authorize failed: {authorize_response.status_code} {authorize_response.text}"
            )

        location = authorize_response.headers.get("location", "")
        query = parse_qs(urlparse(location).query)
        if "error" in query:
            raise PlatformAPIError(f"OAuth authorize error: {query['error'][0]}")
        code = query.get("code", [None])[0]
        if not code:
            raise PlatformAPIError("OAuth authorize response did not include an authorization code.")

        token_response = await client.post(
            f"{resolved_base_url}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
        )
        if token_response.status_code >= 400:
            raise PlatformAPIError(f"OAuth token failed: {token_response.status_code} {token_response.text}")

        payload = token_response.json()
        token = payload.get("access_token")
        if not token:
            raise PlatformAPIError("OAuth token response did not include access_token.")
        return str(token)


async def _request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    access_token: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    resolved_base_url = (base_url or get_platform_base_url()).rstrip("/")
    token = access_token or await get_access_token(resolved_base_url)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=method,
            url=f"{resolved_base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
        )

    if response.status_code >= 400:
        raise PlatformAPIError(f"{method} {path} failed: {response.status_code} {response.text}")
    return response.json()


async def get_product_list(
    offset: int = 0,
    page_size: int = 20,
    status: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Fetch a paginated product list from the Mock Shopee Open API."""
    params: dict[str, Any] = {"offset": offset, "page_size": page_size}
    if status:
        params["status"] = status
    return await _request("GET", "/api/v2/product/list", params=params, access_token=access_token)


async def get_product_detail(product_id: str, access_token: str | None = None) -> dict[str, Any]:
    """Fetch full product details by product_id from the Mock Shopee Open API."""
    return await _request("GET", "/api/v2/product/detail", params={"product_id": product_id}, access_token=access_token)


async def get_product_price(product_id: str, access_token: str | None = None) -> dict[str, Any]:
    """Fetch the current and pending product price by product_id."""
    return await _request("GET", "/api/v2/product/price", params={"product_id": product_id}, access_token=access_token)


async def update_product(
    product_id: str,
    title: str | None = None,
    description: str | None = None,
    price: float | None = None,
    stock: int | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Update product fields; price changes above 10 percent return pending_approval from the platform."""
    body = {
        key: value
        for key, value in {
            "title": title,
            "description": description,
            "price": price,
            "stock": stock,
        }.items()
        if value is not None
    }
    if not body:
        raise ValueError("At least one of title, description, price, stock must be provided.")
    return await _request(
        "PUT", "/api/v2/product/update", params={"product_id": product_id}, json_body=body, access_token=access_token
    )


async def create_product(
    title: str,
    description: str,
    price: float,
    currency: str,
    stock: int,
    images: list[str],
    category: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Create a new product in the Mock Shopee Open API."""
    body = {
        "title": title,
        "description": description,
        "price": price,
        "currency": currency,
        "stock": stock,
        "images": images,
        "category": category,
    }
    return await _request("POST", "/api/v2/product/create", json_body=body, access_token=access_token)


async def delete_product(product_id: str, access_token: str | None = None) -> dict[str, Any]:
    """Soft-delete a product by changing its platform status to DELETED."""
    return await _request(
        "DELETE", "/api/v2/product/delete", params={"product_id": product_id}, access_token=access_token
    )


async def get_order_list(
    offset: int = 0,
    page_size: int = 20,
    status: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Fetch a paginated order list from the Mock Shopee Open API."""
    params: dict[str, Any] = {"offset": offset, "page_size": page_size}
    if status:
        params["status"] = status
    return await _request("GET", "/api/v2/order/list", params=params, access_token=access_token)


async def update_order_status(order_id: str, status: str, access_token: str | None = None) -> dict[str, Any]:
    """Update an order status using the Mock Platform's legal status transition rules."""
    return await _request(
        "PUT", "/api/v2/order/status", params={"order_id": order_id, "status": status}, access_token=access_token
    )


async def approve_return(return_id: str, access_token: str | None = None) -> dict[str, Any]:
    """Approve a pending return request by return_id."""
    return await _request(
        "POST", "/api/v2/order/return/approve", params={"return_id": return_id}, access_token=access_token
    )


if __name__ == "__main__":
    print(f"mock_platform_url={get_platform_base_url()}")
    print(f"oauth_client_id={get_oauth_credentials()[0]}")
