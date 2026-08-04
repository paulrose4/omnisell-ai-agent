from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .config import Settings, get_settings


@dataclass
class TokenState:
    access_token: str
    refresh_token: str | None
    expires_at: float
    refresh_expires_at: float | None = None
    scope: str = ""


class OAuthClient:
    """OAuth2 authorization-code client with token lifecycle management."""

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._token_state: TokenState | None = None
        self._refresh_margin_seconds = 60

    async def get_token(self) -> str:
        """Return a valid access token, refreshing or fetching as needed."""
        now = time.time()
        if self._token_state and self._token_state.expires_at - self._refresh_margin_seconds > now:
            return self._token_state.access_token

        if self._token_state and self._token_state.refresh_token:
            try:
                await self._refresh_access_token()
                if self._token_state:
                    return self._token_state.access_token
            except Exception:
                self._token_state = None

        await self._fetch_new_token()
        if not self._token_state:
            raise RuntimeError("Failed to obtain OAuth access token.")
        return self._token_state.access_token

    async def _fetch_new_token(self) -> TokenState:
        """Fetch a new token pair via OAuth2 Authorization Code Grant."""
        async with self._get_client() as client:
            authorize_response = await client.get(
                f"{self.settings.mock_platform_url.rstrip('/')}/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": self.settings.oauth_client_id,
                    "redirect_uri": self.settings.oauth_redirect_uri,
                    "state": "api-gateway",
                    "scope": "product.read product.write order.read order.write price.read",
                },
                follow_redirects=False,
            )
            if authorize_response.status_code not in {302, 303, 307, 308}:
                raise RuntimeError(
                    f"OAuth authorize failed: {authorize_response.status_code} {authorize_response.text}"
                )

            location = authorize_response.headers.get("location", "")
            query = parse_qs(urlparse(location).query)
            if "error" in query:
                raise RuntimeError(f"OAuth authorize error: {query['error'][0]}")
            code = query.get("code", [None])[0]
            if not code:
                raise RuntimeError("OAuth authorize response did not include code.")

            token_response = await client.post(
                f"{self.settings.mock_platform_url.rstrip('/')}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.settings.oauth_client_id,
                    "client_secret": self.settings.oauth_client_secret,
                    "redirect_uri": self.settings.oauth_redirect_uri,
                },
            )
            if token_response.status_code >= 400:
                raise RuntimeError(f"OAuth token failed: {token_response.status_code} {token_response.text}")

            self._token_state = self._parse_token_response(token_response.json())
            return self._token_state

    async def _refresh_access_token(self) -> TokenState:
        """Refresh the access token using refresh-token rotation."""
        if not self._token_state or not self._token_state.refresh_token:
            return await self._fetch_new_token()

        async with self._get_client() as client:
            response = await client.post(
                f"{self.settings.mock_platform_url.rstrip('/')}/oauth/token/refresh",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._token_state.refresh_token,
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(f"OAuth refresh failed: {response.status_code} {response.text}")

            self._token_state = self._parse_token_response(response.json())
            return self._token_state

    def _parse_token_response(self, payload: dict[str, Any]) -> TokenState:
        now = time.time()
        return TokenState(
            access_token=str(payload["access_token"]),
            refresh_token=payload.get("refresh_token"),
            expires_at=now + int(payload.get("expires_in", 0)),
            refresh_expires_at=now + int(payload.get("refresh_expires_in", 0))
            if payload.get("refresh_expires_in")
            else None,
            scope=str(payload.get("scope", "")),
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return _BorrowedAsyncClient(self._client)  # type: ignore[return-value]
        return httpx.AsyncClient(timeout=30.0)


class _BorrowedAsyncClient:
    """Async context wrapper that does not close a borrowed httpx client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


if __name__ == "__main__":
    print(OAuthClient().settings.mock_platform_url)
