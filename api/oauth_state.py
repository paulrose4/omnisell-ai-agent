from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from uuid import uuid4

from redis import Redis


@dataclass(frozen=True)
class OAuthState:
    tenant_id: str
    nonce: str
    expires_at: int


class OAuthStateManager:
    def __init__(self, secret: str, ttl_seconds: int = 600, redis_url: str | None = None) -> None:
        if not secret:
            raise ValueError("OAuth state signing secret is required")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds
        self.used_nonces: set[str] = set()
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None

    def issue(self, tenant_id: str) -> str:
        nonce = uuid4().hex
        expires_at = int(time.time()) + self.ttl_seconds
        value = f"{tenant_id}.{nonce}.{expires_at}"
        signature = hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{value}.{signature}"

    def consume(self, value: str) -> OAuthState:
        try:
            tenant_id, nonce, expires_raw, signature = value.rsplit(".", 3)
            signed_value = f"{tenant_id}.{nonce}.{expires_raw}"
            expected = hmac.new(self.secret, signed_value.encode("utf-8"), hashlib.sha256).hexdigest()
            expires_at = int(expires_raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid OAuth state") from exc
        if not hmac.compare_digest(expected, signature) or expires_at < int(time.time()):
            raise ValueError("Invalid or expired OAuth state")
        if self.redis is not None:
            ttl = max(expires_at - int(time.time()), 1)
            accepted = self.redis.set(f"omnisell:oauth-state:{nonce}", tenant_id, nx=True, ex=ttl)
            if not accepted:
                raise ValueError("OAuth state was already used")
        else:
            if nonce in self.used_nonces:
                raise ValueError("OAuth state was already used")
            self.used_nonces.add(nonce)
        return OAuthState(tenant_id=tenant_id, nonce=nonce, expires_at=expires_at)
