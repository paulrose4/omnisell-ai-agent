from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Awaitable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple in-memory IP rate limiter."""

    def __init__(self, app: object, requests_per_minute: int = 60) -> None:
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client_host = request.client.host if request.client else "unknown"
        now = time.monotonic()
        hits = self._hits[client_host]

        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

        if len(hits) >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": f"Too many requests. Limit is {self.requests_per_minute} requests per minute.",
                },
            )

        hits.append(now)
        return await call_next(request)


class RequestTelemetryMiddleware(BaseHTTPMiddleware):
    """Attach request IDs, collect latency metrics, and emit structured audit logs."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
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
                audit_logger.log_operation(
                    action="api_request",
                    status="success" if status_code < 500 else "failure",
                    details={
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                    },
                    actor="api",
                    risk_level="low",
                )
