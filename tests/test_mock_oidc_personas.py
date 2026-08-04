from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi.testclient import TestClient

import local_e2e_mock.server as mock_server
from local_e2e_mock.server import AUTHORIZATION_CODES, app


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_mock_oidc_authorization_selects_approver_persona() -> None:
    AUTHORIZATION_CODES.clear()
    verifier = "mock-approver-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    redirect_uri = "http://localhost:3000/api/auth/callback/oidc"
    client = TestClient(app)

    authorization = client.get(
        "/oidc/authorize",
        params={
            "client_id": "omnisell-ops",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid profile email",
            "state": "state-1",
            "nonce": "nonce-1",
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "login_hint": "local-approver",
        },
        follow_redirects=False,
    )
    assert authorization.status_code == 302
    code = parse_qs(urlparse(authorization.headers["location"]).query)["code"][0]

    token = client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": "omnisell-ops",
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    access_claims = jwt.decode(token.json()["access_token"], options={"verify_signature": False})
    id_claims = jwt.decode(token.json()["id_token"], options={"verify_signature": False})
    assert access_claims["sub"] == "local-approver"
    assert access_claims["roles"] == ["approver"]
    assert id_claims["email"] == "approver@local.omnisell.test"
    assert id_claims["amr"] == ["pwd", "mfa"]


def test_mock_oidc_accepts_configured_local_frontend_origin(monkeypatch) -> None:
    monkeypatch.setattr(mock_server, "OIDC_REDIRECT_ORIGIN", "http://localhost:3100")
    client = TestClient(app)

    authorization = client.get(
        "/oidc/authorize",
        params={
            "client_id": "omnisell-ops",
            "redirect_uri": "http://localhost:3100/api/auth/callback/oidc",
            "response_type": "code",
            "code_challenge": "configured-origin-challenge",
            "code_challenge_method": "S256",
            "login_hint": "local-operator",
        },
        follow_redirects=False,
    )

    assert authorization.status_code == 302
