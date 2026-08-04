from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class Bucket:
    rate: float
    burst: int
    tokens: float
    updated_at: float


class OperationRateLimiter:
    """Token buckets isolated by seller, application, operation, and region."""

    def __init__(self, *, rate_multiplier: float = 1.0) -> None:
        if rate_multiplier < 1:
            raise ValueError("rate_multiplier must be at least 1")
        self.rate_multiplier = rate_multiplier
        self._buckets: dict[tuple[str, str, str, str], Bucket] = {}
        self._locks: dict[tuple[str, str, str, str], asyncio.Lock] = {}

    async def acquire(self, key: tuple[str, str, str, str], *, rate: float, burst: int) -> None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            effective_rate = rate * self.rate_multiplier
            bucket = self._buckets.setdefault(key, Bucket(rate=effective_rate, burst=burst, tokens=float(burst), updated_at=time.monotonic()))
            while True:
                now = time.monotonic()
                elapsed = now - bucket.updated_at
                bucket.tokens = min(float(bucket.burst), bucket.tokens + elapsed * bucket.rate)
                bucket.updated_at = now
                if bucket.tokens >= 1:
                    bucket.tokens -= 1
                    return
                await asyncio.sleep(max((1 - bucket.tokens) / bucket.rate, 0.01))
