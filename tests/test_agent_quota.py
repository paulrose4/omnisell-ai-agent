from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agents.quota import AgentQuotaBackendUnavailable, InMemoryAgentQuotaLimiter
from infrastructure.agent_quota import RedisAgentQuotaLimiter


def test_tenant_agent_quota_enforces_minute_and_daily_limits() -> None:
    current = [datetime(2026, 7, 18, 9, 0, tzinfo=UTC)]
    limiter = InMemoryAgentQuotaLimiter(
        per_minute=2,
        per_day=3,
        now=lambda: current[0],
    )

    first = asyncio.run(limiter.consume(tenant_id="tenant-a", workflow_type="operations_brief_analysis"))
    second = asyncio.run(limiter.consume(tenant_id="tenant-a", workflow_type="customer_case_analysis"))
    minute_denied = asyncio.run(limiter.consume(tenant_id="tenant-a", workflow_type="listing_optimization_analysis"))

    assert first.allowed is True
    assert second.allowed is True
    assert minute_denied.allowed is False
    assert minute_denied.scope == "minute"
    assert minute_denied.retry_after_seconds == 60

    current[0] += timedelta(minutes=1)
    third = asyncio.run(limiter.consume(tenant_id="tenant-a", workflow_type="reconciliation_investigation"))
    daily_denied = asyncio.run(limiter.consume(tenant_id="tenant-a", workflow_type="operations_brief_analysis"))

    assert third.allowed is True
    assert daily_denied.allowed is False
    assert daily_denied.scope == "day"
    assert daily_denied.retry_after_seconds > 0


def test_tenant_agent_quota_is_tenant_scoped_and_concurrency_bounded() -> None:
    limiter = InMemoryAgentQuotaLimiter(per_minute=3, per_day=100)

    async def run() -> tuple[list[bool], bool]:
        results = await asyncio.gather(*(
            limiter.consume(tenant_id="tenant-a", workflow_type="operations_brief_analysis")
            for _ in range(20)
        ))
        other_tenant = await limiter.consume(
            tenant_id="tenant-b", workflow_type="operations_brief_analysis"
        )
        return [item.allowed for item in results], other_tenant.allowed

    allowed, other_tenant_allowed = asyncio.run(run())

    assert sum(allowed) == 3
    assert other_tenant_allowed is True


class FakeRedis:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or [1, "", 1, 1]
        self.error = error
        self.calls = []

    async def eval(self, *args):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result


def test_redis_agent_quota_uses_one_atomic_script_and_hashed_tenant_key() -> None:
    redis = FakeRedis()
    limiter = RedisAgentQuotaLimiter(redis=redis, per_minute=5, per_day=50)

    decision = asyncio.run(limiter.consume(
        tenant_id="tenant-sensitive-id",
        workflow_type="customer_case_analysis",
    ))

    assert decision.allowed is True
    assert len(redis.calls) == 1
    script, key_count, minute_key, daily_key, *_ = redis.calls[0]
    assert key_count == 2
    assert "tenant-sensitive-id" not in minute_key
    assert "tenant-sensitive-id" not in daily_key
    assert script.index("if minute_count") < script.index("INCR")
    assert script.index("if daily_count") < script.index("INCR")


def test_redis_agent_quota_fails_closed_when_backend_is_unavailable() -> None:
    limiter = RedisAgentQuotaLimiter(
        redis=FakeRedis(error=ConnectionError("redis unavailable")),
        per_minute=5,
        per_day=50,
    )

    with pytest.raises(AgentQuotaBackendUnavailable):
        asyncio.run(limiter.consume(
            tenant_id="tenant-a",
            workflow_type="operations_brief_analysis",
        ))
