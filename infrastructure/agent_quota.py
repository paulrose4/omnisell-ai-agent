from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from agents.quota import AgentQuotaBackendUnavailable, AgentQuotaDecision

_CONSUME_QUOTA_SCRIPT = """
local minute_count = tonumber(redis.call('GET', KEYS[1]) or '0')
local daily_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if minute_count >= tonumber(ARGV[1]) then
  return {0, 'minute', minute_count, daily_count}
end
if daily_count >= tonumber(ARGV[2]) then
  return {0, 'day', minute_count, daily_count}
end
minute_count = redis.call('INCR', KEYS[1])
if minute_count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[3]) end
daily_count = redis.call('INCR', KEYS[2])
if daily_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[4]) end
return {1, '', minute_count, daily_count}
"""


class RedisAgentQuotaLimiter:
    def __init__(self, *, redis: Redis, per_minute: int, per_day: int) -> None:
        if per_minute <= 0 or per_day < per_minute:
            raise ValueError("Agent quotas require 0 < per_minute <= per_day")
        self.redis = redis
        self.per_minute = per_minute
        self.per_day = per_day

    @classmethod
    def from_url(cls, *, redis_url: str, per_minute: int, per_day: int) -> RedisAgentQuotaLimiter:
        return cls(
            redis=Redis.from_url(redis_url, decode_responses=True),
            per_minute=per_minute,
            per_day=per_day,
        )

    async def consume(self, *, tenant_id: str, workflow_type: str) -> AgentQuotaDecision:
        del workflow_type
        current = datetime.now(UTC)
        tenant_digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        minute_key = f"omnisell:agent-quota:minute:{tenant_digest}:{current.strftime('%Y%m%d%H%M')}"
        daily_key = f"omnisell:agent-quota:day:{tenant_digest}:{current.strftime('%Y%m%d')}"
        minute_ttl = _seconds_until_next_minute(current) + 5
        daily_ttl = _seconds_until_next_day(current) + 60
        try:
            result = await self.redis.eval(
                _CONSUME_QUOTA_SCRIPT,
                2,
                minute_key,
                daily_key,
                self.per_minute,
                self.per_day,
                minute_ttl,
                daily_ttl,
            )
        except Exception as exc:
            raise AgentQuotaBackendUnavailable() from exc
        allowed = bool(int(result[0]))
        scope = str(result[1] or "") or None
        retry_after = 0
        if scope == "minute":
            retry_after = _seconds_until_next_minute(current)
        elif scope == "day":
            retry_after = _seconds_until_next_day(current)
        return AgentQuotaDecision(
            allowed=allowed,
            scope=scope,
            retry_after_seconds=retry_after,
            minute_count=int(result[2]),
            daily_count=int(result[3]),
        )

    async def close(self) -> None:
        await self.redis.aclose()


def _seconds_until_next_minute(current: datetime) -> int:
    next_minute = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(1, math.ceil((next_minute - current).total_seconds()))


def _seconds_until_next_day(current: datetime) -> int:
    next_day = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(1, math.ceil((next_day - current).total_seconds()))
