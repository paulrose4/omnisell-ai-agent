from __future__ import annotations

import argparse
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


def require_status(response: httpx.Response, expected: int) -> httpx.Response:
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text}")
    return response


def with_login_hint(url: str, persona: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["login_hint"] = persona
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the local Auth.js OIDC authorization-code session flow")
    parser.add_argument("--frontend", default="http://localhost:3000")
    parser.add_argument("--persona", default="local-owner")
    parser.add_argument("--expected-email", default="owner@local.omnisell.test")
    args = parser.parse_args()

    with httpx.Client(follow_redirects=False, trust_env=False, timeout=15.0) as client:
        csrf_response = require_status(client.get(f"{args.frontend}/api/auth/csrf"), 200)
        csrf_token = csrf_response.json()["csrfToken"]
        signin_response = require_status(
            client.post(
                f"{args.frontend}/api/auth/signin/oidc",
                data={"csrfToken": csrf_token, "callbackUrl": f"{args.frontend}/dashboard"},
                headers={"X-Auth-Return-Redirect": "1"},
            ),
            200,
        )
        authorization_url = with_login_hint(signin_response.json()["url"], args.persona)
        authorization_response = require_status(client.get(authorization_url), 302)
        callback_url = authorization_response.headers.get("location")
        if not callback_url:
            raise RuntimeError("OIDC authorization did not return a callback URL")
        callback_response = require_status(client.get(callback_url), 302)
        destination_url = callback_response.headers.get("location")
        if destination_url != f"{args.frontend}/dashboard":
            raise RuntimeError(f"Unexpected post-login destination: {destination_url}")
        dashboard_response = require_status(client.get(destination_url), 200)
        if "OmniSell Control Center" not in dashboard_response.text:
            raise RuntimeError("Authenticated dashboard did not render the OmniSell shell")
        session_response = require_status(client.get(f"{args.frontend}/api/auth/session"), 200)
        session = session_response.json()
        if session.get("user", {}).get("email") != args.expected_email:
            raise RuntimeError(f"Unexpected local OIDC identity: {session.get('user')}")
        if not session.get("accessToken"):
            raise RuntimeError("Auth.js session does not contain the OIDC access token")
        snapshots_response = require_status(client.get(f"{args.frontend}/api/listing-quality/snapshots"), 200)
        snapshots = snapshots_response.json()
        if not isinstance(snapshots, dict) or not isinstance(snapshots.get("items"), list):
            raise RuntimeError("Authenticated Listing BFF returned an invalid response contract")

    print(json.dumps({"status": "ok", "persona": args.persona, "email": args.expected_email, "dashboard": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
