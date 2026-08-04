from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from enum import Enum
from threading import RLock
from typing import Any, TypeVar

T = TypeVar("T")
_CIRCUIT_BREAKERS: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = RLock()


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit breaker blocks a call."""


class CircuitBreaker:
    """Circuit breaker with CLOSED -> OPEN -> HALF_OPEN -> CLOSED transitions."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 3,
        name: str = "default",
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive.")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be non-negative.")
        if half_open_max_calls <= 0:
            raise ValueError("half_open_max_calls must be positive.")

        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.name = name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.half_open_call_count = 0
        self._lock = RLock()

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a sync or async callable under circuit-breaker protection."""
        self._before_call()
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return self._call_async_result(result)  # type: ignore[return-value]
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    async def acall(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Async version of call for coroutine tools."""
        self._before_call()
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _call_async_result(self, awaitable: Any) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(awaitable)
            self._on_success()
            return result

        if loop.is_running():
            self._on_success()
            return awaitable
        result = loop.run_until_complete(awaitable)
        self._on_success()
        return result

    def _before_call(self) -> None:
        with self._lock:
            if self.state == CircuitState.OPEN:
                if (
                    self.last_failure_time is not None
                    and time.monotonic() - self.last_failure_time >= self.recovery_timeout
                ):
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_call_count = 0
                else:
                    raise CircuitOpenError(f"Circuit breaker {self.name} is OPEN.")

            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_call_count >= self.half_open_max_calls:
                    raise CircuitOpenError(f"Circuit breaker {self.name} HALF_OPEN call budget exceeded.")
                self.half_open_call_count += 1

    def _on_success(self) -> None:
        with self._lock:
            if self.state in {CircuitState.CLOSED, CircuitState.HALF_OPEN}:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_call_count = 0
                self.last_failure_time = None

    def _on_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN

    def reset(self) -> None:
        """Reset the circuit breaker to CLOSED."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.last_failure_time = None
            self.half_open_call_count = 0


def get_circuit_breaker(name: str) -> CircuitBreaker:
    with _REGISTRY_LOCK:
        if name not in _CIRCUIT_BREAKERS:
            _CIRCUIT_BREAKERS[name] = CircuitBreaker(name=name)
        return _CIRCUIT_BREAKERS[name]


def circuit_breaker_registry() -> dict[str, CircuitBreaker]:
    with _REGISTRY_LOCK:
        return dict(_CIRCUIT_BREAKERS)


if __name__ == "__main__":
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0)
    print(breaker.call(lambda: "ok"))
