from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Callable, Literal, Protocol


QuotaScope = Literal["minute", "day"]


@dataclass(frozen=True)
class AgentQuotaDecision:
    allowed: bool
    scope: QuotaScope | None
    retry_after_seconds: int
    minute_count: int
    daily_count: int


class AgentQuotaExceeded(RuntimeError):
    def __init__(self, *, scope: QuotaScope, retry_after_seconds: int) -> None:
        self.scope = scope
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"Tenant Agent {scope} quota exhausted.")


class AgentQuotaBackendUnavailable(RuntimeError):
    def __init__(self, *, retry_after_seconds: int = 30) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__("Tenant Agent quota backend is unavailable.")


class AgentQuotaLimiter(Protocol):
    async def consume(self, *, tenant_id: str, workflow_type: str) -> AgentQuotaDecision: ...


@dataclass
class _TenantQuotaState:
    minute_bucket: str
    minute_count: int
    day_bucket: str
    daily_count: int


class InMemoryAgentQuotaLimiter:
    def __init__(
        self,
        *,
        per_minute: int,
        per_day: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if per_minute <= 0 or per_day < per_minute:
            raise ValueError("Agent quotas require 0 < per_minute <= per_day")
        self.per_minute = per_minute
        self.per_day = per_day
        self.now = now or (lambda: datetime.now(UTC))
        self._states: dict[str, _TenantQuotaState] = {}
        self._lock = Lock()

    async def consume(self, *, tenant_id: str, workflow_type: str) -> AgentQuotaDecision:
        del workflow_type
        current = self.now().astimezone(UTC)
        minute_bucket = current.strftime("%Y%m%d%H%M")
        day_bucket = current.strftime("%Y%m%d")
        with self._lock:
            state = self._states.get(tenant_id)
            if state is None:
                state = _TenantQuotaState(minute_bucket, 0, day_bucket, 0)
                self._states[tenant_id] = state
            if state.minute_bucket != minute_bucket:
                state.minute_bucket = minute_bucket
                state.minute_count = 0
            if state.day_bucket != day_bucket:
                state.day_bucket = day_bucket
                state.daily_count = 0
            if state.minute_count >= self.per_minute:
                return AgentQuotaDecision(
                    allowed=False,
                    scope="minute",
                    retry_after_seconds=_seconds_until_next_minute(current),
                    minute_count=state.minute_count,
                    daily_count=state.daily_count,
                )
            if state.daily_count >= self.per_day:
                return AgentQuotaDecision(
                    allowed=False,
                    scope="day",
                    retry_after_seconds=_seconds_until_next_day(current),
                    minute_count=state.minute_count,
                    daily_count=state.daily_count,
                )
            state.minute_count += 1
            state.daily_count += 1
            return AgentQuotaDecision(
                allowed=True,
                scope=None,
                retry_after_seconds=0,
                minute_count=state.minute_count,
                daily_count=state.daily_count,
            )


def _seconds_until_next_minute(current: datetime) -> int:
    next_minute = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(1, math.ceil((next_minute - current).total_seconds()))


def _seconds_until_next_day(current: datetime) -> int:
    next_day = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(1, math.ceil((next_day - current).total_seconds()))
