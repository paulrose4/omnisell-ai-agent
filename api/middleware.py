from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from infrastructure.database.session import tenant_context

from .security import bind_tool_caller_context, unbind_tool_caller_context


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """IP rate limiter using Redis in production and process memory in development."""

    def __init__(self, app: ASGIApp, requests_per_minute: int = 60, redis_url: str | None = None) -> None:
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client_host = request.client.host if request.client else "unknown"
        allowed = await self._allow_redis(client_host) if self.redis is not None else self._allow_memory(client_host)
        if not allowed:
            return JSONResponse(
                status_code=429, content={"error": "rate_limited", "message": "Request rate limit exceeded."}
            )
        return await call_next(request)

    def _allow_memory(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.requests_per_minute:
            return False
        hits.append(now)
        return True

    async def _allow_redis(self, key: str) -> bool:
        now_ms = int(time.time() * 1000)
        window_start = now_ms - self.window_seconds * 1000
        redis_key = f"omnisell:rate:{key}"
        member = f"{now_ms}:{uuid.uuid4().hex}"
        redis = self.redis
        assert redis is not None
        pipe = redis.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {member: now_ms})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, self.window_seconds + 5)
        try:
            _, _, count, _ = await pipe.execute()
            return int(count) <= self.requests_per_minute
        except Exception:
            return False


class RequestTelemetryMiddleware(BaseHTTPMiddleware):
    """Attach request IDs, collect latency metrics, and emit structured audit logs."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        tool_context_token = bind_tool_caller_context(request)
        started = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            status_code = response.status_code if response is not None else 500
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Response-Time-MS"] = str(latency_ms)
            metrics = getattr(request.app.state, "metrics", None)
            if metrics is not None:
                await metrics.record_request(path=request.url.path, status_code=status_code, latency_ms=latency_ms)
            audit_logger = getattr(request.app.state, "audit_logger", None)
            if audit_logger is not None:
                caller = getattr(request.state, "caller_context", None)
                tenant_id = getattr(caller, "tenant_id", None)
                if tenant_id and tenant_id != "unbound":
                    with tenant_context(tenant_id):
                        audit_logger.log_operation(
                            action="api_request",
                            status="success" if status_code < 500 else "failure",
                            details={
                                "request_id": request_id,
                                "tenant_id": tenant_id,
                                "method": request.method,
                                "path": request.url.path,
                                "status_code": status_code,
                                "latency_ms": latency_ms,
                            },
                            actor=getattr(caller, "user_id", "api"),
                            risk_level="low",
                        )
            unbind_tool_caller_context(tool_context_token)
